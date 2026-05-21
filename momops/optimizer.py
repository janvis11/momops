"""
AGENT-6: Optimizer
Post-deploy cost/perf advisor that uses Claude to generate actionable suggestions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

try:
    import anthropic
except ModuleNotFoundError:  # pragma: no cover - exercised in minimal local envs
    anthropic = None  # type: ignore[assignment]

from momops.budget import optimize as static_optimize
from momops.config import get_settings
from momops.models import (
    ArchitectureBlueprint,
    OptimizationSuggestion,
    OptimizeFor,
)
from momops.utils.prompts import OPTIMIZER_SYSTEM, OPTIMIZER_USER

logger = logging.getLogger(__name__)


def optimize(
    blueprint: ArchitectureBlueprint,
    for_: OptimizeFor | str = OptimizeFor.COST,
) -> list[OptimizationSuggestion]:
    """
    Generate optimization suggestions for a deployed blueprint.

    Args:
        blueprint: The architecture to optimize
        for_:      Optimization goal — "cost" | "performance" | "reliability"

    Returns:
        List of OptimizationSuggestion with title, description, savings estimate
    """
    optimize_for = OptimizeFor(for_) if isinstance(for_, str) else for_

    if anthropic is None or not get_settings().anthropic_api_key:
        return [
            suggestion.model_copy(update={"for_": optimize_for})
            for suggestion in static_optimize(blueprint)
        ]

    client = anthropic.Anthropic()

    blueprint_summary = {
        "recipe": blueprint.recipe_id,
        "services": [s.name for s in blueprint.aws_services],
        "scale": blueprint.requirement.scale,
        "database": blueprint.requirement.database,
        "monthly_cost": blueprint.cost.total_monthly,
    }

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=OPTIMIZER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": OPTIMIZER_USER.format(
                    blueprint_json=json.dumps(blueprint_summary),
                    optimize_for=optimize_for.value,
                    monthly_cost=blueprint.cost.total_monthly,
                ),
            }
        ],
    )

    # Extract text from first TextBlock in response
    text_block = next(
        (block for block in message.content if hasattr(block, "text")),
        None,
    )
    if not text_block or not hasattr(text_block, "text"):
        logger.warning("Optimizer returned no text — returning empty suggestions")
        return []

    raw = text_block.text.strip()

    try:
        data: dict[str, Any] = json.loads(raw)
        suggestions_raw: list[dict[str, Any]] = data.get("suggestions", [])
    except json.JSONDecodeError:
        logger.warning("Optimizer returned invalid JSON — returning empty suggestions")
        return []

    suggestions: list[OptimizationSuggestion] = []
    for s in suggestions_raw:
        try:
            suggestions.append(
                OptimizationSuggestion(
                    for_=optimize_for,
                    title=s.get("title", "Optimization"),
                    description=s.get("description", ""),
                    monthly_savings_usd=float(s.get("monthly_savings_usd", 0)),
                    trade_off=s.get("trade_off", ""),
                    apply_command=s.get("apply_command", ""),
                )
            )
        except Exception as exc:
            logger.debug("Skipping malformed suggestion: %s", exc)

    total_savings = sum(s.monthly_savings_usd for s in suggestions)
    logger.info(
        "Optimizer (%s): %d suggestions, $%.2f/mo potential savings",
        optimize_for.value,
        len(suggestions),
        total_savings,
    )

    return suggestions
