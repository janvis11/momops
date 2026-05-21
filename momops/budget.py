"""AGENT-3: Pricing engine for MomOps blueprints."""

from __future__ import annotations

from dataclasses import dataclass

from momops.exceptions import BudgetExceededError
from momops.models import (
    ArchitectureBlueprint,
    AWSService,
    CostBreakdown,
    DatabaseType,
    InfraRequirement,
    LineItem,
    OptimizationSuggestion,
    OptimizeFor,
    ScaleHint,
)


@dataclass(frozen=True)
class PriceTable:
    """Static monthly pricing table used for tests and offline estimates."""

    ec2: dict[str, float]
    rds: dict[str, float]
    cache: dict[str, float]
    service: dict[str, float]
    networking_by_scale: dict[ScaleHint, float]


STATIC_PRICES = PriceTable(
    ec2={
        "t3.micro": 8.0,
        "t3.medium": 30.0,
        "c6i.xlarge": 122.0,
        "c6i.2xlarge": 240.0,
        "c6i.4xlarge": 490.0,
        "g4dn.xlarge": 400.0,
    },
    rds={
        "db.t3.micro": 14.0,
        "db.t3.medium": 52.0,
        "db.r6g.large": 175.0,
        "db.r6g.4xlarge": 700.0,
    },
    cache={
        "cache.t3.micro": 16.0,
        "cache.r6g.large": 120.0,
    },
    service={
        "ALB": 18.0,
        "APIGateway": 8.0,
        "CloudFront": 5.0,
        "CloudWatch": 3.0,
        "DynamoDB": 25.0,
        "ECS": 0.0,
        "Lambda": 3.0,
        "Route53": 1.0,
        "S3": 5.0,
        "SecretsManager": 1.0,
        "SQS": 1.0,
        "VPC": 0.0,
    },
    networking_by_scale={
        ScaleHint.HOBBY: 2.0,
        ScaleHint.STARTUP: 10.0,
        ScaleHint.SCALE: 35.0,
        ScaleHint.ENTERPRISE: 120.0,
    },
)


