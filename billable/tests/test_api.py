import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from asgiref.sync import sync_to_async
from billable.models import Product, UserProduct, Order, ExternalIdentity, Referral
from ninja.testing import TestAsyncClient
from billable.api import router

User = get_user_model()

@pytest.fixture
def api_client():
    from django.conf import settings
    return TestAsyncClient(router, headers={"Authorization": f"Bearer {settings.BILLABLE_API_TOKEN}"})

@pytest.fixture
def test_user(db):
    return User.objects.create(username="apiuser")

@pytest.fixture
def sample_product(db):
    return Product.objects.create(
        sku="API_TEST",
        name="API Test Product",
        product_type=Product.ProductType.QUANTITY,
        price=Decimal("50.00"),
        quantity=10,
        is_active=True,
        metadata={"features": ["api_feature"]}
    )

@pytest.fixture
def extra_product(db):
    """Product with another feature for filter tests."""
    return Product.objects.create(
        sku="EXTRA_PROD",
        name="Extra Product",
        product_type=Product.ProductType.QUANTITY,
        price=Decimal("20.00"),
        quantity=5,
        is_active=True,
        metadata={"features": ["other_feature"]}
    )

@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestBillableAPI:
    async def test_list_products(self, api_client, sample_product):
        response = await api_client.get("/products")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(p["sku"] == "API_TEST" for p in data)

    async def test_check_balance(self, api_client, test_user, sample_product):
        # No balance initially
        response = await api_client.get(f"/balance?user_id={test_user.id}&feature=api_feature")
        assert response.status_code == 200
        assert response.json()["can_use"] is False

        # Add product
        await UserProduct.objects.acreate(
            user=test_user,
            product=sample_product,
            total_quantity=10,
            used_quantity=0,
            is_active=True
        )

        response = await api_client.get(f"/balance?user_id={test_user.id}&feature=api_feature")
        assert response.status_code == 200
        assert response.json()["can_use"] is True
        assert response.json()["remaining"] == 10

    async def test_consume_quota(self, api_client, test_user, sample_product):
        await UserProduct.objects.acreate(
            user=test_user,
            product=sample_product,
            total_quantity=5,
            used_quantity=0,
            is_active=True
        )

        payload = {
            "user_id": test_user.id,
            "feature": "api_feature",
            "action_type": "api_call",
            "idempotency_key": "unique_key_123"
        }
        response = await api_client.post("/quota/consume", json=payload)
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["data"]["remaining"] == 4

    async def test_create_order(self, api_client, test_user, sample_product):
        payload = {
            "user_id": test_user.id,
            "products": [{"sku": "API_TEST", "quantity": 2}]
        }
        response = await api_client.post("/orders", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["total_amount"] == "100.00"  # 50 * 2
        assert data["status"] == "pending"

    async def test_confirm_order(self, api_client, test_user, sample_product):
        # Create order manually
        order = await Order.objects.acreate(user=test_user, total_amount=Decimal("50.00"))
        
        payload = {
            "payment_method": "test_gateway",
            "payment_id": "test_pay_id"
        }
        response = await api_client.post(f"/orders/{order.id}/confirm", json=payload)
        assert response.status_code == 200
        assert response.json()["success"] is True
        
        await sync_to_async(order.refresh_from_db)()
        assert order.status == Order.Status.PAID


    async def test_get_product_by_sku(self, api_client, sample_product):
        response = await api_client.get(f"/products/{sample_product.sku}")
        assert response.status_code == 200
        assert response.json()["sku"] == sample_product.sku

        response = await api_client.get("/products/NON_EXISTENT")
        assert response.status_code == 404

    async def test_grant_trial(self, api_client, test_user):
        # Create trial product
        await Product.objects.acreate(
            sku="TRIAL_1",
            name="Trial Product",
            product_type=Product.ProductType.PERIOD,
            price=Decimal("0.00"),
            period_days=7,
            is_active=True,
            metadata={"is_trial": True}
        )

        payload = {
            "user_id": test_user.id,
            "provider": "telegram",
            "external_id": str(test_user.id),
        }
        response = await api_client.post("/grants", json=payload)
        assert response.status_code == 200
        assert response.json()["success"] is True
        
        # Second grant should fail
        response = await api_client.post("/grants", json=payload)
        assert response.status_code == 400
        assert response.json()["data"]["error"] == "trial_already_used"

    async def test_identify_creates_identity_and_user(self, api_client):
        payload = {
            "provider": "telegram",
            "external_id": "777",
            "profile": {"telegram_username": "alice", "first_name": "Alice"},
        }
        response = await api_client.post("/identify", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["provider"] == "telegram"
        assert data["external_id"] == "777"
        assert isinstance(data["user_id"], int)
        assert data["identity_id"] is not None
        assert data["trial_eligible"] in (True, False)

    async def test_get_order_details(self, api_client, test_user):
        order = await Order.objects.acreate(user=test_user, total_amount=Decimal("10.00"))
        response = await api_client.get(f"/orders/{order.id}")
        assert response.status_code == 200
        assert response.json()["id"] == order.id

    async def test_unauthorized_access(self):
        from ninja.testing import TestAsyncClient
        client = TestAsyncClient(router) # No token
        response = await client.get("/products")
        assert response.status_code == 401

    async def test_assign_referral(self, api_client, test_user):
        other_user = await User.objects.acreate(username="referee")
        payload = {
            "referrer_id": test_user.id,
            "referee_id": other_user.id,
            "metadata": {"source": "test"}
        }
        response = await api_client.post("/referrals", json=payload)
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["data"]["created"] is True

        # Retry (get_or_create)
        response = await api_client.post("/referrals", json=payload)
        assert response.status_code == 200
        assert response.json()["data"]["created"] is False

    # --- GET /user-products ---

    async def test_list_user_products_by_user_id(self, api_client, test_user, sample_product):
        """GET /user-products with user_id returns all active user products."""
        await UserProduct.objects.acreate(
            user=test_user,
            product=sample_product,
            total_quantity=10,
            used_quantity=0,
            is_active=True,
        )
        response = await api_client.get(f"/user-products?user_id={test_user.id}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["product"]["sku"] == "API_TEST"
        assert data[0]["product"]["metadata"].get("features") == ["api_feature"]

    async def test_list_user_products_filter_by_feature(self, api_client, test_user, sample_product, extra_product):
        """GET /user-products with feature returns only products containing that feature."""
        await UserProduct.objects.acreate(
            user=test_user,
            product=sample_product,
            total_quantity=10,
            used_quantity=0,
            is_active=True,
        )
        await UserProduct.objects.acreate(
            user=test_user,
            product=extra_product,
            total_quantity=5,
            used_quantity=0,
            is_active=True,
        )
        response = await api_client.get(f"/user-products?user_id={test_user.id}&feature=api_feature")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["product"]["sku"] == "API_TEST"
        assert "api_feature" in data[0]["product"]["metadata"].get("features", [])

    async def test_list_user_products_empty(self, api_client, test_user):
        """GET /user-products for user with no products returns empty list."""
        response = await api_client.get(f"/user-products?user_id={test_user.id}")
        assert response.status_code == 200
        assert response.json() == []

    async def test_list_user_products_by_external_id(self, api_client, test_user, sample_product):
        """GET /user-products with external_id and provider resolves user via ExternalIdentity."""
        await ExternalIdentity.objects.acreate(
            provider="default",
            external_id="ext_123",
            user=test_user,
        )
        await UserProduct.objects.acreate(
            user=test_user,
            product=sample_product,
            total_quantity=10,
            used_quantity=0,
            is_active=True,
        )
        response = await api_client.get("/user-products?external_id=ext_123&provider=default")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["product"]["sku"] == "API_TEST"

    async def test_list_user_products_includes_features(self, api_client, test_user, sample_product):
        """Each product in response includes product.metadata.features."""
        await UserProduct.objects.acreate(
            user=test_user,
            product=sample_product,
            total_quantity=10,
            used_quantity=0,
            is_active=True,
        )
        response = await api_client.get(f"/user-products?user_id={test_user.id}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert "product" in data[0]
        assert "metadata" in data[0]["product"]
        assert data[0]["product"]["metadata"].get("features") == ["api_feature"]

    # --- Negative test cases ---

    async def test_consume_quota_no_balance(self, api_client, test_user):
        """Test quota consumption when user has no balance."""
        payload = {
            "user_id": test_user.id,
            "feature": "api_feature",
            "action_type": "api_call",
            "idempotency_key": "unique_key_no_balance"
        }
        response = await api_client.post("/quota/consume", json=payload)
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "quota_exhausted" in response.json()["data"].get("error", "")

    async def test_consume_quota_exhausted(self, api_client, test_user, sample_product):
        """Test quota consumption when quota is exhausted."""
        await UserProduct.objects.acreate(
            user=test_user,
            product=sample_product,
            total_quantity=5,
            used_quantity=5,  # All used
            is_active=True
        )

        payload = {
            "user_id": test_user.id,
            "feature": "api_feature",
            "action_type": "api_call",
            "idempotency_key": "unique_key_exhausted"
        }
        response = await api_client.post("/quota/consume", json=payload)
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "quota_exhausted" in response.json()["data"].get("error", "")

    async def test_create_order_invalid_sku(self, api_client, test_user):
        """Test order creation with invalid SKU."""
        payload = {
            "user_id": test_user.id,
            "products": [{"sku": "NON_EXISTENT_SKU", "quantity": 1}]
        }
        response = await api_client.post("/orders", json=payload)
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "not found" in response.json()["message"].lower()

    async def test_create_order_empty_products(self, api_client, test_user):
        """Test order creation with empty products list."""
        payload = {
            "user_id": test_user.id,
            "products": []
        }
        response = await api_client.post("/orders", json=payload)
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "no valid products" in response.json()["message"].lower()

    async def test_create_order_mixed_valid_invalid_skus(self, api_client, test_user, sample_product):
        """Test order creation with mix of valid and invalid SKUs.
        
        API ignores invalid SKUs and creates order only with valid products.
        """
        payload = {
            "user_id": test_user.id,
            "products": [
                {"sku": "API_TEST", "quantity": 1},
                {"sku": "INVALID_SKU", "quantity": 1}
            ]
        }
        # Should succeed with only valid products (invalid SKUs are ignored)
        response = await api_client.post("/orders", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["total_amount"] == "50.00"

    async def test_confirm_order_not_found(self, api_client):
        """Test order confirmation when order doesn't exist."""
        payload = {
            "payment_method": "test_gateway",
            "payment_id": "test_pay_id"
        }
        response = await api_client.post("/orders/99999/confirm", json=payload)
        # aprocess_payment returns False when order not found, API returns 400
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "failed to process payment" in response.json()["message"].lower()

    async def test_get_order_not_found(self, api_client):
        """Test getting order that doesn't exist."""
        response = await api_client.get("/orders/99999")
        assert response.status_code == 404
        assert response.json()["success"] is False
        assert "not found" in response.json()["message"].lower()

    async def test_assign_referral_same_user(self, api_client, test_user):
        """Test referral assignment with same user as referrer and referee."""
        payload = {
            "referrer_id": test_user.id,
            "referee_id": test_user.id,
            "metadata": {"source": "test"}
        }
        response = await api_client.post("/referrals", json=payload)
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "cannot be the same" in response.json()["message"].lower()

    async def test_assign_referral_by_external_id(self, api_client):
        """POST /referrals with provider + referrer_external_id + referee_external_id resolves via ExternalIdentity."""
        payload = {
            "provider": "telegram",
            "referrer_external_id": "tg_ref_111",
            "referee_external_id": "tg_ref_222",
            "metadata": {"source": "bot"},
        }
        response = await api_client.post("/referrals", json=payload)
        assert response.status_code == 200
        assert response.json()["success"] is True
        assert response.json()["data"]["created"] is True
        count_before = await Referral.objects.acount()
        response2 = await api_client.post("/referrals", json=payload)
        assert response2.status_code == 200
        assert response2.json()["data"]["created"] is False
        assert await Referral.objects.acount() == count_before

    async def test_assign_referral_same_external_id(self, api_client):
        """POST /referrals with same referrer_external_id and referee_external_id returns 400."""
        payload = {
            "provider": "telegram",
            "referrer_external_id": "tg_same",
            "referee_external_id": "tg_same",
            "metadata": {},
        }
        response = await api_client.post("/referrals", json=payload)
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "cannot be the same" in response.json()["message"].lower()

    async def test_assign_referral_400_incomplete_input(self, api_client):
        """POST /referrals without either (referrer_id, referee_id) or (provider, referrer_external_id, referee_external_id) returns 400."""
        response = await api_client.post("/referrals", json={"metadata": {}})
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "Provide either" in response.json()["message"]

    async def test_list_user_products_400_no_user(self, api_client):
        """GET /user-products without user_id and without valid external_id returns 400."""
        response = await api_client.get("/user-products")
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "user_id" in response.json()["message"].lower()

    async def test_list_user_products_400_external_id_provider_no_identity(self, api_client):
        """GET /user-products with external_id and provider but no matching identity returns 400."""
        response = await api_client.get("/user-products?external_id=no_such_ext&provider=default")
        assert response.status_code == 400
        assert response.json()["success"] is False
        assert "user_id" in response.json()["message"].lower()
