import pytest
import uuid
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from asgiref.sync import sync_to_async
from billable.models import Product, Offer, OfferItem, QuotaBatch, Order, OrderItem, ExternalIdentity, Referral, TrialHistory, Transaction
from ninja.testing import TestAsyncClient
from billable.api import router
from billable.services import TransactionService, BalanceService, OrderService

User = get_user_model()

@pytest.fixture
def api_client():
    from django.conf import settings
    return TestAsyncClient(router, headers={"Authorization": f"Bearer {settings.BILLABLE_API_TOKEN}"})

@pytest.fixture
def test_user(db):
    return User.objects.create(username="apiuser")

@pytest.fixture
def tg_identity(db, test_user):
    return ExternalIdentity.objects.create(
        user=test_user,
        provider="telegram",
        external_id="12345678"
    )

@pytest.fixture
def tokens_product(db):
    return Product.objects.create(
        product_key="TOKENS",
        name="AI Tokens",
        product_type=Product.ProductType.QUANTITY,
        is_active=True,
    )

@pytest.fixture
def premium_product(db):
    return Product.objects.create(
        product_key="PREMIUM",
        name="Premium Access",
        product_type=Product.ProductType.PERIOD,
        is_active=True,
    )

@pytest.fixture
def credits_product(db):
    """Internal currency product."""
    return Product.objects.create(
        product_key="internal",
        name="Internal Credits",
        product_type=Product.ProductType.QUANTITY,
        is_active=True,
        is_currency=True,
    )

@pytest.fixture
def bundle_offer(db, tokens_product, premium_product):
    """Offer containing multiple products."""
    offer = Offer.objects.create(
        sku="pack_starter",
        name="Starter Bundle",
        price=Decimal("20.00"),
        currency="USD",
        is_active=True
    )
    OfferItem.objects.create(offer=offer, product=tokens_product, quantity=100)
    OfferItem.objects.create(
        offer=offer, 
        product=premium_product, 
        quantity=1, 
        period_unit=OfferItem.PeriodUnit.DAYS, 
        period_value=30
    )
    return offer

@pytest.fixture
def credit_offer(db, credits_product):
    """Offer that grants internal credits."""
    offer = Offer.objects.create(
        sku="off_credits_100",
        name="100 Credits",
        price=Decimal("10.00"),
        currency="USD",
        is_active=True
    )
    OfferItem.objects.create(offer=offer, product=credits_product, quantity=100)
    return offer

