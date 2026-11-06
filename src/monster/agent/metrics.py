"""Bounded, in-memory elapsed waits. No prompts, actions, images or provider payloads."""

import time
from typing import ClassVar


class OperationTimings:
    LABELS: ClassVar[dict[str, str]] = {
        "probe": "Model compatibility check",
        "planning": "Planning",
        "recheck": "Scene recheck",
        "verification": "Visual verification",
        "startup": "Input startup",
        "dispatch": "Input dispatch",
        "cleanup": "Input cleanup",
    }
    OUTCOMES = frozenset({"completed", "failed", "cancelled"})

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset()

    def reset(self):
        self.pending = {}
        self.totals = {}

    def begin(self, token, stage):
        if stage not in self.LABELS:
            raise ValueError("Unknown timing stage.")
        self.pending[token] = (stage, self.clock())

    def finish(self, token, outcome):
        if outcome not in self.OUTCOMES:
            raise ValueError("Unknown timing outcome.")
        pending = self.pending.pop(token, None)
        if pending is None:
            return
        stage, started = pending
        elapsed = max(0.0, self.clock() - started)
        totals = self.totals.setdefault(
            stage, {"completed": 0, "failed": 0, "cancelled": 0, "total": 0.0, "max": 0.0}
        )
        totals[outcome] += 1
        totals["total"] += elapsed
        totals["max"] = max(totals["max"], elapsed)

    def cancel_pending(self):
        for token in tuple(self.pending):
            self.finish(token, "cancelled")

    def report(self):
        lines = [
            "Elapsed operation waits (not proof of action success):",
            "Cancelled waits end at cancellation; totals/max include them.",
        ]
        for stage, label in self.LABELS.items():
            if stage not in self.totals:
                continue
            totals = self.totals[stage]
            lines.append(
                f"{label}: completed {totals['completed']}, failed {totals['failed']}, "
                f"cancelled {totals['cancelled']}; total {totals['total']:.3f}s; "
                f"max {totals['max']:.3f}s"
            )
        return "\n".join(lines)
