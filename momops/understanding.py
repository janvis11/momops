"""
AGENT-1: Intent Parser
Converts free-form natural language into a structured InfraRequirement.
Uses configured LLM providers in priority order: Groq, OpenAI, Claude.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from momops.exceptions import IntentParsingError
from momops.llm import (
    LLMProviderError,
    complete_text,
    complete_text_async,
    has_configured_provider,
)
from momops.models import DatabaseType, InfraRequirement, ScaleHint, ServiceType
from momops.utils.prompts import INTENT_PARSER_SYSTEM, INTENT_PARSER_USER
from momops.utils.validators import validate_region

logger = logging.getLogger(__name__)


def parse_intent(intent: str, region: str = "us-east-1") -> InfraRequirement:
    """
    Parse a natural language infrastructure request into a structured model.

    Args:
        intent: Free-form description, e.g. "I need a blog with 50k users"
        region: Default AWS region to embed in the requirement

    Returns:
        InfraRequirement â€” fully typed, validated, immutable

    Raises:
        ValueError: If LLM returns unparseable JSON
        anthropic.APIError: On API failure
    """
    region = validate_region(region)
    if not has_configured_provider():
        logger.info("No LLM provider is configured; using deterministic intent parser")
        return parse_intent_heuristic(intent, region=region)

    logger.debug("Parsing intent: %r", intent)
    try:
        response = complete_text(
            system=INTENT_PARSER_SYSTEM,
            messages=[{"role": "user", "content": INTENT_PARSER_USER.format(intent=intent)}],
            max_tokens=512,
        )
    except LLMProviderError:
        logger.exception("LLM intent parsing failed; using deterministic intent parser")
        return parse_intent_heuristic(intent, region=region)

    raw = response.text.strip()
    logger.debug("Raw LLM response: %s", raw)

    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntentParsingError(f"LLM returned invalid JSON: {raw!r}") from exc

    # Inject fields that the LLM doesn't produce
    data["raw_intent"] = intent
    data.setdefault("region", region)

    return InfraRequirement.model_validate(data)


async def parse_intent_async(intent: str, region: str = "us-east-1") -> InfraRequirement:
    """Async variant using the Anthropic async client."""
    region = validate_region(region)
    if not has_configured_provider():
        logger.info("No LLM provider is configured; using deterministic intent parser")
        return parse_intent_heuristic(intent, region=region)

    logger.debug("Parsing intent (async): %r", intent)
    try:
        response = await complete_text_async(
            system=INTENT_PARSER_SYSTEM,
            messages=[{"role": "user", "content": INTENT_PARSER_USER.format(intent=intent)}],
            max_tokens=512,
        )
    except LLMProviderError:
        logger.exception("Async LLM intent parsing failed; using deterministic intent parser")
        return parse_intent_heuristic(intent, region=region)

    raw = response.text.strip()

    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IntentParsingError(f"LLM returned invalid JSON: {raw!r}") from exc

    data["raw_intent"] = intent
    data.setdefault("region", region)

    return InfraRequirement.model_validate(data)


def parse_intent_heuristic(intent: str, region: str = "us-east-1") -> InfraRequirement:
    """Deterministic parser used for tests, local previews, and missing API keys."""
    text = intent.lower()

    service = ServiceType.API
    if any(token in text for token in ("blog", "wordpress", "cms", "content")):
        service = ServiceType.BLOG
    elif any(token in text for token in ("ml", "machine learning", "inference", "model")):
        service = ServiceType.ML
    elif any(token in text for token in ("shop", "store", "commerce", "checkout")):
        service = ServiceType.ECOMMERCE
    elif any(token in text for token in ("websocket", "realtime", "real-time", "chat")):
        service = ServiceType.REALTIME
    elif "microservice" in text:
        service = ServiceType.MICROSERVICES
    elif any(token in text for token in ("database", "postgres", "mysql", "dynamodb")):
        service = ServiceType.DATABASE
    elif any(token in text for token in ("bucket", "storage", "files", "static site")):
        service = ServiceType.STORAGE

    scale = ScaleHint.STARTUP
    if any(token in text for token in ("hobby", "tiny", "personal", "prototype")):
        scale = ScaleHint.HOBBY
    elif any(token in text for token in ("enterprise", "multi-region", "millions")):
        scale = ScaleHint.ENTERPRISE
    elif any(token in text for token in ("scale", "large", "100k", "1m")):
        scale = ScaleHint.SCALE

    database = DatabaseType.NONE
    if "postgres" in text or "postgresql" in text:
        database = DatabaseType.POSTGRES
    elif "mysql" in text:
        database = DatabaseType.MYSQL
    elif "mongo" in text:
        database = DatabaseType.MONGO
    elif "redis" in text:
        database = DatabaseType.REDIS
    elif "dynamodb" in text:
        database = DatabaseType.DYNAMODB
    elif service in {
        ServiceType.API,
        ServiceType.BLOG,
        ServiceType.ECOMMERCE,
        ServiceType.DATABASE,
    }:
        database = DatabaseType.POSTGRES

    budget_hint = _extract_budget(text)

    return InfraRequirement(
        raw_intent=intent,
        service_type=service,
        scale=scale,
        database=database,
        auth_required=any(token in text for token in ("auth", "login", "users", "accounts")),
        cdn_required=any(token in text for token in ("cdn", "global", "static", "images")),
        websocket_required=service == ServiceType.REALTIME or "websocket" in text,
        region=region,
        budget_hint=budget_hint,
        extra_hints={"parser": "heuristic"},
    )


def _extract_budget(text: str) -> float | None:
    match = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*(?:/mo|per month|monthly|month|budget)", text)
    if not match:
        return None
    return float(match.group(1))
