import pytest
from django.contrib.auth import get_user_model
from ninja.testing import TestAsyncClient
from billable.api import router
from billable.models import Product, Offer, OfferItem, ExternalIdentity

User = get_user_model()

@pytest.fixture
def api_client():
    from django.conf import settings
    return TestAsyncClient(router, headers={"Authorization": f"Bearer {settings.BILLABLE_API_TOKEN}"})

@pytest.fixture
async def sample_offer(db):
    product = await Product.objects.acreate(
        product_key="TEST_PROD",
        name="Test Product",
        is_active=True
    )
    offer = await Offer.objects.acreate(
        sku="TEST_SKU",
        name="Test Offer",
        price=10.0,
        currency="USD",
        is_active=True
    )
    await OfferItem.objects.acreate(offer=offer, product=product, quantity=10)
    return offer

@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestIdentityResolution:
    """Tests for the standardized identity resolution logic (GET lookup, POST create)."""

    async def test_get_wallet_returns_404_for_new_identity(self, api_client):
        """GET /wallet should not create a user and return 404 if identity is missing."""
        provider = "test_get"
        external_id = "missing_user"
        
        res = await api_client.get(f"/wallet?provider={provider}&external_id={external_id}")
        
        assert res.status_code == 404
        assert res.json()["success"] is False
        assert res.json()["message"] == "User not found"
        
        # Verify no user created
        username = f"billable_{provider}_{external_id}"
        assert not await User.objects.filter(username=username).aexists()

    async def test_post_orders_creates_user_for_new_identity(self, api_client, sample_offer):
        """POST /orders should automatically create a user for a new identity."""
        provider = "test_post"
        external_id = "new_user_order"
        
        payload = {
            "provider": provider,
            "external_id": external_id,
            "items": [{"sku": "TEST_SKU", "quantity": 1}]
        }
        
        res = await api_client.post("/orders", json=payload)
        
        assert res.status_code == 200
        data = res.json()
        assert data["user_id"] is not None
        
        # Verify user created
        username = f"billable_{provider}_{external_id}"
        user = await User.objects.aget(username=username)
        assert data["user_id"] == user.id

    async def test_post_consume_creates_user_for_new_identity(self, api_client, sample_offer):
        """POST /wallet/consume should automatically create a user (even if consume fails later)."""
        provider = "test_post"
        external_id = "new_user_consume"
        
        payload = {
            "provider": provider,
            "external_id": external_id,
            "product_key": "TEST_PROD",
            "action_type": "test_usage"
        }
        
        res = await api_client.post("/wallet/consume", json=payload)
        
        # Will likely be 400 because new user has no balance, but user should still be created
        assert res.status_code == 400
        
        # Verify user created
        username = f"billable_{provider}_{external_id}"
        assert await User.objects.filter(username=username).aexists()

    async def test_post_exchange_creates_user_for_new_identity(self, api_client, sample_offer):
        """POST /exchange should automatically create a user."""
        provider = "test_post"
        external_id = "new_user_exchange"
        
        payload = {
            "provider": provider,
            "external_id": external_id,
            "sku": "TEST_SKU"
        }
        
        res = await api_client.post("/exchange", json=payload)
        
        # Will likely be 400 or 404 depending on SKU/balance, but user creation is the focus
        username = f"billable_{provider}_{external_id}"
        assert await User.objects.filter(username=username).aexists()

    async def test_get_balance_lookup_only(self, api_client):
        """GET /balance should remain lookup-only (no auto-creation)."""
        provider = "test_get"
        external_id = "balance_lookup"
        
        res = await api_client.get(f"/balance?provider={provider}&external_id={external_id}&product_key=ANY")
        
        assert res.status_code == 200 # Current implementation returns can_use=False with message
        assert res.json()["can_use"] is False
        assert "user_id is required" in res.json()["message"]
        
        # Verify no user created
        username = f"billable_{provider}_{external_id}"
        assert not await User.objects.filter(username=username).aexists()
