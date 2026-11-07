import asyncio
import json
import os
import subprocess
import sys
import time

import pytest
from test_graph import mission


def test_real_process_exit_restart_and_no_dispatch(tmp_path):
    code = """
import asyncio, os, time, sys
from pathlib import Path
from monster.mission.contracts import Mission, Subtask
from monster.mission.coordinator import MissionCoordinator
from monster.mission.runner import FakeGuiSubtaskRunner, SimulatedCrash
async def main():
    async with MissionCoordinator(Path(sys.argv[1]), FakeGuiSubtaskRunner({'s0':'crash'})) as c:
        try:
            await c.start(Mission(mission_id='mission1', objective_ref='demo', authorized=True,
                deadline_utc=time.time()+600, subtasks=[Subtask(subtask_id='s0',objective_ref='g',
                success_refs=['v'],category='read_only',requires_approval=False)]))
        except SimulatedCrash:
            os._exit(73)
asyncio.run(main())
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=False, timeout=15)
    assert result.returncode == 73

    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        runner = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, runner) as c:
            assert (await c.continue_mission("mission1"))["status"] == "paused"
            assert runner.calls == []
            assert c.store.effect("mission1", "s0").status == "uncertain"

    asyncio.run(scenario())


def test_stale_approval_and_cancel_while_waiting(tmp_path):
    async def scenario():
        from monster.mission.contracts import ApprovalDecision
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        runner = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, runner) as c:
            state = await c.start(mission(count=1))
            a = c.store.get_approval(state["pending_approval_id"])
            wrong = ApprovalDecision(
                approval_id=a.approval_id,
                effect_id="other",
                generation=a.generation,
                decision="approve",
            )
            with pytest.raises(ValueError, match="scope"):
                await c.resume("mission1", wrong)
            assert (await c.stop("mission1", cancel=True))["status"] == "cancelled"
            with pytest.raises(ValueError, match="awaiting"):
                await c.resume("mission1", wrong.model_copy(update={"effect_id": a.effect_id}))
            assert (await c.continue_mission("mission1"))["status"] == "cancelled"
            assert runner.calls == []

    asyncio.run(scenario())


def test_stop_restart_between_subtasks_preserves_progress(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator

        async with MissionCoordinator(tmp_path) as c:
            state = await c.start(mission())
            assert state["status"] == "waiting_approval"
            await c.stop("mission1")
        async with MissionCoordinator(tmp_path) as c:
            assert (await c.snapshot("mission1"))["status"] == "paused"
            state = await c.continue_mission("mission1")
            # A stop invalidates the outstanding approval. It cannot authorize input.
            assert state["status"] in ("paused", "partially_complete", "waiting_approval")
            assert c.runner.calls == []
            assert c.store.metrics("mission1")["verified"] == 2

    asyncio.run(scenario())


def test_explicit_reconciliation_then_continue_without_repeat(tmp_path):
    async def scenario():
        from monster.mission.contracts import TaskResult
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        runner = FakeGuiSubtaskRunner({"s0": "uncertain"})
        async with MissionCoordinator(tmp_path, runner) as c:
            await c.start(mission(review=False))
            e = c.store.effect("mission1", "s0")
            result = TaskResult(
                mission_id=e.mission_id,
                subtask_id=e.subtask_id,
                effect_id=e.effect_id,
                generation=e.generation,
                status="verified_success",
                evidence_ref="review-1",
                evidence_kind="human",
                reason="verified",
            )
            await c.reconcile("mission1", result)
            assert (await c.continue_mission("mission1"))["status"] == "completed"
            assert [r.subtask.subtask_id for r in runner.calls] == ["s0", "s1", "s2"]

    asyncio.run(scenario())


def test_result_lost_after_commit_recovered_without_repeat(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        async with MissionCoordinator(tmp_path) as c:
            # Simulate graph checkpoint lagging a committed effect observation.
            await c.start(mission(review=False, count=1))
            await c.graph.aupdate_state(
                c.config("mission1"),
                {"status": "running", "current_subtask_id": "s0"},
                as_node="preflight",
            )
        runner = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, runner) as c:
            assert (await c.continue_mission("mission1"))["status"] == "completed"
            assert runner.calls == []

    asyncio.run(scenario())


def test_safe_pruning_preserves_uncertain_and_active(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        now = [time.time()]
        async with MissionCoordinator(
            tmp_path, FakeGuiSubtaskRunner({"s0": "uncertain"}), clock=lambda: now[0]
        ) as c:
            await c.start(mission("uncertain", review=False, count=1))
            c.runner.outcomes.clear()
            await c.start(mission("done", review=False, count=1))
            await c.start(mission("waiting", count=1))
            await c.stop("uncertain", cancel=True)
            # terminal() is deliberately called to prove unresolved journal protection.
            c.store.terminal("uncertain")
            now[0] += 31 * 86400
            assert await c.prune() == ["done"]
            assert c.store.effect("uncertain", "s0").status == "uncertain"
            assert c.store.control("waiting")
            assert not (await c.graph.aget_state(c.config("done"))).values

    asyncio.run(scenario())


def test_no_network_input_qt_imports_or_sensitive_persistence(tmp_path):
    code = """
