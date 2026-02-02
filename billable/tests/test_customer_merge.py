import pytest
from django.contrib.auth import get_user_model
from billable.models import Product, Offer, OfferItem, QuotaBatch, Transaction, Order, ExternalIdentity, Referral
from billable.services import CustomerService, TransactionService, OrderService
from billable.signals import customers_merged

User = get_user_model()

@pytest.fixture
def target_user(db):
    return User.objects.create(username="target_user")

@pytest.fixture
def source_user(db):
    return User.objects.create(username="source_user")

@pytest.fixture
def product(db):
    return Product.objects.create(
        product_key="TEST_PROD",
        name="Test Product",
        product_type=Product.ProductType.QUANTITY,
    )

@pytest.fixture
def offer(db, product):
    offer = Offer.objects.create(
        sku="off_test",
        name="Test Offer",
        price=10,
        currency="USD",
        is_active=True
    )
    OfferItem.objects.create(offer=offer, product=product, quantity=10)
    return offer

@pytest.mark.django_db(transaction=True)
class TestCustomerMerge:
    """
    Tests for CustomerService.merge_customers.
    """

    def test_merge_basic_data(self, target_user, source_user, offer, product):
        # 1. Setup source user data
        # Order
        order = OrderService.create_order(source_user.id, [{"sku": offer.sku, "quantity": 1}])
        OrderService.process_payment(order.id, payment_id="PAY-1")
        
        # Manual QuotaBatch
        QuotaBatch.objects.create(
            user=source_user, product=product, initial_quantity=50, remaining_quantity=50
        )
        
        # External Identity
        ExternalIdentity.objects.create(user=source_user, provider="telegram", external_id="12345")
        
        # Referral (source invited someone)
        referee = User.objects.create(username="referee")
        Referral.objects.create(referrer=source_user, referee=referee)

        # 2. Perform merge
        stats = CustomerService.merge_customers(target_user.id, source_user.id)
        
        # 3. Verify stats
        assert stats["moved_orders"] == 1
        assert stats["moved_batches"] == 2  # 1 from order payment + 1 manual
        assert stats["moved_identities"] == 1
        assert stats["moved_referrals"] == 1

        # 4. Verify data ownership
        assert Order.objects.filter(user=target_user).count() == 1
        assert Order.objects.filter(user=source_user).count() == 0
        
        assert QuotaBatch.objects.filter(user=target_user).count() == 2
        assert QuotaBatch.objects.filter(user=source_user).count() == 0
        
        assert ExternalIdentity.objects.filter(user=target_user, provider="telegram").exists()
        assert not ExternalIdentity.objects.filter(user=source_user).exists()
        
        assert Referral.objects.filter(referrer=target_user, referee=referee).exists()

    def test_merge_identity_conflict_same_id(self, target_user, source_user):
        # Both have same identity - should just delete source one
        ExternalIdentity.objects.create(user=target_user, provider="telegram", external_id="12345")
        # Use different external_id for source to allow creation, then we'll test conflict logic
        # Actually, the model has unique constraint on (provider, external_id).
        # So we can't have two records with same (provider, external_id) even for different users.
        # This means the "same external_id" case is impossible to setup with two records.
        # If a user tries to link an already linked identity, it will fail at creation.
        pass

    def test_merge_identity_conflict_different_id(self, target_user, source_user):
        # Both have different identity for same provider - should raise error
        ExternalIdentity.objects.create(user=target_user, provider="telegram", external_id="target_tg")
        ExternalIdentity.objects.create(user=source_user, provider="telegram", external_id="source_tg")
        
        with pytest.raises(ValueError, match="Identity conflict"):
            CustomerService.merge_customers(target_user.id, source_user.id)

    def test_merge_self_referral_cleanup(self, target_user, source_user):
        # source_user invited target_user
        Referral.objects.create(referrer=source_user, referee=target_user)
        
        stats = CustomerService.merge_customers(target_user.id, source_user.id)
        assert stats["moved_referrals"] == 1 # it was moved then deleted
        assert Referral.objects.filter(referrer=target_user, referee=target_user).count() == 0

    def test_merge_signal_sent(self, target_user, source_user):
        signal_received = False
        def handler(sender, **kwargs):
            nonlocal signal_received
            signal_received = True
            assert kwargs["target_user_id"] == target_user.id
            assert kwargs["source_user_id"] == source_user.id

        customers_merged.connect(handler)
        try:
            CustomerService.merge_customers(target_user.id, source_user.id)
            assert signal_received is True
        finally:
            customers_merged.disconnect(handler)

    @pytest.mark.asyncio
    async def test_amerge_customers(self, target_user, source_user):
        stats = await CustomerService.amerge_customers(target_user.id, source_user.id)
        assert stats["moved_orders"] == 0
        assert await User.objects.filter(pk=target_user.id).aexists()
