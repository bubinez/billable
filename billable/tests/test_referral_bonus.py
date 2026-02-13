"""Tests for referral bonus functionality.

Covers:
- Referral model and claim_bonus() method
- API endpoints for referral assignment
- Signal handling
- Edge cases and validation
"""

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from asgiref.sync import sync_to_async
from billable.models import Referral, ExternalIdentity
from billable.signals import referral_attached
from ninja.testing import TestAsyncClient
from billable.api import router
from django.conf import settings

User = get_user_model()


@pytest.fixture
def api_client():
    """API client with authentication token."""
    return TestAsyncClient(router, headers={"Authorization": f"Bearer {settings.BILLABLE_API_TOKEN}"})


@pytest.fixture
def referrer_user(db):
    """User who invites others."""
    return User.objects.create(username="referrer")


@pytest.fixture
def referee_user(db):
    """User who was invited."""
    return User.objects.create(username="referee")


@pytest.fixture
def referrer_identity(db, referrer_user):
    """External identity for referrer."""
    return ExternalIdentity.objects.create(
        user=referrer_user,
        provider="telegram",
        external_id="ref123"
    )


@pytest.fixture
def referee_identity(db, referee_user):
    """External identity for referee."""
    return ExternalIdentity.objects.create(
        user=referee_user,
        provider="telegram",
        external_id="ref456"
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestReferralBonusModel:
    """Tests for Referral model and claim_bonus() method."""

    async def test_claim_bonus_success(self, referrer_user, referee_user):
        """Test successful bonus claim."""
        referral = await Referral.objects.acreate(
            referrer=referrer_user,
            referee=referee_user
        )
        
        assert referral.bonus_granted is False
        assert referral.bonus_granted_at is None
        
        # Claim bonus
        result = await sync_to_async(referral.claim_bonus)()
        
        assert result is True
        await referral.arefresh_from_db()
        assert referral.bonus_granted is True
        assert referral.bonus_granted_at is not None

    async def test_claim_bonus_idempotent(self, referrer_user, referee_user):
        """Test that claim_bonus() can only be called once."""
        referral = await Referral.objects.acreate(
            referrer=referrer_user,
            referee=referee_user
        )
        
        # First claim should succeed
        result1 = await sync_to_async(referral.claim_bonus)()
        assert result1 is True
        
        # Second claim should fail
        result2 = await sync_to_async(referral.claim_bonus)()
        assert result2 is False
        
        # Status should remain True
        await referral.arefresh_from_db()
        assert referral.bonus_granted is True

    async def test_claim_bonus_atomic(self, referrer_user, referee_user):
        """Test atomicity of claim_bonus() - prevents race conditions."""
        referral = await Referral.objects.acreate(
            referrer=referrer_user,
            referee=referee_user
        )
        
        # Simulate concurrent claims by updating DB directly
        # First, mark as granted in DB
        await Referral.objects.filter(pk=referral.pk).aupdate(
            bonus_granted=True,
            bonus_granted_at=timezone.now()
        )
        
        # Now try to claim - should fail because already granted
        result = await sync_to_async(referral.claim_bonus)()
        assert result is False
        
        # Verify only one update happened
        count = await Referral.objects.filter(
            pk=referral.pk,
            bonus_granted=True
        ).acount()
        assert count == 1

    async def test_referral_str_representation(self, referrer_user, referee_user):
        """Test string representation of Referral model."""
        referral = await Referral.objects.acreate(
            referrer=referrer_user,
            referee=referee_user
        )
        
        str_repr = str(referral)
        assert str(referrer_user.id) in str_repr
        assert str(referee_user.id) in str_repr
        assert "✗" in str_repr  # Bonus not granted yet
        
        # After claiming
        await sync_to_async(referral.claim_bonus)()
        await referral.arefresh_from_db()
        str_repr_claimed = str(referral)
        assert "✓" in str_repr_claimed  # Bonus granted

    async def test_referral_metadata(self, referrer_user, referee_user):
        """Test that metadata is stored correctly."""
        metadata = {"source": "telegram_bot", "campaign": "winter2024"}
        referral = await Referral.objects.acreate(
            referrer=referrer_user,
            referee=referee_user,
            metadata=metadata
        )
        
        await referral.arefresh_from_db()
        assert referral.metadata == metadata


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestReferralAPI:
    """Tests for referral API endpoints."""

    async def test_create_referral_by_user_id(self, api_client, referrer_user, referee_user):
        """Test creating referral link using user IDs."""
        payload = {
            "referrer_id": referrer_user.id,
            "referee_id": referee_user.id
        }
        
        res = await api_client.post("/referrals", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"]["created"] is True
        
        # Verify referral was created
        referral = await Referral.objects.filter(
            referrer=referrer_user,
            referee=referee_user
        ).afirst()
        assert referral is not None
        assert referral.bonus_granted is False

    async def test_create_referral_by_external_id(self, api_client, referrer_identity, referee_identity):
        """Test creating referral link using external IDs."""
        payload = {
            "provider": "telegram",
            "referrer_external_id": "ref123",
            "referee_external_id": "ref456"
        }
        
        res = await api_client.post("/referrals", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"]["created"] is True
        
        # Verify referral was created
        referral = await Referral.objects.filter(
            referrer=referrer_identity.user,
            referee=referee_identity.user
        ).afirst()
        assert referral is not None

    async def test_create_referral_with_metadata(self, api_client, referrer_user, referee_user):
        """Test creating referral with metadata; response includes referral_id and metadata."""
        metadata = {"source": "web_app", "campaign": "summer2024"}
        payload = {
            "referrer_id": referrer_user.id,
            "referee_id": referee_user.id,
            "metadata": metadata,
        }

        res = await api_client.post("/referrals", json=payload)
        assert res.status_code == 200
        res_data = res.json()
        assert res_data.get("success") is True
        assert "data" in res_data
        assert "referral_id" in res_data["data"]
        assert res_data["data"]["metadata"] == metadata

        referral = await Referral.objects.filter(
            referrer=referrer_user,
            referee=referee_user
        ).afirst()
        assert referral is not None
        assert referral.metadata == metadata
        assert referral.id == res_data["data"]["referral_id"]

    async def test_create_referral_duplicate(self, api_client, referrer_user, referee_user):
        """Test that duplicate referral cannot be created."""
        # Create first referral
        payload = {
            "referrer_id": referrer_user.id,
            "referee_id": referee_user.id
        }
        res1 = await api_client.post("/referrals", json=payload)
        assert res1.status_code == 200
        assert res1.json()["data"]["created"] is True
        
        # Try to create duplicate
        res2 = await api_client.post("/referrals", json=payload)
        assert res2.status_code == 200
        assert res2.json()["data"]["created"] is False  # Not created, already exists
        
        # Verify only one referral exists
        count = await Referral.objects.filter(
            referrer=referrer_user,
            referee=referee_user
        ).acount()
        assert count == 1

    async def test_create_referral_self_referral_forbidden(self, api_client, referrer_user):
        """Test that self-referral is not allowed."""
        payload = {
            "referrer_id": referrer_user.id,
            "referee_id": referrer_user.id  # Same user
        }
        
        res = await api_client.post("/referrals", json=payload)
        assert res.status_code == 400
        data = res.json()
        assert data["success"] is False
        assert "cannot be same" in data["message"].lower()

    async def test_create_referral_invalid_identifiers(self, api_client):
        """Test that invalid identifier combinations return 400."""
        # Missing both user_id and external_id
        payload1 = {}
        res1 = await api_client.post("/referrals", json=payload1)
        assert res1.status_code == 400
        assert "valid identifiers" in res1.json()["message"].lower()
        
        # Only referrer_id without referee_id
        payload2 = {"referrer_id": 1}
        res2 = await api_client.post("/referrals", json=payload2)
        assert res2.status_code == 400
        
        # Only external_id without provider
        payload3 = {"referrer_external_id": "123"}
        res3 = await api_client.post("/referrals", json=payload3)
        assert res3.status_code == 400

    async def test_referral_attached_signal_on_create(self, api_client, referrer_user, referee_user):
        """Test that referral_attached signal is sent when new referral is created."""
        signal_received = []
        
        def handler(sender, referral, **kwargs):
            signal_received.append(referral)
        
        referral_attached.connect(handler)
        
        try:
            payload = {
                "referrer_id": referrer_user.id,
                "referee_id": referee_user.id
            }
            res = await api_client.post("/referrals", json=payload)
            assert res.status_code == 200
            
            # Signal should be sent for new referral
            assert len(signal_received) == 1
            assert signal_received[0].referrer_id == referrer_user.id
            assert signal_received[0].referee_id == referee_user.id
            
            # Second call (duplicate) should not trigger signal
            await api_client.post("/referrals", json=payload)
            assert len(signal_received) == 1  # Still only one
        finally:
            referral_attached.disconnect(handler)

    async def test_referral_stats_count(self, api_client, referrer_user, referee_user):
        """Test referral stats endpoint returns correct count."""
        # Create multiple referrals
        other_user1 = await User.objects.acreate(username="referee1")
        other_user2 = await User.objects.acreate(username="referee2")
        
        await Referral.objects.acreate(referrer=referrer_user, referee=referee_user)
        await Referral.objects.acreate(referrer=referrer_user, referee=other_user1)
        await Referral.objects.acreate(referrer=referrer_user, referee=other_user2)
        
        res = await api_client.get(f"/referrals/stats?user_id={referrer_user.id}")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"]["count"] == 3

    async def test_referral_stats_by_external_id(self, api_client, referrer_identity):
        """Test referral stats using external_id + provider."""
        referee_user = await User.objects.acreate(username="referee_stats")
        await Referral.objects.acreate(
            referrer=referrer_identity.user,
            referee=referee_user
        )
        
        res = await api_client.get(
            f"/referrals/stats?provider=telegram&external_id={referrer_identity.external_id}"
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["data"]["count"] == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestReferralBonusIntegration:
    """Integration tests for referral bonus flow."""

    async def test_full_referral_bonus_flow(self, api_client, referrer_user, referee_user):
        """Test complete flow: create referral -> claim bonus -> verify status."""
        # 1. Create referral via API
        payload = {
            "referrer_id": referrer_user.id,
            "referee_id": referee_user.id,
            "metadata": {"source": "test"}
        }
        res = await api_client.post("/referrals", json=payload)
        assert res.status_code == 200
        
        # 2. Get referral from DB
        referral = await Referral.objects.filter(
            referrer=referrer_user,
            referee=referee_user
        ).afirst()
        assert referral is not None
        assert referral.bonus_granted is False
        
        # 3. Claim bonus
        result = await sync_to_async(referral.claim_bonus)()
        assert result is True
        
        # 4. Verify bonus status
        await referral.arefresh_from_db()
        assert referral.bonus_granted is True
        assert referral.bonus_granted_at is not None
        
        # 5. Try to claim again (should fail)
        result2 = await sync_to_async(referral.claim_bonus)()
        assert result2 is False

    async def test_referral_created_at_timestamp(self, referrer_user, referee_user):
        """Test that created_at is automatically set."""
        before = timezone.now()
        referral = await Referral.objects.acreate(
            referrer=referrer_user,
            referee=referee_user
        )
        after = timezone.now()
        
        assert referral.created_at is not None
        assert before <= referral.created_at <= after

    async def test_referral_unique_constraint(self, referrer_user, referee_user):
        """Test that unique_together constraint prevents duplicates."""
        # Create first referral
        await Referral.objects.acreate(
            referrer=referrer_user,
            referee=referee_user
        )
        
        # Try to create duplicate using get_or_create - should return existing
        referral, created = await Referral.objects.aget_or_create(
            referrer=referrer_user,
            referee=referee_user
        )
        assert created is False  # Should return existing, not create new
        
        # Verify only one exists
        count = await Referral.objects.filter(
            referrer=referrer_user,
            referee=referee_user
        ).acount()
        assert count == 1

    async def test_referral_related_names(self, referrer_user, referee_user):
        """Test that related_name works correctly for reverse lookups."""
        referral = await Referral.objects.acreate(
            referrer=referrer_user,
            referee=referee_user
        )
        
        # Check referrer -> referrals_made
        referrals_made = await sync_to_async(list)(
            referrer_user.referrals_made.all()
        )
        assert len(referrals_made) == 1
        assert referrals_made[0].id == referral.id
        
        # Check referee -> referrals_received
        referrals_received = await sync_to_async(list)(
            referee_user.referrals_received.all()
        )
        assert len(referrals_received) == 1
        assert referrals_received[0].id == referral.id
