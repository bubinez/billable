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

from ..models import Order, OrderItem, Product, UserProduct
from ..signals import order_confirmed

logger = logging.getLogger(__name__)


def _prepare_order_items_data(product_items: List[dict[str, Any]]) -> tuple[Decimal, List[dict[str, Any]]]:
    """
    Подготавливает данные для создания заказа.
    
    Args:
        product_items: List of dictionaries {"product": ProductInstance, "quantity": int}.
        
    Returns:
        Tuple of (total_amount, order_items_data).
    """
    total_amount = Decimal("0")
    order_items_data = []

    for item in product_items:
        product = item["product"]
        quantity = item.get("quantity", 1)
        price = item.get("price", product.price)
        
        line_total = Decimal(str(price)) * quantity
        total_amount += line_total
        
        order_items_data.append({
            "product": product,
            "quantity": quantity,
            "price": price,
            "total_quantity": (product.quantity or 0) * quantity,
            "period_days": product.period_days
        })
    
    return total_amount, order_items_data


class OrderService:
    """Service for working with orders."""

    @classmethod
    def create_order(
        cls, 
        user_id: int, 
        product_items: List[dict[str, Any]], 
        metadata: dict[str, Any] | None = None
    ) -> Order:
        """
        Creates a new order.
        
        Args:
            user_id: User ID.
            product_items: List of dictionaries {"product": ProductInstance, "quantity": int}.
            metadata: Additional data for the order.
            
        Returns:
            The created Order object.
        """
        total_amount, order_items_data = _prepare_order_items_data(product_items)

        with transaction.atomic():
            order = Order.objects.create(
                user_id=user_id,
                total_amount=total_amount,
                status=Order.Status.PENDING,
                metadata=metadata or {}
            )

            for item_data in order_items_data:
                OrderItem.objects.create(
                    order=order,
                    **item_data
                )

        return order

    @classmethod
    def process_payment(
        cls, 
        order_id: int, 
        payment_id: str | None = None,
        payment_method: str = "provider_payments"
    ) -> bool:
        """
        Confirms order payment and activates products.
        
        Args:
            order_id: Order ID.
            payment_id: Transaction ID from an external system.
            payment_method: Payment method.
            
        Returns:
            True if the payment was successfully processed.
        """
        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order_id)
            
            if order.status == Order.Status.PAID:
                logger.info(f"Order {order_id} already paid")
                return True

            order.status = Order.Status.PAID
            order.payment_id = payment_id
            order.payment_method = payment_method
            order.paid_at = timezone.now()
            order.save()

            # Product activation
            for item in order.items.all():
                expires_at = None
                if item.product.product_type == Product.ProductType.PERIOD:
                    expires_at = timezone.now() + timezone.timedelta(days=item.period_days or 30)
                
                UserProduct.objects.create(
                    user=order.user,
                    product=item.product,
                    order_item=item,
                    total_quantity=item.total_quantity,
                    expires_at=expires_at,
                    is_active=True
                )
            
            # Send signal about successful payment
            order_confirmed.send(sender=cls, order=order)

            logger.info(f"Order {order_id} processed successfully")
            return True

    @classmethod
    def cancel_order(cls, order_id: int, reason: str | None = None) -> bool:
        """Cancels the order."""
        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order_id)
            if order.status in [Order.Status.PAID, Order.Status.REFUNDED]:
                return False
                
            order.status = Order.Status.CANCELLED
            if reason:
                if not order.metadata:
                    order.metadata = {}
                order.metadata["cancel_reason"] = reason
            order.save()
            return True

    @classmethod
    async def acreate_order(
        cls, 
        user_id: int, 
        product_items: List[dict[str, Any]], 
        metadata: dict[str, Any] | None = None
    ) -> Order:
        """
        Async version: Creates a new order.
        
        Args:
            user_id: User ID.
            product_items: List of dictionaries {"product": ProductInstance, "quantity": int}.
            metadata: Additional data for the order.
            
        Returns:
            The created Order object.
        """
        # 1. Подготовка данных (не требует транзакции)
        total_amount, order_items_data = _prepare_order_items_data(product_items)

        # 2. Изолированная синхронная транзакция
        try:
            order = await cls._create_order_transactionally(
                user_id, total_amount, order_items_data, metadata
            )
        except Exception as e:
            logger.error(f"Error creating order: {e}", exc_info=True)
            raise

        return order

    @classmethod
    @sync_to_async
    def _create_order_transactionally(
        cls,
        user_id: int,
        total_amount: Decimal,
        order_items_data: List[dict[str, Any]],
        metadata: dict[str, Any] | None
    ) -> Order:
        """
        Синхронное ядро транзакции создания заказа.
        """
        with transaction.atomic():
            order = Order.objects.create(
                user_id=user_id,
                total_amount=total_amount,
                status=Order.Status.PENDING,
                metadata=metadata or {}
            )

            for item_data in order_items_data:
                OrderItem.objects.create(
                    order=order,
                    **item_data
                )

        return order

    @classmethod
    async def aprocess_payment(
        cls, 
        order_id: int, 
        payment_id: str | None = None,
        payment_method: str = "provider_payments"
    ) -> bool:
        """
        Async version: Confirms order payment and activates products.
        
        Args:
            order_id: Order ID.
            payment_id: Transaction ID from an external system.
            payment_method: Payment method.
            
        Returns:
            True if the payment was successfully processed.
        """
        # 1. Проверка существования заказа (async read)
        try:
            await Order.objects.aget(id=order_id)
        except Order.DoesNotExist:
            logger.error(f"Order {order_id} not found")
            return False

        # 2. Изолированная синхронная транзакция
        # Получаем items внутри транзакции для консистентности
        try:
            result = await cls._process_payment_transactionally(
                order_id, payment_id, payment_method
            )
        except Exception as e:
            logger.error(f"Error processing payment: {e}", exc_info=True)
            return False

        return result

    @classmethod
    @sync_to_async
    def _process_payment_transactionally(
        cls,
        order_id: int,
        payment_id: str | None,
        payment_method: str
    ) -> bool:
        """
        Синхронное ядро транзакции обработки платежа.
        """
        with transaction.atomic():
            # Блокируем заказ для обновления и загружаем связанные данные
            order = Order.objects.select_for_update().select_related('user').prefetch_related('items__product').get(id=order_id)
            
            if order.status == Order.Status.PAID:
                logger.info(f"Order {order_id} already paid")
                return True

            order.status = Order.Status.PAID
            order.payment_id = payment_id
            order.payment_method = payment_method
            order.paid_at = timezone.now()
            order.save()

            # Product activation
            for item in order.items.all():
                expires_at = None
                if item.product.product_type == Product.ProductType.PERIOD:
                    expires_at = timezone.now() + timezone.timedelta(days=item.period_days or 30)
                
                UserProduct.objects.create(
                    user=order.user,
                    product=item.product,
                    order_item=item,
                    total_quantity=item.total_quantity,
                    expires_at=expires_at,
                    is_active=True
                )
            
            # Send signal about successful payment
            order_confirmed.send(sender=cls, order=order)

            logger.info(f"Order {order_id} processed successfully")
            return True

    @classmethod
    async def aserialize_order_to_dict(cls, order: Order) -> Dict[str, Any]:
        """
        Serializes Order instance to dictionary for API response.
        
        Args:
            order: Order instance with prefetched items__product and user.
            
        Returns:
            Dictionary representation of the order suitable for OrderSchema.
        """
        # Load items with select_related using async QuerySet
        items_qs = OrderItem.objects.filter(order_id=order.id).select_related("product")
        
        items_list = []
        async for item in items_qs.aiterator():
            # Product is loaded via select_related
            product = item.product
            item_dict = {
                "id": item.id,
                "product_id": item.product_id,
                "product_name": product.name if product else "",
                "sku": product.sku if product else None,
                "quantity": item.quantity,
                "price": item.price,
                "total_quantity": item.total_quantity,
                "period_days": item.period_days,
            }
            items_list.append(item_dict)
        
        order_dict = {
            "id": order.id,
            "user_id": order.user_id,
            "status": order.status,
            "total_amount": order.total_amount,
            "currency": order.currency,
            "payment_method": order.payment_method,
            "payment_id": order.payment_id,
            "created_at": order.created_at,
            "paid_at": order.paid_at,
            "items": items_list,
            "metadata": order.metadata,
        }
        return order_dict
