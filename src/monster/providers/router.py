"""Model router: one provider that picks a model per call.

Models: the local Ollama model (free) and Claude Haiku through Claude Code (paid, capped).
Each call has a job (orchestrate, step, check, probe) with an ordered list of models:

    orchestrate  Haiku -> Gemma        (Haiku is the default orchestrator; never Sonnet)
    step         Gemma -> Haiku        (escalated to Haiku first when the work is harder)
    check        Gemma -> Haiku
    probe        Gemma -> Haiku

A call falls through to the next model when one times out, cannot be reached or returns
an invalid reply. Repeated failures open a short circuit breaker for that model. Paid
models stop being used for the day once the daily spending cap is reached.
"""

import json
import time
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from monster.providers.base import ProviderError
from monster.providers.claude_code import ClaudeCodeProvider
from monster.providers.ollama import OllamaProvider

HAIKU = "claude-haiku-5-5"
BREAKER_FAILURES = 2  # Consecutive failures that take a model out of rotation...
BREAKER_SECONDS = 120  # ...for this long.

ROLES = {
    "next_subtask": "orchestrate", "plan": "orchestrate", "assess": "orchestrate",
    "decide": "step",
    "verify_action": "check", "verify_completion": "check",
    "verify_drawing_target": "check", "verify_draft_target": "check",
}
ORDER = {
    "orchestrate": ("haiku", "local"),
    "step": ("local", "haiku"),
    "check": ("local", "haiku"),
    "probe": ("local", "haiku"),
}


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    paid: bool


MODELS = {
    "local": Model("local", "Ollama model", paid=False),
    "haiku": Model("haiku", "Claude Haiku", paid=True),
}