class PricingEngine:
    """Compute monthly cost estimates from blueprint services."""

    def __init__(self, prices: PriceTable = STATIC_PRICES) -> None:
        self.prices = prices

    def estimate_requirement(self, requirement: InfraRequirement) -> CostBreakdown:
        """Estimate a requirement before a recipe is built."""
        compute = {
            ScaleHint.HOBBY: "t3.micro",
            ScaleHint.STARTUP: "t3.medium",
            ScaleHint.SCALE: "c6i.xlarge",
            ScaleHint.ENTERPRISE: "c6i.4xlarge",
        }[requirement.scale]
        items = [
            LineItem(
                label="compute",
                service="EC2",
                instance_type=compute,
                monthly_usd=self.prices.ec2[compute],
            ),
            LineItem(
                label="networking",
                service="ALB + Data Transfer",
                monthly_usd=self.prices.networking_by_scale[requirement.scale],
            ),
            LineItem(label="monitoring", service="CloudWatch", monthly_usd=3.0),
        ]
        if requirement.database != DatabaseType.NONE:
            db = {
                ScaleHint.HOBBY: "db.t3.micro",
                ScaleHint.STARTUP: "db.t3.medium",
                ScaleHint.SCALE: "db.r6g.large",
                ScaleHint.ENTERPRISE: "db.r6g.4xlarge",
            }[requirement.scale]
            items.append(
                LineItem(
                    label="database",
                    service="RDS",
                    instance_type=db,
                    monthly_usd=self.prices.rds[db],
                )
            )
        return CostBreakdown(
            items=items, savings_available=round(self.prices.ec2[compute] * 0.4, 2)
        )

    def estimate_blueprint(self, blueprint: ArchitectureBlueprint) -> CostBreakdown:
        """Estimate a blueprint from its AWS services."""
        items: list[LineItem] = []
        has_networking = False
        has_monitoring = False

        for service in blueprint.aws_services:
            item = self._estimate_service(service)
            if item is None:
                continue
            if item.label == "networking":
                has_networking = True
            if item.label == "monitoring":
                has_monitoring = True
            items.append(item)

        if not has_networking:
            items.append(
                LineItem(
                    label="networking",
                    service="Data Transfer",
                    monthly_usd=self.prices.networking_by_scale[blueprint.requirement.scale],
                )
            )
        if not has_monitoring:
            items.append(LineItem(label="monitoring", service="CloudWatch", monthly_usd=3.0))

        savings = round(sum(i.monthly_usd for i in items if i.label == "compute") * 0.4, 2)
        return CostBreakdown(items=items, savings_available=savings)

    def _estimate_service(self, service: AWSService) -> LineItem | None:
        service_name = service.service
        instance_type = service.instance_type
        label = service_name.lower()

        if service_name == "EC2" and instance_type:
            return LineItem(
                label="compute",
                service=service_name,
                instance_type=instance_type,
                monthly_usd=self.prices.ec2.get(instance_type, 30.0),
            )
        if service_name == "RDS" and instance_type:
            return LineItem(
                label="database",
                service=service_name,
                instance_type=instance_type,
                monthly_usd=self.prices.rds.get(instance_type, 50.0),
            )
        if service_name == "ElastiCache" and instance_type:
            return LineItem(
                label="cache",
                service=service_name,
                instance_type=instance_type,
                monthly_usd=self.prices.cache.get(instance_type, 16.0),
            )
        if service_name == "ALB":
            return LineItem(label="networking", service="ALB + Data Transfer", monthly_usd=18.0)
        if service_name in {"VPC", "ACM"}:
            return None
        monthly = self.prices.service.get(service_name)
        if monthly is None:
            return None
        return LineItem(label=label, service=service_name, monthly_usd=monthly)


def estimate_cost(blueprint: ArchitectureBlueprint) -> CostBreakdown:
    """Convenience wrapper around the default pricing engine."""
    return PricingEngine().estimate_blueprint(blueprint)


def enforce_budget(blueprint: ArchitectureBlueprint, limit: float | None = None) -> None:
    """Raise if the blueprint exceeds the explicit or requirement budget."""
    budget = limit if limit is not None else blueprint.requirement.budget_hint
    if budget is None:
        return
    if blueprint.cost.total_monthly > budget:
        raise BudgetExceededError(
            estimated=blueprint.cost.total_monthly,
            limit=budget,
        )


def optimize(blueprint: ArchitectureBlueprint) -> list[OptimizationSuggestion]:
    """Return deterministic cost optimizations for budget previews."""
    suggestions: list[OptimizationSuggestion] = []
    if blueprint.cost.savings_available > 0:
        suggestions.append(
            OptimizationSuggestion(
                for_=OptimizeFor.COST,
                title="Use spot capacity for stateless compute",
                description=(
                    "Move autoscaled API and worker nodes to a mixed on-demand/spot policy."
                ),
                monthly_savings_usd=blueprint.cost.savings_available,
                trade_off="Spot nodes can be interrupted, so keep at least one on-demand instance.",
                apply_command=f"momops update {blueprint.recipe_id} --spot-mix 60",
            )
        )
    if blueprint.requirement.scale in {ScaleHint.SCALE, ScaleHint.ENTERPRISE}:
        suggestions.append(
            OptimizationSuggestion(
                for_=OptimizeFor.COST,
                title="Buy Compute Savings Plans",
                description="Commit baseline steady-state compute for a one-year term.",
                monthly_savings_usd=round(blueprint.cost.total_monthly * 0.12, 2),
                trade_off="Lower flexibility if workloads shrink dramatically.",
                apply_command=f"momops optimize {blueprint.recipe_id} --savings-plan",
            )
        )
    return suggestions
