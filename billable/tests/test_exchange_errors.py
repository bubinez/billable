import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from billable.models import Product, Offer, OfferItem, QuotaBatch
from ninja.testing import TestAsyncClient
from billable.api import router

User = get_user_model()

@pytest.fixture
def api_client():
    from django.conf import settings
    return TestAsyncClient(router, headers={"Authorization": f"Bearer {settings.BILLABLE_API_TOKEN}"})

@pytest.fixture
def test_user(db):
    return User.objects.create(username="exchange_user")

@pytest.fixture
def credits_product(db):
    return Product.objects.create(
        product_key="internal",
        name="Internal Credits",
        product_type=Product.ProductType.QUANTITY,
        is_active=True,
        is_currency=True,
    )

@pytest.fixture
def non_currency_product(db):
    return Product.objects.create(
        product_key="not_currency",
        name="Not a Currency",
        product_type=Product.ProductType.QUANTITY,
        is_active=True,
        is_currency=False,
    )

@pytest.fixture
def target_product(db):
    return Product.objects.create(
        product_key="TARGET",
        name="Target Product",
        product_type=Product.ProductType.QUANTITY,
        is_active=True,
    )

@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestExchangeErrors:
    async def test_exchange_insufficient_funds(self, api_client, test_user, credits_product, target_product):
        # Offer costs 100 credits
        offer = await Offer.objects.acreate(
            sku="expensive_offer",
            name="Expensive Offer",
            price=Decimal("100.00"),
            currency="INTERNAL",
            is_active=True
        )
        await OfferItem.objects.acreate(offer=offer, product=target_product, quantity=1)

        # User has only 50 credits
        await QuotaBatch.objects.acreate(
            user=test_user, product=credits_product, initial_quantity=50, remaining_quantity=50,
            state=QuotaBatch.State.ACTIVE
        )

        res = await api_client.post("/exchange", json={"user_id": test_user.id, "sku": offer.sku})
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert "Insufficient balance" in data["message"]

    async def test_exchange_currency_not_found(self, api_client, test_user, target_product):
        # Offer uses a currency that doesn't exist as a Product
        offer = await Offer.objects.acreate(
            sku="ghost_currency_offer",
            name="Ghost Currency Offer",
            price=Decimal("10.00"),
            currency="GHOST",
            is_active=True
        )
        await OfferItem.objects.acreate(offer=offer, product=target_product, quantity=1)

        res = await api_client.post("/exchange", json={"user_id": test_user.id, "sku": offer.sku})
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert "Currency product 'GHOST' not found" in data["message"]

    async def test_exchange_not_a_currency(self, api_client, test_user, non_currency_product, target_product):
        # Offer uses a product that is NOT marked as is_currency
        offer = await Offer.objects.acreate(
            sku="non_currency_offer",
            name="Non Currency Offer",
            price=Decimal("10.00"),
            currency="NOT_CURRENCY",
            is_active=True
        )
        await OfferItem.objects.acreate(offer=offer, product=target_product, quantity=1)

        res = await api_client.post("/exchange", json={"user_id": test_user.id, "sku": offer.sku})
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert "not marked as a currency" in data["message"]
