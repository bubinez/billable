"""Minimal User model for billable tests (standard AbstractUser, no extra fields)."""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Test user model: plain AbstractUser for testing with host apps that use a minimal user model."""

    class Meta:
        app_label = "test_app"
