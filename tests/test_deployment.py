from __future__ import annotations

import pytest

from momops.deployment import Deployer
from momops.models import DatabaseType, DeployStatus, InfraRequirement, ServiceType
from momops.recipes import get_blueprint


@pytest.mark.asyncio
async def test_dry_run_deploy_yields_events_and_result() -> None:
    req = InfraRequirement(
        raw_intent="api with postgres",
        service_type=ServiceType.API,
        database=DatabaseType.POSTGRES,
    )
    deployer = Deployer(get_blueprint(req), dry_run=True)

    events = [event async for event in deployer.deploy()]

    assert events[-1].status == DeployStatus.COMPLETE
    assert deployer.result.endpoint == "https://dry-run.momops.dev"
