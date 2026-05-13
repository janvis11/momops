"""Typed exception hierarchy for MomOps."""

from __future__ import annotations


class MomOpsError(Exception):
    """Base class for all MomOps domain errors."""


class ConfigurationError(MomOpsError):
    """Raised when required runtime configuration is missing or invalid."""


class IntentParsingError(MomOpsError):
    """Raised when natural-language intent cannot be parsed safely."""


class BudgetExceededError(MomOpsError):
    """Raised when a blueprint would exceed configured budget guardrails."""


class ProviderError(MomOpsError):
    """Raised when a cloud provider call fails."""


class StateError(MomOpsError):
    """Raised when local deployment state cannot be read or written."""
