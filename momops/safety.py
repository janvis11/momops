"""
AGENT-4: Security Layer
Validates and enforces production safety defaults on every blueprint.
MomOps never deploys insecure infrastructure — period.
"""

from __future__ import annotations

import logging

from momops.models import ArchitectureBlueprint, SecurityManifest

logger = logging.getLogger(__name__)


class SecurityViolationError(Exception):
    """Raised when a blueprint fails the minimum security bar."""


def validate_blueprint(blueprint: ArchitectureBlueprint) -> None:
    """
    Validate that a blueprint meets minimum production security requirements.

    Raises:
        SecurityViolationError: If any required security control is missing

    This is called before every deploy — there are no exceptions or overrides.
    Mom doesn't ship insecure infrastructure.
    """
    manifest = blueprint.security
    violations: list[str] = []

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

    if violations:
        msg = "Security validation failed:\n" + "\n".join(f"  ✗ {v}" for v in violations)
        logger.error(msg)
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
