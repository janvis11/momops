from __future__ import annotations

from momops.models import DatabaseType, ScaleHint, ServiceType
from momops.understanding import parse_intent, parse_intent_heuristic


def test_heuristic_parses_blog_with_budget() -> None:
    req = parse_intent_heuristic("I need a hobby blog with images under $25 monthly")

    assert req.service_type == ServiceType.BLOG
    assert req.scale == ScaleHint.HOBBY
    assert req.database == DatabaseType.POSTGRES
    assert req.budget_hint == 25.0


def test_parse_intent_falls_back_without_anthropic_key() -> None:
    req = parse_intent("I need realtime chat with websocket users")

    assert req.service_type == ServiceType.REALTIME
    assert req.websocket_required is True
