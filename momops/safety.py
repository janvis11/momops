"""
AGENT-4: Security Layer
Validates and enforces production safety defaults on every blueprint.
MomOps never deploys insecure infrastructure — period.
"""

from __future__ import annotations

import logging
from typing import Any

from momops.models import ArchitectureBlueprint, SecurityManifest

logger = logging.getLogger(__name__)


class SecurityViolationError(Exception):
    """Raised when a blueprint fails the minimum security bar."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        msg = "Security validation failed:\n" + "\n".join(f"  ✗ {v}" for v in violations)
        super().__init__(msg)


def validate_blueprint(blueprint: ArchitectureBlueprint) -> dict[str, Any]:
    """
    Validate that a blueprint meets minimum production security requirements.

    Args:
        blueprint: Architecture blueprint to validate

    Returns:
        Dict with validation results including warnings

    Raises:
        SecurityViolationError: If any required security control is missing

    This is called before every deploy — there are no exceptions or overrides.
    Mom doesn't ship insecure infrastructure.
    """
    manifest = blueprint.security
    violations: list[str] = []
    warnings: list[str] = []

    # ── Critical Controls ──────────────────────────────────────────────────

    if not manifest.ssl_enabled:
        violations.append("SSL/TLS must be enabled — no plain HTTP in production")

    if not manifest.backup_enabled:
        violations.append("Automated backups must be enabled")

    if not manifest.vpc_isolated:
        violations.append("Database must be in a private VPC subnet")

    if not manifest.iam_least_privilege:
        violations.append("IAM must use least-privilege roles — no root keys")

    if not manifest.monitoring_enabled:
        violations.append("CloudWatch monitoring must be enabled")

    # ── Warnings (non-blocking) ────────────────────────────────────────────

    if manifest.backup_retention_days < 7:
        warnings.append(f"Backup retention is only {manifest.backup_retention_days} days (recommend 30+)")

    if not manifest.encryption_at_rest:
        warnings.append("Encryption at rest is disabled")

    if not manifest.encryption_in_transit:
        warnings.append("Encryption in transit is disabled")

    if manifest.multi_az is False:
        warnings.append("Multi-AZ is not enabled (single point of failure)")

    if violations:
        logger.error("Critical security violations detected")
        for v in violations:
            logger.error(f"  ✗ {v}")
        raise SecurityViolationError(violations)

    if warnings:
        logger.warning("Security warnings detected:")
        for w in warnings:
            logger.warning(f"  ⚠ {w}")

    return {
        "valid": True,
        "violations": violations,
        "warnings": warnings,
    }


def apply_security_defaults(blueprint: ArchitectureBlueprint) -> ArchitectureBlueprint:
    """
    Apply MomOps security defaults to a blueprint.

    This ensures every deployed infrastructure has baseline protections.
    """
    manifest = blueprint.security

    # Enforce defaults if not already set
    if not manifest.ssl_enabled:
        logger.info("Enabling SSL/TLS (security default)")
        manifest.ssl_enabled = True

    if not manifest.backup_enabled:
        logger.info("Enabling automated backups (security default)")
        manifest.backup_enabled = True

    if not manifest.vpc_isolated:
        logger.info("Isolating database in private VPC (security default)")
        manifest.vpc_isolated = True

    if not manifest.iam_least_privilege:
        logger.info("Applying least-privilege IAM (security default)")
        manifest.iam_least_privilege = True

    if not manifest.monitoring_enabled:
        logger.info("Enabling CloudWatch monitoring (security default)")
        manifest.monitoring_enabled = True

    if not manifest.encryption_at_rest:
        logger.info("Enabling encryption at rest (security default)")
        manifest.encryption_at_rest = True

    if not manifest.encryption_in_transit:
        logger.info("Enabling encryption in transit (security default)")
        manifest.encryption_in_transit = True

    return blueprint
        raise SecurityViolationError(msg)

    logger.info("Security validation passed (%d rules applied)", len(manifest.applied_rules))


def enforce_defaults(manifest: SecurityManifest) -> SecurityManifest:
    """
    Return a new manifest with all required defaults enforced.
    Call this to 'fix up' a partially configured manifest before validation.
    """
    current = manifest.model_dump()

    # Force all critical fields to True — non-negotiable
    forced = {
        "ssl_enabled": True,
        "backup_enabled": True,
        "vpc_isolated": True,
        "iam_least_privilege": True,
        "monitoring_enabled": True,
        "encryption_at_rest": True,
        "encryption_in_transit": True,
    }
    current.update(forced)

    # Append any new rules to the existing list
    new_rules = [k.replace("_", " ").title() for k, v in forced.items() if v]
    existing = set(current.get("applied_rules", []))
    current["applied_rules"] = list(existing | set(new_rules))

    return SecurityManifest.model_validate(current)
