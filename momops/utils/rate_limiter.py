"""Rate limiting to protect against abuse and API throttling."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class RateLimiter:
    """Token bucket rate limiter for API calls."""

    def __init__(self, calls_per_minute: int = 60) -> None:
        """Initialize rate limiter.

        Args:
            calls_per_minute: Maximum API calls per minute
        """
        self.calls_per_minute = calls_per_minute
        self.min_interval = 60.0 / calls_per_minute
        self.last_call = 0.0
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait if necessary to respect rate limit."""
        async with self.lock:
            elapsed = time.time() - self.last_call
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self.last_call = time.time()


class CallCounter:
    """Track API calls per service for monitoring."""

    def __init__(self) -> None:
        self.calls: dict[str, deque[float]] = defaultdict(lambda: deque())
        self.lock = asyncio.Lock()

    async def record(self, service: str) -> None:
        """Record an API call."""
        async with self.lock:
            now = time.time()
            self.calls[service].append(now)
            # Keep only last 60 seconds of calls
            while self.calls[service] and self.calls[service][0] < now - 60:
                self.calls[service].popleft()

    async def get_rate(self, service: str) -> float:
        """Get calls per second for a service."""
        async with self.lock:
            calls = len(self.calls.get(service, []))
            return calls / 60.0  # Return calls per second


# Global rate limiters
_claude_limiter = RateLimiter(calls_per_minute=30)
_aws_limiter = RateLimiter(calls_per_minute=100)
_call_counter = CallCounter()


async def rate_limit_claude() -> None:
    """Enforce rate limit for Claude API calls."""
    await _claude_limiter.acquire()
    await _call_counter.record("claude")


async def rate_limit_aws() -> None:
    """Enforce rate limit for AWS API calls."""
    await _aws_limiter.acquire()
    await _call_counter.record("aws")


def rate_limited(
    service: str = "api",
    calls_per_minute: int = 30,
) -> Callable[[F], F]:
    """Decorator to rate limit async functions.

    Args:
        service: Service name for logging
        calls_per_minute: Max calls per minute
    """
    limiter = RateLimiter(calls_per_minute)

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            await limiter.acquire()
            await _call_counter.record(service)
            logger.debug(f"Rate limited call to {service}.{func.__name__}")
            return await func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator
