"""Pytest hooks and fixtures for billable tests.

Ensures DJANGO_SETTINGS_MODULE is set when running tests from the repo root
without pyproject.toml in effect (e.g. when invoked from another cwd).
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "billable.tests.test_settings")
