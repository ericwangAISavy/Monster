"""Model router: per-job order, escalation, fallback on failure, breaker and daily cap."""

import asyncio
from datetime import date

import pytest

from monster.providers.base import ProviderError
from monster.providers.claude_code import provider_for
from monster.providers.config import OllamaConfig
from monster.providers.router import BREAKER_FAILURES, Health, RoutingProvider, Spend


class Fake:
    def __init__(self, name, log, fail=None, cost=0.0, router=None):
        self.name, self.log, self.fail, self.cost, self.router = name, log, fail, cost, router

    async def _answer(self, method, model):
        self.log.append((self.name, method, model))
        if self.fail:
            raise ProviderError(self.fail)
        if self.cost:
            self.router._forward("provider_model_response", {"cost_usd": self.cost})
        return f"{self.name}:{method}"

    def __getattr__(self, method):
        async def call(model, *args, **kwargs):
            return await self._answer(method, model)
        return call


def router(local_fail=None, haiku_fail=None, haiku_cost=0.0, cap=2.0, spend=None, health=None):
    config = OllamaConfig(mode="supervised", provider="router", model="gemma4:26b",
                          local_server_confirmed=True, claude_code_confirmed=True,
                          paid_daily_cap_usd=cap)
    log = []
    r = RoutingProvider(config, health=health or Health(), spend=spend or Spend())
    r.factories = {"local": lambda: Fake("local", log, local_fail, router=r),
                   "haiku": lambda: Fake("haiku", log, haiku_fail, haiku_cost, router=r)}
    r.events = []
    r.diagnostic_callback = lambda event, details, content=None: r.events.append((event, details))
    return r, log


def test_jobs_go_to_their_default_models():
    r, log = router()
    assert asyncio.run(r.next_subtask("m", "goal", None, [], b"P", 10, 10)) == "haiku:next_subtask"
    assert asyncio.run(r.decide("m", "task", b"P", 10, 10)) == "local:decide"
    assert asyncio.run(r.verify_action("m", "t", {}, "e", b"P", 10, 10)) == "local:verify_action"
    assert log[1] == ("local", "decide", "gemma4:26b") and log[0][2] == "claude-haiku-5-5"


def test_harder_steps_escalate_to_haiku_and_drop_back():
    r, _ = router()
    r.hints = {"failures": 1}
    assert asyncio.run(r.decide("m", "task", b"P", 10, 10)) == "haiku:decide"
    r.hints = {"failures": 0}
    assert asyncio.run(r.decide("m", "task", b"P", 10, 10)) == "local:decide"
    r.hints = {"upload": True}
    assert asyncio.run(r.decide("m", "task", b"P", 10, 10)) == "haiku:decide"


def test_a_failed_model_falls_through_to_the_next_one():
    r, log = router(local_fail="Ollama request timed out; check model load and server capacity.")
    assert asyncio.run(r.decide("m", "task", b"P", 10, 10)) == "haiku:decide"
    assert [name for name, *_ in log] == ["local", "haiku"]
    routed = [d for e, d in r.events if e == "model_routed"][0]
    assert routed["model"] == "haiku" and routed["reason"] == "fallback"


def test_breaker_takes_a_failing_model_out_of_rotation():
    health = Health(clock=lambda: 0.0)
    r, log = router(local_fail="Cannot reach Ollama.", health=health)
    for _ in range(BREAKER_FAILURES):
        asyncio.run(r.decide("m", "task", b"P", 10, 10))
    log.clear()
    asyncio.run(r.decide("m", "task", b"P", 10, 10))
    assert [name for name, *_ in log] == ["haiku"]  # Ollama skipped while the breaker is open.


def test_daily_paid_limit_switches_to_local_only(tmp_path):
    spend = Spend(tmp_path / "spend.json", today=lambda: date(2026, 11, 17))
    r, log = router(haiku_cost=1.5, spend=spend)
    asyncio.run(r.next_subtask("m", "goal", None, [], b"P", 10, 10))
    asyncio.run(r.next_subtask("m", "goal", None, [], b"P", 10, 10))
    assert spend.spent_today() == 3.0
    log.clear()
    assert asyncio.run(r.next_subtask("m", "goal", None, [], b"P", 10, 10)) == "local:next_subtask"
    assert [name for name, *_ in log] == ["local"]
    assert Spend(tmp_path / "spend.json", today=lambda: date(2026, 11, 18)).spent_today() == 0.0


def test_when_every_model_fails_the_error_says_why():
    r, _ = router(local_fail="Cannot reach Ollama.", haiku_fail="Claude Code timed out.")
    with pytest.raises(ProviderError, match="Ollama model: Cannot reach Ollama.*Claude Haiku"):
        asyncio.run(r.decide("m", "task", b"P", 10, 10))


def test_config_and_factory():
    config = OllamaConfig(mode="supervised", provider="router", model="gemma4:26b",
                          local_server_confirmed=True)
    config.validate()
    assert isinstance(provider_for(config), RoutingProvider)
    with pytest.raises(ValueError, match="Auto routing needs at least one model"):
        OllamaConfig(mode="supervised", provider="router").validate()
    with pytest.raises(ValueError, match="daily limit"):
        OllamaConfig(paid_daily_cap_usd=-1).validate()


def test_a_timeout_takes_the_model_out_of_rotation_at_once():
    health = Health(clock=lambda: 0.0)
    r, log = router(local_fail="Ollama request timed out; check model load and server capacity.",
                    health=health)
    asyncio.run(r.decide("m", "task", b"P", 10, 10))
    log.clear()
    asyncio.run(r.decide("m", "task", b"P", 10, 10))
    assert [name for name, *_ in log] == ["haiku"]
