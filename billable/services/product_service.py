"""Service for working with the product catalog.

Provides access to available products and their configurations.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import List

from ..models import Product, Offer

logger = logging.getLogger(__name__)


class ProductService:
    """Service for working with the product catalog."""

    @classmethod
    def get_active_products(cls) -> List[Product]:
        """
        Returns a list of active products.

        Returns:
            List of active products.
        """
        return list(Product.objects.filter(is_active=True))

    @classmethod
    def get_product_by_key(cls, product_key: str) -> Product | None:
        """
        Finds a product by its product_key.
        
        Normalizes product_key to uppercase before searching.
        """
        normalized_key = product_key.upper() if product_key else None
        return Product.objects.filter(product_key=normalized_key, is_active=True).first()

    @classmethod
    def get_trial_products(cls) -> List[Product]:
        """Returns products marked as trial."""
        return list(Product.objects.filter(
            is_active=True,
            name__icontains="trial"
        ))

    @classmethod
    async def aget_active_products(cls) -> List[Product]:
        """
        Async version: Returns a list of active products.
        """
        products = []
        qs = Product.objects.filter(is_active=True)
        async for product in qs.aiterator():
            products.append(product)
        return products

    @classmethod
    async def aget_product_by_key(cls, product_key: str) -> Product | None:
        """
        Async version: Finds a product by its product_key.
        
        Normalizes product_key to uppercase before searching.
        """
        normalized_key = product_key.upper() if product_key else None
        return await Product.objects.filter(product_key=normalized_key, is_active=True).afirst()