class Health:
    """Per-model failure streaks and breaker state, shared by every router call."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.failures = {}
        self.open_until = {}

    def available(self, model_id):
        return self.clock() >= self.open_until.get(model_id, 0.0)

    def failed(self, model_id, unreachable=False):
        # A timeout or unreachable server costs a full timeout per call, so one is enough.
        self.failures[model_id] = self.failures.get(model_id, 0) + (BREAKER_FAILURES if unreachable else 1)
        if self.failures[model_id] >= BREAKER_FAILURES:
            self.open_until[model_id] = self.clock() + BREAKER_SECONDS
            self.failures[model_id] = 0

    def succeeded(self, model_id):
        self.failures[model_id] = 0
        self.open_until.pop(model_id, None)


class Spend:
    """Paid-model spending for today, kept in a small JSON file (or memory when no path)."""

    def __init__(self, path=None, today=date.today):
        self.path = Path(path) if path else None
        self.today = today
        self.memory = {}

    def _load(self):
        if self.path is None:
            return dict(self.memory)
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def spent_today(self):
        return float(self._load().get(self.today().isoformat(), 0.0))

    def add(self, usd):
        if not usd or usd <= 0:
            return
        data = self._load()
        key = self.today().isoformat()
        data = {key: round(float(data.get(key, 0.0)) + float(usd), 6)}  # Only today matters.
        if self.path is None:
            self.memory = data
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            self.memory = data


HEALTH = Health()
SPEND = Spend()  # The app points this at a file beside its settings.


def is_fallback_error(error):
    """Errors where another model may well succeed (as opposed to a refused permission)."""
    text = str(error)
    return not text.startswith("Confirm ")


class RoutingProvider(OllamaProvider):
    """Same interface as the single-model providers; routes each call to a model."""

    def __init__(self, config, transport=None, diagnostic_callback=None, factories=None,
                 health=None, spend=None):
        super().__init__(config, transport, diagnostic_callback)
        self.health = health or HEALTH
        self.spend = spend or SPEND
        self.hints = {}  # Set by the controller: how hard the current work is.
        self.factories = factories or {"local": self._local, "haiku": self._haiku}

    # ---- models ------------------------------------------------------------------
    def _local(self):
        if not (self.config.model and self.config.local_server_confirmed):
            return None
        return OllamaProvider(replace(self.config, provider="ollama"), self.transport,
                              self._forward)

    def _haiku(self):
        if not self.config.claude_code_confirmed:
            return None
        return ClaudeCodeProvider(
            replace(self.config, provider="claude_code", claude_model=HAIKU,
                    claude_check_model=HAIKU),
            diagnostic_callback=self._forward)

    def _forward(self, event, details, content=None):
        if event == "provider_model_response" and details.get("cost_usd"):
            self.spend.add(details["cost_usd"])
        self.diagnostic(event, details, content)

    def may_transmit_screenshots(self):
        return bool(self.config.local_server_confirmed or self.config.claude_code_confirmed)

    # ---- routing -------------------------------------------------------------------
    def harder(self):
        """Escalate a step to Haiku when the work has visibly become harder."""
        hints = self.hints or {}
        return bool(hints.get("failures", 0) >= 1 or hints.get("subtask_retry")
                    or hints.get("orchestration_retry") or hints.get("drawing")
                    or hints.get("large_context") or hints.get("upload"))

    def order(self, role):
        order = list(ORDER[role])
        if role in {"step", "check"} and self.harder():
            order = ["haiku", "local"]
        return order

    def _usable(self, model_id):
        model = MODELS[model_id]
        if not self.health.available(model_id):
            return None, "circuit breaker open"
        if model.paid and self.spend.spent_today() >= self.config.paid_daily_cap_usd:
            return None, "daily paid limit reached"
        provider = self.factories[model_id]()
        if provider is None:
            return None, "not configured"
        return provider, None

    async def _route(self, role, method, model_arg, *args, **kwargs):
        errors, skipped = [], []
        for position, model_id in enumerate(self.order(role)):
            provider, reason = self._usable(model_id)
            if provider is None:
                skipped.append(f"{MODELS[model_id].label}: {reason}")
                continue
            why = "primary" if position == 0 else ("escalated" if self.harder() and role != "orchestrate"
                                                   else "fallback")
            started = time.monotonic()
            try:
                model_name = self.config.model if model_id == "local" else HAIKU
                result = await getattr(provider, method)(model_name, *args, **kwargs)
            except ProviderError as exc:
                if not is_fallback_error(exc):
                    raise
                text = str(exc).lower()
                self.health.failed(model_id, unreachable=any(
                    word in text for word in ("timed out", "cannot reach", "could not start", "not found")))
                errors.append(f"{MODELS[model_id].label}: {exc}")
                self.diagnostic("model_route_failed", {"role": role, "model": model_id,
                                                       "elapsed_s": time.monotonic() - started},
                                {"error": str(exc)[:300]})
                continue
            self.health.succeeded(model_id)
            self.diagnostic("model_routed", {
                "role": role, "model": model_id, "reason": why if not errors else "fallback",
                "elapsed_s": time.monotonic() - started,
                "spent_today_usd": round(self.spend.spent_today(), 4)})
            return result
        detail = "; ".join(errors + skipped) or "no model configured"
        raise ProviderError("No model could handle this request (" + detail + ").")

    # ---- the provider interface ------------------------------------------------------
    async def probe(self, model):
        return await self._route("probe", "probe", model)

    async def decide(self, model, *args, **kwargs):
        return await self._route("step", "decide", model, *args, **kwargs)

    async def next_subtask(self, model, *args, **kwargs):
        return await self._route("orchestrate", "next_subtask", model, *args, **kwargs)

    async def plan(self, model, *args, **kwargs):
        return await self._route("orchestrate", "plan", model, *args, **kwargs)

    async def assess(self, model, *args, **kwargs):
        return await self._route("orchestrate", "assess", model, *args, **kwargs)

    async def verify_action(self, model, *args, **kwargs):
        return await self._route("check", "verify_action", model, *args, **kwargs)

    async def verify_completion(self, model, *args, **kwargs):
        return await self._route("check", "verify_completion", model, *args, **kwargs)

    async def verify_drawing_target(self, model, *args, **kwargs):
        return await self._route("check", "verify_drawing_target", model, *args, **kwargs)

    async def verify_draft_target(self, model, *args, **kwargs):
        return await self._route("check", "verify_draft_target", model, *args, **kwargs)
