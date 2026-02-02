"""Empty URLconf for isolated test runs."""

from django.urls import path, include
import os

os.environ["NINJA_SKIP_REGISTRY"] = "yes"

from billable.urls import api

urlpatterns = [
    path("api/v1/billing/", include((api.urls[0], api.urls[1]))),
]
