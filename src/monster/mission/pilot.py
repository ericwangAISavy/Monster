"""Explicit simulation-only Phase4 scenarios. Never constructs a GUI/input runner."""

import argparse
import asyncio
import json
from pathlib import Path

from .coordinator import MissionCoordinator
from .plan_templates import editor_plan
from .runner import FakeGuiSubtaskRunner, SimulatedCrash
from .scheduling import MissionBudget


async def run(directory, scenario):
    runner = FakeGuiSubtaskRunner(
        {"pilot-a": "wait_success"}
        if scenario == "pause_restart"
        else ({"pilot-a": "crash"} if scenario == "ambiguous" else None)
    )
    if scenario == "pure_timeout":
        runner.preflight_faults = ["transient_pre_dispatch", "ready"]
    plan = editor_plan(
        mode="observe_only" if scenario == "observe_only" else "simulation",
        budget=MissionBudget(max_subtask_starts=2) if scenario == "budget" else None,
    )
    mid = plan.mission.mission_id
    async with MissionCoordinator(directory, runner) as c:
        await c.start_scheduled(plan)
        if scenario == "pause_restart":
            task = asyncio.create_task(
                c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            )
            await runner.started.wait()
            await c.request_pause(mid)
            runner.release.set()
            await task
        else:
            try:
                await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            except SimulatedCrash:
                pass
        report = await c.mission_report(mid)
    if scenario in ("pause_restart", "ambiguous"):
        async with MissionCoordinator(directory) as c:
            await c.continue_mission(mid)
            await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            report = await c.mission_report(mid)
    return {"simulation_only": True, "scenario": scenario, "report": report}


def main():
    parser = argparse.ArgumentParser(
        description="Phase4 fake-only mission pilot; zero desktop input."
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--scenario",
        choices=("success", "pause_restart", "pure_timeout", "ambiguous", "budget", "observe_only"),
        default="success",
    )
    parser.add_argument(
        "--approve-simulation",
        action="store_true",
        required=True,
        help="Explicitly consent to this known synthetic scenario, including restart review.",
    )
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.directory, args.scenario)), indent=2))


if __name__ == "__main__":
    main()
