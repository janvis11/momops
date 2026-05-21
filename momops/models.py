"""
Core data models for MomOps.
All models are immutable Pydantic v2 BaseModels with full type coverage.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field


# ── Enums ──────────────────────────────────────────────────────────────────


class ServiceType(StrEnum):
    API = "api"
    BLOG = "blog"
    ML = "ml"
    ECOMMERCE = "ecommerce"
    REALTIME = "realtime"
    MICROSERVICES = "microservices"
    DATABASE = "database"
    STORAGE = "storage"
    UNKNOWN = "unknown"


class ScaleHint(StrEnum):
    HOBBY = "hobby"  # <1k req/day, single instance, no HA
    STARTUP = "startup"  # <100k req/day, basic autoscaling
    SCALE = "scale"  # >100k req/day, multi-AZ, caching
    ENTERPRISE = "enterprise"  # custom, multi-region


class DatabaseType(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MONGO = "mongo"
    REDIS = "redis"
    DYNAMODB = "dynamodb"
    NONE = "none"


class OptimizeFor(StrEnum):
    COST = "cost"
    PERFORMANCE = "performance"
    RELIABILITY = "reliability"


class DeployStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


# ── Core requirement model ──────────────────────────────────────────────────


class InfraRequirement(BaseModel):
    """Parsed user intent — output of the NLP understanding layer."""

    model_config = {"frozen": True}

    raw_intent: str = Field(..., description="Original user message, verbatim")
    service_type: ServiceType = ServiceType.UNKNOWN
    scale: ScaleHint = ScaleHint.STARTUP
    database: DatabaseType = DatabaseType.NONE
    auth_required: bool = False
    cdn_required: bool = False
    websocket_required: bool = False
    region: str = "us-east-1"
    budget_hint: float | None = Field(None, description="Max monthly spend hint in USD")
    extra_hints: dict[str, Any] = Field(default_factory=dict)


# ── Pricing models ─────────────────────────────────────────────────────────


class LineItem(BaseModel):
    """Single cost line in a breakdown."""

    label: str
    service: str
    instance_type: str | None = None
    monthly_usd: float
    note: str = ""


class CostBreakdown(BaseModel):
    """Full itemized cost breakdown returned by the pricing engine."""

    items: list[LineItem] = Field(default_factory=list)
    savings_available: float = 0.0
    currency: str = "USD"

    @computed_field  # type: ignore[misc]
    @property
    def total_monthly(self) -> float:
        return round(sum(i.monthly_usd for i in self.items), 2)

    @computed_field  # type: ignore[misc]
    @property
    def estimated_annual(self) -> float:
        return round(self.total_monthly * 12, 2)

    def display(self) -> dict[str, Any]:
        """Dict suitable for pretty-printing / preview()."""
        d: dict[str, Any] = {i.label: i.monthly_usd for i in self.items}
        d["total_monthly"] = self.total_monthly
        d["estimated_annual"] = self.estimated_annual
        if self.savings_available > 0:
            d["savings_available"] = self.savings_available
        return d


# ── Security models ────────────────────────────────────────────────────────


class SecurityManifest(BaseModel):
    """Security defaults applied to every deployment."""

    model_config = {"frozen": True}

    ssl_enabled: bool = True
    backup_enabled: bool = True
    vpc_isolated: bool = True
    iam_least_privilege: bool = True
    monitoring_enabled: bool = True
    ddos_protection: bool = True
    encryption_at_rest: bool = True
    encryption_in_transit: bool = True
    applied_rules: list[str] = Field(default_factory=list)

    @property
    def is_production_ready(self) -> bool:
        return all(
            [
                self.ssl_enabled,
                self.backup_enabled,
                self.vpc_isolated,
                self.iam_least_privilege,
                self.monitoring_enabled,
            ]
        )


# ── Architecture blueprint ─────────────────────────────────────────────────


class AWSService(BaseModel):
    """A single AWS service in the deployment blueprint."""

    name: str
    service: str  # e.g. "EC2", "RDS", "ALB"
    instance_type: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class DeployStep(BaseModel):
    """Single step in a deployment sequence."""

    order: int
    name: str
    description: str
    aws_service: str
    estimated_duration_seconds: int = 30
    rollback_action: str = ""


class ArchitectureBlueprint(BaseModel):
    """Full deployment blueprint — output of recipe engine."""

    recipe_id: str
    recipe_name: str
    description: str
    aws_services: list[AWSService]
    deploy_steps: list[DeployStep]
    cost: CostBreakdown
    security: SecurityManifest
    requirement: InfraRequirement

    @property
    def total_estimated_minutes(self) -> int:
        total_secs = sum(s.estimated_duration_seconds for s in self.deploy_steps)
        return max(1, total_secs // 60)


# ── Optimization suggestion ────────────────────────────────────────────────


class OptimizationSuggestion(BaseModel):
    """Single optimization recommendation."""

    for_: OptimizeFor
    title: str
    description: str
    monthly_savings_usd: float = 0.0
    trade_off: str = ""
    apply_command: str = ""  # CLI command to apply


# ── Deployment event (for streaming progress) ──────────────────────────────


class DeployEvent(BaseModel):
    """Emitted during deployment to show real-time progress."""

    step: str
    status: DeployStatus
    message: str
    aws_resource_id: str | None = None
    elapsed_seconds: float = 0.0


# ── Deployed app state ─────────────────────────────────────────────────────


class DeployedApp(BaseModel):
    """Returned after successful deployment."""

    app_id: str
    name: str
    endpoint: str | None = None  # e.g. https://api.myapp.cloud
    region: str
    blueprint: ArchitectureBlueprint
    aws_resource_ids: dict[str, str] = Field(default_factory=dict)
    deployed_at: str = ""  # ISO 8601

    def __str__(self) -> str:
        return f"DeployedApp(id={self.app_id}, endpoint={self.endpoint})"
