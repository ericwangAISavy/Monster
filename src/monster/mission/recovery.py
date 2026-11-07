"""Pure pre-dispatch recovery only. GUI handoffs are never replayed."""

import random
from dataclasses import dataclass


class PreDispatchFault(RuntimeError):
    def __init__(self, kind):
        self.kind = kind
        super().__init__("Pre-dispatch operation unavailable")


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason_code: str


def decide(kind, *, may_have_dispatched=False, attempt=1, max_attempts=2):
    if may_have_dispatched:
        return RecoveryDecision("reconcile_effect", "possibly_dispatched")
    if kind == "transient_pre_dispatch" and attempt < max_attempts:
        return RecoveryDecision("reobserve", "transient_pre_dispatch")
    if kind in ("blocked_preflight", "stale_scene"):
        return RecoveryDecision("pause_for_user", "blocked_preflight")
    return RecoveryDecision("stop_incomplete", "no_progress")


def backoff(attempt, *, random_value=None):
    jitter = random.random() if random_value is None else random_value
    return min(2.0, 0.05 * 2 ** (attempt - 1)) * (0.75 + 0.25 * jitter)
