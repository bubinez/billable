"""Minimal User model for billable tests (AbstractUser + optional identity fields for migrate_identities tests)."""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Test user model: plain AbstractUser for testing with host apps that use a minimal user model."""

    chat_id = models.BigIntegerField(null=True, blank=True)
    stripe_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        app_label = "test_app"
