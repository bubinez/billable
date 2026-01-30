import pytest
from django.core.exceptions import ValidationError
from django.contrib.admin.sites import AdminSite
from billable.models import Product, Offer, OfferItem, ExternalIdentity
from django.contrib.auth import get_user_model
from billable.admin import ProductAdmin
from django.test import RequestFactory

User = get_user_model()

@pytest.mark.django_db
class TestSharedNamespace:
    """Tests for the Shared Namespace validator (product_key vs sku)."""

    def test_product_key_conflict_with_offer_sku(self):
        """Test that Product cannot have a product_key that is already an Offer SKU."""
        Offer.objects.create(sku="conflicting_key", name="Test Offer", price=100, currency="USD")
        
        product = Product(product_key="conflicting_key", name="Test Product", product_type=Product.ProductType.QUANTITY)
        
        with pytest.raises(ValidationError) as excinfo:
            product.clean()
        
        assert "Conflict: 'conflicting_key' is already used as an Offer SKU." in str(excinfo.value)

    def test_offer_sku_conflict_with_product_key(self):
        """Test that Offer cannot have a sku that is already a Product product_key."""
        Product.objects.create(product_key="conflicting_key", name="Test Product", product_type=Product.ProductType.QUANTITY)
        
        offer = Offer(sku="conflicting_key", name="Test Offer", price=100, currency="USD")
        
        with pytest.raises(ValidationError) as excinfo:
            offer.clean()
        
        assert "Conflict: 'conflicting_key' is already used as a Product Key." in str(excinfo.value)

    def test_no_conflict_different_keys(self):
        """Test that no validation error occurs when keys are different."""
        Product.objects.create(product_key="prod_key", name="Test Product", product_type=Product.ProductType.QUANTITY)
        Offer.objects.create(sku="offer_sku", name="Test Offer", price=100, currency="USD")
        
        # Should not raise
        p = Product(product_key="new_prod", name="New", product_type="quantity")
        p.clean()
        o = Offer(sku="new_offer", name="New", price=10, currency="USD")
        o.clean()

@pytest.mark.django_db
class TestAdminAutoOfferCreation:
    """Tests for automatic offer creation in ProductAdmin."""

    def test_save_model_creates_offer_with_get_prefix(self):
        """Test that saving a Product with offer details creates an Offer with 'get_' prefix."""
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        # Mock message_user to avoid session/middleware requirements in tests
        admin.message_user = lambda request, message, level=None, extra_tags=None, fail_silently=False: None
        
        factory = RequestFactory()
        request = factory.get('/')
        
        product = Product.objects.create(
            product_key="diamonds", 
            name="Diamonds", 
            product_type=Product.ProductType.QUANTITY
        )
        
        class MockForm:
            cleaned_data = {
                "offer_price": 100.00,
                "offer_currency": "USD",
                "offer_quantity": 50,
                "offer_period_unit": OfferItem.PeriodUnit.FOREVER,
                "offer_period_value": None
            }
        
        form = MockForm()
        
        # Call save_model to trigger offer creation
        admin.save_model(request, product, form, change=True)
        
        # Verify Offer was created
        offer = Offer.objects.get(sku="get_diamonds")
        assert offer.name == product.name
        assert offer.price == 100.00
        assert offer.currency == "USD"
        
        # Verify OfferItem was created
        item = OfferItem.objects.get(offer=offer, product=product)
        assert item.quantity == 50

    def test_save_model_handles_duplicate_get_sku(self):
        """Test that it handles cases where the 'get_' SKU already exists."""
        site = AdminSite()
        admin = ProductAdmin(Product, site)
        # Mock message_user
        admin.message_user = lambda request, message, level=None, extra_tags=None, fail_silently=False: None
        
        factory = RequestFactory()
        request = factory.get('/')
        
        product = Product.objects.create(
            product_key="gold", 
            name="Gold", 
            product_type=Product.ProductType.QUANTITY
        )
        
        # Create an existing offer with get_gold
        Offer.objects.create(sku="get_gold", name="Existing Gold", price=50, currency="USD")
        
        class MockForm:
            cleaned_data = {
                "offer_price": 150.00,
                "offer_currency": "USD",
                "offer_quantity": 100,
                "offer_period_unit": OfferItem.PeriodUnit.FOREVER,
                "offer_period_value": None
            }
        
        form = MockForm()
        admin.save_model(request, product, form, change=True)
        
        # Verify a new offer with a suffix was created
        offer = Offer.objects.get(sku="get_gold_1")
        assert offer.price == 150.00

@pytest.mark.django_db
class TestExternalIdentity:
    """Tests for ExternalIdentity identity resolution methods."""

    def test_get_user_by_identity_found(self):
        """Test that get_user_by_identity returns the correct user when it exists."""
        user = User.objects.create(username="testuser")
        ExternalIdentity.objects.create(provider="telegram", external_id="12345", user=user)
        
        found_user = ExternalIdentity.get_user_by_identity("12345", provider="telegram")
        assert found_user == user
        assert found_user.username == "testuser"

    def test_get_user_by_identity_not_found(self):
        """Test that get_user_by_identity returns None when identity doesn't exist."""
        found_user = ExternalIdentity.get_user_by_identity("nonexistent", provider="unknown")
        assert found_user is None

    def test_get_user_by_identity_no_user_linked(self):
        """Test that get_user_by_identity returns None when identity exists but has no user."""
        ExternalIdentity.objects.create(provider="n8n", external_id="67890", user=None)
        
        found_user = ExternalIdentity.get_user_by_identity("67890", provider="n8n")
        assert found_user is None

    @pytest.mark.asyncio
    async def test_aget_user_by_identity_found(self):
        """Test that aget_user_by_identity returns the correct user asynchronously."""
        user = await User.objects.acreate(username="asyncuser")
        await ExternalIdentity.objects.acreate(provider="telegram", external_id="54321", user=user)
        
        found_user = await ExternalIdentity.aget_user_by_identity("54321", provider="telegram")
        assert found_user == user
        assert found_user.username == "asyncuser"

    @pytest.mark.asyncio
    async def test_aget_user_by_identity_not_found(self):
        """Test that aget_user_by_identity returns None asynchronously when not found."""
        found_user = await ExternalIdentity.aget_user_by_identity("nonexistent", provider="unknown")
        assert found_user is None
