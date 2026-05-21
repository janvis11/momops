"""Input validation and sanitization for production safety."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class ValidationError(ValueError):
    """Raised when input validation fails."""


def validate_intent(intent: str, max_length: int = 2000) -> str:
    """
    Validate natural language intent for safety.

    Args:
        intent: User's natural language description
        max_length: Maximum allowed length

    Returns:
        Validated and normalized intent

    Raises:
        ValidationError: If intent is invalid
    """
    if not isinstance(intent, str):
        raise ValidationError("Intent must be a string")

    if not intent.strip():
        raise ValidationError("Intent cannot be empty")

    if len(intent) > max_length:
        raise ValidationError(f"Intent exceeds maximum length of {max_length} characters")

    # Check for injection patterns
    dangerous_patterns = [
        r"(DROP|DELETE|TRUNCATE)\s+(TABLE|DATABASE)",  # SQL injection
        r"rm\s+-rf",  # Dangerous shell commands
        r"\$\(.*\)",  # Command substitution
        r"`.*`",  # Backtick execution
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, intent, re.IGNORECASE):
            logger.warning(f"Potentially dangerous pattern detected in intent: {pattern}")
            raise ValidationError("Intent contains suspicious patterns")

    return intent.strip()


def validate_aws_resource_name(name: str) -> str:
    """
    Validate AWS resource naming conventions.

    Args:
        name: Resource name

    Returns:
        Validated resource name

    Raises:
        ValidationError: If name is invalid
    """
    if not isinstance(name, str):
        raise ValidationError("Resource name must be a string")

    if not name:
        raise ValidationError("Resource name cannot be empty")

    if len(name) > 255:
        raise ValidationError("Resource name exceeds 255 characters")

    # AWS naming rules: alphanumeric, hyphens, underscores
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValidationError("Resource name contains invalid characters")

    return name


def validate_region(region: str) -> str:
    """
    Validate AWS region name.

    Args:
        region: AWS region code

    Returns:
        Validated region code

    Raises:
        ValidationError: If region is invalid
    """
    valid_regions = {
        "us-east-1",
        "us-east-2",
        "us-west-1",
        "us-west-2",
        "eu-west-1",
        "eu-central-1",
        "ap-southeast-1",
        "ap-northeast-1",
        "ap-south-1",
        "ca-central-1",
        "sa-east-1",
    }

    if region not in valid_regions:
        raise ValidationError(
            f"Invalid region '{region}'. Supported regions: {', '.join(sorted(valid_regions))}"
        )

    return region


def validate_budget(budget: float | None) -> float | None:
    """
    Validate budget amount.

    Args:
        budget: Monthly budget in USD or None

    Returns:
        Validated budget

    Raises:
        ValidationError: If budget is invalid
    """
    if budget is None:
        return None

    if not isinstance(budget, (int, float)):
        raise ValidationError("Budget must be a number")

    if budget < 0:
        raise ValidationError("Budget cannot be negative")

    if budget > 100000:
        logger.warning(f"Very high budget specified: ${budget}/month")

    return float(budget)


def validate_cost_estimate(cost: float) -> float:
    """
    Validate cost estimate.

    Args:
        cost: Estimated cost in USD

    Returns:
        Validated cost

    Raises:
        ValidationError: If cost is invalid
    """
    if not isinstance(cost, (int, float)):
        raise ValidationError("Cost must be a number")

    if cost < 0:
        raise ValidationError("Cost cannot be negative")

    if cost > 1000000:
        logger.error(f"Unusually high cost estimate detected: ${cost}")
        raise ValidationError("Cost estimate exceeds safety threshold")

    return float(cost)


def sanitize_log_message(message: str) -> str:
    """
    Sanitize log message to remove sensitive data.

    Args:
        message: Original log message

    Returns:
        Sanitized message
    """
    # Mask common patterns
    message = re.sub(r"sk-[A-Za-z0-9]+", "[REDACTED_API_KEY]", message)
    message = re.sub(r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]", message)
    message = re.sub(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", "[REDACTED_TOKEN]", message)
    message = re.sub(r"password['\"]?\s*[:=]\s*['\"]?[^'\"]+['\"]?", "[REDACTED_PASSWORD]", message)

    return message