import asyncio, socket, sys, time
from pathlib import Path
# Fails if SDK tracing or orchestration attempts an external connection.
def forbidden(*args, **kwargs): raise AssertionError('network forbidden')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
from monster.mission.contracts import Mission, Subtask
from monster.mission.coordinator import MissionCoordinator
from monster.mission.runner import FakeGuiSubtaskRunner
async def main():
    async with MissionCoordinator(Path(sys.argv[1]),FakeGuiSubtaskRunner({'s0':'malformed'})) as c:
        s = await c.start(Mission(mission_id='privacy',objective_ref='demo',authorized=True,
            deadline_utc=time.time()+60,subtasks=[Subtask(subtask_id='s0',objective_ref='g',
            success_refs=['v'],category='read_only',requires_approval=False)]))
        assert s['status']=='paused'
    assert not any(n.startswith(('PySide6','monster.computer','monster.agent.controller',
                                'monster.providers.ollama')) for n in sys.modules)
asyncio.run(main())
"""
    env = os.environ | {
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGSMITH_API_KEY": "test-not-a-real-key",
    }
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        check=False,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    contents = b"".join(p.read_bytes() for p in tmp_path.glob("*.sqlite3*"))
    for secret in (b"must-not-persist", b"password", b"test-not-a-real-key"):
        assert secret not in contents


def test_non_fake_runner_cannot_be_enabled(tmp_path):
    from monster.mission.coordinator import MissionCoordinator

    with pytest.raises(ValueError, match="fake"):
        MissionCoordinator(tmp_path, object())


def test_transition_budget_and_deadline_stop_safely(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator

        async with MissionCoordinator(tmp_path) as c:
            state = await c.start(mission(review=False, max_transitions=8))
            assert state["status"] == "partially_complete"
            assert len(c.runner.calls) <= 1
            expired = mission("expired").model_copy(update={"deadline_utc": time.time() - 1})
            assert (await c.start(expired))["status"] == "partially_complete"

    asyncio.run(scenario())


def test_concurrent_resume_does_not_mutate_live_graph(tmp_path):
    async def scenario():
        from monster.mission.contracts import ApprovalDecision
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        runner = FakeGuiSubtaskRunner({"s0": "hang"})
        async with MissionCoordinator(tmp_path, runner) as c:
            state = await c.start(mission(count=1))
            a = c.store.get_approval(state["pending_approval_id"])
            decision = ApprovalDecision(
                approval_id=a.approval_id,
                effect_id=a.effect_id,
                generation=a.generation,
                decision="approve",
            )
            one = asyncio.create_task(c.resume("mission1", decision))
            await asyncio.wait_for(runner.started.wait(), 2)
            with pytest.raises(RuntimeError, match="running"):
                await c.continue_mission("mission1")
            await c.stop("mission1", cancel=True)
            await one
            assert len(runner.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "boundary",
    [
        "prepared",
        "approved",
        "dispatch_started",
        "uncertain",
        "observed_success",
        "observed_failure",
    ],
)
def test_restart_at_effect_boundaries(tmp_path, boundary):
    async def scenario():
        from monster.mission.contracts import Effect, TaskResult
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.graph import stable_id
        from monster.mission.runner import FakeGuiSubtaskRunner

        async with MissionCoordinator(tmp_path) as c:
            c.store.create(mission(review=False, count=1))
            e = Effect(
                effect_id=stable_id("mission1", "s0"),
                mission_id="mission1",
                subtask_id="s0",
                generation=1,
                status="prepared",
                target_ref="fake-app",
                category="read_only",
            )
            c.store.prepare(e)
            for stage in ["approved", "dispatch_started", boundary]:
                e = c.store.effect("mission1", "s0")
                if e.status == boundary:
                    break
                result = None
                if stage in ("observed_success", "observed_failure"):
                    result = TaskResult(
                        mission_id="mission1",
                        subtask_id="s0",
                        effect_id=e.effect_id,
                        generation=1,
                        status="verified_success"
                        if stage == "observed_success"
                        else "verified_failure",
                        evidence_ref="fake-evidence",
                        evidence_kind="fake",
                        reason="verified",
                    )
                c.store.transition(e, stage, result)
        runner = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, runner) as c:
            state = await c.continue_mission("mission1")
            if boundary in ("prepared", "approved"):
                assert state["status"] == "completed"
                assert len(runner.calls) == 1
            else:
                assert runner.calls == []
                assert state["status"] == {
                    "observed_success": "completed",
                    "observed_failure": "partially_complete",
                }.get(boundary, "paused")

    asyncio.run(scenario())


def test_scope_mismatch_and_evidenceless_success_rejected(tmp_path, monkeypatch):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        runner = FakeGuiSubtaskRunner()
        original = runner.run

        async def wrong(request):
            return (await original(request)).model_copy(update={"effect_id": "different"})

        monkeypatch.setattr(runner, "run", wrong)
        async with MissionCoordinator(tmp_path, runner) as c:
            assert (await c.start(mission(review=False)))["status"] == "paused"
            assert c.store.metrics("mission1")["verified"] == 0

    asyncio.run(scenario())


def test_cancelled_approval_mission_can_be_pruned(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator

        now = [time.time()]
        async with MissionCoordinator(tmp_path, clock=lambda: now[0]) as c:
            await c.start(mission(count=1))
            await c.stop("mission1", cancel=True)
            now[0] += 31 * 86400
            assert await c.prune() == ["mission1"]

    asyncio.run(scenario())


def test_overlapping_approval_calls_rejected_before_first_await(tmp_path, monkeypatch):
    async def scenario():
        from monster.mission.contracts import ApprovalDecision
        from monster.mission.coordinator import MissionCoordinator

        async with MissionCoordinator(tmp_path) as c:
            state = await c.start(mission(count=1))
            a = c.store.get_approval(state["pending_approval_id"])
            decision = ApprovalDecision(
                approval_id=a.approval_id,
                effect_id=a.effect_id,
                generation=a.generation,
                decision="approve",
            )
            original = c.snapshot
            entered, release = asyncio.Event(), asyncio.Event()
            calls = 0

            async def delayed(mid):
                nonlocal calls
                calls += 1
                if calls == 1:
                    entered.set()
                    await release.wait()
                return await original(mid)

            monkeypatch.setattr(c, "snapshot", delayed)
            first = asyncio.create_task(c.resume("mission1", decision))
            await asyncio.wait_for(entered.wait(), 2)
            try:
                with pytest.raises(RuntimeError, match="running"):
                    await c.resume("mission1", decision)
            finally:
                release.set()
                await asyncio.gather(first, return_exceptions=True)

    asyncio.run(scenario())


def test_demo_is_offline_and_fake_only(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "monster.mission.demo",
            "--directory",
            str(tmp_path),
            "--scenario",
            "approval",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["simulation_only"] is True
    assert output["before_restart"] == "waiting_approval"
    assert output["after_restart"] == "completed"
    assert output["metrics"]["dispatches"] == 3


def test_legacy_checkpoint_state_migrates_before_approval_resume(tmp_path):
    async def scenario():
        from monster.mission.contracts import ApprovalDecision
        from monster.mission.coordinator import MissionCoordinator

        async with MissionCoordinator(tmp_path) as c:
            state = await c.start(mission(count=1))
            a = c.store.get_approval(state["pending_approval_id"])
            await c.graph.aupdate_state(
                c.config("mission1"),
                {"schema_version": 0, "status": "waiting"},
                as_node="preflight",
            )
        async with MissionCoordinator(tmp_path) as c:
            state = await c.resume(
                "mission1",
                ApprovalDecision(
                    approval_id=a.approval_id,
                    effect_id=a.effect_id,
                    generation=a.generation,
                    decision="approve",
                ),
            )
            assert state["schema_version"] == 1
            assert state["status"] == "completed"

    asyncio.run(scenario())


def test_three_subtasks_survive_actual_process_restart(tmp_path):
    code = """
