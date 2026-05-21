from __future__ import annotations

import pytest

from momops.models import DatabaseType, InfraRequirement, ScaleHint, ServiceType
from momops.recipes import get_blueprint
from momops.safety import SecurityViolationError, validate_blueprint


@pytest.mark.parametrize(
    "service_type",
    [
        ServiceType.API,
        ServiceType.BLOG,
        ServiceType.ML,
        ServiceType.REALTIME,
        ServiceType.ECOMMERCE,
        ServiceType.MICROSERVICES,
        ServiceType.DATABASE,
        ServiceType.STORAGE,
    ],
)
def test_blueprint_generated_for_supported_services(service_type: ServiceType) -> None:
    req = InfraRequirement(
        raw_intent=f"test {service_type.value}",
        service_type=service_type,
        scale=ScaleHint.STARTUP,
        database=DatabaseType.POSTGRES,
    )

    blueprint = get_blueprint(req)

    assert blueprint.recipe_id
    assert blueprint.aws_services
    assert blueprint.deploy_steps
    assert blueprint.cost.total_monthly > 0
    validate_blueprint(blueprint)


def test_security_rejects_insecure_manifest() -> None:
    req = InfraRequirement(raw_intent="api", service_type=ServiceType.API)
    blueprint = get_blueprint(req)
    insecure = blueprint.model_copy(
        update={"security": blueprint.security.model_copy(update={"ssl_enabled": False})}
    )

    with pytest.raises(SecurityViolationError):
        validate_blueprint(insecure)
