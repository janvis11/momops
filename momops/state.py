"""Local deployment state in ~/.momops."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from momops.config import get_settings
from momops.exceptions import StateError
from momops.models import DeployedApp


class DeploymentRecord(BaseModel):
    """Small persisted view of a deployment."""

    app_id: str
    name: str
    endpoint: str | None = None
    region: str
    status: str = "deployed"
    monthly_cost: float
    resource_ids: dict[str, str] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class StateStore:
    """JSON-backed local state store."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().state_dir
        self.path = self.root / "deployments.json"

    def load(self) -> list[DeploymentRecord]:
        """Load all deployment records."""
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise StateError(f"Could not read state file: {self.path}") from exc
        return [DeploymentRecord.model_validate(item) for item in data]

    def save(self, records: list[DeploymentRecord]) -> None:
        """Persist all deployment records."""
        self.root.mkdir(parents=True, exist_ok=True)
        payload = [record.model_dump(mode="json") for record in records]
        try:
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            raise StateError(f"Could not write state file: {self.path}") from exc

    def upsert_app(self, app: DeployedApp, status: str = "deployed") -> DeploymentRecord:
        """Insert or update a deployment from a DeployedApp."""
        now = datetime.now(timezone.utc).isoformat()
        record = DeploymentRecord(
            app_id=app.app_id,
            name=app.name,
            endpoint=app.endpoint,
            region=app.region,
            status=status,
            monthly_cost=app.blueprint.cost.total_monthly,
            resource_ids=app.aws_resource_ids,
            created_at=app.deployed_at or now,
            updated_at=now,
        )
        records = [item for item in self.load() if item.app_id != app.app_id]
        records.append(record)
        self.save(records)
        return record

    def get(self, app_id: str) -> DeploymentRecord | None:
        """Find a record by exact app ID or name."""
        for record in self.load():
            if record.app_id == app_id or record.name == app_id:
                return record
        return None

    def mark_destroyed(self, app_id: str) -> DeploymentRecord | None:
        """Mark a record destroyed without deleting history."""
        records = self.load()
        now = datetime.now(timezone.utc).isoformat()
        found: DeploymentRecord | None = None
        updated: list[DeploymentRecord] = []
        for record in records:
            if record.app_id == app_id or record.name == app_id:
                found = record.model_copy(update={"status": "destroyed", "updated_at": now})
                updated.append(found)
            else:
                updated.append(record)
        if found:
            self.save(updated)
        return found

    def update(
        self,
        app_id: str,
        *,
        status: str | None = None,
        endpoint: str | None = None,
        monthly_cost: float | None = None,
    ) -> DeploymentRecord | None:
        """Update editable local metadata for a deployment record."""
        records = self.load()
        now = datetime.now(timezone.utc).isoformat()
        found: DeploymentRecord | None = None
        updated: list[DeploymentRecord] = []
        for record in records:
            if record.app_id == app_id or record.name == app_id:
                changes: dict[str, Any] = {"updated_at": now}
                if status is not None:
                    changes["status"] = status
                if endpoint is not None:
                    changes["endpoint"] = endpoint
                if monthly_cost is not None:
                    changes["monthly_cost"] = monthly_cost
                found = record.model_copy(update=changes)
                updated.append(found)
            else:
                updated.append(record)
        if found:
            self.save(updated)
        return found

    def as_rows(self) -> list[dict[str, Any]]:
        """Return records as plain dictionaries for CLI rendering."""
        return [record.model_dump(mode="json") for record in self.load()]
