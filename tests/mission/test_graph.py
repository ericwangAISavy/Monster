import asyncio
import time

import pytest


def mission(mid="mission1", *, review=True, count=3, **kwargs):
    from monster.mission.contracts import Mission, Subtask

    return Mission(
        mission_id=mid,
        objective_ref="demo",
        authorized=True,
        deadline_utc=time.time() + 600,
        subtasks=[
            Subtask(
                subtask_id=f"s{i}",
                objective_ref=f"objective-{i}",
                success_refs=[f"visible-{i}"],
                depends_on=[f"s{i - 1}"] if i else [],
                category="local_reversible",
                requires_approval=review and i == count - 1,
            )
            for i in range(count)
        ],
        **kwargs,
    )


def test_three_subtasks_restart_approval(tmp_path):
    async def scenario():
        from monster.mission.contracts import ApprovalDecision
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        first = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, first) as c:
            state = await c.start(mission())
            assert state["status"] == "waiting_approval"
            assert [e.status for e in c.store.effects("mission1")] == [
                "observed_success",
                "observed_success",
                "prepared",
            ]
            aid = state["pending_approval_id"]
            approval = c.store.get_approval(aid)
            assert len(first.calls) == 2
        second = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, second) as c:
            assert (await c.snapshot("mission1"))["status"] == "waiting_approval"
            state = await c.resume(
                "mission1",
                ApprovalDecision(
                    approval_id=aid,
                    effect_id=approval.effect_id,
                    generation=approval.generation,
                    decision="approve",
                ),
            )
            assert state["status"] == "completed"
            assert [r.subtask.subtask_id for r in second.calls] == ["s2"]
            assert c.store.metrics("mission1")["verified"] == 3

    asyncio.run(scenario())


def test_crash_after_dispatch_does_not_replay(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner, SimulatedCrash

        async with MissionCoordinator(tmp_path, FakeGuiSubtaskRunner({"s0": "crash"})) as c:
            with pytest.raises(SimulatedCrash):
                await c.start(mission(review=False))
            assert c.store.effect("mission1", "s0").status == "dispatch_started"
        runner = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, runner) as c:
            state = await c.continue_mission("mission1")
            assert state["status"] == "paused"
            assert c.store.effect("mission1", "s0").status == "uncertain"
            assert runner.calls == []
            state = await c.continue_mission("mission1")
            assert state["status"] == "paused"
            assert runner.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome", ["timeout", "malformed", "error", "uncertain", "verified_failure"]
)
def test_runner_failure_never_completes_or_retries(tmp_path, outcome):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        runner = FakeGuiSubtaskRunner({"s0": outcome})
        async with MissionCoordinator(tmp_path, runner) as c:
            state = await c.start(mission(review=False))
            assert state["status"] in ("paused", "partially_complete")
            assert len(runner.calls) == 1
            await c.continue_mission("mission1")
            assert len(runner.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("decision,expired", [("deny", False), ("approve", True)])
def test_denied_and_expired_approval_no_dispatch(tmp_path, decision, expired):
    async def scenario():
        from monster.mission.contracts import ApprovalDecision
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        now = [time.time()]
        runner = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, runner, clock=lambda: now[0]) as c:
            state = await c.start(mission(count=1))
            approval = c.store.get_approval(state["pending_approval_id"])
            if expired:
                now[0] += 61
            state = await c.resume(
                "mission1",
                ApprovalDecision(
                    approval_id=approval.approval_id,
                    effect_id=approval.effect_id,
                    generation=approval.generation,
                    decision=decision,
                ),
            )
            assert state["status"] == "partially_complete"
            assert runner.calls == []

    asyncio.run(scenario())


def test_stop_during_hung_runner_and_stale_result(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        runner = FakeGuiSubtaskRunner({"s0": "hang"})
        async with MissionCoordinator(tmp_path, runner) as c:
            running = asyncio.create_task(c.start(mission(review=False)))
            await asyncio.wait_for(runner.started.wait(), 2)
            state = await asyncio.wait_for(c.stop("mission1", cancel=True), 2)
            await running
            assert state["status"] == "cancelled"
            assert c.store.effect("mission1", "s0").status == "uncertain"
            assert len(runner.calls) == 1

    asyncio.run(scenario())


def test_budgets_and_unauthorized_mission(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.runner import FakeGuiSubtaskRunner

        runner = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, runner) as c:
            state = await c.start(mission(max_delegations=1, review=False))
            assert state["status"] == "partially_complete"
            assert len(runner.calls) == 1
            denied = mission("denied").model_copy(update={"authorized": False})
            assert (await c.start(denied))["status"] == "failed"
            assert len(runner.calls) == 1

    asyncio.run(scenario())
