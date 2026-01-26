from config.settings import *

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Disable migrations for ALL apps for speed and SQLite compatibility
class DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None

MIGRATION_MODULES = DisableMigrations()

# Ensure we don't try to use Celery in tests
CELERY_TASK_ALWAYS_EAGER = True

# Token for API testing
BILLING_API_TOKEN = "test_billing_token_123"
N8N_SERVICE_KEY = "test_n8n_service_key_123"
