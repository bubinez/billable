import pytest
import uuid
from decimal import Decimal
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from billable.models import Product, Offer, OfferItem, QuotaBatch, Transaction, Order, OrderItem, TrialHistory
from billable.services import TransactionService, BalanceService, OrderService, ProductService

User = get_user_model()

@pytest.fixture
def test_user(db):
    return User.objects.create(username="testuser")

@pytest.fixture
def qty_product(db):
    return Product.objects.create(
        product_key="GEN_AI",
        name="Generative AI Tokens",
        product_type=Product.ProductType.QUANTITY,
    )

@pytest.fixture
def basic_offer(db, qty_product):
    offer = Offer.objects.create(
        sku="off_ai_tokens_100",
        name="100 AI Tokens Pack",
        price=Decimal("10.00"),
        currency="USD",
        is_active=True
    )
    OfferItem.objects.create(
        offer=offer,
        product=qty_product,
        quantity=100
    )
    return offer

@pytest.fixture
def internal_currency_product(db):
    return Product.objects.create(
        product_key="internal",
        name="Internal Credits",
        product_type=Product.ProductType.QUANTITY,
        is_currency=True,
    )

@pytest.fixture
def exchange_offer(db, qty_product):
    """An offer that can be bought with INTERNAL currency."""
    offer = Offer.objects.create(
        sku="off_exchange_tokens_50",
        name="Exchange AI Tokens",
        price=Decimal("50.00"),
        currency="INTERNAL",
        is_active=True
    )
    OfferItem.objects.create(
        offer=offer,
        product=qty_product,
        quantity=50
    )
    return offer

