"""Service for managing orders and payments.

Handles the process of creating orders, confirming payments, and
granting product rights to users.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from ..models import Order, OrderItem, Offer
from ..signals import order_confirmed
from .transaction_service import TransactionService

logger = logging.getLogger(__name__)


def _prepare_order_items(items: List[dict[str, Any]]) -> tuple[Decimal, List[dict[str, Any]]]:
    """
    Prepare order items.
    """
    total_amount = Decimal("0")
    order_items_data = []

    for item in items:
        sku = item.get("sku")
        if not sku:
            raise ValueError("Item must have 'sku'")
        quantity = item.get("quantity", 1)

        offer = Offer.objects.filter(sku=sku, is_active=True).first()
        if not offer:
            offer = Offer.objects.filter(sku=sku).first()
        if not offer:
            raise ValueError(f"Offer not found for sku: {sku!r}")

        price = item.get("price", offer.price)
        line_total = Decimal(str(price)) * quantity
        total_amount += line_total
        
        order_items_data.append({
            "offer": offer,
            "quantity": quantity,
            "price": price,
        })
    
    return total_amount, order_items_data


async def _aprepare_order_items(items: List[dict[str, Any]]) -> tuple[Decimal, List[dict[str, Any]]]:
    """
    Prepare order items (async version).
    """
    total_amount = Decimal("0")
    order_items_data = []

    for item in items:
        sku = item.get("sku")
        if not sku:
            raise ValueError("Item must have 'sku'")
        quantity = item.get("quantity", 1)

        offer = await Offer.objects.filter(sku=sku, is_active=True).afirst()
        if not offer:
            offer = await Offer.objects.filter(sku=sku).afirst()
        if not offer:
            raise ValueError(f"Offer not found for sku: {sku!r}")

        price = item.get("price", offer.price)
        line_total = Decimal(str(price)) * quantity
        total_amount += line_total
        
        order_items_data.append({
            "offer": offer,
            "quantity": quantity,
            "price": price,
        })
    
    return total_amount, order_items_data


class OrderService:
    """Service for working with orders."""

    @classmethod
    def create_order(
        cls, 
        user_id: int, 
        items: List[dict[str, Any]], 
        metadata: dict[str, Any] | None = None
    ) -> Order:
        """Creates a new order synchronously."""
        total_amount, order_items_data = _prepare_order_items(items)

        with transaction.atomic():
            order = Order.objects.create(
                user_id=user_id,
                total_amount=total_amount,
                status=Order.Status.PENDING,
                metadata=metadata or {}
            )
            for item_data in order_items_data:
                OrderItem.objects.create(order=order, **item_data)
        return order

    @classmethod
    async def acreate_order(
        cls, 
        user_id: int, 
        items: List[dict[str, Any]], 
        metadata: dict[str, Any] | None = None
    ) -> Order:
        """Native async version (prepare async, wrap transaction)."""
        # Prepare data async
        total_amount, order_items_data = await _aprepare_order_items(items)

        def _do_save_sync():
            with transaction.atomic():
                order = Order.objects.create(
                    user_id=user_id,
                    total_amount=total_amount,
                    status=Order.Status.PENDING,
                    metadata=metadata or {}
                )
                for item_data in order_items_data:
                    OrderItem.objects.create(order=order, **item_data)
                return order

        return await sync_to_async(_do_save_sync, thread_sensitive=True)()

    @classmethod
    def process_payment(
        cls, 
        order_id: int, 
        payment_id: str | None = None,
        payment_method: str = "provider_payments"
    ) -> bool:
        """Confirms order payment and activates products."""
        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order_id)
            if order.status == Order.Status.PAID: return True

            order.status = Order.Status.PAID
            order.payment_id = payment_id
            order.payment_method = payment_method
            order.paid_at = timezone.now()
            order.save()

            for item in order.items.all():
                if item.offer:
                    TransactionService.grant_offer(user_id=order.user_id, offer=item.offer, order_item=item, source="purchase")
            
            order_confirmed.send(sender=cls, order=order)
            return True

    @classmethod
    async def aprocess_payment(cls, *args, **kwargs) -> bool:
        """Async variant using sync block for transactional logic."""
        return await sync_to_async(cls.process_payment, thread_sensitive=True)(*args, **kwargs)

    @classmethod
    def cancel_order(cls, order_id: int, reason: str | None = None) -> bool:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order_id)
            if order.status in [Order.Status.PAID, Order.Status.REFUNDED]: return False
            order.status = Order.Status.CANCELLED
            if reason:
                if not order.metadata: order.metadata = {}
                order.metadata["cancel_reason"] = reason
            order.save()
            return True

    @classmethod
    async def acancel_order(cls, order_id: int, reason: str | None = None) -> bool:
        """Async version of cancel_order."""
        return await sync_to_async(cls.cancel_order, thread_sensitive=True)(order_id, reason)

    @classmethod
    def refund_order(cls, order_id: int, reason: str | None = None) -> bool:
        """
        Refunds a paid order.
        Changes status to REFUNDED and revokes associated quotas.
        """
        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order_id)
            if order.status != Order.Status.PAID:
                logger.warning(f"refund_order: Order #{order_id} is not PAID (status: {order.status})")
                return False
            
            # 1. Revoke quotas
            TransactionService.revoke_order_items(order, reason="refund")
            
            # 2. Update order status
            order.status = Order.Status.REFUNDED
            if reason:
                if not order.metadata: order.metadata = {}
                order.metadata["refund_reason"] = reason
            order.save()
            
            logger.info(f"refund_order: Order #{order_id} refunded successfully")
            return True

    @classmethod
    async def arefund_order(cls, order_id: int, reason: str | None = None) -> bool:
        """Async version of refund_order."""
        return await sync_to_async(cls.refund_order, thread_sensitive=True)(order_id, reason)

    @classmethod
    async def aserialize_order_to_dict(cls, order: Order) -> Dict[str, Any]:
        """Native async serialization using aiterator."""
        items_list = []
        async for item in order.items.select_related("offer").aiterator():
            items_list.append({
                "id": item.id,
                "sku": item.offer.sku if item.offer else "unknown",
                "quantity": item.quantity,
                "price": item.price,
            })
        
        return {
            "id": order.id, "user_id": order.user_id, "status": order.status,
            "total_amount": order.total_amount, "currency": order.currency,
            "payment_method": order.payment_method, "payment_id": order.payment_id,
            "created_at": order.created_at, "paid_at": order.paid_at,
            "items": items_list, "metadata": order.metadata,
        }
