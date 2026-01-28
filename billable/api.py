"""API endpoints for the billable module.

Implemented using Django Ninja for integration with the main project API.
"""

from __future__ import annotations

import logging
from typing import List

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from ninja import Router
from ninja.security import HttpBearer

from .conf import billable_settings
from .models import Order, Product, UserProduct, TrialHistory, Referral, ExternalIdentity
from .schemas import (
    BalanceFeatureSchema, 
    CommonResponse,
    IdentifySchemaIn,
    IdentifySchemaOut,
    OrderConfirmSchema, 
    OrderCreateSchema, 
    OrderSchema,
    ProductSchema, 
    QuotaCheckSchema, 
    QuotaConsumeSchema,
    TrialGrantSchema,
    UserProductSchema,
    ReferralAssignSchema
)
from .services import OrderService, QuotaService, UserProductService, ProductService

User = get_user_model()
logger = logging.getLogger(__name__)


class APIKeyAuth(HttpBearer):
    """Token authentication in the Authorization: Bearer <token> header."""
    
    def authenticate(self, request, token):
        if token == billable_settings.API_TOKEN:
            return token
        return None


# Router for billing with mandatory authorization
router = Router(tags=["billing"], auth=APIKeyAuth())


async def _resolve_external_to_user_id(provider: str, external_id: str) -> int:
    """
    Resolve (provider, external_id) to billable user_id.

    Creates ExternalIdentity and User if missing. Same semantics as /identify.
    """
    identity, _ = await ExternalIdentity.objects.aupdate_or_create(
        provider=provider,
        external_id=external_id.strip(),
        defaults={},
    )
    if identity.user_id:
        return identity.user_id
    username_value = f"billable_{provider}_{external_id.strip()}"
    user, _ = await User.objects.aget_or_create(
        username=username_value,
        defaults={"first_name": "", "last_name": ""},
    )
    identity.user_id = user.id
    await identity.asave(update_fields=["user_id", "updated_at"])
    return user.id


@router.post("/identify", response={200: IdentifySchemaOut, 400: CommonResponse})
async def identify(request, data: IdentifySchemaIn):
    """
    Identify an external identity and ensure a local User exists.

    Contract:
    - provider defaults to "default" when not provided.
    - User is always created/linked; response always includes user_id.
    - Trial eligibility is checked only by {provider: external_id}.
    """
    provider_value = data.provider or "default"
    external_id_value = str(data.external_id).strip()
    profile = data.profile or {}

    identity, created_identity = await ExternalIdentity.objects.aupdate_or_create(
        provider=provider_value,
        external_id=external_id_value,
        defaults={"metadata": profile},
    )

    created_user = False
    user_id = identity.user_id

    if not user_id:
        # User model may be standard AbstractUser. Use username as unique key per (provider, external_id).
        username_value = f"billable_{provider_value}_{external_id_value}"
        user, created_user = await User.objects.aget_or_create(
            username=username_value,
            defaults={
                "first_name": profile.get("first_name") or "",
                "last_name": profile.get("last_name") or "",
            },
        )

        identity.user_id = user.id
        identity.metadata = profile
        await identity.asave(update_fields=["user_id", "metadata", "updated_at"])
        user_id = user.id

    trial_eligible = not await TrialHistory.has_used_trial_async(identities={provider_value: external_id_value})

    return {
        "user_id": user_id,
        "identity_id": identity.id,
        "provider": provider_value,
        "external_id": external_id_value,
        "created_identity": created_identity,
        "created_user": created_user,
        "trial_eligible": trial_eligible,
        "metadata": identity.metadata or {},
    }


# --- Product Endpoints ---

@router.get("/products", response=List[ProductSchema])
async def list_products(request):
    """List of active products."""
    return await ProductService.aget_active_products()


@router.get("/products/{sku}", response={200: ProductSchema, 404: CommonResponse})
async def get_product(request, sku: str):
    """Get product by SKU."""
    product = await ProductService.aget_product_by_sku(sku)
    if not product:
        return 404, {"success": False, "message": "Product not found"}
    return product


# --- Quota and Balance Endpoints ---

@router.get("/balance", response=BalanceFeatureSchema)
async def check_user_balance(request, user_id: int | None = None, feature: str = "", external_id: str | None = None, provider: str | None = None):
    """Check if a feature can be used."""
    provider_value = provider or "default"
    resolved_user_id = user_id

    if resolved_user_id is None and external_id:
        identity = await ExternalIdentity.objects.filter(provider=provider_value, external_id=external_id).values("user_id").afirst()
        resolved_user_id = identity["user_id"] if identity else None

    if resolved_user_id is None:
        return {"can_use": False, "feature": feature, "remaining": 0, "message": "user_id is required (or provide external_id + provider mapped to a user)"}

    if external_id:
        await ExternalIdentity.objects.aupdate_or_create(
            provider=provider_value,
            external_id=external_id,
            defaults={"user_id": resolved_user_id},
        )

    result = await QuotaService.acheck_quota(resolved_user_id, feature)
    return result


