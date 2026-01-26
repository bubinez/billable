import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from billable.models import Product, UserProduct, Order, OrderItem, TrialHistory
from billable.services import QuotaService, OrderService, UserProductService, ProductService

User = get_user_model()

@pytest.fixture
def test_user(db):
    return User.objects.create(username="testuser", chat_id=123456789)

@pytest.fixture
def quantity_product(db):
    return Product.objects.create(
        sku="TEST_QTY",
        name="Test Quantity Product",
        product_type=Product.ProductType.QUANTITY,
        price=Decimal("100.00"),
        quantity=5,
        metadata={"features": ["test_feature"]}
    )

@pytest.fixture
def trial_product(db):
    return Product.objects.create(
        sku="TEST_TRIAL",
        name="Test Trial Product",
        product_type=Product.ProductType.PERIOD,
        price=Decimal("0.00"),
        period_days=7,
        metadata={"features": ["trial_feature"], "is_trial": True}
    )

@pytest.mark.django_db
class TestQuotaService:
    def test_consume_quota_success(self, test_user, quantity_product):
        # Activate product for user
        up = UserProduct.objects.create(
            user=test_user,
            product=quantity_product,
            total_quantity=5,
            used_quantity=0,
            is_active=True
        )
        
        result = QuotaService.consume_quota(
            user_id=test_user.id,
            feature="test_feature",
            action_type="test_action"
        )
        
        assert result["success"] is True
        up.refresh_from_db()
        assert up.used_quantity == 1
        assert result["remaining"] == 4

    def test_consume_quota_insufficient(self, test_user, quantity_product):
        UserProduct.objects.create(
            user=test_user,
            product=quantity_product,
            total_quantity=1,
            used_quantity=1,
            is_active=True
        )
        
        result = QuotaService.consume_quota(
            user_id=test_user.id,
            feature="test_feature",
            action_type="test_action"
        )
        
        assert result["success"] is False
        assert result["error"] == "quota_exhausted"

    def test_activate_trial_success(self, test_user, trial_product):
        result = QuotaService.activate_trial(
            user_id=test_user.id,
            telegram_id="123456789"
        )
        
        assert result["success"] is True
        # Check if hash was generated
        expected_hash = TrialHistory.generate_identity_hash("123456789")
        assert TrialHistory.objects.filter(identity_type="telegram", identity_hash=expected_hash).exists()
        assert UserProduct.objects.filter(user=test_user, product=trial_product).exists()

    def test_activate_trial_already_used(self, test_user, trial_product):
        h = TrialHistory.generate_identity_hash("123456789")
        TrialHistory.objects.create(identity_type="telegram", identity_hash=h, trial_plan_name="test")
        
        result = QuotaService.activate_trial(
            user_id=test_user.id,
            telegram_id="123456789"
        )
        
        assert result["success"] is False
        assert result["error"] == "trial_already_used"

@pytest.mark.django_db
class TestOrderService:
    def test_create_and_process_order(self, test_user, quantity_product):
        # 1. Order creation
        items = [{"product": quantity_product, "quantity": 1}]
        order = OrderService.create_order(user_id=test_user.id, product_items=items)
        
        assert order.total_amount == quantity_product.price
        assert order.items.count() == 1
        assert order.status == Order.Status.PENDING
        
        # 2. Order payment
        success = OrderService.process_payment(order_id=order.id, payment_id="PAY-123")
        
        assert success is True
        order.refresh_from_db()
        assert order.status == Order.Status.PAID
        assert order.payment_id == "PAY-123"
        
        # 3. Check UserProduct accrual
        assert UserProduct.objects.filter(user=test_user, product=quantity_product).exists()

@pytest.mark.django_db
class TestUserProductService:
    def test_get_balance_summary(self, test_user, quantity_product):
        UserProduct.objects.create(
            user=test_user,
            product=quantity_product,
            total_quantity=10,
            used_quantity=3,
            is_active=True
        )
        
        summary = UserProductService.get_balance_summary(test_user.id)
        assert "test_feature" in summary
        assert summary["test_feature"]["remaining"] == 7
        assert summary["test_feature"]["total"] == 10
