"""Service for managing quotas and product functionality.

Provides centralized logic for feature availability checks,
atomic quota consumption, and trial period activation.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from asgiref.sync import sync_to_async
from django.db import connection, transaction, NotSupportedError, OperationalError
from django.utils import timezone
from django.db.models import F, Q

from ..models import Product, ProductUsage, TrialHistory, UserProduct
from ..signals import quota_consumed, trial_activated

logger = logging.getLogger(__name__)


class QuotaService:
    """Central service for working with quotas."""

    @classmethod
    def _filter_by_feature(cls, qs, feature: str):
        """
        Helper to filter by feature with support for different DB backends.
        
        Args:
            qs: UserProduct queryset.
            feature: Feature name to filter by.
            
        Returns:
            Filtered queryset.
        """
        if connection.vendor == 'postgresql':
            return qs.filter(product__metadata__features__contains=[feature])
        else:
            # Fallback for SQLite: search for substring in JSON field
            return qs.filter(product__metadata__icontains=feature)

    @classmethod
    def _filter_usable_products(cls, qs):
        """
        Filter queryset to only include products that can be used (SQL-level filtering).
        
        For QUANTITY: used_quantity < total_quantity
        For PERIOD: expires_at IS NULL OR expires_at > now()
        For UNLIMITED: always available if is_active=True
        
        Args:
            qs: UserProduct queryset
            
        Returns:
            Filtered queryset with only usable products.
        """
        now = timezone.now()
        return qs.filter(
            Q(
                # QUANTITY products: check if used_quantity < total_quantity
                product__product_type=Product.ProductType.QUANTITY,
                used_quantity__lt=F('total_quantity')
            ) | Q(
                # PERIOD products: check if not expired
                product__product_type=Product.ProductType.PERIOD,
                expires_at__gt=now
            ) | Q(
                # PERIOD products with no expiration
                product__product_type=Product.ProductType.PERIOD,
                expires_at__isnull=True
            ) | Q(
                # UNLIMITED products: always available if active
                product__product_type=Product.ProductType.UNLIMITED
            )
        )

    @classmethod
    def _find_product_by_sku_or_feature(cls, qs, feature: str):
        """
        Находит продукт по SKU или feature.
        
        Args:
            qs: UserProduct queryset.
            feature: SKU или feature name.
            
        Returns:
            Filtered queryset.
        """
        sku_products_qs = qs.filter(product__sku__iexact=feature)
        if sku_products_qs.exists():
            return sku_products_qs
        return cls._filter_by_feature(qs, feature)

    @classmethod
    async def _afind_product_by_sku_or_feature(cls, qs, feature: str):
        """
        Async version: находит продукт по SKU или feature.
        
        Args:
            qs: UserProduct queryset.
            feature: SKU или feature name.
            
        Returns:
            Filtered queryset.
        """
        sku_products_qs = qs.filter(product__sku__iexact=feature)
        if await sku_products_qs.aexists():
            return sku_products_qs
        return cls._filter_by_feature(qs, feature)

    @classmethod
    def _build_quota_check_response(cls, best_product, feature: str) -> dict[str, Any]:
        """
        Формирует ответ проверки квоты.
        
        Args:
            best_product: UserProduct instance or None.
            feature: Feature name.
            
        Returns:
            Dictionary with check results.
        """
        if not best_product:
            return {
                "can_use": False,
                "feature": feature,
                "message": f"No active product with feature {feature}",
                "remaining": 0
            }

        remaining = best_product.get_remaining_quantity()

        return {
            "can_use": True,
            "feature": feature,
            "product_name": best_product.product.name,
            "remaining": remaining,
            "message": f"Available: {best_product.product.name}" + (f" ({remaining} left)" if remaining is not None else "")
        }

    @classmethod
    def check_quota(cls, user_id: int, feature: str) -> dict[str, Any]:
        """
        Checks feature availability for a user.
        
        Args:
            user_id: User ID.
            feature: Feature name (e.g., 'report_generation').
            
        Returns:
            Dictionary with check results:
            - 'can_use': bool
            - 'message': description
            - 'remaining': remaining quantity (if applicable)
        """
        active_products_qs = UserProduct.objects.filter(
            user_id=user_id,
            is_active=True
        )
        # SKU-first lookup: allow passing product SKU in the "feature" param (backward compatibility).
        # Fallback: search by metadata.features (feature name).
        active_products_qs = cls._find_product_by_sku_or_feature(active_products_qs, feature)

        # Filter to only usable products at SQL level (FIFO: purchased_at, id)
        usable_products_qs = cls._filter_usable_products(active_products_qs).select_related("product").order_by("purchased_at", "id")
        
        best_product = usable_products_qs.first()

        return cls._build_quota_check_response(best_product, feature)

    @classmethod
    async def acheck_quota(cls, user_id: int, feature: str) -> dict[str, Any]:
        """
        Async version: Checks feature availability for a user.
        
        Args:
            user_id: User ID.
            feature: Feature name (e.g., 'report_generation').
            
        Returns:
            Dictionary with check results:
            - 'can_use': bool
            - 'message': description
            - 'remaining': remaining quantity (if applicable)
        """
        active_products_qs = UserProduct.objects.filter(
            user_id=user_id,
            is_active=True
        )
        # SKU-first lookup: allow passing product SKU in the "feature" param (backward compatibility).
        # Fallback: search by metadata.features (feature name).
        active_products_qs = await cls._afind_product_by_sku_or_feature(active_products_qs, feature)

        # Filter to only usable products at SQL level (FIFO: purchased_at, id)
        usable_products_qs = cls._filter_usable_products(active_products_qs).select_related("product").order_by("purchased_at", "id")
        
        best_product = await usable_products_qs.afirst()

        return cls._build_quota_check_response(best_product, feature)

    @classmethod
    def consume_quota(
        cls, 
        user_id: int, 
        feature: str, 
        action_type: str,
        action_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Atomsically consumes a user's quota.
        
        Args:
            user_id: User ID.
            feature: Feature name.
            action_type: Action type for logging.
            action_id: Entity identifier (e.g., report_id).
            idempotency_key: Idempotency key.
            metadata: Additional data for ProductUsage.
            
        Returns:
            Dictionary with consumption result.
        """
        if idempotency_key:
            existing_usage = ProductUsage.objects.filter(
                user_id=user_id,
                action_id=idempotency_key
            ).first()
            if existing_usage:
                return {
                    "success": True,
                    "message": "Quota was consumed previously (idempotent)",
                    "usage_id": existing_usage.id
                }

        with transaction.atomic():
            # Search for a suitable product with lock for update
            up_qs = UserProduct.objects.filter(
                user_id=user_id,
                is_active=True
            )
            # SKU-first lookup: allow passing product SKU in the "feature" param (backward compatibility).
            # Fallback: search by metadata.features (feature name).
            up_qs = cls._find_product_by_sku_or_feature(up_qs, feature)
            
            # Filter to only usable products at SQL level (FIFO: purchased_at, id)
            up_qs = cls._filter_usable_products(up_qs).select_related('product')
            
            try:
                # FIFO: use oldest purchased products first (purchased_at ASC, then id ASC)
                up = up_qs.select_for_update().order_by('purchased_at', 'id').first()
            except (NotSupportedError, OperationalError):
                # SQLite does not support select_for_update
                up = up_qs.order_by('purchased_at', 'id').first()

            if not up:
                return {
                    "success": False,
                    "error": "quota_exhausted",
                    "message": f"Quota for {feature} is exhausted or unavailable"
                }

            # Consumption
            if up.product.product_type == Product.ProductType.QUANTITY:
                up.used_quantity = F('used_quantity') + 1
                up.save(update_fields=['used_quantity'])
                # After F(), the object in memory doesn't have the current used_quantity,
                # but for QUANTITY products we can refresh the object if needed
                up.refresh_from_db()

            # Log usage
            usage = ProductUsage.objects.create(
                user_id=user_id,
                user_product=up,
                action_type=action_type,
                action_id=idempotency_key or action_id,
                metadata=metadata or {}
            )

            # Send signal about quota consumption
            quota_consumed.send(sender=cls, usage=usage)

            return {
                "success": True,
                "message": "Quota consumed successfully",
                "usage_id": usage.id,
                "remaining": up.get_remaining_quantity()
            }

    @classmethod
    async def aconsume_quota(
        cls, 
        user_id: int, 
        feature: str, 
        action_type: str,
        action_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Async version: Atomically consumes a user's quota.
        
        Args:
            user_id: User ID.
            feature: Feature name.
            action_type: Action type for logging.
            action_id: Entity identifier (e.g., report_id).
            idempotency_key: Idempotency key.
            metadata: Additional data for ProductUsage.
            
        Returns:
            Dictionary with consumption result.
        """
        # 1. Асинхронные проверки (Read operations - не требуют блокировок)
        if idempotency_key:
            existing_usage = await ProductUsage.objects.filter(
                user_id=user_id,
                action_id=idempotency_key
            ).values('id').afirst()
            if existing_usage:
                return {
                    "success": True,
                    "message": "Quota was consumed previously (idempotent)",
                    "usage_id": existing_usage['id']
                }

        # 2. Изолированная синхронная транзакция
        try:
            result = await cls._consume_transactionally(
                user_id, feature, action_type, action_id, idempotency_key, metadata
            )
        except Exception as e:
            logger.error(f"Error consuming quota: {e}", exc_info=True)
            return {
                "success": False,
                "error": "internal_error",
                "message": str(e)
            }

        return result

    @classmethod
    @sync_to_async
    def _consume_transactionally(
        cls,
        user_id: int,
        feature: str,
        action_type: str,
        action_id: str | None,
        idempotency_key: str | None,
        metadata: dict[str, Any] | None
    ) -> dict[str, Any]:
        """
        Синхронное ядро транзакции. Выполняется в отдельном потоке, не блокируя Event Loop.
        """
        with transaction.atomic():
            # Формируем базовый QuerySet
            up_qs = UserProduct.objects.filter(
                user_id=user_id,
                is_active=True
            ).select_related('product')

            # Логика поиска по SKU или Feature
            up_qs = cls._find_product_by_sku_or_feature(up_qs, feature)
            
            up_qs = cls._filter_usable_products(up_qs)

            # Блокировка строки (FOR UPDATE)
            try:
                up = up_qs.select_for_update().order_by('purchased_at', 'id').first()
            except (NotSupportedError, OperationalError):
                # SQLite does not support select_for_update
                up = up_qs.order_by('purchased_at', 'id').first()

            if not up:
                return {
                    "success": False,
                    "error": "quota_exhausted",
                    "message": f"Quota for {feature} is exhausted or unavailable"
                }

            # Атомарное обновление
            remaining_quantity = 0
            
            if up.product.product_type == Product.ProductType.QUANTITY:
                UserProduct.objects.filter(pk=up.pk).update(used_quantity=F('used_quantity') + 1)
                up.refresh_from_db(fields=['used_quantity'])
                remaining_quantity = up.get_remaining_quantity()

            # Логирование использования
            usage = ProductUsage.objects.create(
                user_id=user_id,
                user_product=up,
                action_type=action_type,
                action_id=idempotency_key or action_id,
                metadata=metadata or {}
            )

            # Send signal about quota consumption
            quota_consumed.send(sender=cls, usage=usage)

            return {
                "success": True,
                "message": "Quota consumed successfully",
                "usage_id": usage.id,
                "remaining": remaining_quantity
            }

    @classmethod
    def activate_trial(
        cls, 
        user_id: int, 
        telegram_id: str | None = None, 
        identities: dict[str, str | int] | None = None,
        sku: str | None = None
    ) -> dict[str, Any]:
        """
        Activates a free trial or specific gift product for a user.
        
        Args:
            user_id: Django User ID.
            external_id: Optional external ID for TrialHistory check (backward compatibility).
            identities: Dictionary of identities for trial check.
            sku: Specific product SKU to grant. If not provided, searches for products with is_trial metadata.
            
        Returns:
            dict[str, Any]: Activation result.
        """
        ids_to_check = identities.copy() if identities else {}
        if telegram_id:
            ids_to_check['telegram'] = telegram_id

        if TrialHistory.has_used_trial(identities=ids_to_check):
            return {
                "success": False,
                "error": "trial_already_used",
                "message": "You have already used the trial period."
            }

        # Search for products
        if sku:
            trial_products = Product.objects.filter(sku=sku, is_active=True)
        else:
            # Fallback to metadata flag instead of name search (removing hardcode)
            trial_products = Product.objects.filter(is_active=True, metadata__is_trial=True)

        if not trial_products.exists():
            return {
                "success": False,
                "error": "no_products_found",
                "message": f"Product {f'with SKU {sku}' if sku else 'for trial'} not found or inactive."
            }

        with transaction.atomic():
            activated = []
            for product in trial_products:
                expires_at = None
                if product.product_type == Product.ProductType.PERIOD:
                    expires_at = timezone.now() + timezone.timedelta(days=product.period_days or 7)
                
                UserProduct.objects.create(
                    user_id=user_id,
                    product=product,
                    total_quantity=product.quantity or 0,
                    expires_at=expires_at,
                    is_active=True
                )
                activated.append(product.name)

            # Record in history for each identity to prevent reuse
            plan_name = ", ".join(activated)
            if ids_to_check:
                for id_type, id_value in ids_to_check.items():
                    if id_value:
                        TrialHistory.objects.get_or_create(
                            identity_type=id_type,
                            identity_hash=TrialHistory.generate_identity_hash(id_value),
                            defaults={"trial_plan_name": plan_name}
                        )

            # Send signal about trial activation
            trial_activated.send(
                sender=cls, 
                user_id=user_id, 
                telegram_id=telegram_id, 
                products=activated
            )

            return {
                "success": True,
                "message": f"Trial products activated: {', '.join(activated)}",
                "products": activated
            }

    @classmethod
    async def aactivate_trial(
        cls, 
        user_id: int, 
        telegram_id: str | None = None, 
        identities: dict[str, str | int] | None = None,
        sku: str | None = None
    ) -> dict[str, Any]:
        """
        Async version: Activates a free trial or specific gift product for a user.
        
        Args:
            user_id: Django User ID.
            identities: Dictionary of identities for trial check.
            sku: Specific product SKU to grant. If not provided, searches for products with is_trial metadata.
            
        Returns:
            dict[str, Any]: Activation result.
        """
        # 1. Асинхронные проверки (Read operations)
        ids_to_check = identities.copy() if identities else {}
        if telegram_id:
            ids_to_check['telegram'] = telegram_id

        if await TrialHistory.has_used_trial_async(identities=ids_to_check):
            return {
                "success": False,
                "error": "trial_already_used",
                "message": "You have already used the trial period."
            }

        # Search for products
        if sku:
            trial_products_qs = Product.objects.filter(sku=sku, is_active=True)
        else:
            trial_products_qs = Product.objects.filter(is_active=True, metadata__is_trial=True)

        if not await trial_products_qs.aexists():
            return {
                "success": False,
                "error": "no_products_found",
                "message": f"Product {f'with SKU {sku}' if sku else 'for trial'} not found or inactive."
            }

        # 2. Получаем список продуктов асинхронно
        products_list = [product async for product in trial_products_qs.aiterator()]

        # 3. Изолированная синхронная транзакция
        try:
            result = await cls._activate_trial_transactionally(
                user_id, telegram_id, ids_to_check, products_list
            )
        except Exception as e:
            logger.error(f"Error activating trial: {e}", exc_info=True)
            return {
                "success": False,
                "error": "internal_error",
                "message": str(e)
            }

        return result

    @classmethod
    @sync_to_async
    def _activate_trial_transactionally(
        cls,
        user_id: int,
        telegram_id: str | None,
        ids_to_check: dict[str, str | int],
        products_list: list[Product]
    ) -> dict[str, Any]:
        """
        Синхронное ядро транзакции активации trial.
        """
        with transaction.atomic():
            activated = []
            for product in products_list:
                expires_at = None
                if product.product_type == Product.ProductType.PERIOD:
                    expires_at = timezone.now() + timezone.timedelta(days=product.period_days or 7)
                
                UserProduct.objects.create(
                    user_id=user_id,
                    product=product,
                    total_quantity=product.quantity or 0,
                    expires_at=expires_at,
                    is_active=True
                )
                activated.append(product.name)

            # Record in history for each identity to prevent reuse
            plan_name = ", ".join(activated)
            if ids_to_check:
                for id_type, id_value in ids_to_check.items():
                    if id_value:
                        TrialHistory.objects.get_or_create(
                            identity_type=id_type,
                            identity_hash=TrialHistory.generate_identity_hash(id_value),
                            defaults={"trial_plan_name": plan_name}
                        )

            # Send signal about trial activation
            trial_activated.send(
                sender=cls, 
                user_id=user_id, 
                telegram_id=telegram_id, 
                products=activated
            )

            return {
                "success": True,
                "message": f"Trial products activated: {', '.join(activated)}",
                "products": activated
            }
