"""Explicit fake-only strategic pilot; never produces a real design artifact."""

import argparse
import asyncio
import json
from pathlib import Path

from ..coordinator import MissionCoordinator
from ..runner import FakeGuiSubtaskRunner
from .planning import FakeStrategicPlanner
from .service import interpret_design_goal


async def run(
    directory,
    *,
    goal_text="Prepare a short technical design proposal.",
    replan=True,
    planner=None,
    roles=None,
):
    goal = interpret_design_goal(goal_text)
    if roles and roles.planner.provider != "fake":
        goal = goal.model_copy(update={"permitted_destinations": (roles.planner.destination_ref,)})
    runner = FakeGuiSubtaskRunner()
    if replan:
        runner.preflight_faults = ["ready", "ready", "blocked_preflight"]
    planner = planner or FakeStrategicPlanner()
    async with MissionCoordinator(directory, runner) as c:
        mid, consent = await c.strategy.prepare(goal, planner, roles=roles)
        await c.approve_strategic(mid, consent)
        state = await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
        if replan:
            if state["status"] != "paused" or len(runner.calls) != 2:
                raise ValueError("Expected safe pre-dispatch blocker after two simulated outcomes")
            consent = await c.strategy.propose_revision(mid, planner, "stage-3")
            await c.approve_strategic(mid, consent)
            # Reopen the real ledger and LangGraph checkpoints before any revised work.
        else:
            return await c.mission_report(mid)
    async with MissionCoordinator(directory) as c:
        state = await c.continue_mission(mid)
        if state["reason"] != "mission_consent_required":
            raise ValueError("Restart must require consent")
        await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
        return await c.mission_report(mid)


def main():
    parser = argparse.ArgumentParser(
        description="Phase5 six-stage fixture simulation; no native input or artifact creation"
    )
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--goal", default="Prepare a short technical design proposal.")
    parser.add_argument(
        "--scenario", choices=("success", "replan_restart"), default="replan_restart"
    )
    parser.add_argument(
        "--approve-simulation",
        action="store_true",
        required=True,
        help="Consent to the fixed synthetic plan, safe revision and restart",
    )
    parser.add_argument(
        "--planner-config", type=Path, help="Optional existing Ollama configuration; no fallback"
    )
    parser.add_argument(
        "--allow-planner-data",
        action="store_true",
        help="Consent to text-only reference metadata to that saved endpoint",
    )
    args = parser.parse_args()
    planner = roles = None
    if args.planner_config:
        if not args.allow_planner_data:
            parser.error("--planner-config requires --allow-planner-data")
        from monster.providers.config import load

        from ..scheduling import provider_binding
        from .contracts import ModelBinding, fake_roles
        from .planning import OllamaStrategicPlanner

        config = load(args.planner_config)
        binding = ModelBinding(
            provider="ollama",
            model_ref=config.model,
            destination_ref="ollama-" + provider_binding(config)[:16],
            configuration_digest=provider_binding(config),
            capabilities=("text_schema",),
        )
        roles = fake_roles().model_copy(update={"planner": binding, "planner_data_consent": True})
        planner = OllamaStrategicPlanner(config, binding)
    print(
        json.dumps(
            asyncio.run(
                run(
                    args.directory,
                    goal_text=args.goal,
                    replan=args.scenario == "replan_restart",
                    planner=planner,
                    roles=roles,
                )
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