@router.get("/user-products", response={200: List[UserProductSchema], 400: CommonResponse})
async def list_user_products(request, user_id: int | None = None, feature: str = "", external_id: str | None = None, provider: str | None = None):
    """
    List active user products (optionally filtered by feature).

    Notes:
    - Resolve user either by explicit user_id or by (provider + external_id) mapping.
    - Product features are returned via product.metadata.features in the response.
    """
    provider_value = provider or "default"
    resolved_user_id = user_id

    if resolved_user_id is None and external_id:
        identity = await ExternalIdentity.objects.filter(
            provider=provider_value,
            external_id=external_id,
        ).values("user_id").afirst()
        resolved_user_id = identity["user_id"] if identity else None

    if resolved_user_id is None:
        return 400, {"success": False, "message": "user_id is required (or provide external_id + provider mapped to a user)"}

    if external_id:
        await ExternalIdentity.objects.aupdate_or_create(
            provider=provider_value,
            external_id=external_id,
            defaults={"user_id": resolved_user_id},
        )

    feature_value = feature or None
    return await UserProductService.aget_user_active_products(user_id=resolved_user_id, feature=feature_value)


@router.post("/quota/consume", response={200: CommonResponse, 400: CommonResponse})
async def consume_user_quota(request, data: QuotaConsumeSchema):
    """Consume quota."""
    provider_value = data.provider or "default"
    resolved_user_id = data.user_id

    if resolved_user_id is None and data.external_id:
        identity = await ExternalIdentity.objects.filter(provider=provider_value, external_id=data.external_id).values("user_id").afirst()
        resolved_user_id = identity["user_id"] if identity else None

    if resolved_user_id is None:
        return 400, {"success": False, "message": "user_id is required (or provide external_id + provider mapped to a user)", "data": {}}

    if data.external_id:
        await ExternalIdentity.objects.aupdate_or_create(
            provider=provider_value,
            external_id=data.external_id,
            defaults={"user_id": resolved_user_id, "metadata": data.metadata or {}},
        )

    result = await QuotaService.aconsume_quota(
        user_id=resolved_user_id,
        feature=data.feature,
        action_type=data.action_type,
        action_id=data.action_id,
        idempotency_key=data.idempotency_key,
        metadata=data.metadata
    )
    if not result.get("success"):
        return 400, {"success": False, "message": result.get("message"), "data": result}
    return {"success": True, "message": "Quota consumed", "data": result}


@router.post("/grants", response={200: CommonResponse, 400: CommonResponse})
async def grant_trial(request, data: TrialGrantSchema):
    """Grant a trial period or a specific product by SKU."""
    provider_value = data.provider or "default"
    resolved_external_id = data.external_id
    resolved_provider = provider_value
    resolved_user_id = data.user_id

    if resolved_user_id is None and resolved_external_id:
        identity = await ExternalIdentity.objects.filter(provider=resolved_provider, external_id=resolved_external_id).values("user_id").afirst()
        resolved_user_id = identity["user_id"] if identity else None

    if resolved_user_id is None:
        return 400, {"success": False, "message": "user_id is required (or provide external_id + provider mapped to a user)", "data": {}}

    if resolved_external_id:
        await ExternalIdentity.objects.aupdate_or_create(
            provider=resolved_provider,
            external_id=resolved_external_id,
            defaults={"user_id": resolved_user_id, "metadata": {}},
        )

    identities = {resolved_provider: resolved_external_id} if resolved_external_id else None
    result = await QuotaService.aactivate_trial(
        user_id=resolved_user_id,
        identities=identities,
        sku=data.sku
    )
    if not result.get("success"):
        return 400, {"success": False, "message": result.get("message"), "data": result}
    return {"success": True, "message": "Trial granted", "data": result}


# --- Order Endpoints ---

@router.post("/orders", response={200: OrderSchema, 400: CommonResponse})
async def create_order(request, data: OrderCreateSchema):
    """
    Create a new order.
    
    Args:
        request: HTTP request object.
        data: Order creation data with user_id, products list, and optional metadata.
        
    Returns:
        OrderSchema on success, CommonResponse with error on failure.
    """
    # Convert SKUs to Product objects
    product_items = []
    invalid_skus = []
    for item in data.products:
        sku = item.get("sku")
        if not sku:
            continue
        product = await ProductService.aget_product_by_sku(sku)
        if not product:
            invalid_skus.append(sku)
            logger.warning(f"Product with SKU '{sku}' not found during order creation")
            continue
        product_items.append({
            "product": product,
            "quantity": item.get("quantity", 1)
        })
    
    if not product_items:
        error_message = "No valid products found"
        if invalid_skus:
            error_message = f"Products not found: {', '.join(invalid_skus)}"
        return 400, {"success": False, "message": error_message}

    provider_value = data.provider or "default"
    resolved_user_id = data.user_id

    if resolved_user_id is None and data.external_id:
        identity = await ExternalIdentity.objects.filter(provider=provider_value, external_id=data.external_id).values("user_id").afirst()
        resolved_user_id = identity["user_id"] if identity else None

    if resolved_user_id is None:
        return 400, {"success": False, "message": "user_id is required (or provide external_id + provider mapped to a user)"}

    if data.external_id:
        await ExternalIdentity.objects.aupdate_or_create(
            provider=provider_value,
            external_id=data.external_id,
            defaults={"user_id": resolved_user_id, "metadata": data.metadata or {}},
        )

    order = await OrderService.acreate_order(
        user_id=resolved_user_id,
        product_items=product_items,
        metadata=data.metadata
    )
    # Prefetch items with product for serialization
    order = await Order.objects.prefetch_related("items__product").select_related("user").aget(id=order.id)
    # Serialize order to dict using service method
    order_dict = await OrderService.aserialize_order_to_dict(order)
    return order_dict


