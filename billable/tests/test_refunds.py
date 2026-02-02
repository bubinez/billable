import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from billable.models import Product, Offer, OfferItem, QuotaBatch, Transaction, Order, OrderItem
from billable.services import TransactionService, OrderService, BalanceService
from ninja.testing import TestAsyncClient
from billable.api import router

User = get_user_model()

@pytest.fixture
def test_user(db):
    return User.objects.create(username="refunduser")

@pytest.fixture
def qty_product(db):
    return Product.objects.create(
        product_key="DIAMONDS",
        name="Diamonds",
        product_type=Product.ProductType.QUANTITY,
    )

@pytest.fixture
def time_product(db):
    return Product.objects.create(
        product_key="VIP",
        name="VIP Access",
        product_type=Product.ProductType.PERIOD,
    )

@pytest.fixture
def qty_offer(db, qty_product):
    offer = Offer.objects.create(
        sku="off_diamonds_100",
        name="100 Diamonds Pack",
        price=Decimal("1000.00"),
        currency="RUB",
        is_active=True
    )
    OfferItem.objects.create(
        offer=offer,
        product=qty_product,
        quantity=100
    )
    return offer

@pytest.fixture
def time_offer(db, time_product):
    offer = Offer.objects.create(
        sku="off_vip_30d",
        name="30 Days VIP",
        price=Decimal("500.00"),
        currency="RUB",
        is_active=True
    )
    OfferItem.objects.create(
        offer=offer,
        product=time_product,
        quantity=1,
        period_unit=OfferItem.PeriodUnit.DAYS,
        period_value=30
    )
    return offer

@pytest.fixture
def api_client():
    from django.conf import settings
    return TestAsyncClient(router, headers={"Authorization": f"Bearer {settings.BILLABLE_API_TOKEN}"})

@pytest.mark.django_db(transaction=True)
class TestRefunds:
    """
    Tests for refund functionality covering quantity, time, and money scenarios.
    """

    def test_refund_quantity_product(self, test_user, qty_offer):
        # 1. Create and pay for order
        items = [{"sku": qty_offer.sku, "quantity": 1}]
        order = OrderService.create_order(test_user.id, items)
        OrderService.process_payment(order.id, payment_id="PAY-QTY")
        
        assert TransactionService.get_balance(test_user.id, "DIAMONDS") == 100
        
        # 2. Consume some quota
        TransactionService.consume_quota(test_user.id, "DIAMONDS", amount=20)
        assert TransactionService.get_balance(test_user.id, "DIAMONDS") == 80
        
        # 3. Process refund
        success = OrderService.refund_order(order.id, reason="Customer changed mind")
        assert success is True
        
        # 4. Verify results
        order.refresh_from_db()
        assert order.status == Order.Status.REFUNDED
        assert order.metadata.get("refund_reason") == "Customer changed mind"
        
        # Balance should be 0
        assert TransactionService.get_balance(test_user.id, "DIAMONDS") == 0
        
        # Batch should be REVOKED
        batch = QuotaBatch.objects.get(order_item__order=order)
        assert batch.state == QuotaBatch.State.REVOKED
        assert batch.remaining_quantity == 0
        
        # Should have a DEBIT transaction for the remaining 80
        refund_tx = Transaction.objects.filter(
            quota_batch=batch, 
            direction=Transaction.Direction.DEBIT,
            action_type="refund"
        ).first()
        assert refund_tx is not None
        assert refund_tx.amount == 80

    def test_refund_time_product(self, test_user, time_offer):
        # 1. Create and pay for order
        items = [{"sku": time_offer.sku, "quantity": 1}]
        order = OrderService.create_order(test_user.id, items)
        OrderService.process_payment(order.id, payment_id="PAY-TIME")
        
        assert TransactionService.get_balance(test_user.id, "VIP") == 1
        
        # 2. Process refund
        success = OrderService.refund_order(order.id)
        assert success is True
        
        # 3. Verify results
        assert TransactionService.get_balance(test_user.id, "VIP") == 0
        batch = QuotaBatch.objects.get(order_item__order=order)
        assert batch.state == QuotaBatch.State.REVOKED
        
        # Should have a DEBIT transaction for 1 unit
        refund_tx = Transaction.objects.filter(
            quota_batch=batch, 
            direction=Transaction.Direction.DEBIT,
            action_type="refund"
        ).first()
        assert refund_tx.amount == 1

    @pytest.mark.asyncio
    async def test_refund_api_flow(self, api_client, test_user, qty_offer):
        # 1. Create order via API
        payload = {
            "user_id": test_user.id,
            "items": [{"sku": qty_offer.sku, "quantity": 1}]
        }
        res_o = await api_client.post("/orders", json=payload)
        order_id = res_o.json()["id"]
        
        # 2. Confirm payment
        await api_client.post(f"/orders/{order_id}/confirm", json={"payment_id": "API-PAY"})
        
        # 3. Call refund endpoint
        res_r = await api_client.post(f"/orders/{order_id}/refund", json={"reason": "API Refund Test"})
        assert res_r.status_code == 200
        assert res_r.json()["success"] is True
        
        # 4. Verify balance
        res_w = await api_client.get(f"/wallet?user_id={test_user.id}")
        assert res_w.json()["balances"].get("DIAMONDS", 0) == 0
        
        # 5. Verify order status
        res_get = await api_client.get(f"/orders/{order_id}")
        assert res_get.json()["status"] == "refunded"

    def test_refund_fails_for_non_paid_order(self, test_user, qty_offer):
        # Create order but don't pay
        items = [{"sku": qty_offer.sku, "quantity": 1}]
        order = OrderService.create_order(test_user.id, items)
        
        # Refund should fail
        success = OrderService.refund_order(order.id)
        assert success is False
        
        order.refresh_from_db()
        assert order.status == Order.Status.PENDING

    def test_refund_money_multiple_items(self, test_user, qty_offer, time_offer):
        # Test refund for an order with multiple items (quantity and time)
        # simulating a "money" purchase of a bundle
        items = [
            {"sku": qty_offer.sku, "quantity": 2}, # 200 diamonds
            {"sku": time_offer.sku, "quantity": 1}, # 30 days VIP
        ]
        order = OrderService.create_order(test_user.id, items)
        OrderService.process_payment(order.id, payment_id="PAY-BUNDLE")
        
        assert TransactionService.get_balance(test_user.id, "DIAMONDS") == 200
        assert TransactionService.get_balance(test_user.id, "VIP") == 1
        
        # Process refund
        success = OrderService.refund_order(order.id)
        assert success is True
        
        # Verify both are revoked
        assert TransactionService.get_balance(test_user.id, "DIAMONDS") == 0
        assert TransactionService.get_balance(test_user.id, "VIP") == 0
        
        # Check transactions
        assert Transaction.objects.filter(action_type="refund").count() == 2
