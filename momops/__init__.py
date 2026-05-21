"""Public API for MomOps."""

from __future__ import annotations

from momops.models import (
    ArchitectureBlueprint,
    CostBreakdown,
    DeployedApp,
    DeployEvent,
    InfraRequirement,
    OptimizationSuggestion,
)
from momops.mom import MomApp, MomSession, talk_to_mom

__all__ = [
    "ArchitectureBlueprint",
    "CostBreakdown",
    "DeployEvent",
    "DeployedApp",
    "InfraRequirement",
    "MomApp",
    "MomSession",
    "OptimizationSuggestion",
    "mom",
    "talk_to_mom",
]


def mom(intent: str, region: str = "us-east-1", dry_run: bool = False) -> MomApp:
    """Create a MomOps app from a natural-language infrastructure request."""
    return MomApp(intent=intent, region=region, dry_run=dry_run)
