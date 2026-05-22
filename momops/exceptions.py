from __future__ import annotations

from typing import Any


class MomOpsError(Exception):
    """Base class for all MomOps domain errors."""

    def __init__(
        self, message: str, code: str = "UNKNOWN", context: dict[str, Any] | None = None
    ) -> None:
        """Initialize MomOps error with context.

        Args:
            message: Human-readable error message
            code: Machine-readable error code for programmatic handling
            context: Additional context for debugging
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.context = context or {}

    def __str__(self) -> str:
        """Return formatted error message."""
        base = f"[{self.code}] {self.message}"
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{base} ({context_str})"
        return base


class ConfigurationError(MomOpsError):
    """Raised when required runtime configuration is missing or invalid."""

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="CONFIG_ERROR", context=context)


class IntentParsingError(MomOpsError):
    """Raised when natural-language intent cannot be parsed safely."""

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="PARSE_ERROR", context=context)


class BudgetExceededError(MomOpsError):
    """Raised when a blueprint would exceed configured budget guardrails."""

    def __init__(
        self, estimated: float, limit: float, context: dict[str, Any] | None = None
    ) -> None:
        msg = f"Estimated cost ${estimated:.2f}/month exceeds budget limit ${limit:.2f}/month"
        context = context or {}
        context.update({"estimated_cost": estimated, "budget_limit": limit})
        super().__init__(msg, code="BUDGET_EXCEEDED", context=context)


class ProviderError(MomOpsError):
    """Raised when a cloud provider call fails."""

    def __init__(
        self, provider: str, operation: str, message: str, context: dict[str, Any] | None = None
    ) -> None:
        msg = f"{provider} API error during {operation}: {message}"
        context = context or {}
        context.update({"provider": provider, "operation": operation})
        super().__init__(msg, code="PROVIDER_ERROR", context=context)


class StateError(MomOpsError):
    """Raised when local deployment state cannot be read or written."""

    def __init__(self, operation: str, reason: str, context: dict[str, Any] | None = None) -> None:
        msg = f"State {operation} failed: {reason}"
        context = context or {}
        context.update({"operation": operation})
        super().__init__(msg, code="STATE_ERROR", context=context)


class ValidationError(MomOpsError):
    """Raised when input validation fails."""

    def __init__(
        self, field: str, value: Any, reason: str, context: dict[str, Any] | None = None
    ) -> None:
        msg = f"Validation failed for {field}: {reason}"
        context = context or {}
        context.update({"field": field, "value": str(value)})
        super().__init__(msg, code="VALIDATION_ERROR", context=context)


class DeploymentError(MomOpsError):
    """Raised when deployment fails."""

    def __init__(
        self, message: str, recoverable: bool = False, context: dict[str, Any] | None = None
    ) -> None:
        context = context or {}
        context["recoverable"] = recoverable
        super().__init__(message, code="DEPLOYMENT_ERROR", context=context)
        self.recoverable = recoverable


class RollbackError(MomOpsError):
    """Raised when automatic rollback fails."""

    def __init__(
        self, deployment_id: str, message: str, context: dict[str, Any] | None = None
    ) -> None:
        context = context or {}
        context["deployment_id"] = deployment_id
        super().__init__(message, code="ROLLBACK_ERROR", context=context)
