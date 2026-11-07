"""Observe-only human handoff. No screenshot/input engine or automatic retry."""

from dataclasses import dataclass
from typing import Protocol

from .contracts import Effect


@dataclass(frozen=True)
class ReconciliationResult:
    effect_id: str
    status: str
    source: str = "human_review_required"


class EffectReconciler(Protocol):
    async def reconcile(self, effect: Effect, *, workspace_id: str) -> ReconciliationResult: ...


class HumanEffectReconciler:
    def __init__(self, journal):
        self.journal = journal

    async def reconcile(self, effect, *, workspace_id):
        return ReconciliationResult(
            effect.effect_id,
            "human_review_required"
            if (workspace_id == effect.workspace_id)
            else "workspace_unavailable",
        )

    def attest(self, effect_id, outcome):
        return self.journal.attest(effect_id, outcome, observation_ref="user-inspection")
