"""Offline scenarios, explicitly separate from the GUI and real computer tools."""

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from .contracts import ApprovalDecision, Mission, Subtask
from .coordinator import MissionCoordinator
from .runner import FakeGuiSubtaskRunner, SimulatedCrash


async def scenario(directory: Path, kind: str) -> dict:
    mid = "demo-" + uuid4().hex
    mission = Mission(
        mission_id=mid,
        objective_ref="synthetic-demo",
        authorized=True,
        deadline_utc=time.time() + 600,
        subtasks=[
            Subtask(
                subtask_id=f"s{i}",
                objective_ref=f"synthetic-{i}",
                success_refs=[f"fake-visible-{i}"],
                depends_on=[f"s{i - 1}"] if i else [],
                category="local_reversible",
                requires_approval=kind == "approval" and i == 2,
            )
            for i in range(3)
        ],
    )
    async with MissionCoordinator(
        directory, FakeGuiSubtaskRunner({"s0": "crash"} if kind == "crash" else None)
    ) as first:
        try:
            state = await first.start(mission)
            before = state["status"]
        except SimulatedCrash:
            before = "dispatch_started"
    async with MissionCoordinator(directory) as second:
        if kind == "approval":
            state = await second.snapshot(mid)
            approval = second.store.get_approval(state["pending_approval_id"])
            # Only this explicitly selected fake scenario manufactures a review decision.
            state = await second.resume(
                mid,
                ApprovalDecision(
                    approval_id=approval.approval_id,
                    effect_id=approval.effect_id,
                    generation=approval.generation,
                    decision="approve",
                ),
            )
        else:
            state = await second.continue_mission(mid)
        return {
            "simulation_only": True,
            "mission_id": mid,
            "before_restart": before,
            "after_restart": state["status"],
            "reason": state["reason"],
            "metrics": second.store.metrics(mid),
        }


def main():
    parser = argparse.ArgumentParser(
        description="Fake-only durable mission scenarios; no OS input."
    )
    parser.add_argument(
        "--directory",
        type=Path,
        required=True,
        help="Local private SQLite state directory (metadata only).",
    )
    parser.add_argument("--scenario", choices=("approval", "crash"), default="approval")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(scenario(args.directory, args.scenario)), indent=2))


if __name__ == "__main__":
    main()
