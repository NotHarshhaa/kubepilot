"""Shared test fixtures."""

import pytest

from app.config import settings as settings_module
from app.tools.approvals import manager


@pytest.fixture
def configure(monkeypatch):
    """Returns a helper that sets env vars and resets cached singletons (settings, approvals)."""

    def _configure(**env: str) -> None:
        settings_module.get_settings.cache_clear()
        manager._records.clear()
        for key, value in env.items():
            monkeypatch.setenv(key, value)

    yield _configure
    settings_module.get_settings.cache_clear()
    manager._records.clear()
