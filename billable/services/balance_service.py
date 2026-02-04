"""Service for managing active user balances and inventory.

Provides information about a user's current subscriptions and quota balances.
In Engine v2.0, this service acts as a bridge to QuotaBatch and Transaction layers.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone
from django.db.models import Q

from ..models import Product, QuotaBatch
from .transaction_service import TransactionService

logger = logging.getLogger(__name__)


class BalanceService:
    """Service for working with user balances and inventory."""

    @classmethod
    def get_user_active_products(cls, user_id: int, product_key: str | None = None):
        """
        Returns a list of a user's active products (from QuotaBatch).

        Args:
            user_id: User ID.
            product_key: Optional filter by product_key.

        Returns:
            QuerySet[QuotaBatch]: Active batches.
        """
        qs = QuotaBatch.objects.filter(
            user_id=user_id,
            state=QuotaBatch.State.ACTIVE
        ).select_related('product')

        if product_key:
            # Normalize product_key to uppercase
            normalized_key = product_key.upper()
            qs = qs.filter(product__product_key=normalized_key)

        now = timezone.now()
        qs = qs.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )
        return qs

    @classmethod
    async def aget_user_active_products(cls, user_id: int, product_key: str | None = None) -> list[QuotaBatch]:
        """
        Returns active products asynchronously.
        """
        qs = cls.get_user_active_products(user_id, product_key)
        results = []
        async for obj in qs.aiterator():
            results.append(obj)
        return results

    @classmethod
    def get_balance_summary(cls, user_id: int) -> dict[str, Any]:
        """
        Returns summary information about a user's balance by product_key (v2 engine).
        """
        batches = QuotaBatch.objects.filter(
            user_id=user_id,
            state=QuotaBatch.State.ACTIVE
        ).select_related('product')

        summary = {}
        for qb in batches:
            key = qb.product.product_key
            if key not in summary:
                summary[key] = {
                    "total": 0,
                    "used": 0,
                    "remaining": 0,
                    "is_unlimited": False,
                    "expiry": None
                }

            if qb.product.product_type in [Product.ProductType.UNLIMITED, Product.ProductType.PERIOD]:
                summary[key]["is_unlimited"] = True

            summary[key]["total"] += qb.initial_quantity
            summary[key]["remaining"] += qb.remaining_quantity
            summary[key]["used"] += (qb.initial_quantity - qb.remaining_quantity)

            if qb.expires_at:
                if not summary[key]["expiry"] or qb.expires_at < summary[key]["expiry"]:
                    summary[key]["expiry"] = qb.expires_at

        return summary

    @classmethod
    async def aget_balance_summary(cls, user_id: int) -> dict[str, Any]:
        """
        Returns summary information about a user's balance by product_key asynchronously.
        """
        batches = QuotaBatch.objects.filter(
            user_id=user_id,
            state=QuotaBatch.State.ACTIVE
        ).select_related('product')

        summary = {}
        async for qb in batches.aiterator():
            key = qb.product.product_key
            if key not in summary:
                summary[key] = {
                    "total": 0,
                    "used": 0,
                    "remaining": 0,
                    "is_unlimited": False,
                    "expiry": None
                }

            if qb.product.product_type in [Product.ProductType.UNLIMITED, Product.ProductType.PERIOD]:
                summary[key]["is_unlimited"] = True

            summary[key]["total"] += qb.initial_quantity
            summary[key]["remaining"] += qb.remaining_quantity
            summary[key]["used"] += (qb.initial_quantity - qb.remaining_quantity)

            if qb.expires_at:
                if not summary[key]["expiry"] or qb.expires_at < summary[key]["expiry"]:
                    summary[key]["expiry"] = qb.expires_at

        return summary

    @classmethod
    def deactivate_expired_products(cls, user_id: int | None = None) -> int:
        """
        Deactivates all expired batches. Delegates to TransactionService.expire_batches.
        """
        return TransactionService.expire_batches()
