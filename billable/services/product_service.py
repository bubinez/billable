"""Service for working with the product catalog.

Provides access to available products and their configurations.
"""

from __future__ import annotations

import logging
from typing import Any, List

from django.db.models import Q
from ..models import Product

logger = logging.getLogger(__name__)


class ProductService:
    """Service for working with the product catalog."""

    @classmethod
    def get_active_products(cls, feature: str | None = None) -> List[Product]:
        """
        Returns a list of active products.
        
        Args:
            feature: Optional filter by feature in metadata.
            
        Returns:
            List of active products.
        """
        qs = Product.objects.filter(is_active=True)
        
        if feature:
            qs = qs.filter(metadata__features__contains=[feature])
            
        return list(qs)

    @classmethod
    def get_product_by_sku(cls, sku: str) -> Product | None:
        """Finds a product by SKU."""
        return Product.objects.filter(sku=sku, is_active=True).first()

    @classmethod
    def get_trial_products(cls) -> List[Product]:
        """Returns products marked as trial."""
        # Search by name or via a special flag in metadata
        return list(Product.objects.filter(
            is_active=True
        ).filter(
            Q(name__icontains="trial") | 
            Q(metadata__is_trial=True)
        ))

    @classmethod
    async def aget_active_products(cls, feature: str | None = None) -> List[Product]:
        """
        Async version: Returns a list of active products.
        
        Args:
            feature: Optional filter by feature in metadata.
            
        Returns:
            List of active products.
        """
        qs = Product.objects.filter(is_active=True)
        
        if feature:
            qs = qs.filter(metadata__features__contains=[feature])
        
        return [product async for product in qs.aiterator()]

    @classmethod
    async def aget_product_by_sku(cls, sku: str) -> Product | None:
        """Async version: Finds a product by SKU."""
        return await Product.objects.filter(sku=sku, is_active=True).afirst()
