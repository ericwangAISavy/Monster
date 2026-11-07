import asyncio
import time

import pytest


def test_template_and_budget_hard_limits():
    from pydantic import ValidationError

    from monster.mission.plan_templates import editor_plan
    from monster.mission.scheduling import MissionBudget

    plan = editor_plan()
    assert len(plan.mission.subtasks) == 6
    assert len({s.subtask_id for s in plan.mission.subtasks}) == 6
    for values in (
        {"max_wall_seconds": 7201},
        {"max_active_seconds": 3601},
        {"max_total_input_transactions": 121},
        {"max_total_model_calls": 241},
        {"max_subtask_starts": 13},
    ):
        with pytest.raises(ValidationError):
            MissionBudget(**values)


def test_six_stages_need_exact_consent_and_finish_once(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan
        from monster.mission.runner import FakeGuiSubtaskRunner

        plan = editor_plan()
        runner = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, runner) as c:
            waiting = await c.start_scheduled(plan)
            assert waiting["status"] == "waiting_approval"
            assert runner.calls == []
            consent = c.scheduler.consent_request(plan.mission.mission_id)
            state = await c.consent_and_continue(plan.mission.mission_id, consent)
            assert state["status"] == "completed"
            assert [r.subtask.subtask_id for r in runner.calls] == [
                s.subtask_id for s in plan.mission.subtasks
            ]
            report = await c.mission_report(plan.mission.mission_id)
            assert report["provenance"] == "simulated"
            assert len(report["verified_subtask_ids"]) == 6
            assert report["input_transactions_confirmed"] == 6

    asyncio.run(scenario())