@pytest.mark.django_db(transaction=True)
class TestServicesSyncAsync:
    """
    Test class covering both Sync and Async methods of billable services.
    Ensures core logic is identical and thread-safe across both paradigms.
    """

    # --- TransactionService ---

    def test_transaction_service_sync(self, test_user, basic_offer):
        # 1. Grant
        batches = TransactionService.grant_offer(test_user.id, basic_offer)
        assert len(batches) == 1
        assert TransactionService.get_balance(test_user.id, "GEN_AI") == 100
        
        # 2. Check Quota
        check = TransactionService.check_quota(test_user.id, "GEN_AI")
        assert check["can_use"] is True
        assert check["remaining"] == 100

        # 3. Consume
        res = TransactionService.consume_quota(test_user.id, "GEN_AI", amount=30)
        assert res["success"] is True
        assert res["remaining"] == 70

    @pytest.mark.asyncio
    async def test_transaction_service_async(self, test_user, basic_offer):
        # 1. Grant (Sync only, typically called from Task or Payment Hook)
        await TransactionService.agrant_offer(test_user.id, basic_offer)
        
        # 2. Check Quota (Async)
        check = await TransactionService.acheck_quota(test_user.id, "GEN_AI")
        assert check["can_use"] is True
        assert check["remaining"] == 100

        # 3. Consume (Async)
        res = await TransactionService.aconsume_quota(test_user.id, "GEN_AI", amount=30)
        assert res["success"] is True
        assert res["remaining"] == 70

    # --- ProductService ---

    def test_product_service_sync(self, qty_product, basic_offer):
        products = ProductService.get_active_products()
        assert len(products) == 1
        
        p = ProductService.get_product_by_key("GEN_AI")
        assert p is not None
        assert p.product_key == "GEN_AI"

    @pytest.mark.asyncio
    async def test_product_service_async(self, qty_product, basic_offer):
        products = await ProductService.aget_active_products()
        assert len(products) == 1
        
        p = await ProductService.aget_product_by_key("GEN_AI")
        assert p is not None
        assert p.product_key == "GEN_AI"

    # --- OrderService ---

    def test_order_service_sync(self, test_user, basic_offer):
        # 1. Create
        items = [{"sku": basic_offer.sku, "quantity": 1}]
        order = OrderService.create_order(test_user.id, items)
        assert order.status == Order.Status.PENDING
        
        # 2. Payment
        success = OrderService.process_payment(order.id, payment_id="PAY-SYNC")
        assert success is True
        assert TransactionService.get_balance(test_user.id, "GEN_AI") == 100

    @pytest.mark.asyncio
    async def test_order_service_async(self, test_user, basic_offer):
        # 1. Create
        items = [{"sku": basic_offer.sku, "quantity": 1}]
        order = await OrderService.acreate_order(test_user.id, items)
        assert order.status == Order.Status.PENDING
        
        # 2. Payment
        success = await OrderService.aprocess_payment(order.id, payment_id="PAY-ASYNC")
        assert success is True
        
        balance = await TransactionService.aget_balance(test_user.id, "GEN_AI")
        assert balance == 100
        
        # 3. Serialization
        data = await OrderService.aserialize_order_to_dict(order)
        assert data["id"] == order.id
        assert len(data["items"]) == 1
        assert data["items"][0]["sku"] == basic_offer.sku

    def test_order_create_raises_on_unknown_sku(self, test_user):
        """create_order raises ValueError when offer for sku is not found."""
        items = [{"sku": "nonexistent_sku_xyz", "quantity": 1}]
        with pytest.raises(ValueError, match=r"Offer not found for sku: 'nonexistent_sku_xyz'"):
            OrderService.create_order(test_user.id, items)

    @pytest.mark.asyncio
    async def test_order_acreate_raises_on_unknown_sku(self, test_user):
        """acreate_order raises ValueError when offer for sku is not found."""
        items = [{"sku": "nonexistent_sku_xyz", "quantity": 1}]
        with pytest.raises(ValueError, match=r"Offer not found for sku: 'nonexistent_sku_xyz'"):
            await OrderService.acreate_order(test_user.id, items)

    def test_order_create_raises_on_missing_sku(self, test_user):
        """create_order raises ValueError when item has no 'sku'."""
        items = [{"quantity": 1}]
        with pytest.raises(ValueError, match=r"Item must have 'sku'"):
            OrderService.create_order(test_user.id, items)

    # --- BalanceService ---

    def test_balance_service_sync(self, test_user, qty_product):
        # Setup manual batch
        QuotaBatch.objects.create(
            user=test_user, product=qty_product, initial_quantity=50, remaining_quantity=40,
            state=QuotaBatch.State.ACTIVE
        )
        
        # 1. List products
        items = BalanceService.get_user_active_products(test_user.id)
        assert len(items) == 1
        assert items[0].remaining_quantity == 40
        
        # 2. Summary (by product_key)
        summary = BalanceService.get_balance_summary(test_user.id)
        assert summary["GEN_AI"]["remaining"] == 40

    @pytest.mark.asyncio
    async def test_balance_service_async(self, test_user, qty_product):
        # Setup manual batch
        from asgiref.sync import sync_to_async
        await sync_to_async(QuotaBatch.objects.create)(
            user=test_user, product=qty_product, initial_quantity=50, remaining_quantity=40,
            state=QuotaBatch.State.ACTIVE
        )
        
        # 1. List products
        items = await BalanceService.aget_user_active_products(test_user.id)
        assert len(items) == 1
        assert items[0].remaining_quantity == 40

    # --- Cross-Paradigm Integrity ---

    @pytest.mark.asyncio
    async def test_sync_consumption_reflects_in_async_balance(self, test_user, qty_product):
        """Standard scenario: Web (Async) checks balance after Backend Worker (Sync) granted it."""
        # 1. Setup (Simulate worker)
        from asgiref.sync import sync_to_async
        await sync_to_async(QuotaBatch.objects.create)(
            user=test_user, product=qty_product, initial_quantity=100, remaining_quantity=100,
            state=QuotaBatch.State.ACTIVE
        )
        
        # 2. Consume via sync (Simulate another process/worker)
        await sync_to_async(TransactionService.consume_quota)(test_user.id, "GEN_AI", amount=10)

        # 3. Check via async (Simulate web API)
        res = await TransactionService.acheck_quota(test_user.id, "GEN_AI")
        assert res["remaining"] == 90
