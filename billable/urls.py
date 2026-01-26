"""URL configuration for the billable module.

This module provides a "boxed" installation approach, allowing users to include
the billable app with a single line in their main urls.py. All NinjaAPI initialization
logic is encapsulated within this package.
"""

from django.urls import path
from ninja import NinjaAPI
from .api import router as billing_router
from .conf import billing_settings

# 1. Read settings from billing_settings wrapper
SHOW_DOCS = billing_settings.SHOW_DOCS
API_TITLE = billing_settings.API_TITLE

# 2. Create API instance
# If SHOW_DOCS=False, pass None, which disables documentation path generation
api = NinjaAPI(
    title=API_TITLE,
    docs_url="/docs" if SHOW_DOCS else None,
    redoc_url="/redoc" if SHOW_DOCS else None,
    urls_namespace="billable_default_api",  # To avoid conflicts with other APIs
)

# 3. Connect router
# Empty string "" means the router will be connected to the root of this API
api.add_router("", billing_router)

# 4. Export urlpatterns for use in include()
urlpatterns = [
    path("", api.urls),
]
