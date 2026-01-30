import pytest
import threading
from decimal import Decimal
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import connection, transaction as db_transaction
from billable.models import Product, Offer, OfferItem, QuotaBatch, Transaction, Order, OrderItem, Referral
from billable.services import TransactionService, OrderService
from billable.signals import referral_attached, transaction_created, quota_consumed

User = get_user_model()

@pytest.fixture
def test_user(db):
    return User.objects.create(username="adv_user")

@pytest.fixture
def qty_product(db):
    return Product.objects.create(
        product_key="tokens",
        name="Tokens",
        product_type=Product.ProductType.QUANTITY,
    )

@pytest.fixture
def period_product(db):
    return Product.objects.create(
        product_key="premium",
        name="Premium",
        product_type=Product.ProductType.PERIOD,
    )

@pytest.fixture
def unlimited_product(db):
    return Product.objects.create(
        product_key="unlimited_access",
        name="Unlimited Access",
        product_type=Product.ProductType.UNLIMITED,
    )

@pytest.fixture
def basic_offer(db, qty_product):
    offer = Offer.objects.create(sku="off_tokens_100", name="100 Tokens", price=10, currency="USD")
    OfferItem.objects.create(offer=offer, product=qty_product, quantity=100)
    return offer

@pytest.mark.django_db
class TestExpiration:
    def test_expires_at_calculation(self, test_user, qty_product):
        offer = Offer.objects.create(sku="off_exp", name="Exp Offer", price=10, currency="USD")
        OfferItem.objects.create(
            offer=offer, product=qty_product, quantity=10,
            period_unit=OfferItem.PeriodUnit.DAYS, period_value=30
        )
        
        now = timezone.now()
        TransactionService.grant_offer(test_user.id, offer)
        
        batch = QuotaBatch.objects.get(user=test_user, product=qty_product)
        assert batch.expires_at is not None
        # Should be roughly 30 days from now
        assert (batch.expires_at - now).days == 30

    def test_active_batches_filtering(self, test_user, qty_product):
        # Active batch
        QuotaBatch.objects.create(
            user=test_user, product=qty_product, initial_quantity=10, remaining_quantity=10,
            state=QuotaBatch.State.ACTIVE, expires_at=timezone.now() + timedelta(days=1)
        )
        # Expired batch (by date)
        QuotaBatch.objects.create(
            user=test_user, product=qty_product, initial_quantity=10, remaining_quantity=10,
            state=QuotaBatch.State.ACTIVE, expires_at=timezone.now() - timedelta(days=1)
        )
        # Expired batch (by state)
        QuotaBatch.objects.create(
            user=test_user, product=qty_product, initial_quantity=10, remaining_quantity=10,
            state=QuotaBatch.State.EXPIRED, expires_at=timezone.now() + timedelta(days=1)
        )
        
        balance = TransactionService.get_balance(test_user.id, "tokens")
        assert balance == 10 # Only the first one is truly active

    def test_expire_batches_service(self, test_user, qty_product):
        batch = QuotaBatch.objects.create(
            user=test_user, product=qty_product, initial_quantity=10, remaining_quantity=10,
            state=QuotaBatch.State.ACTIVE, expires_at=timezone.now() - timedelta(minutes=1)
        )
        
        count = TransactionService.expire_batches()
        assert count == 1
        batch.refresh_from_db()
        assert batch.state == QuotaBatch.State.EXPIRED

@pytest.mark.django_db
class TestProductTypes:
    def test_unlimited_product_behavior(self, test_user, unlimited_product):
        # Current implementation still requires a batch for unlimited
        offer = Offer.objects.create(sku="off_unlimited", name="Unlimited", price=0, currency="USD")
        OfferItem.objects.create(offer=offer, product=unlimited_product, quantity=1) # 1 "unit" of unlimited
        
        TransactionService.grant_offer(test_user.id, offer)
        
        # Check if we can consume it multiple times if we wanted to, 
        # but usually unlimited means "can_use" is always true.
        # Here we just check it grants correctly.
        assert TransactionService.get_balance(test_user.id, "unlimited_access") == 1