@router.post("/orders/{order_id}/confirm", response={200: CommonResponse, 400: CommonResponse, 404: CommonResponse})
async def confirm_order_payment(request, order_id: int, data: OrderConfirmSchema):
    """
    Confirm order payment and activate associated products.

    This endpoint is called after a successful payment notification.
    It transitions the order to 'paid' status and creates UserProduct records.
    Returns the full order data including items with SKUs.

    Args:
        request: HTTP request object.
        order_id: Order ID to confirm payment for.
        data: Payment confirmation data with payment_id and payment_method.
        
    Returns:
        CommonResponse with order data on success, error response on failure.
    """
    success = await OrderService.aprocess_payment(
        order_id=order_id,
        payment_id=data.payment_id,
        payment_method=data.payment_method
    )
    if not success:
        return 400, {"success": False, "message": "Failed to process payment", "data": {}}
    
    try:
        order = await Order.objects.prefetch_related(
            "items__product"
        ).select_related("user").aget(id=order_id)
    except Order.DoesNotExist:
        logger.error(f"Order {order_id} not found during payment confirmation")
        return 404, {"success": False, "message": "Order not found", "data": {}}
    
    # Serialize order to dict using service method
    order_dict = await OrderService.aserialize_order_to_dict(order)
    order_data = OrderSchema.model_validate(order_dict).model_dump(mode="json")
    
    return {
        "success": True, 
        "message": "Order paid and products activated", 
        "data": order_data
    }


@router.get("/orders/{order_id}", response={200: OrderSchema, 404: CommonResponse})
async def get_order(request, order_id: int):
    """
    Get order information by ID.
    
    Args:
        request: HTTP request object.
        order_id: Order ID to retrieve.
        
    Returns:
        OrderSchema on success, CommonResponse with error on failure.
    """
    try:
        order = await Order.objects.prefetch_related("items__product").select_related("user").aget(id=order_id)
        # Serialize order to dict using service method
        order_dict = await OrderService.aserialize_order_to_dict(order)
        return order_dict
    except Order.DoesNotExist:
        logger.error(f"Order {order_id} not found")
        return 404, {"success": False, "message": "Order not found"}


@router.post("/referrals", response={200: CommonResponse, 400: CommonResponse})
async def assign_referral(request, data: ReferralAssignSchema):
    """
    Establish a referral link between referrer and referee users.

    Accepts either (referrer_id, referee_id) or (provider, referrer_external_id, referee_external_id).
    In the external-id mode both identities are resolved via ExternalIdentity (user created if missing).

    Args:
        request: HTTP request object.
        data: Referral assignment data.

    Returns:
        CommonResponse with success status and referral data.
    """
    by_ids = data.referrer_id is not None and data.referee_id is not None
    by_external = (
        data.provider is not None
        and data.referrer_external_id is not None
        and data.referee_external_id is not None
    )
    if not by_ids and not by_external:
        return 400, {
            "success": False,
            "message": "Provide either (referrer_id, referee_id) or (provider, referrer_external_id, referee_external_id)",
        }

    if by_ids:
        referrer_user_id, referee_user_id = data.referrer_id, data.referee_id
    else:
        provider_value = data.provider or "default"
        referrer_user_id = await _resolve_external_to_user_id(
            provider_value, data.referrer_external_id
        )
        referee_user_id = await _resolve_external_to_user_id(
            provider_value, data.referee_external_id
        )

    if referrer_user_id == referee_user_id:
        return 400, {"success": False, "message": "Referrer and referee cannot be the same user"}

    try:
        referral, created = await Referral.objects.aget_or_create(
            referrer_id=referrer_user_id,
            referee_id=referee_user_id,
            defaults={"metadata": data.metadata or {}},
        )
        return {"success": True, "message": "Referral assigned", "data": {"created": created}}
    except IntegrityError:
        logger.error(
            f"Integrity error assigning referral: referrer_id={referrer_user_id}, referee_id={referee_user_id}",
            exc_info=True,
        )
        return 400, {"success": False, "message": "Referral relationship already exists or invalid user IDs"}
    except Exception:
        logger.error(
            f"Unexpected error assigning referral: referrer_id={referrer_user_id}, referee_id={referee_user_id}",
            exc_info=True,
        )
        return 400, {"success": False, "message": "Failed to assign referral"}
