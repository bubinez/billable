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

from .conf import billing_settings
from .models import Order, Product, UserProduct, TrialHistory, Referral
from .schemas import (
    BalanceFeatureSchema, 
    CommonResponse,
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
        if token == billing_settings.API_TOKEN:
            return token
        return None


# Router for billing with mandatory authorization
router = Router(tags=["billing"], auth=APIKeyAuth())


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
async def check_user_balance(request, user_id: int, feature: str):
    """Check if a feature can be used."""
    result = await QuotaService.acheck_quota(user_id, feature)
    return result


@router.post("/quota/consume", response={200: CommonResponse, 400: CommonResponse})
async def consume_user_quota(request, data: QuotaConsumeSchema):
    """Consume quota."""
    result = await QuotaService.aconsume_quota(
        user_id=data.user_id,
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
    result = await QuotaService.aactivate_trial(
        user_id=data.user_id,
        telegram_id=data.telegram_id,
        identities=data.identities,
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

    order = await OrderService.acreate_order(
        user_id=data.user_id,
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
    
    Args:
        request: HTTP request object.
        data: Referral assignment data with referrer_id, referee_id, and optional metadata.
        
    Returns:
        CommonResponse with success status and referral data.
    """
    if data.referrer_id == data.referee_id:
        return 400, {"success": False, "message": "Referrer and referee cannot be the same user"}
    
    try:
        referral, created = await Referral.objects.aget_or_create(
            referrer_id=data.referrer_id,
            referee_id=data.referee_id,
            defaults={"metadata": data.metadata or {}}
        )
        return {"success": True, "message": "Referral assigned", "data": {"created": created}}
    except IntegrityError:
        logger.error(
            f"Integrity error assigning referral: referrer_id={data.referrer_id}, referee_id={data.referee_id}",
            exc_info=True
        )
        return 400, {"success": False, "message": "Referral relationship already exists or invalid user IDs"}
    except Exception:
        logger.error(
            f"Unexpected error assigning referral: referrer_id={data.referrer_id}, referee_id={data.referee_id}",
            exc_info=True
        )
        return 400, {"success": False, "message": "Failed to assign referral"}