@pytest.mark.django_db
class TestIdempotency:
    def test_consume_idempotency(self, test_user, qty_product):
        QuotaBatch.objects.create(
            user=test_user, product=qty_product, initial_quantity=100, remaining_quantity=100,
            state=QuotaBatch.State.ACTIVE
        )
        
        # First consumption
        res1 = TransactionService.consume_quota(
            test_user.id, "tokens", amount=10, idempotency_key="unique_key_123"
        )
        assert res1["success"] is True
        assert TransactionService.get_balance(test_user.id, "tokens") == 90
        
        # Second consumption with same key
        res2 = TransactionService.consume_quota(
            test_user.id, "tokens", amount=10, idempotency_key="unique_key_123"
        )
        assert res2["success"] is True
        assert "idempotent" in res2["message"]
        assert TransactionService.get_balance(test_user.id, "tokens") == 90 # Still 90

@pytest.mark.django_db
class TestSignals:
    def test_referral_attached_signal(self, test_user):
        other_user = User.objects.create(username="referee")
        
        signal_received = []
        def handler(sender, referral, **kwargs):
            signal_received.append(referral)
            
        referral_attached.connect(handler)
        
        # Trigger signal (this is usually done in API or Service)
        referral = Referral.objects.create(referrer=test_user, referee=other_user)
        referral_attached.send(sender=None, referral=referral)
        
        assert len(signal_received) == 1
        assert signal_received[0] == referral
        referral_attached.disconnect(handler)

    def test_transaction_created_signal(self, test_user, basic_offer):
        signal_received = []
        def handler(sender, transaction, **kwargs):
            signal_received.append(transaction)
            
        transaction_created.connect(handler)
        
        TransactionService.grant_offer(test_user.id, basic_offer)
        
        assert len(signal_received) == 1
        assert signal_received[0].direction == Transaction.Direction.CREDIT
        transaction_created.disconnect(handler)

@pytest.mark.django_db
class TestBusinessLogicBoundaries:
    def test_insufficient_funds_spanning_batches(self, test_user, qty_product):
        # Prepare 10 tokens in two batches
        QuotaBatch.objects.create(
            user=test_user, product=qty_product, initial_quantity=5, remaining_quantity=5,
            state=QuotaBatch.State.ACTIVE, created_at=timezone.now() - timedelta(days=1)
        )
        QuotaBatch.objects.create(
            user=test_user, product=qty_product, initial_quantity=5, remaining_quantity=5,
            state=QuotaBatch.State.ACTIVE, created_at=timezone.now()
        )
        
        # Try to consume 11 tokens
        res = TransactionService.consume_quota(test_user.id, "tokens", amount=11)
        assert res["success"] is False
        assert res["error"] == "insufficient_funds"
        
        # Balance should still be 10
        assert TransactionService.get_balance(test_user.id, "tokens") == 10

    def test_exhaustion_logic(self, test_user, qty_product):
        QuotaBatch.objects.create(
            user=test_user, product=qty_product, initial_quantity=10, remaining_quantity=10,
            state=QuotaBatch.State.ACTIVE
        )
        
        # Consume exactly all
        res = TransactionService.consume_quota(test_user.id, "tokens", amount=10)
        assert res["success"] is True
        
        batch = QuotaBatch.objects.get(user=test_user, product=qty_product)
        assert batch.remaining_quantity == 0
        assert batch.state == QuotaBatch.State.EXHAUSTED

@pytest.mark.django_db
class TestAuditRefund:
    def test_trace_to_order_item(self, test_user, basic_offer):
        # Create order
        order = Order.objects.create(user=test_user, total_amount=10, currency="USD")
        order_item = OrderItem.objects.create(order=order, offer=basic_offer, quantity=1, price=10)
        
        # Grant
        TransactionService.grant_offer(test_user.id, basic_offer, order_item=order_item)
        
        # Check batch
        batch = QuotaBatch.objects.get(user=test_user, product__product_key="tokens")
        assert batch.order_item == order_item
        assert batch.order_item.order == order
        
        # Check transaction
        tx = Transaction.objects.get(quota_batch=batch, direction=Transaction.Direction.CREDIT)
        assert tx.related_object == order_item
