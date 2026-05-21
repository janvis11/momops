from __future__ import annotations

import pytest

from momops.budget import BudgetExceededError, PricingEngine, enforce_budget, optimize
from momops.models import DatabaseType, InfraRequirement, ScaleHint, ServiceType
from momops.recipes import get_blueprint


def test_pricing_engine_estimates_blueprint() -> None:
    req = InfraRequirement(
        raw_intent="api with postgres",
        service_type=ServiceType.API,
        scale=ScaleHint.STARTUP,
        database=DatabaseType.POSTGRES,
    )
    blueprint = get_blueprint(req)

    cost = PricingEngine().estimate_blueprint(blueprint)

    assert cost.total_monthly >= blueprint.cost.total_monthly - 20
    assert cost.estimated_annual == cost.total_monthly * 12


def test_budget_guardrail_raises() -> None:
    req = InfraRequirement(
        raw_intent="enterprise api",
        service_type=ServiceType.API,
        scale=ScaleHint.ENTERPRISE,
        database=DatabaseType.POSTGRES,
    )
    blueprint = get_blueprint(req)

    with pytest.raises(BudgetExceededError):
        enforce_budget(blueprint, limit=1)


def test_static_optimizer_returns_savings() -> None:
    req = InfraRequirement(raw_intent="api", service_type=ServiceType.API)
    blueprint = get_blueprint(req)

    suggestions = optimize(blueprint)

    assert suggestions
    assert suggestions[0].monthly_savings_usd > 0
