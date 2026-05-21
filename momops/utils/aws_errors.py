"""AWS API error handling with retries and exponential backoff."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TypeVar, Callable, Any
from functools import wraps

from momops.exceptions import ProviderError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AWSRetryConfig:
    """Configuration for AWS API retries."""

    def __init__(
        self,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
    ) -> None:
        """Initialize retry config.

        Args:
            max_attempts: Maximum number of retry attempts
            initial_delay: Initial delay in seconds
            max_delay: Maximum delay between retries
            backoff_factor: Exponential backoff multiplier
        """
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for retry attempt."""
        delay = self.initial_delay * (self.backoff_factor**attempt)
        # Add jitter to prevent thundering herd
        jitter = random.uniform(0, delay * 0.1)
        return min(delay + jitter, self.max_delay)


# Transient AWS errors that should be retried
RETRYABLE_ERRORS = {
    "ThrottlingException",
    "RequestLimitExceeded",
    "ProvisionedThroughputExceededException",
    "InternalFailure",
    "ServiceUnavailable",
    "RequestTimeout",
    "Timeout",
}


def is_retryable(error: Exception) -> bool:
    """Check if error is retryable."""
    error_code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
    return error_code in RETRYABLE_ERRORS or "Throttl" in str(error)


async def retry_with_backoff(
    func: Callable[..., Any],
    *args: Any,
    operation: str = "operation",
    config: AWSRetryConfig | None = None,
    **kwargs: Any,
) -> Any:
    """Execute function with exponential backoff retries.

    Args:
        func: Async function to retry
        operation: Human-readable operation name
        config: Retry configuration
        *args, **kwargs: Arguments to pass to function

    Returns:
        Function result

    Raises:
        ProviderError: If all retries exhausted
    """
    config = config or AWSRetryConfig()

    last_error: Exception | None = None

    for attempt in range(config.max_attempts):
        try:
            logger.debug(f"AWS {operation} (attempt {attempt + 1}/{config.max_attempts})")
            return await func(*args, **kwargs)

        except Exception as e:
            last_error = e

            if not is_retryable(e):
                logger.error(f"AWS {operation} failed (non-retryable): {e}")
                raise ProviderError("AWS", operation, str(e)) from e

            if attempt < config.max_attempts - 1:
                delay = config.get_delay(attempt)
                logger.warning(
                    f"AWS {operation} failed (attempt {attempt + 1}), retrying in {delay:.2f}s: {e}"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"AWS {operation} failed after {config.max_attempts} attempts: {e}")

    # All retries exhausted
    raise ProviderError(
        "AWS", operation, f"Failed after {config.max_attempts} retries"
    ) from last_error


def aws_retry(
    max_attempts: int = 3,
    operation: str = "AWS operation",
) -> Callable:
    """Decorator for async functions to add AWS retry logic.

    Args:
        max_attempts: Maximum retry attempts
        operation: Operation name for logging
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            config = AWSRetryConfig(max_attempts=max_attempts)
            return await retry_with_backoff(
                func,
                *args,
                operation=operation,
                config=config,
                **kwargs,
            )

        return wrapper

    return decorator


# Common AWS service error handlers
def handle_iam_error(error: Exception) -> str:
    """Generate helpful message for IAM errors."""
    code = getattr(error, "response", {}).get("Error", {}).get("Code", "")

    if code == "AccessDenied":
        return (
            "IAM permission denied. Ensure your AWS user has permissions for: "
            "ec2, rds, elasticloadbalancing, iam, s3, route53, cloudwatch"
        )
    elif code == "InvalidClientTokenId":
        return "AWS credentials are invalid or expired"
    elif code == "SignatureDoesNotMatch":
        return "AWS signature mismatch (bad secret key?)"

    return f"IAM error: {code}"


def handle_ec2_error(error: Exception) -> str:
    """Generate helpful message for EC2 errors."""
    code = getattr(error, "response", {}).get("Error", {}).get("Code", "")

    if "VpcLimitExceeded" in code:
        return "VPC limit exceeded. Request increase in AWS Service Quotas"
    elif "InsufficientFreeAddressesInSubnet" in code:
        return "Not enough IP addresses in subnet. Use larger CIDR block"
    elif "InvalidParameterValue" in code:
        return "Invalid parameter. Check instance type and region support"

    return f"EC2 error: {code}"


def handle_rds_error(error: Exception) -> str:
    """Generate helpful message for RDS errors."""
    code = getattr(error, "response", {}).get("Error", {}).get("Code", "")

    if "DBInstanceAlreadyExists" in code:
        return "Database name already exists. Delete the old one or use different name"
    elif "InsufficientDBInstanceCapacity" in code:
        return "AWS has insufficient capacity. Try different instance type or region"
    elif "DBSubnetGroupNotFoundFault" in code:
        return "Database subnet group not found. Check VPC configuration"

    return f"RDS error: {code}"


def get_helpful_error_message(error: Exception, service: str) -> str:
    """Get helpful error message based on AWS service."""
    handlers = {
        "iam": handle_iam_error,
        "ec2": handle_ec2_error,
        "rds": handle_rds_error,
    }

    handler = handlers.get(service.lower())
    if handler:
        return handler(error)

    return f"{service} error: {error}"
