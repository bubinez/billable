import pytest
from django.urls import reverse
from billable.models import Product, Offer, OfferItem, ExternalIdentity
from billable.services import CustomerService

@pytest.fixture
def target_user(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create(username="api_target")

@pytest.fixture
def source_user(db):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    return User.objects.create(username="api_source")

@pytest.mark.django_db
class TestCustomerMergeAPI:
    """
    Tests for the Customer Merge API endpoint.
    """

    def test_merge_api_success(self, client, target_user, source_user):
        # Setup: add some data to source user
        ExternalIdentity.objects.create(user=source_user, provider="telegram", external_id="source_tg")
        
        url = "/api/v1/billing/customers/merge"
        payload = {
            "target_user_id": target_user.id,
            "source_user_id": source_user.id
        }
        
        # We need the API token for authentication
        from billable.conf import billable_settings
        headers = {"HTTP_AUTHORIZATION": f"Bearer {billable_settings.API_TOKEN}"}
        
        response = client.post(
            url, 
            data=payload, 
            content_type="application/json",
            **headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["moved_identities"] == 1
        
        # Verify in DB
        assert not ExternalIdentity.objects.filter(user=source_user).exists()
        assert ExternalIdentity.objects.filter(user=target_user, external_id="source_tg").exists()

    def test_merge_api_self_merge_error(self, client, target_user):
        url = "/api/v1/billing/customers/merge"
        payload = {
            "target_user_id": target_user.id,
            "source_user_id": target_user.id
        }
        
        from billable.conf import billable_settings
        headers = {"HTTP_AUTHORIZATION": f"Bearer {billable_settings.API_TOKEN}"}
        
        response = client.post(
            url, 
            data=payload, 
            content_type="application/json",
            **headers
        )
        
        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert "different" in data["message"]

    def test_merge_api_unauthorized(self, client, target_user, source_user):
        url = "/api/v1/billing/customers/merge"
        payload = {
            "target_user_id": target_user.id,
            "source_user_id": source_user.id
        }
        
        # No headers or wrong token
        response = client.post(
            url, 
            data=payload, 
            content_type="application/json"
        )
        
        assert response.status_code == 401
