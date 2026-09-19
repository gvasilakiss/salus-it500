"""Shared pytest fixtures for the Salus iT500 test suite."""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Make custom_components/salus_it500 loadable by every test that needs hass."""
    yield
