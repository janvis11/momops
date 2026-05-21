"""
MomApp — the central orchestrator.
This is what users interact with: mom("I need an API").preview() / .deploy()
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

from momops.budget import enforce_budget
from momops.config import get_settings
from momops.deployment import Deployer, deploy_blueprint
from momops.models import (
    ArchitectureBlueprint,
    DeployedApp,
    DeployEvent,
    InfraRequirement,
    OptimizationSuggestion,
    OptimizeFor,
)
from momops.optimizer import optimize
from momops.recipes import get_blueprint
from momops.safety import validate_blueprint
from momops.state import StateStore
from momops.understanding import parse_intent
from momops.utils.validators import validate_region

logger = logging.getLogger(__name__)


class MomApp:
    """
    Represents a cloud app that Mom is preparing to deploy.

    Typical flow:
        app = MomApp("I need a blog")
        cost = app.preview()        # see the bill
        app.validate_all()          # dry-run checks
        deployed = app.deploy()     # ship it
        suggestions = app.optimize(for_="cost")
    """

    def __init__(self, intent: str, region: str = "us-east-1", dry_run: bool = False) -> None:
        self._intent = intent
        self._region = validate_region(region)
        self._dry_run = dry_run or get_settings().dry_run
        self._requirement: InfraRequirement | None = None
        self._blueprint: ArchitectureBlueprint | None = None
        self._deployed: DeployedApp | None = None

    # ── Lazy resolution ────────────────────────────────────────────────────

    def _get_requirement(self) -> InfraRequirement:
        if self._requirement is None:
            logger.info("Parsing intent: %r", self._intent)
            self._requirement = parse_intent(self._intent, region=self._region)
        return self._requirement

    def _get_blueprint(self) -> ArchitectureBlueprint:
        if self._blueprint is None:
            req = self._get_requirement()
            self._blueprint = get_blueprint(req)
        return self._blueprint

    # ── Public API ──────────────────────────────────────────────────────────

    def preview(self) -> dict[str, Any]:
        """
        Show the estimated cost before deploying — always call this first.

        Returns:
            Dict with itemized cost breakdown (compute, db, networking, total)

        Example:
            >>> cost = app.preview()
            >>> print(cost)
            {'compute': 30.0, 'database': 52.0, 'networking': 10.0, 'total_monthly': 95.0, ...}
        """
        bp = self._get_blueprint()
        enforce_budget(bp, get_settings().budget_limit)
        return bp.cost.display()

    def deploy(self) -> DeployedApp:
        """
        Provision and deploy the infrastructure to AWS.
        Shows real-time progress. Rolls back automatically on failure.

        Returns:
            DeployedApp with endpoint URL and resource IDs
        """
        bp = self._get_blueprint()

        # Safety gate — validate before any AWS calls
        validate_blueprint(bp)
        enforce_budget(bp, get_settings().budget_limit)

        logger.info(
            "Deploying %s (dry_run=%s) — estimated $%.2f/mo",
            bp.recipe_name,
            self._dry_run,
            bp.cost.total_monthly,
        )

        self._deployed = asyncio.run(deploy_blueprint(bp, dry_run=self._dry_run))
        StateStore().upsert_app(self._deployed, status="dry_run" if self._dry_run else "deployed")
        return self._deployed

    async def deploy_async(self) -> AsyncGenerator[DeployEvent, None]:
        """
        Async variant of deploy() — yields progress events for streaming UIs.

        Usage:
            async for event in app.deploy_async():
                print(f"[{event.status}] {event.message}")
        """
        bp = self._get_blueprint()
        deployer = Deployer(bp, dry_run=self._dry_run)
        async for event in deployer.deploy():
            yield event
        self._deployed = deployer.result
        StateStore().upsert_app(self._deployed, status="dry_run" if self._dry_run else "deployed")

    def optimize(self, for_: str = "cost") -> list[OptimizationSuggestion]:
        """
        Get AI-powered optimization suggestions.

        Args:
            for_: "cost" | "performance" | "reliability"

        Returns:
            List of actionable OptimizationSuggestion
        """
        bp = self._get_blueprint()
        enforce_budget(bp, get_settings().budget_limit)
        return optimize(bp, for_=OptimizeFor(for_))

    def dry_run(self) -> None:
        """
        Validate everything without touching real AWS resources.
        Raises on validation failure. Prints pass/fail for each check.
        """
        original = self._dry_run
        self._dry_run = True
        try:
            result = self.deploy()
            logger.info("Dry run passed ✓ — ready to deploy (app_id=%s)", result.app_id)
        finally:
            self._dry_run = original

    def validate_all(self) -> dict[str, bool]:
        """
        Run all pre-deploy validation checks.

        Returns:
            Dict of check_name → passed
        """
        bp = self._get_blueprint()
        checks: dict[str, bool] = {}

        # Intent parsed
        checks["intent_parsed"] = self._requirement is not None

        # Blueprint generated
        checks["blueprint_generated"] = True

        # Security manifest valid
        try:
            validate_blueprint(bp)
            checks["security_valid"] = True
        except Exception:
            checks["security_valid"] = False

        # Cost below budget hint (if provided)
        if bp.requirement.budget_hint is not None:
            checks["within_budget"] = bp.cost.total_monthly <= bp.requirement.budget_hint
        else:
            checks["within_budget"] = True

        return checks

    def load_test(self, users: int = 1000, duration_minutes: int = 5) -> dict[str, Any]:
        """
        Stub: simulate a load test recommendation.
        In production, this would trigger k6 or Locust on a staging env.
        """
        bp = self._get_blueprint()
        return {
            "recommended_tool": "k6",
            "target_rps": users // duration_minutes,
            "instance_type": next(
                (s.instance_type for s in bp.aws_services if s.service == "EC2"), "t3.medium"
            ),
            "expected_p99_latency_ms": 200 if users < 10000 else 500,
            "note": "Run: k6 run --vus {users} --duration {duration_minutes}m load_test.js",
        }

    def security_scan(self) -> dict[str, bool]:
        """Return the applied security controls as a pass/fail dict."""
        bp = self._get_blueprint()
        manifest = bp.security
        return {
            "ssl_tls": manifest.ssl_enabled,
            "automated_backups": manifest.backup_enabled,
            "vpc_isolation": manifest.vpc_isolated,
            "iam_least_privilege": manifest.iam_least_privilege,
            "monitoring": manifest.monitoring_enabled,
            "ddos_protection": manifest.ddos_protection,
            "encryption_at_rest": manifest.encryption_at_rest,
            "encryption_in_transit": manifest.encryption_in_transit,
        }

    def project_cost(self, months: int = 12) -> dict[str, float]:
        """Return cost projection over N months."""
        bp = self._get_blueprint()
        monthly = bp.cost.total_monthly
        return {
            "monthly_usd": monthly,
            "projected_total_usd": round(monthly * months, 2),
            "months": months,
            "savings_if_optimized_usd": round(bp.cost.savings_available * months, 2),
        }

    def __repr__(self) -> str:
        return f"MomApp(intent={self._intent!r}, region={self._region!r})"


# ── Conversational mode ─────────────────────────────────────────────────────


class MomSession:
    """
    Interactive conversational session with Mom.
    Uses Claude to maintain conversation context and guide the user.
    """

    def __init__(self) -> None:
        self._history: list[dict[str, str]] = []
        self._app: MomApp | None = None

    def chat(self, user_message: str) -> str:
        """Send a message to Mom and get a response."""
        import anthropic

        from momops.utils.prompts import MOM_TALK_SYSTEM

        self._history.append({"role": "user", "content": user_message})

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=MOM_TALK_SYSTEM,
            messages=self._history,
        )

        reply = response.content[0].text
        self._history.append({"role": "assistant", "content": reply})
        return reply

    def run_interactive(self) -> None:
        """Run a blocking interactive REPL."""
        print("\nMom: What do you need, honey?\n")
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nMom: Take care! 👋")
                break

            if not user_input:
                continue
            if user_input.lower() in {"quit", "exit", "bye"}:
                print("Mom: Take care! 👋")
                break

            reply = self.chat(user_input)
            print(f"\nMom: {reply}\n")


def talk_to_mom() -> MomSession:
    """
    Start an interactive conversational session with Mom.

    Example:
        from momops import talk_to_mom
        session = talk_to_mom()
        session.run_interactive()
    """
    return MomSession()