import asyncio, time, sys
from pathlib import Path
from monster.mission.contracts import Mission, Subtask
from monster.mission.coordinator import MissionCoordinator
async def main():
    async with MissionCoordinator(Path(sys.argv[1])) as c:
        state = await c.start(Mission(mission_id='mission1',objective_ref='demo',authorized=True,
            deadline_utc=time.time()+600,subtasks=[Subtask(subtask_id=f's{i}',objective_ref=f'g{i}',
            success_refs=[f'v{i}'],category='read_only',requires_approval=i==2,
            depends_on=[f's{i-1}'] if i else []) for i in range(3)]))
        assert state['status']=='waiting_approval'
        assert c.store.metrics('mission1')['verified']==2
asyncio.run(main())
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=False, timeout=15)
    assert result.returncode == 0

    async def scenario():
        from monster.mission.contracts import ApprovalDecision
        from monster.mission.coordinator import MissionCoordinator

        async with MissionCoordinator(tmp_path) as c:
            state = await c.snapshot("mission1")
            a = c.store.get_approval(state["pending_approval_id"])
            state = await c.resume(
                "mission1",
                ApprovalDecision(
                    approval_id=a.approval_id,
                    effect_id=a.effect_id,
                    generation=a.generation,
                    decision="approve",
                ),
            )
            assert state["status"] == "completed"
            assert [r.subtask.subtask_id for r in c.runner.calls] == ["s2"]
            assert c.store.metrics("mission1")["verified"] == 3

    asyncio.run(scenario())
