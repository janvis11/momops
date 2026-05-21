from __future__ import annotations

from types import SimpleNamespace

from momops.config import get_settings
from momops.models import InfraRequirement, ServiceType
from momops.optimizer import optimize
from momops.recipes import get_blueprint


class _FakeMessages:
    def create(self, **kwargs):
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    text='{"suggestions":[{"title":"Rightsize","description":"Use smaller nodes",'
                    '"monthly_savings_usd":12,"trade_off":"Less headroom","apply_command":"momops update"}]}'
                )
            ]
        )


class _FakeAnthropic:
    messages = _FakeMessages()


def test_optimizer_parses_claude_json(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr("momops.optimizer.anthropic.Anthropic", lambda: _FakeAnthropic())
    blueprint = get_blueprint(InfraRequirement(raw_intent="api", service_type=ServiceType.API))

    suggestions = optimize(blueprint, for_="cost")

    assert suggestions[0].title == "Rightsize"
    assert suggestions[0].monthly_savings_usd == 12
