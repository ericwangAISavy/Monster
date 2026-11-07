import asyncio
import json
import sqlite3
import subprocess
import sys

import pytest


def test_real_process_crash_retains_full_reservation_and_never_replays(tmp_path):
    code = """
import asyncio,os,sys
from pathlib import Path
from monster.mission.coordinator import MissionCoordinator
from monster.mission.plan_templates import editor_plan
from monster.mission.runner import FakeGuiSubtaskRunner
async def main():
    r=FakeGuiSubtaskRunner()
    async def crash(req):os._exit(79)
    r.run=crash
    async with MissionCoordinator(Path(sys.argv[1]),r) as c:
        plan=editor_plan(mission_id='crash-pilot')
        await c.start_scheduled(plan)
        await c.consent_and_continue('crash-pilot',c.scheduler.consent_request('crash-pilot'))
asyncio.run(main())
"""
    p = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=False, timeout=15)
    assert p.returncode == 79

    async def scenario():
        from monster.mission.coordinator import MissionCoordinator

        async with MissionCoordinator(tmp_path) as c:
            before = c.scheduler.usage("crash-pilot")
            assert before.input_charged == 10 and before.models_charged == 20
            assert before.active_ms == 310000 and before.starts == 1
            state = await c.consent_and_continue(
                "crash-pilot", c.scheduler.consent_request("crash-pilot")
            )
            assert state["status"] == "paused"
            assert c.runner.calls == []
            assert c.scheduler.usage("crash-pilot").input_charged == 10
            assert (await c.mission_report("crash-pilot"))["uncertain_delegation_count"] == 1

    asyncio.run(scenario())


def test_schema3_copy_preserves_prior_checkpoint_and_effect_audit(tmp_path):
    from test_graph import mission

    async def create():
        from monster.mission.coordinator import MissionCoordinator

        async with MissionCoordinator(tmp_path) as c:
            state = await c.start(mission("legacy", count=1))
            assert state["status"] == "waiting_approval"
            return c.store.connection.execute("SELECT count(*) FROM effect_audit").fetchone()[0]

    audit_count = asyncio.run(create())
    # Exact prior schema copy: remove only newly additive v4 tables; all v3 tables
    # and the separately persisted LangGraph checkpoint remain real SQLite data.
    conn = sqlite3.connect(tmp_path / "ledger.sqlite3")
    for name in ("mission_progress", "mission_reservations", "mission_schedules"):
        conn.execute(f"DROP TABLE {name}")
    conn.execute("PRAGMA user_version=3")
    conn.commit()
    conn.close()

    async def restore():
        from monster.mission.contracts import ApprovalDecision
        from monster.mission.coordinator import MissionCoordinator

        async with MissionCoordinator(tmp_path) as c:
            assert c.store.connection.execute("PRAGMA user_version").fetchone()[0] == 6
            assert (
                c.store.connection.execute("SELECT count(*) FROM effect_audit").fetchone()[0]
                == audit_count
            )
            state = await c.snapshot("legacy")
            a = c.store.get_approval(state["pending_approval_id"])
            result = await c.resume(
                "legacy",
                ApprovalDecision(
                    approval_id=a.approval_id,
                    effect_id=a.effect_id,
                    generation=a.generation,
                    decision="approve",
                ),
            )
            assert result["status"] == "completed"

    asyncio.run(restore())


def test_two_independent_simulations_share_no_counters(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan

        async with MissionCoordinator(tmp_path) as c:
            plans = [editor_plan(mission_id=x) for x in ("one", "two")]
            await asyncio.gather(*(c.start_scheduled(p) for p in plans))
            states = await asyncio.gather(
                *(
                    c.consent_and_continue(
                        p.mission.mission_id, c.scheduler.consent_request(p.mission.mission_id)
                    )
                    for p in plans
                )
            )
            assert all(s["status"] == "completed" for s in states)
            assert len(c.runner.calls) == 12
            assert all(c.scheduler.usage(p.mission.mission_id).input_confirmed == 6 for p in plans)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "scenario,status",
    [
        ("success", "completed"),
        ("pause_restart", "completed"),
        ("pure_timeout", "completed"),
        ("ambiguous", "paused"),
        ("budget", "partially_complete"),
        ("observe_only", "paused"),
    ],
)
def test_simulation_cli_has_explicit_provenance_and_no_native_input(tmp_path, scenario, status):
    p = subprocess.run(
        [
            sys.executable,
            "-m",
            "monster.mission.pilot",
            "--directory",
            str(tmp_path),
            "--scenario",
            scenario,
            "--approve-simulation",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout)
    assert result["simulation_only"]
    assert result["report"]["status"] == status
    assert result["report"]["native_input_transactions"] == 0
    assert result["report"]["provenance"] in ("simulated", "observe_only")


def test_phase4_progress_and_report_contain_no_canned_text(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import PILOT_TEXT, editor_plan

        async with MissionCoordinator(tmp_path) as c:
            plan = editor_plan()
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            report = await c.mission_report(mid)
            encoded = json.dumps(report)
            assert len(encoded) < 20000
            assert all(text not in encoded for text in PILOT_TEXT.values())
            content = b"".join(p.read_bytes() for p in tmp_path.glob("*.sqlite3*"))
            assert all(text.encode() not in content for text in PILOT_TEXT.values())
            assert (
                c.store.connection.execute("SELECT count(*) FROM mission_progress").fetchone()[0]
                <= 64
            )

    asyncio.run(scenario())
