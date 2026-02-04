"""Tests for SKU and Product Key normalization to uppercase."""

import pytest
from django.contrib.auth import get_user_model
from billable.models import Product, Offer, TrialHistory
from ninja.testing import TestAsyncClient
from billable.api import router
from django.conf import settings

User = get_user_model()


@pytest.fixture
def api_client():
    """API client fixture for testing."""
    from django.conf import settings
    return TestAsyncClient(router, headers={"Authorization": f"Bearer {settings.BILLABLE_API_TOKEN}"})


@pytest.fixture
def test_user(db):
    """Test user fixture."""
    return User.objects.create(username="apiuser")


@pytest.mark.django_db
class TestProductKeyNormalization:
    """Tests for Product.product_key normalization to uppercase."""

    def test_product_key_normalized_on_save(self):
        """Test that product_key is normalized to uppercase when saving."""
        product = Product.objects.create(
            product_key="test_product",
            name="Test Product",
            product_type=Product.ProductType.QUANTITY
        )
        assert product.product_key == "TEST_PRODUCT"

    def test_product_key_normalized_on_update(self):
        """Test that product_key is normalized when updating via QuerySet.update()."""
        product = Product.objects.create(
            product_key="original",
            name="Test Product",
            product_type=Product.ProductType.QUANTITY
        )
        Product.objects.filter(pk=product.pk).update(product_key="updated_key")
        product.refresh_from_db()
        assert product.product_key == "UPDATED_KEY"

    def test_product_key_normalized_on_bulk_create(self):
        """Test that product_key is normalized when using bulk_create."""
        products = [
            Product(product_key="bulk1", name="Bulk 1", product_type=Product.ProductType.QUANTITY),
            Product(product_key="bulk2", name="Bulk 2", product_type=Product.ProductType.QUANTITY),
        ]
        Product.objects.bulk_create(products)
        assert Product.objects.get(product_key="BULK1").product_key == "BULK1"
        assert Product.objects.get(product_key="BULK2").product_key == "BULK2"

    def test_product_key_none_handled(self):
        """Test that None product_key is handled correctly."""
        product = Product.objects.create(
            product_key=None,
            name="Test Product",
            product_type=Product.ProductType.QUANTITY
        )
        assert product.product_key is None


@pytest.mark.django_db
class TestSKUNormalization:
    """Tests for Offer.sku normalization to uppercase."""

    def test_sku_normalized_on_save(self):
        """Test that SKU is normalized to uppercase when saving."""
        offer = Offer.objects.create(
            sku="test_offer",
            name="Test Offer",
            price=100,
            currency="USD"
        )
        assert offer.sku == "TEST_OFFER"

    def test_sku_normalized_on_update(self):
        """Test that SKU is normalized when updating via QuerySet.update()."""
        offer = Offer.objects.create(
            sku="original",
            name="Test Offer",
            price=100,
            currency="USD"
        )
        Offer.objects.filter(pk=offer.pk).update(sku="updated_sku")
        offer.refresh_from_db()
        assert offer.sku == "UPDATED_SKU"

    def test_sku_normalized_on_bulk_create(self):
        """Test that SKU is normalized when using bulk_create."""
        offers = [
            Offer(sku="bulk1", name="Bulk 1", price=100, currency="USD"),
            Offer(sku="bulk2", name="Bulk 2", price=200, currency="USD"),
        ]
        Offer.objects.bulk_create(offers)
        assert Offer.objects.get(sku="BULK1").sku == "BULK1"
        assert Offer.objects.get(sku="BULK2").sku == "BULK2"


@pytest.fixture
def test_product_for_api(db):
    """Fixture to create a test product for API tests."""
    return Product.objects.create(
        product_key="TEST_KEY",
        name="Test Product",
        product_type=Product.ProductType.QUANTITY,
        is_active=True
    )


@pytest.mark.django_db
class TestAPINormalization:
    """Tests for API-level normalization (silent normalization)."""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="SQLite locking issues in async context - normalization tested via services")
    async def test_api_accepts_lowercase_product_key(self, api_client, test_user, test_product_for_api):
        """Test that API accepts lowercase product_key and stores it as uppercase."""
        # Note: Skipped due to SQLite locking issues in async context.
        # Normalization is already tested via ProductService tests.
        
        # Product is created via fixture to avoid SQLite locking issues
        # Try to get product with lowercase key
        response = await api_client.get(f"/products/test_key")
        assert response.status_code == 200
        data = response.json()
        assert data["product_key"] == "TEST_KEY"

    @pytest.mark.asyncio
    async def test_api_accepts_lowercase_sku(self, api_client):
        """Test that API accepts lowercase SKU and finds the offer."""
        # Create offer with uppercase SKU
        offer = await Offer.objects.acreate(
            sku="TEST_SKU",
            name="Test Offer",
            price=100,
            currency="USD",
            is_active=True
        )
        
        # Try to get offer with lowercase SKU
        response = await api_client.get("/catalog/test_sku")
        assert response.status_code == 200
        data = response.json()
        assert data["sku"] == "TEST_SKU"

    @pytest.mark.asyncio
    async def test_api_accepts_mixed_case_sku_in_catalog_list(self, api_client):
        """Test that API accepts mixed case SKU in catalog list query."""
        offer1 = await Offer.objects.acreate(
            sku="OFFER_ONE",
            name="Offer One",
            price=100,
            currency="USD",
            is_active=True
        )
        offer2 = await Offer.objects.acreate(
            sku="OFFER_TWO",
            name="Offer Two",
            price=200,
            currency="USD",
            is_active=True
        )
        
        # Query with mixed case
        response = await api_client.get("/catalog?sku=offer_one&sku=OFFER_TWO")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        skus = [item["sku"] for item in data]
        assert "OFFER_ONE" in skus
        assert "OFFER_TWO" in skus


