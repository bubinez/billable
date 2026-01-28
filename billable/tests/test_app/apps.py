"""AppConfig for the test-only User model (minimal AbstractUser)."""

from django.apps import AppConfig


class TestAppConfig(AppConfig):
    """Config for test app used when running billable tests in isolation."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "billable.tests.test_app"
    label = "test_app"
    verbose_name = "Test App (minimal User)"
