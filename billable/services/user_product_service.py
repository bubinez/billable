"""Service for managing active user products.

Provides information about a user's current subscriptions and quota balances.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone
from django.db.models import Sum, Q

from ..models import UserProduct, Product
from ..signals import product_deactivated

logger = logging.getLogger(__name__)


class UserProductService:
    """Service for working with user products."""

    @classmethod
    def get_user_active_products(cls, user_id: int, feature: str | None = None) -> list[UserProduct]:
        """
        Returns a list of a user's active products.
        
        Args:
            user_id: User ID.
            feature: Optional filter by feature.
            
        Returns:
            List of active UserProducts.
        """
        qs = UserProduct.objects.filter(
            user_id=user_id,
            is_active=True
        ).select_related('product')

        if feature:
            # Use __contains for Postgres array lookup in metadata.features
            qs = qs.filter(product__metadata__features__contains=[feature])

        # Filter out expired products by time (lazy deactivation in the model helps,
        # but it's better to cut them off here as well for accuracy)
        now = timezone.now()
        qs = qs.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=now)
        )

        return list(qs)

    @classmethod
    def get_balance_summary(cls, user_id: int) -> dict[str, Any]:
        """
        Returns summary information about a user's balance across all features.
        
        Args:
            user_id: User ID.
            
        Returns:
            Dictionary with feature balances.
        """
        active_products = cls.get_user_active_products(user_id)
        
        summary = {}
        for up in active_products:
            features = up.product.metadata.get("features", [])
            for feature in features:
                if feature not in summary:
                    summary[feature] = {
                        "total": 0,
                        "used": 0,
                        "remaining": 0,
                        "is_unlimited": False,
                        "expiry": None
                    }
                
                # If at least one product is unlimited or period-based (no quantity limit),
                # the feature is considered unlimited in quantity
                if up.product.product_type in [Product.ProductType.UNLIMITED, Product.ProductType.PERIOD]:
                    summary[feature]["is_unlimited"] = True
                
                if up.total_quantity:
                    summary[feature]["total"] += up.total_quantity
                    summary[feature]["used"] += up.used_quantity
                    
                # Track the earliest expiration date
                if up.expires_at:
                    if not summary[feature]["expiry"] or up.expires_at < summary[feature]["expiry"]:
                        summary[feature]["expiry"] = up.expires_at

        for feature in summary:
            if not summary[feature]["is_unlimited"]:
                summary[feature]["remaining"] = max(0, summary[feature]["total"] - summary[feature]["used"])
            else:
                summary[feature]["remaining"] = None

        return summary

    @classmethod
    def deactivate_expired_products(cls, user_id: int | None = None) -> int:
        """
        Deactivates all expired products.
        
        Args:
            user_id: Optional user_id to filter by.
            
        Returns:
            Number of deactivated records.
        """
        now = timezone.now()
        qs = UserProduct.objects.filter(
            is_active=True,
            expires_at__lt=now
        )
        
        if user_id:
            qs = qs.filter(user_id=user_id)
            
        expired_products = list(qs.select_related('product'))
        count = len(expired_products)
        
        if count > 0:
            # Bulk update для оптимизации
            UserProduct.objects.filter(
                id__in=[up.id for up in expired_products]
            ).update(is_active=False)
            
            # Отправляем сигналы для каждого продукта
            for up in expired_products:
                up.is_active = False
                product_deactivated.send(
                    sender=cls, 
                    user_product=up, 
                    reason="expired"
                )
            
            logger.info(f"Deactivated {count} expired user products and sent signals")
            
        return count