@pytest.mark.django_db
class TestTrialHistoryLowercase:
    """Tests that TrialHistory uses lowercase for hashing (exception to CAPS rule)."""

    def test_trial_history_uses_lowercase_for_hashing(self):
        """Test that TrialHistory.generate_identity_hash uses lowercase."""
        email1 = "User@Example.com"
        email2 = "user@example.com"
        email3 = "USER@EXAMPLE.COM"
        
        hash1 = TrialHistory.generate_identity_hash(email1)
        hash2 = TrialHistory.generate_identity_hash(email2)
        hash3 = TrialHistory.generate_identity_hash(email3)
        
        # All should produce the same hash (normalized to lowercase)
        assert hash1 == hash2 == hash3
        
        # Verify it's actually lowercase-based
        assert hash1 == TrialHistory.generate_identity_hash(email1.lower())


@pytest.mark.django_db
class TestServiceNormalization:
    """Tests for service-level normalization (ProductService, OrderService, TransactionService)."""

    def test_product_service_normalizes_product_key(self):
        """Test that ProductService.get_product_by_key normalizes input."""
        from billable.services import ProductService
        
        product = Product.objects.create(
            product_key="TEST_KEY",
            name="Test Product",
            product_type=Product.ProductType.QUANTITY,
            is_active=True
        )
        
        # Should find product even with lowercase input
        found = ProductService.get_product_by_key("test_key")
        assert found is not None
        assert found.product_key == "TEST_KEY"
        
        # Should find product with mixed case
        found2 = ProductService.get_product_by_key("TeSt_KeY")
        assert found2 is not None
        assert found2.product_key == "TEST_KEY"

    @pytest.mark.asyncio
    async def test_product_service_async_normalizes_product_key(self):
        """Test that ProductService.aget_product_by_key normalizes input."""
        from billable.services import ProductService
        
        product = await Product.objects.acreate(
            product_key="ASYNC_KEY",
            name="Async Product",
            product_type=Product.ProductType.QUANTITY,
            is_active=True
        )
        
        # Should find product even with lowercase input
        found = await ProductService.aget_product_by_key("async_key")
        assert found is not None
        assert found.product_key == "ASYNC_KEY"

    def test_order_service_normalizes_sku(self):
        """Test that OrderService normalizes SKU when creating orders."""
        from billable.services import OrderService
        from django.contrib.auth import get_user_model
        
        User = get_user_model()
        user = User.objects.create(username="testuser")
        
        offer = Offer.objects.create(
            sku="TEST_OFFER",
            name="Test Offer",
            price=100,
            currency="USD",
            is_active=True
        )
        
        # Create order with lowercase SKU - should work
        order = OrderService.create_order(
            user_id=user.id,
            items=[{"sku": "test_offer", "quantity": 1}]
        )
        
        assert order is not None
        assert order.items.first().offer.sku == "TEST_OFFER"

    @pytest.mark.asyncio
    async def test_order_service_async_normalizes_sku(self):
        """Test that OrderService.acreate_order normalizes SKU."""
        from billable.services import OrderService
        from django.contrib.auth import get_user_model
        
        User = get_user_model()
        import uuid
        unique_username = f"asyncuser_{uuid.uuid4().hex[:8]}"
        user = await User.objects.acreate(username=unique_username)
        
        offer = await Offer.objects.acreate(
            sku="ASYNC_OFFER",
            name="Async Offer",
            price=200,
            currency="USD",
            is_active=True
        )
        
        # Create order with mixed case SKU - should work
        order = await OrderService.acreate_order(
            user_id=user.id,
            items=[{"sku": "AsYnC_OfFeR", "quantity": 1}]
        )
        
        assert order is not None
        order_item = await order.items.select_related("offer").afirst()
        assert order_item.offer.sku == "ASYNC_OFFER"

    def test_transaction_service_normalizes_product_key(self):
        """Test that TransactionService normalizes product_key in queries."""
        from billable.services import TransactionService
        from django.contrib.auth import get_user_model
        from billable.models import QuotaBatch
        
        User = get_user_model()
        user = User.objects.create(username="txuser")
        
        product = Product.objects.create(
            product_key="TX_PRODUCT",
            name="TX Product",
            product_type=Product.ProductType.QUANTITY,
            is_active=True
        )
        
        batch = QuotaBatch.objects.create(
            user=user,
            product=product,
            initial_quantity=100,
            remaining_quantity=100,
            state=QuotaBatch.State.ACTIVE
        )
        
        # Check balance with lowercase product_key - should work
        balance = TransactionService.get_balance(user.id, "tx_product")
        assert balance == 100
        
        # Check quota with mixed case - should work
        result = TransactionService.check_quota(user.id, "Tx_PrOdUcT")
        assert result["can_use"] is True
        assert result["product_key"] == "TX_PRODUCT"
