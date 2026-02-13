"""Billable module for billing and product management."""

# uuid7 is available in stdlib since Python 3.14; backport for 3.12–3.13
import uuid
if not hasattr(uuid, 'uuid7'):
    from future_uuid import uuid7 as _uuid7  # noqa: F401
    uuid.uuid7 = _uuid7

# Модели не реэкспортируем здесь: импорт до django.setup() приводит к AppRegistryNotReady.
# Используйте: from billable.models import ...
