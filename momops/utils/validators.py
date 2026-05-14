"""Input validation helpers."""

from __future__ import annotations

import re

from momops.models import ScaleHint

_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d$")


def validate_region(region: str) -> str:
    """Validate a basic AWS region shape."""
    normalized = region.strip().lower()
    if not _REGION_RE.match(normalized):
        raise ValueError(f"Invalid AWS region: {region!r}")
    return normalized


def validate_budget_hint(value: float | None) -> float | None:
    """Validate an optional monthly budget in USD."""
    if value is None:
        return None
    if value <= 0:
        raise ValueError("Budget must be greater than zero")
    return round(value, 2)


def infer_scale_from_users(users: int) -> ScaleHint:
    """Map a rough user count to a scale hint."""
    if users < 1_000:
        return ScaleHint.HOBBY
    if users < 100_000:
        return ScaleHint.STARTUP
    if users < 1_000_000:
        return ScaleHint.SCALE
    return ScaleHint.ENTERPRISE
