from __future__ import annotations

from datetime import datetime, timezone

from typer.testing import CliRunner

from momops.cli import app
from momops.state import DeploymentRecord, StateStore


def test_cli_preview_uses_local_parser() -> None:
    result = CliRunner().invoke(app, ["preview", "I need a hobby blog"])

    assert result.exit_code == 0
    assert "Total Monthly" in result.output


def test_cli_list_empty_state() -> None:
    result = CliRunner().invoke(app, ["list"])

    assert result.exit_code == 0
    assert "No MomOps deployments" in result.output


def test_cli_update_state_record() -> None:
    now = datetime.now(timezone.utc).isoformat()
    StateStore().save(
        [
            DeploymentRecord(
                app_id="abc123",
                name="API",
                endpoint="https://old.example.com",
                region="us-east-1",
                status="deployed",
                monthly_cost=10.0,
                created_at=now,
                updated_at=now,
            )
        ]
    )

    result = CliRunner().invoke(
        app,
        ["update", "abc123", "--status", "paused", "--endpoint", "https://new.example.com"],
    )

    assert result.exit_code == 0
    assert "Updated abc123" in result.output
    assert StateStore().get("abc123").status == "paused"  # type: ignore[union-attr]
