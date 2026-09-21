"""Shared fixtures.

Tests fall into two groups. Most exercise pure logic and need nothing but the
source tree. A few need PostGIS; those are skipped automatically unless
``TRACKER_TEST_DATABASE_URL`` is set, so the suite runs anywhere.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TRACKER_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TRACKER_TEST_DATABASE_URL to run database tests")
    return url