def test_wrong_digest_or_expired_consent_never_dispatches(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan

        plan = editor_plan()
        async with MissionCoordinator(tmp_path) as c:
            await c.start_scheduled(plan)
            consent = c.scheduler.consent_request(plan.mission.mission_id)
            for change in ({"plan_digest": "0" * 64}, {"expires_at": time.time() - 1}):
                with pytest.raises(ValueError):
                    await c.consent_and_continue(
                        plan.mission.mission_id, consent.model_copy(update=change)
                    )
            assert c.runner.calls == []

    asyncio.run(scenario())


def test_live_pilot_refused_without_native_qualification(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan

        async with MissionCoordinator(tmp_path) as c:
            with pytest.raises(ValueError, match="qualification"):
                await c.start_scheduled(editor_plan(mode="supervised_pilot"))

    asyncio.run(scenario())


def test_boundary_pause_restart_preserves_a_and_counters(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan
        from monster.mission.runner import FakeGuiSubtaskRunner

        plan = editor_plan()
        mid = plan.mission.mission_id
        first = FakeGuiSubtaskRunner({"pilot-a": "wait_success"})
        async with MissionCoordinator(tmp_path, first) as c:
            await c.start_scheduled(plan)
            run = asyncio.create_task(c.consent_and_continue(mid, c.scheduler.consent_request(mid)))
            await asyncio.wait_for(first.started.wait(), 2)
            assert (await c.request_pause(mid))["reason"] == "pause_pending"
            first.release.set()
            state = await run
            assert state["status"] == "paused"
            assert len(first.calls) == 1
            assert c.scheduler.usage(mid).input_confirmed == 1
        second = FakeGuiSubtaskRunner()
        async with MissionCoordinator(tmp_path, second) as c:
            assert (await c.continue_mission(mid))["reason"] == "mission_consent_required"
            state = await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            assert state["status"] == "completed"
            assert [r.subtask.subtask_id for r in second.calls] == [f"pilot-{x}" for x in "bcdef"]
            assert c.scheduler.usage(mid).input_confirmed == 6
            assert c.scheduler.usage(mid).starts == 6

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_total_input_transactions", 10),
        ("max_total_model_calls", 20),
        ("max_active_seconds", 310),
        ("max_subtask_starts", 1),
    ],
)
def test_global_envelope_stops_incomplete_without_extra_calls(tmp_path, field, value):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan
        from monster.mission.scheduling import MissionBudget

        plan = editor_plan(budget=MissionBudget(**{field: value}))
        async with MissionCoordinator(tmp_path) as c:
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            state = await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            assert state["status"] == "partially_complete"
            assert len(c.runner.calls) == 1
            u = c.scheduler.usage(mid)
            assert u.starts == 1

    asyncio.run(scenario())


def test_pure_timeout_retries_but_dispatched_timeout_never_retries(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan
        from monster.mission.runner import FakeGuiSubtaskRunner

        for name, unsafe in [("pure", False), ("after-handoff", True)]:
            plan = editor_plan(mission_id=name)
            r = FakeGuiSubtaskRunner({"pilot-a": "timeout"} if unsafe else None)
            if not unsafe:
                r.preflight_faults = ["transient_pre_dispatch", "ready"]
            async with MissionCoordinator(tmp_path / name, r) as c:
                await c.start_scheduled(plan)
                state = await c.consent_and_continue(name, c.scheduler.consent_request(name))
                assert state["status"] == ("paused" if unsafe else "completed")
                assert len(r.calls) == (1 if unsafe else 6)

    asyncio.run(scenario())


def test_two_no_progress_faults_pause_and_do_not_spin(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan
        from monster.mission.runner import FakeGuiSubtaskRunner

        r = FakeGuiSubtaskRunner()
        r.preflight_faults = ["transient_pre_dispatch"] * 3
        plan = editor_plan()
        async with MissionCoordinator(tmp_path, r) as c:
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            state = await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            assert state["status"] == "paused" and state["reason"] == "no_progress"
            assert r.calls == [] and len(r.preflight_faults) == 1
            assert c.scheduler.usage(mid).no_progress == 2

    asyncio.run(scenario())


def test_observe_only_rehearsal_never_delegates(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan

        plan = editor_plan(mode="observe_only")
        async with MissionCoordinator(tmp_path) as c:
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            state = await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            assert state["status"] == "paused" and state["reason"] == "observe_only_handoff"
            assert c.runner.calls == []

    asyncio.run(scenario())


def test_clock_rollback_and_plan_mutation_fail_closed(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan

        now = [time.time()]
        plan = editor_plan(clock=lambda: now[0])
        async with MissionCoordinator(tmp_path, clock=lambda: now[0]) as c:
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            now[0] -= 10
            state = await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            assert state["status"] == "paused" and state["reason"] == "timing_uncertain"
            assert c.runner.calls == []

    asyncio.run(scenario())


def test_missing_cleanup_or_wrong_provenance_never_verifies(tmp_path, monkeypatch):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan
        from monster.mission.runner import FakeGuiSubtaskRunner

        r = FakeGuiSubtaskRunner()
        original = r.run

        async def incomplete(req):
            return (await original(req)).model_copy(update={"cleanup_confirmed": False})

        monkeypatch.setattr(r, "run", incomplete)
        plan = editor_plan()
        async with MissionCoordinator(tmp_path, r) as c:
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            state = await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            assert state["status"] == "paused"
            assert (await c.mission_report(mid))["verified_subtask_ids"] == []

    asyncio.run(scenario())


def test_changed_catalog_invalidates_plan_before_consent(tmp_path, monkeypatch):
    async def scenario():
        from monster.mission import plan_templates
        from monster.mission.coordinator import MissionCoordinator

        plan = plan_templates.editor_plan()
        async with MissionCoordinator(tmp_path) as c:
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            consent = c.scheduler.consent_request(mid)
            monkeypatch.setitem(plan_templates.PILOT_TEXT, "pilot-a", "Changed body")
            with pytest.raises(ValueError):
                await c.consent_and_continue(mid, consent)
            assert c.runner.calls == []

    asyncio.run(scenario())


def test_pause_latches_before_snapshot_await(tmp_path, monkeypatch):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan
        from monster.mission.runner import FakeGuiSubtaskRunner

        r = FakeGuiSubtaskRunner({"pilot-a": "wait_success"})
        plan = editor_plan()
        async with MissionCoordinator(tmp_path, r) as c:
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            run = asyncio.create_task(c.consent_and_continue(mid, c.scheduler.consent_request(mid)))
            await asyncio.wait_for(r.started.wait(), 2)
            entered, release = asyncio.Event(), asyncio.Event()
            original = c.snapshot
            first = True

            async def delayed(key):
                nonlocal first
                if first:
                    first = False
                    entered.set()
                    await release.wait()
                return await original(key)

            monkeypatch.setattr(c, "snapshot", delayed)
            pause = asyncio.create_task(c.request_pause(mid))
            await entered.wait()
            r.release.set()
            await asyncio.sleep(0.1)
            count = len(r.calls)
            release.set()
            await pause
            await run
            assert count == 1

    asyncio.run(scenario())


def test_three_configured_pure_attempts_still_stop_at_two_no_progress(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan
        from monster.mission.runner import FakeGuiSubtaskRunner
        from monster.mission.scheduling import MissionBudget

        r = FakeGuiSubtaskRunner()
        r.preflight_faults = ["transient_pre_dispatch"] * 2 + ["ready"]
        plan = editor_plan(budget=MissionBudget(max_pure_attempts=3))
        async with MissionCoordinator(tmp_path, r) as c:
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            state = await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            assert state["status"] == "paused" and state["reason"] == "no_progress"
            assert r.calls == []

    asyncio.run(scenario())


def test_pure_retry_cannot_spend_subtask_model_cap_before_handoff(tmp_path):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan
        from monster.mission.runner import FakeGuiSubtaskRunner

        plan = editor_plan()
        plan = plan.model_copy(
            update={
                "mission": plan.mission.model_copy(
                    update={
                        "subtasks": [
                            s.model_copy(update={"max_model_calls": 1})
                            for s in plan.mission.subtasks
                        ]
                    }
                )
            }
        )
        r = FakeGuiSubtaskRunner()
        r.preflight_faults = ["transient_pre_dispatch", "ready"]
        async with MissionCoordinator(tmp_path, r) as c:
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            state = await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            assert state["status"] == "partially_complete"
            assert r.calls == []
            assert c.scheduler.usage(mid).models_charged == 1
            assert (await c.mission_report(mid))["verified_subtask_ids"] == []

    asyncio.run(scenario())


def test_active_envelope_overrun_is_recorded_and_blocks_next_stage(tmp_path, monkeypatch):
    async def scenario():
        from monster.mission.coordinator import MissionCoordinator
        from monster.mission.plan_templates import editor_plan
        from monster.mission.runner import FakeGuiSubtaskRunner

        mono = [100.0]
        r = FakeGuiSubtaskRunner()
        original = r.run

        async def overrun(req):
            result = await original(req)
            mono[0] += 311
            return result

        monkeypatch.setattr(r, "run", overrun)
        plan = editor_plan()
        async with MissionCoordinator(tmp_path, r, monotonic=lambda: mono[0]) as c:
            mid = plan.mission.mission_id
            await c.start_scheduled(plan)
            state = await c.consent_and_continue(mid, c.scheduler.consent_request(mid))
            assert state["status"] == "partially_complete"
            assert len(r.calls) == 1
            assert c.scheduler.usage(mid).active_ms >= 311000
            assert (await c.mission_report(mid))["mission_verified_completion_rate"] == 0

    asyncio.run(scenario())
