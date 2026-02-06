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
async def some_offer(db):
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
    await OfferItem.objects.acreate(offer=offer, product=product, quantity=1)
    return offer

@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_standardized_identity_logic(api_client, some_offer):
    # 1. GET /wallet for non-existent user should NOT create user and return 404
    res_get = await api_client.get("/wallet?provider=test&external_id=missing_gets")
    assert res_get.status_code == 404
    assert not await User.objects.filter(username="billable_test_missing_gets").aexists()

    # 2. POST /orders for non-existent user should CREATE user and return 200
    payload = {
        "provider": "test",
        "external_id": "new_posts",
        "items": [{"sku": "TEST_SKU", "quantity": 1}]
    }
    res_post = await api_client.post("/orders", json=payload)
    assert res_post.status_code == 200
    assert await User.objects.filter(username="billable_test_new_posts").aexists()

    # 3. POST /wallet/consume for non-existent user should CREATE user
    payload_consume = {
        "provider": "test",
        "external_id": "consume_post",
        "product_key": "TEST_PROD",
        "action_type": "test"
    }
    # It might return 400 because of insufficient quota, but the user should be created
    res_consume = await api_client.post("/wallet/consume", json=payload_consume)
    assert await User.objects.filter(username="billable_test_consume_post").aexists()
