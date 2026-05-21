"""
AGENT-1: Intent Parser
Converts free-form natural language into a structured InfraRequirement.
Uses Claude claude-sonnet-4-20250514 via the Anthropic SDK.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

try:
    import anthropic
except ModuleNotFoundError:  # pragma: no cover - exercised in minimal local envs
    anthropic = None  # type: ignore[assignment]

from momops.config import get_settings
from momops.exceptions import IntentParsingError
from momops.models import DatabaseType, InfraRequirement, ScaleHint, ServiceType
from momops.utils.prompts import INTENT_PARSER_SYSTEM, INTENT_PARSER_USER
from momops.utils.validators import validate_region

logger = logging.getLogger(__name__)

_client: Any | None = None


def _get_client() -> Any:
    if anthropic is None:
        raise IntentParsingError("anthropic is required for Claude intent parsing")
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return _client


def parse_intent(intent: str, region: str = "us-east-1") -> InfraRequirement:
    """
    Parse a natural language infrastructure request into a structured model.

    Args:
        intent: Free-form description, e.g. "I need a blog with 50k users"
        region: Default AWS region to embed in the requirement

    Returns:
        InfraRequirement — fully typed, validated, immutable

    Raises:
        ValueError: If LLM returns unparseable JSON
        anthropic.APIError: On API failure
    """
    region = validate_region(region)
    if anthropic is None or not get_settings().anthropic_api_key:
        logger.info("ANTHROPIC_API_KEY is not set; using deterministic intent parser")
        return parse_intent_heuristic(intent, region=region)

    client = _get_client()
    logger.debug("Parsing intent: %r", intent)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        system=INTENT_PARSER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": INTENT_PARSER_USER.format(intent=intent),
            }
        ],
    )

    # Extract text from first TextBlock in response
    text_block = next(
        (block for block in message.content if hasattr(block, "text")),
        None,
    )
    if not text_block or not hasattr(text_block, "text"):
        raise IntentParsingError("LLM response contained no text")

    raw = text_block.text.strip()
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
    if anthropic is None or not get_settings().anthropic_api_key:
        logger.info("ANTHROPIC_API_KEY is not set; using deterministic intent parser")
        return parse_intent_heuristic(intent, region=region)

    async_client = anthropic.AsyncAnthropic()
    logger.debug("Parsing intent (async): %r", intent)

    message = await async_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        system=INTENT_PARSER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": INTENT_PARSER_USER.format(intent=intent),
            }
        ],
    )

    # Extract text from first TextBlock in response
    text_block = next(
        (block for block in message.content if hasattr(block, "text")),
        None,
    )
    if not text_block or not hasattr(text_block, "text"):
        raise IntentParsingError("LLM response contained no text")

    raw = text_block.text.strip()

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
