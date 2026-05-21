from __future__ import annotations

from momops import mom
from momops.state import StateStore


def test_mom_preview_and_dry_run_persist_state() -> None:
    app = mom("I need a hobby blog with images", dry_run=True)

    preview = app.preview()
    result = app.deploy()

    assert preview["total_monthly"] > 0
    assert result.app_id
    assert StateStore().get(result.app_id) is not None
