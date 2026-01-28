"""
Self-contained Django settings for running billable tests in isolation.

Use: DJANGO_SETTINGS_MODULE=billable.tests.test_settings pytest billable/tests/
"""

SECRET_KEY = "test-secret-key-not-for-production"
DEBUG = True
USE_TZ = True
TIME_ZONE = "UTC"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.admin",
    "billable.tests.test_app",
    "billable",
]

AUTH_USER_MODEL = "test_app.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

ROOT_URLCONF = "billable.tests.test_app.urls"
MIDDLEWARE = []

# Disable migrations for ALL apps for speed and SQLite compatibility
class DisableMigrations:
    def __contains__(self, item: str) -> bool:
        return True

    def __getitem__(self, item: str):
        return None


MIGRATION_MODULES = DisableMigrations()

# Ensure we don't try to use Celery in tests
CELERY_TASK_ALWAYS_EAGER = True

# Token for API testing
BILLABLE_API_TOKEN = "test_billing_token_123"
N8N_SERVICE_KEY = "test_n8n_service_key_123"
