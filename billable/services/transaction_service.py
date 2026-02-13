"""Service for managing entitlement (quotas, offers, transactions).

Implements the Core Service Layer of the Entitlement Engine:
- Granting (Purchase)
- Access Control
- Consumption (FIFO)
- Expiration
- Exchange
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from uuid import UUID

from dateutil.relativedelta import relativedelta

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from ..models import Product, Offer, OfferItem, QuotaBatch, Transaction, OrderItem, TrialHistory
from ..signals import quota_consumed, trial_activated, transaction_created

logger = logging.getLogger(__name__)


class TransactionService:
    """
    Core Service Layer for Billable Entitlement Engine.
    Handles all balance-changing operations.
    """

    @classmethod
    def _find_active_batches(cls, user_id: int, product_key: str | None = None):
        """
        Finds active batches for a user, filtered by product_key.
        Returns QuerySet[QuotaBatch].
        
        Normalizes product_key to uppercase before filtering.
        """
        now = timezone.now()
        qs = QuotaBatch.objects.filter(
            user_id=user_id,
            state=QuotaBatch.State.ACTIVE
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        ).select_related('product')

        if product_key:
            # Normalize product_key to uppercase
            normalized_key = product_key.upper()
            qs = qs.filter(product__product_key=normalized_key)
        return qs

    @classmethod
    def get_balance(cls, user_id: int, product_key: str) -> int:
        """
        3.2 Access Check (Simple)
        Returns total active remaining quantity for a specific product key.
        """
        batches = cls._find_active_batches(user_id, product_key=product_key)
        total = batches.aggregate(total=Sum('remaining_quantity'))['total']
        return total or 0

    @classmethod
    async def aget_balance(cls, user_id: int, product_key: str) -> int:
        """Async version of get_balance using aaggregate."""
        batches = cls._find_active_batches(user_id, product_key=product_key)
        res = await batches.aaggregate(total=Sum('remaining_quantity'))
        return res['total'] or 0

    @classmethod
    def check_quota(cls, user_id: int, product_key: str) -> dict[str, Any]:
        """
        3.2 Access Check (Detailed)
        Checks availability for a product_key.
        """
        batches = cls._find_active_batches(user_id, product_key=product_key)
        total = batches.aggregate(total=Sum('remaining_quantity'))['total'] or 0

        if total > 0:
            first_batch = batches.first()
            product_name = first_batch.product.name if first_batch else "Product"
            # Return normalized product_key
            normalized_key = product_key.upper() if product_key else ""
            return {
                "can_use": True,
                "product_key": normalized_key,
                "product_name": product_name,
                "remaining": total,
                "message": f"Available: {product_name} ({total} left)"
            }
        # Return normalized product_key
        normalized_key = product_key.upper() if product_key else ""
        return {
            "can_use": False,
            "product_key": normalized_key,
            "message": f"No active quota for {normalized_key}",
            "remaining": 0
        }

    @classmethod
    async def acheck_quota(cls, user_id: int, product_key: str) -> dict[str, Any]:
        """Native async version of check_quota."""
        batches = cls._find_active_batches(user_id, product_key=product_key)
        total = 0
        product_name = "Product"
        found = False
        async for batch in batches.aiterator():
            if not found:
                product_name = batch.product.name
                found = True
            total += batch.remaining_quantity
        # Return normalized product_key
        normalized_key = product_key.upper() if product_key else ""
        if total > 0:
            return {
                "can_use": True,
                "product_key": normalized_key,
                "product_name": product_name,
                "remaining": total,
                "message": f"Available: {product_name} ({total} left)"
            }
        return {
            "can_use": False,
            "product_key": normalized_key,
            "message": f"No active quota for {normalized_key}",
            "remaining": 0
        }

    @classmethod
    @transaction.atomic
    def grant_offer(
        cls, 
        user_id: int, 
        offer: Offer, 
        order_item: OrderItem | None = None,
        source: str = "purchase",
        metadata: dict[str, Any] | None = None
    ) -> list[QuotaBatch]:
        """3.1 Granting / Purchase"""
        items = offer.items.all()
        created_batches = []
        now = timezone.now()

        for item in items:
            expires_at = None
            if item.period_unit != OfferItem.PeriodUnit.FOREVER and item.period_value:
                if item.period_unit == OfferItem.PeriodUnit.HOURS:
                    delta = timedelta(hours=item.period_value)
                elif item.period_unit == OfferItem.PeriodUnit.DAYS:
                    delta = timedelta(days=item.period_value)
                elif item.period_unit == OfferItem.PeriodUnit.MONTHS:
                    delta = relativedelta(months=item.period_value)
                elif item.period_unit == OfferItem.PeriodUnit.YEARS:
                    delta = relativedelta(years=item.period_value)
                else:
                    delta = timedelta(days=0)
                expires_at = now + delta

            total_quantity = item.quantity
            if order_item:
                total_quantity *= order_item.quantity

            batch = QuotaBatch.objects.create(
                user_id=user_id,
                product=item.product,
                source_offer=offer,
                order_item=order_item,
                initial_quantity=total_quantity,
                remaining_quantity=total_quantity,
                valid_from=now,
                expires_at=expires_at,
                state=QuotaBatch.State.ACTIVE
            )
            created_batches.append(batch)

            tx = Transaction.objects.create(
                user_id=user_id,
                quota_batch=batch,
                amount=total_quantity,
                direction=Transaction.Direction.CREDIT,
                action_type=source,
                related_object=order_item if order_item else offer,
                metadata=metadata or {}
            )
            transaction_created.send(sender=cls, transaction=tx)

        return created_batches

    @classmethod
    async def agrant_offer(
        cls, 
        user_id: int, 
        offer: Offer, 
        order_item: OrderItem | None = None,
        source: str = "purchase",
        metadata: dict[str, Any] | None = None
    ) -> list[QuotaBatch]:
        """3.1 Granting / Purchase (Async version)"""
        now = timezone.now()
        
        # Collect items asynchronously
        items = []
        async for item in offer.items.select_related('product').aiterator():
            items.append(item)

        # Pre-fetch related data if needed to avoid sync DB calls inside sync_to_async if they are not cached
        # But here we pass 'items' which are already fetched.
        # Note: offer and order_item are model instances. 
        # Accessing their fields is fine, but accessing related fields might trigger sync DB calls.

        def _do_grant_sync():
            # Ensure we are in a sync context where Django allows DB operations
            with transaction.atomic():
                created_batches = []
                for item in items:
                    expires_at = None
                    if item.period_unit != OfferItem.PeriodUnit.FOREVER and item.period_value:
                        if item.period_unit == OfferItem.PeriodUnit.HOURS:
                            delta = timedelta(hours=item.period_value)
                        elif item.period_unit == OfferItem.PeriodUnit.DAYS:
                            delta = timedelta(days=item.period_value)
                        elif item.period_unit == OfferItem.PeriodUnit.MONTHS:
                            delta = relativedelta(months=item.period_value)
                        elif item.period_unit == OfferItem.PeriodUnit.YEARS:
                            delta = relativedelta(years=item.period_value)
                        else:
                            delta = timedelta(days=0)
                        expires_at = now + delta

                    total_quantity = item.quantity
                    if order_item:
                        total_quantity *= order_item.quantity

                    batch = QuotaBatch.objects.create(
                        user_id=user_id,
                        product=item.product,
                        source_offer=offer,
                        order_item=order_item,
                        initial_quantity=total_quantity,
                        remaining_quantity=total_quantity,
                        valid_from=now,
                        expires_at=expires_at,
                        state=QuotaBatch.State.ACTIVE
                    )
                    created_batches.append(batch)

                    tx = Transaction.objects.create(
                        user_id=user_id,
                        quota_batch=batch,
                        amount=total_quantity,
                        direction=Transaction.Direction.CREDIT,
                        action_type=source,
                        related_object=order_item if order_item else offer,
                        metadata=metadata or {}
                    )
                    transaction_created.send(sender=cls, transaction=tx)
                return created_batches

        return await sync_to_async(_do_grant_sync, thread_sensitive=True)()

    @classmethod
    def consume_quota(
        cls,
        user_id: int,
        product_key: str,
        action_type: str = "usage",
        amount: int = 1,
        action_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """3.3 Consumption (FIFO)"""
        with transaction.atomic():
            if idempotency_key:
                existing = Transaction.objects.filter(
                    user_id=user_id,
                    action_type=action_type,
                    metadata__idempotency_key=idempotency_key
                ).first()
                if existing:
                    return {
                        "success": True,
                        "message": "Quota was consumed previously (idempotent)",
                        "usage_id": str(existing.id),
                        "remaining": cls.get_balance(user_id, existing.quota_batch.product.product_key),
                        "metadata": existing.metadata or {},
                    }

            batches_qs = cls._find_active_batches(user_id, product_key=product_key).order_by('created_at').select_for_update()
            remaining_needed = amount
            consumed_info = []
            active_batches = list(batches_qs)

            if not active_batches:
                return {"success": False, "error": "quota_exhausted", "message": f"No active quota for {product_key}"}
                
            total_available = sum(b.remaining_quantity for b in active_batches)
            if total_available < amount:
                 return {"success": False, "error": "insufficient_funds", "message": "Insufficient balance"}

            for batch in active_batches:
                if remaining_needed <= 0: break
                consume = min(batch.remaining_quantity, remaining_needed)
                batch.remaining_quantity -= consume
                remaining_needed -= consume
                if batch.remaining_quantity == 0: batch.state = QuotaBatch.State.EXHAUSTED
                batch.save(update_fields=['remaining_quantity', 'state'])
                
                tx = Transaction.objects.create(
                    user_id=user_id, quota_batch=batch, amount=consume,
                    direction=Transaction.Direction.DEBIT, action_type=action_type,
                    object_id=action_id, metadata={**(metadata or {}), "idempotency_key": idempotency_key}
                )
                consumed_info.append(tx)
                transaction_created.send(sender=cls, transaction=tx)
                quota_consumed.send(sender=cls, usage=tx)

            return {
                "success": True,
                "message": "Quota consumed",
                "usage_id": str(consumed_info[-1].id),
                "remaining": cls.get_balance(user_id, active_batches[0].product.product_key),
                "metadata": consumed_info[-1].metadata or {},
            }
        
    @classmethod
    async def aconsume_quota(cls, *args, **kwargs) -> dict[str, Any]:
        """Async variant using sync block for transaction."""
        return await sync_to_async(cls.consume_quota, thread_sensitive=True)(*args, **kwargs)

    @classmethod
    @transaction.atomic
    def exchange(
        cls,
        user_id: int,
        offer_id: UUID | str | None = None,
        offer: Offer | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """3.5 Exchange"""
        if not offer and offer_id: offer = Offer.objects.get(pk=offer_id)
        if not offer: raise ValueError("Offer not found")
        
        # 1. Determine the source product key from offer.currency (normalize to uppercase)
        currency_sku = offer.currency.strip().upper()
        
        # 2. Validate that the product is marked as a currency
        try:
            source_product = Product.objects.get(product_key=currency_sku)
            if not source_product.is_currency:
                raise ValueError(f"Product '{currency_sku}' is not marked as a currency for exchange.")
            # Use the actual product key from the DB for consumption
            currency_sku = source_product.product_key
        except Product.DoesNotExist:
            raise ValueError(f"Currency product '{currency_sku}' not found.")

        price = int(offer.price)
        tx_metadata = {**(metadata or {}), "price": price}

        res = cls.consume_quota(user_id=user_id, product_key=currency_sku, amount=price, action_type="exchange")
        if not res['success']: return res

        cls.grant_offer(user_id, offer, source="exchange", metadata=tx_metadata)
        return {"success": True, "message": "Exchanged", "metadata": tx_metadata}

    @classmethod
    async def aexchange(
        cls,
        user_id: int,
        offer_id: UUID | str | None = None,
        offer: Offer | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """3.5 Exchange (Async version)
        
        Calls the sync exchange method via sync_to_async to ensure that the entire
        operation (consumption of currency and granting of offer) happens within
        a single atomic transaction.
        """
        return await sync_to_async(cls.exchange, thread_sensitive=True)(
            user_id=user_id, offer_id=offer_id, offer=offer, metadata=metadata
        )


    @classmethod
    def expire_batches(cls) -> int:
        """
        Marks expired batches as EXPIRED.
        
        Returns:
            int: Number of batches updated.
        """
        now = timezone.now()
        return QuotaBatch.objects.filter(state=QuotaBatch.State.ACTIVE, expires_at__lt=now).update(state=QuotaBatch.State.EXPIRED)

    @classmethod
    @transaction.atomic
    def revoke_order_items(cls, order: Order, reason: str = "refund") -> int:
        """
        Revokes all active quota batches associated with the order.
        Creates DEBIT transactions for the remaining quantity.
        
        Args:
            order: The Order to revoke.
            reason: The action_type for the transactions.
            
        Returns:
            int: Number of batches revoked.
        """
        batches = QuotaBatch.objects.filter(
            order_item__order=order,
            state=QuotaBatch.State.ACTIVE
        ).select_for_update()

        revoked_count = 0
        for batch in batches:
            amount_to_revoke = batch.remaining_quantity
            if amount_to_revoke > 0:
                # Create DEBIT transaction for the remaining amount
                tx = Transaction.objects.create(
                    user_id=batch.user_id,
                    quota_batch=batch,
                    amount=amount_to_revoke,
                    direction=Transaction.Direction.DEBIT,
                    action_type=reason,
                    related_object=order,
                    metadata={"reason": "order_refunded"}
                )
                transaction_created.send(sender=cls, transaction=tx)
            
            # Mark batch as REVOKED and zero out remaining quantity
            batch.remaining_quantity = 0
            batch.state = QuotaBatch.State.REVOKED
            batch.save(update_fields=["remaining_quantity", "state"])
            revoked_count += 1
            
        return revoked_count

    @classmethod
    async def arevoke_order_items(cls, order: Order, reason: str = "refund") -> int:
        """Async version of revoke_order_items."""
        return await sync_to_async(cls.revoke_order_items, thread_sensitive=True)(order, reason)
