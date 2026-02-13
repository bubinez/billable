"""REST API endpoints for the billable billing engine.

Implemented with Django Ninja. All endpoints require Bearer token authentication
(BILLABLE_API_TOKEN). The API exposes identity resolution, product catalog,
quota/balance checks, wallet, orders, exchange (internal currency), referrals,
and demo trial grant. Docstrings are used to generate OpenAPI descriptions.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Dict, Any
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from ninja import Router
from ninja.security import HttpBearer

from .conf import billable_settings
from .models import (
    Order, 
    Product, 
    TrialHistory, 
    Referral, 
    ExternalIdentity, 
    Offer, 
    QuotaBatch, 
    Transaction
)
from .schemas import (
    BalanceFeatureSchema, 
    CommonResponse,
    ExchangeSchema,
    IdentifySchemaIn,
    IdentifySchemaOut,
    OrderConfirmSchema, 
    OrderCreateSchema, 
    OrderRefundSchema,
    OrderSchema,
    ProductSchema, 
    QuotaConsumeSchema,
    TrialGrantSchema,
    ActiveBatchSchema,
    ReferralAssignSchema,
    OfferSchema,
    QuotaBatchSchema,
    TransactionSchema,
    WalletBalanceSchema,
    CustomerMergeSchema,
    CustomerMergeResponse
)
from .services import OrderService, TransactionService, BalanceService, ProductService, CustomerService

User = get_user_model()
logger = logging.getLogger(__name__)


class APIKeyAuth(HttpBearer):
    """Bearer token authentication for the billing API.

    Validates the Authorization: Bearer <token> header against BILLABLE_API_TOKEN.
    """

    def authenticate(self, request, token):
        """Validate token and return it if it matches the configured API token.

        Args:
            request: The HTTP request (unused).
            token: The Bearer token from the Authorization header.

        Returns:
            The token string if valid; None otherwise.
        """
        if token == billable_settings.API_TOKEN:
            return token
        return None


# Router for billing with mandatory authorization
router = Router(tags=["billing"], auth=APIKeyAuth())


async def aresolve_user_id_by_identity(provider: str, external_id: str) -> int:
    """Resolve (provider, external_id) to local billing user_id (create if missing).

    Creates ExternalIdentity and User if missing. Same semantics as POST /identify.
    Used internally when a POST/write endpoint accepts external_id + provider.

    Args:
        provider: Identity provider (e.g. telegram, n8n). Used for ExternalIdentity lookup.
        external_id: Stable external identifier (e.g. telegram user id).

    Returns:
        The primary key of the billing User (settings.AUTH_USER_MODEL).
    
    Raises:
        ValueError: If external_id is empty or whitespace-only after stripping.
    """
    external_id_stripped = external_id.strip()
    if not external_id_stripped:
        raise ValueError("external_id cannot be empty or whitespace-only")
    
    identity, _ = await ExternalIdentity.objects.aupdate_or_create(
        provider=provider,
        external_id=external_id_stripped,
        defaults={},
    )
    if identity.user_id:
        return identity.user_id
    username_value = f"billable_{provider}_{external_id_stripped}"
    user, _ = await User.objects.aget_or_create(
        username=username_value,
        defaults={"first_name": "", "last_name": ""},
    )
    identity.user_id = user.id
    await identity.asave(update_fields=["user_id", "updated_at"])
    return user.id


@router.post("/identify", response={200: IdentifySchemaOut, 400: CommonResponse})
async def aidentify(request, data: IdentifySchemaIn):
    """Identify an external identity and ensure a local billing user exists.

    Creates or updates ExternalIdentity and links it to a User (creating the user
    if missing). Call this at the start of a flow before other billing calls.
    Returns user_id for use in subsequent requests, plus trial_eligible flag.

    Request body: provider (optional, default 'default'), external_id (required), profile (optional).
    """
    provider_value = data.provider or "default"
    # external_id is already validated and stripped by IdentifySchemaIn validator
    external_id_value = data.external_id
    profile = data.profile or {}

    identity, created_identity = await ExternalIdentity.objects.aupdate_or_create(
        provider=provider_value,
        external_id=external_id_value,
        defaults={"metadata": profile},
    )

    created_user = False
    user_id = identity.user_id

    if not user_id:
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

    trial_eligible = not await TrialHistory.ahas_used_trial(identities={provider_value: external_id_value})

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
async def alist_products(request):
    """List all active products from the catalog.

    Returns products with is_active=True. Used for admin or catalog display.
    """
    products = await ProductService.aget_active_products()
    return products


@router.get("/products/{product_key}", response={200: ProductSchema, 404: CommonResponse})
async def aget_product(request, product_key: str):
    """Get a single product by its product_key.

    Path: product_key — unique identifier (e.g. diamonds, vip_access).
    Returns 404 if the product does not exist or is inactive.
    """
    product = await ProductService.aget_product_by_key(product_key)
    if not product:
        return 404, {"success": False, "message": "Product not found"}
    return product


# --- Quota and Balance Endpoints ---

@router.get("/balance", response=BalanceFeatureSchema)
async def acheck_user_balance(request, user_id: int | None = None, product_key: str = "", external_id: str | None = None, provider: str | None = None):
    """Check whether the user has quota for a product_key (without consuming).

    Query params: user_id (optional), product_key (optional), external_id (optional), provider (optional).
    Provide either user_id or (external_id + provider). Returns can_use, remaining, and message.
    """
    provider_value = provider or "default"
    resolved_user_id = user_id

    if resolved_user_id is None and external_id:
        user = await ExternalIdentity.aget_user_by_identity(
            external_id=external_id, provider=provider_value
        )
        resolved_user_id = user.id if user else None

    if resolved_user_id is None:
        return {"can_use": False, "product_key": product_key, "remaining": 0, "message": "user_id is required"}

    # Normalize product_key to uppercase
    normalized_product_key = product_key.upper() if product_key else ""
    result = await TransactionService.acheck_quota(resolved_user_id, normalized_product_key)
    return result


@router.get("/user-products", response={200: List[ActiveBatchSchema], 400: CommonResponse})
async def alist_user_products(request, user_id: int | None = None, product_key: str = "", external_id: str | None = None, provider: str | None = None):
    """List active quota batches for the user (user-products / inventory).

    Query params: user_id (optional), product_key (optional filter), external_id (optional), provider (optional).
    Provide either user_id or (external_id + provider). Returns list of ActiveBatchSchema.
    """
    provider_value = provider or "default"
    resolved_user_id = user_id

    if resolved_user_id is None and external_id:
        user = await ExternalIdentity.aget_user_by_identity(
            external_id=external_id, provider=provider_value
        )
        resolved_user_id = user.id if user else None

    if resolved_user_id is None:
        return 400, {"success": False, "message": "user_id is required"}

    # Normalize product_key to uppercase
    normalized_product_key = product_key.upper() if product_key else None
    return await BalanceService.aget_user_active_products(user_id=resolved_user_id, product_key=normalized_product_key)


@router.post("/wallet/consume", response={200: CommonResponse, 400: CommonResponse})
async def aconsume_user_quota(request, data: QuotaConsumeSchema):
    """Consume one unit of quota for a product_key (admin or server-to-server).

    Request body: user_id or (external_id + provider), product_key, action_type, optional action_id,
    idempotency_key, metadata. Uses FIFO consumption. Returns 400 if user not resolved or insufficient quota.
    """
    provider_value = data.provider or "default"
    resolved_user_id = data.user_id

    if resolved_user_id is None and data.external_id:
        resolved_user_id = await aresolve_user_id_by_identity(
            provider=provider_value, external_id=data.external_id
        )

    if resolved_user_id is None:
        return 400, {"success": False, "message": "user_id is required"}

    # Normalize product_key to uppercase
    normalized_product_key = data.product_key.upper() if data.product_key else ""
    result = await TransactionService.aconsume_quota(
        user_id=resolved_user_id,
        product_key=normalized_product_key,
        action_type=data.action_type,
        action_id=data.action_id,
        idempotency_key=data.idempotency_key,
        metadata=data.metadata
    )
    if not result.get("success"):
        return 400, {"success": False, "message": result.get("message"), "data": result}
    return {"success": True, "message": "Quota consumed", "data": result}


@router.post("/demo/trial-grant", response={200: CommonResponse, 400: CommonResponse})
async def ademo_grant_trial(request, data: TrialGrantSchema):
    """(Demo) Grant a trial offer by SKU with abuse protection.

    Reference implementation: uses TrialHistory to prevent double-granting, then
    TransactionService.agrant_offer and marks trial as used. For production,
    move this logic to a dedicated PromotionService in your application code.

    Request body: user_id or (external_id + provider), sku (offer to grant). Returns 400 if trial already used or offer not found.
    """
    provider_value = data.provider or "default"
    resolved_external_id = data.external_id
    resolved_user_id = data.user_id

    if resolved_user_id is None and resolved_external_id:
        resolved_user_id = await aresolve_user_id_by_identity(
            provider=provider_value, external_id=resolved_external_id
        )

    if resolved_user_id is None:
        return 400, {"success": False, "message": "user_id is required"}

    # 1. Fraud Prevention: Check if trial was already used
    identities = data.identities if data.identities else (
        {provider_value: resolved_external_id} if resolved_external_id else None
    )
    if await TrialHistory.ahas_used_trial(identities=identities):
        return 400, {
            "success": False, 
            "message": "Trial already used", 
            "data": {"error": "trial_already_used"}
        }

    # 2. Find the trial offer (you should create an Offer with sku="trial" in your DB)
    try:
        # Normalize SKU to uppercase
        normalized_sku = data.sku.upper() if data.sku else None
        offer = await Offer.objects.filter(sku=normalized_sku, is_active=True).afirst() if normalized_sku else None
        if not offer:
            return 400, {"success": False, "message": "Trial offer not found"}
    except Exception as e:
        logger.error(f"Error finding trial offer: {e}")
        return 400, {"success": False, "message": "Trial offer not found"}

    # 3. Grant the offer using TransactionService
    trial_metadata = {**(data.metadata or {}), "identities": identities}
    batches = await TransactionService.agrant_offer(
        user_id=resolved_user_id,
        offer=offer,
        source="trial_activation",
        metadata=trial_metadata,
    )

    # 4. Mark trial as used in TrialHistory
    if identities:
        for id_type, id_value in identities.items():
            if id_value:
                await TrialHistory.objects.acreate(
                    identity_type=id_type,
                    identity_hash=TrialHistory.generate_identity_hash(id_value),
                    trial_plan_name=offer.name
                )

    # 5. Send signal for notifications
    from .signals import trial_activated
    product_names = [batch.product.name async for batch in QuotaBatch.objects.filter(id__in=[b.id for b in batches]).select_related('product').aiterator()]
    trial_activated.send(sender=TransactionService, user_id=resolved_user_id, products=product_names)

    return {"success": True, "message": "Trial granted", "data": {"products": product_names, "metadata": trial_metadata}}



# --- Catalog & Entitlement v2 Endpoints ---

@router.get("/catalog/{sku}", response={200: OfferSchema, 404: CommonResponse})
async def aget_catalog_offer(request, sku: str):
    """Get a single active offer by SKU.

    Path: sku — unique offer identifier (e.g. off_credits_100).
    Returns 404 if offer does not exist or is inactive.
    Response fields: sku, name, price, currency, description, image, is_active, items, metadata.
    """
    # Normalize SKU to uppercase
    normalized_sku = sku.upper() if sku else ""
    offer = await (
        Offer.objects.filter(sku=normalized_sku, is_active=True)
        .prefetch_related("items__product")
        .afirst()
    )
    if not offer:
        return 404, {"success": False, "message": "Offer not found"}
    return offer


@router.get("/catalog", response=List[OfferSchema])
async def alist_catalog(request):
    """List all active offers (catalog) with nested offer items and products.

    Returns offers with is_active=True, prefetched items and product details.
    Each offer includes: sku, name, price, currency, description, image, is_active, items, metadata.
    Optional query param: sku (repeatable) — filter by SKU list; preserves order.
    If sku not provided, returns full catalog.
    """
    sku_list = request.GET.getlist("sku")
    if not sku_list:
        offers = []
        async for offer in Offer.objects.filter(is_active=True).prefetch_related("items__product").aiterator():
            offers.append(offer)
        return offers

    # Normalize all SKUs to uppercase
    normalized_sku_list = [sku.upper() for sku in sku_list]
    qs = (
        Offer.objects.filter(sku__in=normalized_sku_list, is_active=True)
        .prefetch_related("items__product")
    )
    by_sku: dict[str, Offer] = {}
    async for offer in qs.aiterator():
        by_sku[offer.sku] = offer
    # Return in original order, matching by normalized SKU
    return [by_sku[normalized_sku] for normalized_sku in normalized_sku_list if normalized_sku in by_sku]


@router.get("/wallet", response={200: WalletBalanceSchema, 404: CommonResponse})
async def aget_wallet(request, user_id: int | None = None, external_id: str | None = None, provider: str | None = None):
    """Get aggregated wallet balance: user_id and map of product_key -> total remaining quantity.

    Query params: user_id (optional), external_id (optional), provider (optional).
    Provide either user_id or (external_id + provider). Resolves user and sums remaining_quantity per product_key.
    """
    provider_value = provider or "default"
    uid = user_id
    if not uid and external_id:
        user = await ExternalIdentity.aget_user_by_identity(
            external_id=external_id, provider=provider_value
        )
        uid = user.id if user else None
    
    if not uid:
         return 404, {"success": False, "message": "User not found"}

    balances = {}
    async for batch in QuotaBatch.objects.filter(
        user_id=uid, 
        state=QuotaBatch.State.ACTIVE
    ).select_related('product').aiterator():
        key = batch.product.product_key or f"prod_{batch.product.id}"
        balances[key] = balances.get(key, 0) + batch.remaining_quantity

    return {"user_id": uid, "balances": balances}


@router.get("/wallet/batches", response={200: List[QuotaBatchSchema], 404: CommonResponse})
async def aget_wallet_batches(request, user_id: int | None = None, external_id: str | None = None, provider: str | None = None):
    """List detailed active quota batches for the user (wallet entries with state and expiry).

    Query params: user_id (optional), external_id (optional), provider (optional).
    Returns all ACTIVE batches; each batch has product, initial/remaining quantity, expires_at, state.
    """
    provider_value = provider or "default"
    uid = user_id
    if not uid and external_id:
        user = await ExternalIdentity.aget_user_by_identity(
            external_id=external_id, provider=provider_value
        )
        uid = user.id if user else None
    
    if not uid:
         return 404, {"success": False, "message": "User not found"}

    batches = []
    async for batch in QuotaBatch.objects.filter(
        user_id=uid, 
        state=QuotaBatch.State.ACTIVE
    ).select_related('product').aiterator():
        batches.append(batch)
    return batches


@router.get("/wallet/transactions", response={200: List[TransactionSchema], 404: CommonResponse})
async def aget_wallet_transactions(
    request, 
    user_id: int | None = None, 
    external_id: str | None = None, 
    provider: str | None = None,
    product_key: str | None = None,
    action_type: str | None = None,
    date_from: datetime | None = None,
):
    """List transaction history (ledger) for the user with optional filters.

    Query params: user_id (optional), external_id (optional), provider (optional),
    product_key (optional), action_type (optional), date_from (optional).
    Returns up to 100 transactions, newest first.
    """
    provider_value = provider or "default"
    uid = user_id
    if not uid and external_id:
        user = await ExternalIdentity.aget_user_by_identity(
            external_id=external_id, provider=provider_value
        )
        uid = user.id if user else None
    
    if not uid:
         return 404, {"success": False, "message": "User not found"}

    txs = []
    qs = Transaction.objects.filter(user_id=uid)
    if product_key:
        # Normalize product_key to uppercase
        normalized_product_key = product_key.upper()
        qs = qs.filter(quota_batch__product__product_key=normalized_product_key)
    if action_type:
        qs = qs.filter(action_type=action_type)
    if date_from:
        qs = qs.filter(created_at__gte=date_from)
        
    qs = qs.order_by('-created_at')[:100]
    async for tx in qs.aiterator():
        txs.append(tx)
    return txs


@router.post("/exchange", response={200: CommonResponse, 400: CommonResponse, 404: CommonResponse})
async def aexchange_offer(request, data: ExchangeSchema):
    """Exchange internal currency for an offer (spend balance, grant SKU).

    Atomically debits the internal-currency product (FIFO) and grants the target offer
    via TransactionService. Request body: user_id or (external_id + provider), sku.
    Returns 404 if offer not found; 400 if user not resolved or insufficient balance.
    """
    provider_value = data.provider or "default"
    resolved_user_id = data.user_id

    if resolved_user_id is None and data.external_id:
        resolved_user_id = await aresolve_user_id_by_identity(
            provider=provider_value, external_id=data.external_id
        )

    if resolved_user_id is None:
        return 400, {"success": False, "message": "user_id is required"}

    try:
        # Normalize SKU to uppercase
        normalized_sku = data.sku.upper() if data.sku else ""
        offer = await Offer.objects.aget(sku=normalized_sku)
        result = await TransactionService.aexchange(
            user_id=resolved_user_id, offer=offer, metadata=data.metadata
        )
        if not result.get("success", True):
            return 400, {"success": False, "message": result.get("message", "Exchange failed")}
    except Offer.DoesNotExist:
        return 404, {"success": False, "message": "Offer not found"}
    except Exception as e:
        return 400, {"success": False, "message": str(e)}

    return {"success": True, "message": "Exchange successful", "data": result}


# --- Order Endpoints ---

@router.post("/orders", response={200: OrderSchema, 400: CommonResponse})
async def acreate_order(request, data: OrderCreateSchema):
    """Create a new order (financial intent before sending invoice to client).

    Request body: user_id or (external_id + provider), items (list of {sku, quantity}), optional metadata.
    Order is created in PENDING status; pass order_id to payment provider, then confirm via POST /orders/{id}/confirm.
    """
    provider_value = data.provider or "default"
    resolved_user_id = data.user_id

    if resolved_user_id is None and data.external_id:
        resolved_user_id = await aresolve_user_id_by_identity(
            provider=provider_value, external_id=data.external_id
        )

    if resolved_user_id is None:
        return 400, {"success": False, "message": "user_id is required"}

    try:
        order = await OrderService.acreate_order(
            user_id=resolved_user_id,
            items=data.items,
            metadata=data.metadata
        )
    except ValueError as e:
        return 400, {"success": False, "message": str(e)}

    order = await Order.objects.prefetch_related("items__offer__items__product").select_related("user").aget(id=order.id)
    order_dict = await OrderService.aserialize_order_to_dict(order)
    return order_dict


@router.post("/orders/{order_id}/confirm", response={200: CommonResponse, 400: CommonResponse, 404: CommonResponse})
async def aconfirm_order_payment(request, order_id: int, data: OrderConfirmSchema):
    """Confirm payment for an order and grant products (called by payment webhook).

    Transitions order to PAID and calls TransactionService.grant_offer(source='purchase') for each item.
    Request body: payment_id (for idempotency), payment_method. Returns 400 if processing fails; 404 if order not found.
    """
    success = await OrderService.aprocess_payment(
        order_id=order_id,
        payment_id=data.payment_id,
        payment_method=data.payment_method
    )
    if not success:
        return 400, {"success": False, "message": "Failed to process payment"}
    
    try:
        order = await Order.objects.prefetch_related(
            "items__offer__items__product"
        ).select_related("user").aget(id=order_id)
    except Order.DoesNotExist:
        return 404, {"success": False, "message": "Order not found"}
    
    order_dict = await OrderService.aserialize_order_to_dict(order)
    from .schemas import OrderSchema
    order_data = OrderSchema.model_validate(order_dict).model_dump(mode="json")
    
    return {
        "success": True, 
        "message": "Order paid and products activated", 
        "data": order_data
    }


@router.post("/orders/{order_id}/refund", response={200: CommonResponse, 400: CommonResponse, 404: CommonResponse})
async def arefund_order(request, order_id: int, data: OrderRefundSchema):
    """Refund a paid order and revoke associated products.

    Changes order status to REFUNDED and creates DEBIT transactions for any 
    remaining quantity in the batches granted by this order.
    """
    success = await OrderService.arefund_order(
        order_id=order_id,
        reason=data.reason
    )
    if not success:
        return 400, {"success": False, "message": "Failed to process refund. Order might not be in PAID status."}
    
    try:
        order = await Order.objects.prefetch_related(
            "items__offer__items__product"
        ).select_related("user").aget(id=order_id)
    except Order.DoesNotExist:
        return 404, {"success": False, "message": "Order not found"}
    
    order_dict = await OrderService.aserialize_order_to_dict(order)
    from .schemas import OrderSchema
    order_data = OrderSchema.model_validate(order_dict).model_dump(mode="json")
    
    return {
        "success": True, 
        "message": "Order refunded and products revoked", 
        "data": order_data
    }


@router.get("/orders/{order_id}", response={200: OrderSchema, 404: CommonResponse})
async def aget_order(request, order_id: int):
    """Get a single order by ID with line items and totals.

    Path: order_id — order primary key. Returns 404 if order does not exist.
    """
    try:
        order = await Order.objects.prefetch_related("items__offer__items__product").select_related("user").aget(id=order_id)
        order_dict = await OrderService.aserialize_order_to_dict(order)
        return order_dict
    except Order.DoesNotExist:
        return 404, {"success": False, "message": "Order not found"}


# --- Referral Endpoints ---

@router.post("/referrals", response={200: CommonResponse, 400: CommonResponse})
async def aassign_referral(request, data: ReferralAssignSchema):
    """Create a referral link between referrer and referee.

    Two modes: (referrer_id, referee_id) or (provider, referrer_external_id, referee_external_id).
    By external IDs: only existing ExternalIdentity records are used; if either identity is missing, returns 400.
    Returns 400 if invalid, duplicate, or identity not found.
    """
    by_ids = data.referrer_id is not None and data.referee_id is not None
    by_external = (
        data.provider is not None
        and data.referrer_external_id is not None
        and data.referee_external_id is not None
    )
    if not by_ids and not by_external:
        return 400, {"success": False, "message": "Provide valid identifiers"}

    if by_ids:
        referrer_user_id, referee_user_id = data.referrer_id, data.referee_id
        # Explicitly verify user existence to satisfy "not processed if user does not exist" requirement
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if not await User.objects.filter(pk=referrer_user_id).aexists():
            return 400, {"success": False, "message": "Referrer user not found in database"}
        if not await User.objects.filter(pk=referee_user_id).aexists():
            return 400, {"success": False, "message": "Referee user not found in database"}
    else:
        provider_value = data.provider or "default"
        referrer_user = await ExternalIdentity.aget_user_by_identity(
            external_id=data.referrer_external_id, provider=provider_value
        )
        referee_user = await ExternalIdentity.aget_user_by_identity(
            external_id=data.referee_external_id, provider=provider_value
        )
        if referrer_user is None:
            return 400, {"success": False, "message": "Referrer identity not found"}
        if referee_user is None:
            return 400, {"success": False, "message": "Referee identity not found"}
        referrer_user_id = referrer_user.id
        referee_user_id = referee_user.id

    if referrer_user_id == referee_user_id:
        return 400, {"success": False, "message": "Referrer and referee cannot be same"}

    try:
        referral, created = await Referral.objects.aget_or_create(
            referrer_id=referrer_user_id,
            referee_id=referee_user_id,
            defaults={"metadata": data.metadata or {}},
        )
        if created:
             from .signals import referral_attached
             referral_attached.send(sender=None, referral=referral)
        return {
            "success": True,
            "message": "Referral assigned",
            "data": {"created": created, "referral_id": referral.id, "metadata": referral.metadata or {}},
        }
    except IntegrityError:
        return 400, {"success": False, "message": "Relationship exists or invalid IDs"}


@router.get("/referrals/stats", response={200: CommonResponse, 400: CommonResponse})
async def areferral_stats(request, user_id: int | None = None, external_id: str | None = None, provider: str | None = None):
    """Get referral statistics for the referrer (e.g. count of invited users).

    Query params: user_id (optional), external_id (optional), provider (optional).
    Provide either user_id or (external_id + provider). Response data contains count.
    """
    provider_value = provider or "default"
    uid = user_id

    if uid is None and external_id:
        user = await ExternalIdentity.aget_user_by_identity(
            external_id=external_id, provider=provider_value
        )
        uid = user.id if user else None

    if uid is None:
        return 400, {"success": False, "message": "user_id is required"}

    count = await Referral.objects.filter(referrer_id=uid).acount()
    return {"success": True, "message": "Stats retrieved", "data": {"count": count}}


# --- Customer Management Endpoints ---

@router.post("/customers/merge", response={200: CustomerMergeResponse, 400: CommonResponse})
async def amerge_customers(request, data: CustomerMergeSchema):
    """Merge two customers: move all data from source_user to target_user.

    Moves orders, quota batches, transactions, identities, and referrals.
    Atomically performs the merge and sends customers_merged signal.
    """
    try:
        stats = await CustomerService.amerge_customers(
            target_user_id=data.target_user_id,
            source_user_id=data.source_user_id
        )
        return {
            "success": True,
            "message": f"Successfully merged customer {data.source_user_id} into {data.target_user_id}",
            **stats
        }
    except ValueError as e:
        return 400, {"success": False, "message": str(e)}
    except Exception as e:
        logger.error(f"Error merging customers {data.source_user_id} -> {data.target_user_id}: {e}", exc_info=True)
        return 400, {"success": False, "message": "Internal error during customer merge"}
