"""Configuration layer for the billable module.

This module provides a settings wrapper that allows setting default values
if the user hasn't specified them in settings.py.
"""

from django.conf import settings


class AppSettings:
    """
    Settings wrapper for accessing configuration.
    Allows setting default values if the user hasn't specified them in settings.py.
    """

    @property
    def API_TOKEN(self):
        return getattr(settings, "BILLING_API_TOKEN", None)

    @property
    def USER_MODEL(self):
        return getattr(settings, "AUTH_USER_MODEL", "auth.User")

    @property
    def TABLE_PREFIX(self):
        return "billable_"

    @property
    def SHOW_DOCS(self):
        return getattr(settings, "BILLING_SHOW_DOCS", True)
    
    @property
    def API_TITLE(self):
        return getattr(settings, "BILLING_API_TITLE", "Billable Engine API")


# Create singleton instance
billing_settings = AppSettings()