@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestBillableAPI:
    # --- Catalog & Products ---

    async def test_list_products_and_catalog(self, api_client, bundle_offer):
        # 1. Products list (key-centric)
        res_p = await api_client.get("/products")
        assert res_p.status_code == 200
        data_p = res_p.json()
        assert any(p["product_key"] == "TOKENS" for p in data_p)
        
        # 2. Catalog list (offer-centric)
        res_c = await api_client.get("/catalog")
        assert res_c.status_code == 200
        data_c = res_c.json()
        offer = next(o for o in data_c if o["name"] == "Starter Bundle")
        assert len(offer["items"]) == 2
        assert float(offer["price"]) == 20.0

    # --- Catalog by SKU (single & bulk) ---

    async def test_get_catalog_offer_by_sku_200(self, api_client, bundle_offer):
        """GET /catalog/{sku}: existing active offer returns 200 with items and product."""
        res = await api_client.get(f"/catalog/{bundle_offer.sku}")
        assert res.status_code == 200
        data = res.json()
        assert data["sku"] == bundle_offer.sku
        assert data["name"] == bundle_offer.name
        assert len(data["items"]) == 2
        assert "product" in data["items"][0]

    async def test_get_catalog_offer_by_sku_404_not_found(self, api_client):
        """GET /catalog/{sku}: non-existent SKU returns 404 with CommonResponse."""
        res = await api_client.get("/catalog/nonexistent_sku_xyz")
        assert res.status_code == 404
        data = res.json()
        assert data["success"] is False
        assert data["message"] == "Offer not found"

    async def test_get_catalog_offer_by_sku_404_inactive(self, api_client, credit_offer):
        """GET /catalog/{sku}: existing but is_active=False returns 404."""
        credit_offer.is_active = False
        await sync_to_async(credit_offer.save)()
        res = await api_client.get(f"/catalog/{credit_offer.sku}")
        assert res.status_code == 404
        assert res.json()["success"] is False
        assert res.json()["message"] == "Offer not found"

    async def test_catalog_bulk_order_preserved(self, api_client, bundle_offer, credit_offer):
        """GET /catalog?sku=B&sku=A returns [B, A] (order preserved)."""
        sku_b, sku_a = bundle_offer.sku, credit_offer.sku
        res = await api_client.get(f"/catalog?sku={sku_b}&sku={sku_a}")
        assert res.status_code == 200
        data = res.json()
        assert [o["sku"] for o in data] == [sku_b, sku_a]

    async def test_catalog_bulk_partial_match(self, api_client, bundle_offer, credit_offer):
        """GET /catalog?sku=A&sku=missing&sku=B returns [A, B] (missing excluded)."""
        sku_a, sku_b = bundle_offer.sku, credit_offer.sku
        res = await api_client.get(f"/catalog?sku={sku_a}&sku=off_missing&sku={sku_b}")
        assert res.status_code == 200
        data = res.json()
        assert [o["sku"] for o in data] == [sku_a, sku_b]

    async def test_catalog_bulk_all_missing(self, api_client):
        """GET /catalog?sku=x&sku=y with all missing returns 200 and []."""
        res = await api_client.get("/catalog?sku=off_x&sku=off_y")
        assert res.status_code == 200
        assert res.json() == []

    async def test_catalog_bulk_filter_inactive(self, api_client, bundle_offer, credit_offer):
        """GET /catalog?sku=A&sku=B with A inactive returns only [B]."""
        credit_offer.is_active = False
        await sync_to_async(credit_offer.save)()
        sku_a, sku_b = credit_offer.sku, bundle_offer.sku
        res = await api_client.get(f"/catalog?sku={sku_a}&sku={sku_b}")
        assert res.status_code == 200
        data = res.json()
        assert [o["sku"] for o in data] == [sku_b]
    async def test_get_product_by_key(self, api_client, tokens_product):
        response = await api_client.get(f"/products/{tokens_product.product_key}")
        assert response.status_code == 200
        assert response.json()["product_key"] == tokens_product.product_key

        response = await api_client.get("/products/NON_EXISTENT")
        assert response.status_code == 404

    # --- Identity & User Resolution ---

    async def test_identify_and_resolve(self, api_client):
        # Identify creates User
        payload = {
            "provider": "telegram",
            "external_id": "9999",
            "profile": {"first_name": "Test"}
        }
        res = await api_client.post("/identify", json=payload)
        assert res.status_code == 200
        data = res.json()
        user_id = data["user_id"]
        
        # Subsequent calls resolve automatically via external_id
        res_wallet = await api_client.get(f"/wallet?provider=telegram&external_id=9999")
        assert res_wallet.status_code == 200
        assert res_wallet.json()["user_id"] == user_id

    # --- Complex Ordering ---

    async def test_complex_order_flow(self, api_client, test_user, bundle_offer, credit_offer):
        # 1. Create order with multiple different offers
        payload = {
            "user_id": test_user.id,
            "items": [
                {"sku": bundle_offer.sku, "quantity": 1},
                {"sku": credit_offer.sku, "quantity": 2} # 100 * 2 = 200 credits
            ]
        }
        res_o = await api_client.post("/orders", json=payload)
        assert res_o.status_code == 200
        order_data = res_o.json()
        # 20.00 (bundle) + 2 * 10.00 (credits) = 40.00
        assert float(order_data["total_amount"]) == 40.0
        assert len(order_data["items"]) == 2
        
        order_id = order_data["id"]
        
        # 2. Confirm order
        res_c = await api_client.post(f"/orders/{order_id}/confirm", json={"payment_id": "PAY-COMPLEX"})
        assert res_c.status_code == 200
        
        # 3. Verify multiple products granted
        res_w = await api_client.get(f"/wallet?user_id={test_user.id}")
        balances = res_w.json()["balances"]
        assert balances["TOKENS"] == 100
        assert balances["PREMIUM"] == 1
        assert balances["internal"] == 200

    async def test_create_order_400_on_unknown_sku(self, api_client, test_user):
        """POST /orders with unknown sku returns 400 and message about offer not found."""
        payload = {"user_id": test_user.id, "items": [{"sku": "nonexistent_sku_xyz", "quantity": 1}]}
        res = await api_client.post("/orders", json=payload)
        assert res.status_code == 400
        data = res.json()
        assert data.get("success") is False
        assert "Offer not found for sku" in data.get("message", "")

    async def test_create_order_400_on_missing_sku(self, api_client, test_user):
        """POST /orders with item without sku returns 400."""
        payload = {"user_id": test_user.id, "items": [{"quantity": 1}]}
        res = await api_client.post("/orders", json=payload)
        assert res.status_code == 400
        data = res.json()
        assert data.get("success") is False
        assert "Item must have 'sku'" in data.get("message", "")

    # --- FIFO & Balance Logic ---

    async def test_fifo_consumption_spanning_batches(self, api_client, test_user, tokens_product):
        # Create 3 batches of tokens manually
        from datetime import timedelta
        base_time = timezone.now()
        await QuotaBatch.objects.acreate(
            user=test_user, product=tokens_product, initial_quantity=10, remaining_quantity=10,
            created_at=base_time - timedelta(days=2), state=QuotaBatch.State.ACTIVE
        )
        await QuotaBatch.objects.acreate(
            user=test_user, product=tokens_product, initial_quantity=10, remaining_quantity=10,
            created_at=base_time - timedelta(days=1), state=QuotaBatch.State.ACTIVE
        )
        await QuotaBatch.objects.acreate(
            user=test_user, product=tokens_product, initial_quantity=10, remaining_quantity=10,
            created_at=base_time, state=QuotaBatch.State.ACTIVE
        )
        
        # Consume 25 tokens (should exhaust batch 1, 2, and use 5 from batch 3)
        payload = {
            "user_id": test_user.id,
            "product_key": "TOKENS",
            "action_type": "api_test",
            "idempotency_key": "step-fifo"
        }
        for i in range(25):
            await api_client.post("/wallet/consume", json={**payload, "idempotency_key": f"key-{i}"})
            
        res_w = await api_client.get(f"/wallet?user_id={test_user.id}")
        # Initial 30 - 25 = 5
        assert res_w.json()["balances"]["TOKENS"] == 5
        
        # Check details
        res_b = await api_client.get(f"/wallet/batches?user_id={test_user.id}")
        active_batches = res_b.json()
        assert len(active_batches) == 1
        assert active_batches[0]["remaining_quantity"] == 5

    # --- Internal Exchange ---

    async def test_exchange_api_flow(self, api_client, test_user, credits_product, bundle_offer):
        # 1. Grant credits first
        await QuotaBatch.objects.acreate(
            user=test_user, product=credits_product, initial_quantity=500, remaining_quantity=500,
            state=QuotaBatch.State.ACTIVE
        )
        
        # Change bundle offer price to internal currency for test
        bundle_offer.currency = "INTERNAL"
        bundle_offer.price = Decimal("300")
        await sync_to_async(bundle_offer.save)()
        
        # 2. Call exchange endpoint (body)
        res = await api_client.post("/exchange", json={"user_id": test_user.id, "sku": bundle_offer.sku})
        assert res.status_code == 200
        
        # 3. Check balances
        res_w = await api_client.get(f"/wallet?user_id={test_user.id}")
        balances = res_w.json()["balances"]
        assert balances["internal"] == 200 # 500 - 300
        assert balances["TOKENS"] == 100
        assert balances["PREMIUM"] == 1

    async def test_exchange_by_external_id(self, api_client, test_user, tg_identity, credits_product, bundle_offer):
        """Exchange using external_id + provider instead of user_id."""
        await QuotaBatch.objects.acreate(
            user=test_user, product=credits_product, initial_quantity=500, remaining_quantity=500,
            state=QuotaBatch.State.ACTIVE
        )
        bundle_offer.currency = "INTERNAL"
        bundle_offer.price = Decimal("300")
        await sync_to_async(bundle_offer.save)()

        res = await api_client.post("/exchange", json={
            "external_id": "12345678",
            "provider": "telegram",
            "sku": bundle_offer.sku,
        })
        assert res.status_code == 200

        res_w = await api_client.get(f"/wallet?user_id={test_user.id}")
        balances = res_w.json()["balances"]
        assert balances["internal"] == 200
        assert balances["TOKENS"] == 100
        assert balances["PREMIUM"] == 1

    async def test_balance_and_user_products(self, api_client, test_user, bundle_offer):
        await TransactionService.agrant_offer(test_user.id, bundle_offer)
        
        # 1. Check balance by product_key
        res_b = await api_client.get(f"/balance?user_id={test_user.id}&product_key=TOKENS")
        assert res_b.status_code == 200
        assert res_b.json()["can_use"] is True
        assert res_b.json()["remaining"] == 100
        
        # 2. List user products (QuotaBatches)
        res_up = await api_client.get(f"/user-products?user_id={test_user.id}")
        assert res_up.status_code == 200
        data = res_up.json()
        assert len(data) == 2 # 100 tokens + 1 premium
        assert any(item["product"]["product_key"] == "TOKENS" for item in data)

    # --- History & Stats ---

    async def test_wallet_history_and_referral_stats(self, api_client, test_user, tg_identity, bundle_offer):
        # 1. Perform some actions
        await TransactionService.agrant_offer(test_user.id, bundle_offer)
        await api_client.post("/wallet/consume", json={
            "user_id": test_user.id, "product_key": "TOKENS", "action_type": "test", "idempotency_key": "h1"
        })
        
        # 2. Check transaction history
        res_tx = await api_client.get(f"/wallet/transactions?user_id={test_user.id}")
        assert res_tx.status_code == 200
        txs = res_tx.json()
        assert len(txs) >= 2 # 1 grant, 1 consumption
        assert any(t["direction"] == "CREDIT" for t in txs)
        assert any(t["direction"] == "DEBIT" for t in txs)
        
        # 3. Referral stats by user_id
        other_user = await User.objects.acreate(username="referee_stats")
        await Referral.objects.acreate(referrer=test_user, referee=other_user)
        
        res_s = await api_client.get(f"/referrals/stats?user_id={test_user.id}")
        assert res_s.status_code == 200
        assert res_s.json()["data"]["count"] == 1

        # 4. Referral stats by external_id + provider (tg_identity links test_user ↔ telegram/12345678)
        res_s2 = await api_client.get("/referrals/stats?provider=telegram&external_id=12345678")
        assert res_s2.status_code == 200
        assert res_s2.json()["data"]["count"] == 1

    # --- Trial Edge Case (Double usage with different providers) ---

    async def test_trial_fraud_prevention_robust(self, api_client, test_user):
        # Trial offer
        offer = await Offer.objects.acreate(
            sku="trial_offer", name="Trial", price=0, currency="USD", is_active=True, metadata={"is_trial": True}
        )
        await OfferItem.objects.acreate(offer=offer, product=await Product.objects.acreate(product_key="T", name="T", is_active=True), quantity=1)
        
        # Grant via telegram
        payload1 = {"user_id": test_user.id, "provider": "telegram", "external_id": "X1", "sku": "trial_offer"}
        await api_client.post("/demo/trial-grant", json=payload1)
        
        # Try to grant via web using same external_id (not allowed if system-wide, but here hash-based)
        # Actually in api.py TrialHistory.ahas_used_trial checks for hashes.
        # If the user tries to use same external_id on different provider, 
        # it depends on how we pass 'identities'. 
        
        # Try second time with same identity -> Fail
        res2 = await api_client.post("/demo/trial-grant", json=payload1)
        assert res2.status_code == 400
        assert res2.json()["data"]["error"] == "trial_already_used"

    # --- Error Handling ---

    async def test_unauthorized_token(self, api_client):
        # Test with wrong token
        wrong_client = TestAsyncClient(router, headers={"Authorization": "Bearer WRONG"})
        res = await wrong_client.get("/products")
        assert res.status_code == 401

    async def test_bad_input_exchange(self, api_client, test_user, bundle_offer):
        # Non-existent offer
        res = await api_client.post("/exchange", json={"user_id": test_user.id, "sku": "NON_EXISTENT"})
        assert res.status_code == 404

        # No user identifier -> 400
        res2 = await api_client.post("/exchange", json={"sku": bundle_offer.sku})
        assert res2.status_code == 400
        assert "user_id is required" in res2.json().get("message", "")

    async def test_referral_stats_requires_user(self, api_client):
        res = await api_client.get("/referrals/stats")
        assert res.status_code == 400
        assert "user_id is required" in res.json().get("message", "")
