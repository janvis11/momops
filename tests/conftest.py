from __future__ import annotations

import pytest

from momops.config import get_settings


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MOMOPS_BUDGET_LIMIT", raising=False)
    monkeypatch.setenv("MOMOPS_STATE_DIR", str(tmp_path / "state"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
