import pytest
from django.conf import settings
from billable.models import ExternalIdentity
from billable.api import router
from ninja.testing import TestAsyncClient

@pytest.fixture
def api_client():
    return TestAsyncClient(router, headers={"Authorization": f"Bearer {settings.BILLABLE_API_TOKEN}"})

@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_referral_creation_does_not_create_identity(api_client):
    """
    Test that calling /referrals with a non-existent external ID 
    does NOT create a new identity record.
    """
    # 1. Verify identity "0" does not exist
    count_before = await ExternalIdentity.objects.filter(external_id="0").acount()
    assert count_before == 0
    
    # 2. Call /referrals with non-existent IDs
    payload = {
        "provider": "telegram",
        "referee_external_id": "5454776146",
        "referrer_external_id": "0"
    }
    
    res = await api_client.post("/referrals", json=payload)
    
    # 3. Should return 400 because referrer doesn't exist
    assert res.status_code == 400
    assert "Referrer identity not found" in res.json()["message"]
    
    # 4. Verify identity "0" was NOT created
    count_after = await ExternalIdentity.objects.filter(external_id="0").acount()
    assert count_after == 0
