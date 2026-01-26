"""Application configuration for billable domain models."""

from __future__ import annotations

from django.apps import AppConfig


class BillableConfig(AppConfig):
    """AppConfig for billable domain."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "billable"

    def ready(self) -> None:
        """Register signals when application is ready."""
        import billable.signals  # noqa: F401
        import billable.admin  # noqa: F401
