"""Future async adapter seam; Phase 1 ships only deterministic, input-free simulation."""

import asyncio
from typing import Protocol

from .contracts import GuiSubtaskRequest, TaskResult


class GuiSubtaskRunner(Protocol):
    async def run(self, request: GuiSubtaskRequest) -> TaskResult: ...
    async def cancel(self, mission_id: str, subtask_id: str) -> None: ...


class SimulatedCrash(BaseException):
    """Test-only hard-failure scenario, deliberately outside normal error handling."""


class FakeGuiSubtaskRunner:
    """Every success is synthetic evidence, never a claim about a real application."""

    def __init__(self, outcomes: dict[str, str] | None = None):
        self.outcomes = dict(outcomes or {})
        self.calls: list[GuiSubtaskRequest] = []
        self.started = asyncio.Event()

    async def run(self, request: GuiSubtaskRequest) -> TaskResult:
        self.calls.append(request)
        self.started.set()
        outcome = self.outcomes.get(request.subtask.subtask_id, "verified_success")
        if outcome == "crash":
            raise SimulatedCrash("Simulated dispatch crash")
        if outcome == "hang":
            await asyncio.Event().wait()
        if outcome == "timeout":
            raise TimeoutError
        if outcome == "error":
            raise RuntimeError("Private error detail must not persist")
        if outcome == "malformed":
            return {"password": "must-not-persist"}
        verified = outcome in ("verified_success", "verified_failure")
        return TaskResult(
            mission_id=request.mission_id,
            subtask_id=request.subtask.subtask_id,
            effect_id=request.effect_id,
            generation=request.generation,
            status=outcome,
            evidence_ref=f"fake:{request.effect_id}" if verified else None,
            evidence_kind="fake" if verified else "none",
            reason="verified" if verified else "not_observed",
        )

    async def cancel(self, mission_id: str, subtask_id: str) -> None:
        # The coordinator cancels the active awaitable; there is no worker/input to clean.
        pass
