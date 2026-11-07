import asyncio
import threading
import time

import pytest
from PySide6.QtCore import QObject, Signal

from monster.mission.contracts import GuiSubtaskRequest, Subtask


def request(effect="attempt1", generation=1, **changes):
    return GuiSubtaskRequest(
        mission_id="m1",
        workspace_id="desktop",
        effect_id=effect,
        generation=generation,
        subtask=Subtask(
            subtask_id="s1",
            objective_ref="editor-a",
            success_refs=["visible-a"],
            category="local_reversible",
        ),
        authorization_expires_at=time.time() + 60,
        **changes,
    )


class FakeController(QObject):
    terminal_ready = Signal(object)

    def __init__(self):
        super().__init__()
        from monster.providers.config import OllamaConfig

        self.config = OllamaConfig(model="test-vision", approve_each_step=False)
        self.active = False
        self.phase = "idle"
        self.cleanup_blocked = False
        self.generation = 0
        self.calls = []
        self.cancels = 0
        self.report = None

    def submit(self, instruction):
        assert threading.current_thread() is threading.main_thread()
        self.calls.append(instruction)
        self.generation += 1
        self.active = True
        self.phase = "observing"
        return True

    def cancel(self):
        assert threading.current_thread() is threading.main_thread()
        self.cancels += 1
        self.active = False
        self.phase = "closing"

    def finish(self, *, cleanup=True, visual=True, human=True):
        from monster.agent.terminal import ExecutionReport

        self.active = False
        self.phase = "idle"
        self.report = ExecutionReport(
            self.generation, "complete", 1, True, cleanup, visual, human, "frame1"
        )
        self.terminal_ready.emit(self.report)


def setup(qtbot, tmp_path):
    from monster.mission.gui_bridge import MissionGuiBridge
    from monster.mission.gui_subtask_adapter import AsyncGuiSubtaskRunner
    from monster.mission.workspace import WorkspaceGate

    controller = FakeController()
    gate = WorkspaceGate(tmp_path / "session.lock")
    bridge = MissionGuiBridge(
        controller,
        gate,
        preflight=lambda: 77,
        workspace_id="desktop",
        objective_catalog={"editor-a": "Type harmless sentence; do not send."},
    )
    runner = AsyncGuiSubtaskRunner(bridge, cleanup_seconds=0.2)
    return controller, gate, bridge, runner


def background(coro):
    result = []

    def run():
        try:
            result.append(asyncio.run(coro))
        except BaseException as e:  # noqa: BLE001 -- test-thread error transport.
            result.append(e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


def test_queued_start_and_cleanup_gate(qtbot, tmp_path):
    controller, gate, bridge, runner = setup(qtbot, tmp_path)
    thread, output = background(runner.run(request()))
    qtbot.waitUntil(lambda: len(controller.calls) == 1)
    assert controller.config.approve_each_step
    controller.active = False
    assert gate.busy
    assert thread.is_alive()
    controller.finish()
    qtbot.waitUntil(lambda: not thread.is_alive())
    assert output[0].status == "verified_success"
    assert output[0].model_id == "test-vision"
    assert output[0].cleanup_confirmed
    assert not gate.busy
    bridge.shutdown()


@pytest.mark.parametrize(
    "cleanup,visual,human", [(False, True, True), (True, False, True), (True, True, False)]
)
def test_success_requires_all_evidence(qtbot, tmp_path, cleanup, visual, human):
    c, gate, b, r = setup(qtbot, tmp_path)
    thread, out = background(r.run(request()))
    qtbot.waitUntil(lambda: len(c.calls) == 1)
    c.finish(cleanup=cleanup, visual=visual, human=human)
    qtbot.waitUntil(lambda: not thread.is_alive())
    assert out[0].status == "uncertain"
    assert gate.busy is (not cleanup)
    b.shutdown()


def test_stale_terminal_and_duplicate_attempt(qtbot, tmp_path):
    c, gate, b, r = setup(qtbot, tmp_path)
    thread, _out = background(r.run(request()))
    qtbot.waitUntil(lambda: len(c.calls) == 1)
    from monster.agent.terminal import ExecutionReport

    c.terminal_ready.emit(ExecutionReport(999, "complete", 1, True, True, True, True, "frame-old"))
    assert thread.is_alive() and gate.busy
    c.finish()
    qtbot.waitUntil(lambda: not thread.is_alive())
    second, out2 = background(r.run(request()))
    qtbot.waitUntil(lambda: not second.is_alive())
    assert out2[0].status == "blocked"
    assert len(c.calls) == 1
    b.shutdown()


def test_cancel_waits_for_cleanup_and_no_next_start(qtbot, tmp_path):
    c, gate, b, r = setup(qtbot, tmp_path)

    async def job():
        task = asyncio.create_task(r.run(request()))
        while not c.calls:
            await asyncio.sleep(0.01)
        task.cancel()
        return await task

    thread, out = background(job())
    qtbot.waitUntil(lambda: c.cancels == 1)
    assert gate.busy
    assert thread.is_alive()
    c.finish()
    qtbot.waitUntil(lambda: not thread.is_alive())
    assert out[0].status == "uncertain"
    assert not gate.busy
    b.shutdown()


def test_shutdown_resolves_pending_and_quarantines(qtbot, tmp_path):
    c, gate, b, r = setup(qtbot, tmp_path)
    thread, out = background(r.run(request()))
    qtbot.waitUntil(lambda: len(c.calls) == 1)
    b.shutdown()
    qtbot.waitUntil(lambda: not thread.is_alive())
    assert out[0].status == "uncertain"
    assert gate.busy


def test_manual_lease_refuses_mission(qtbot, tmp_path):
    c, gate, b, r = setup(qtbot, tmp_path)
    token = gate.acquire("manual", 1)
    thread, out = background(r.run(request()))
    qtbot.waitUntil(lambda: not thread.is_alive())
    assert out[0].status == "blocked"
    assert c.calls == []
    gate.release(token, cleanup_confirmed=True)
    b.shutdown()


def test_two_subtasks_through_real_bridge_and_coordinator(qtbot, tmp_path):
    from monster.mission.contracts import ApprovalDecision, Mission
    from monster.mission.coordinator import MissionCoordinator

    c, gate, b, r = setup(qtbot, tmp_path)

    async def job():
        plan = Mission(
            mission_id="mission2",
            objective_ref="test",
            workspace_id="desktop",
            authorized=True,
            deadline_utc=time.time() + 600,
            subtasks=[
                Subtask(
                    subtask_id=f"s{i}",
                    objective_ref="editor-a",
                    success_refs=["v"],
                    requires_approval=True,
                    category="local_reversible",
                    depends_on=["s0"] if i else [],
                )
                for i in range(2)
            ],
        )
        async with MissionCoordinator(tmp_path / "store", r, experimental_gui=True) as coord:
            state = await coord.start(plan)
            while state["status"] == "waiting_approval":
                a = coord.store.get_approval(state["pending_approval_id"])
                state = await coord.resume(
                    plan.mission_id,
                    ApprovalDecision(
                        approval_id=a.approval_id,
                        effect_id=a.effect_id,
                        generation=a.generation,
                        decision="approve",
                    ),
                )
            return state, coord.store.metrics(plan.mission_id)

    thread, out = background(job())
    for i in (1, 2):
        qtbot.waitUntil(lambda i=i: len(c.calls) == i)
        assert gate.busy
        c.finish()
    qtbot.waitUntil(lambda: not thread.is_alive())
    assert out[0][0]["status"] == "completed"
    assert out[0][1]["verified"] == 2
    assert not gate.busy
    b.shutdown()


def test_mission_runtime_routes_review_without_blocking_qt(qtbot, tmp_path):
    from monster.mission.contracts import Mission
    from monster.mission.runtime import MissionRuntime

    c, _gate, b, r = setup(qtbot, tmp_path)
    plan = Mission(
        mission_id="runtime1",
        objective_ref="demo",
        workspace_id="desktop",
        authorized=True,
        deadline_utc=time.time() + 600,
        subtasks=[
            Subtask(
                subtask_id="s1",
                objective_ref="editor-a",
                success_refs=["v"],
                category="local_reversible",
            )
        ],
    )
    runtime = MissionRuntime(tmp_path / "store", r, b, plan=plan)
    updates = []
    runtime.changed.connect(updates.append)
    runtime.start()
    qtbot.waitUntil(lambda: bool(updates))
    assert updates[-1]["state"]["status"] == "waiting_approval"
    runtime.answer("approve")
    qtbot.waitUntil(lambda: len(c.calls) == 1)
    c.finish()
    qtbot.waitUntil(lambda: updates[-1]["state"]["status"] == "completed")
    runtime.close()
    qtbot.waitUntil(lambda: not runtime.thread.is_alive())


def test_cancel_before_queued_start_sends_no_input(qtbot, tmp_path):
    from monster.mission.gui_bridge import Handoff

    c, gate, b, _r = setup(qtbot, tmp_path)
    cancelled = threading.Event()
    cancelled.set()
    answers = []
    b.resolved.connect(answers.append)
    b.start_requested.emit(Handoff(request(), cancelled))
    qtbot.waitUntil(lambda: bool(answers))
    assert c.calls == [] and not gate.busy
    b.shutdown()


@pytest.mark.parametrize(
    "change", [{"authorization_expires_at": 1.0}, {"workspace_id": "different"}]
)
def test_stale_authorization_or_workspace_refused(qtbot, tmp_path, change):
    c, _gate, b, r = setup(qtbot, tmp_path)
    req = request().model_copy(update=change)
    thread, out = background(r.run(req))
    qtbot.waitUntil(lambda: not thread.is_alive())
    assert c.calls == [] and out[0].status == "blocked"
    b.shutdown()


def test_session_lock_blocks_other_gate(tmp_path):
    from monster.mission.workspace import WorkspaceGate

    first, second = (
        WorkspaceGate(tmp_path / "desktop.lock"),
        WorkspaceGate(tmp_path / "desktop.lock"),
    )
    token = first.acquire("first", 1)
    assert second.acquire("second", 1) is None
    assert not first.release(token, cleanup_confirmed=False)
    assert second.acquire("second", 1) is None
    # Quarantined gates must not be released even by a late normal cleanup call.
    assert not first.release(token, cleanup_confirmed=True)


def test_restart_after_a_demands_fresh_approval_for_b(qtbot, tmp_path):
    from monster.mission.contracts import ApprovalDecision, Mission
    from monster.mission.coordinator import MissionCoordinator

    c, _gate, b, r = setup(qtbot, tmp_path)
    plan = Mission(
        mission_id="restart",
        objective_ref="test",
        workspace_id="desktop",
        authorized=True,
        deadline_utc=time.time() + 600,
        subtasks=[
            Subtask(
                subtask_id=f"s{i}",
                objective_ref="editor-a",
                success_refs=["v"],
                category="local_reversible",
                depends_on=["s0"] if i else [],
            )
            for i in range(2)
        ],
    )

    def decision(a):
        return ApprovalDecision(
            approval_id=a.approval_id,
            effect_id=a.effect_id,
            generation=a.generation,
            decision="approve",
        )

    async def first():
        async with MissionCoordinator(tmp_path / "store", r, experimental_gui=True) as coord:
            state = await coord.start(plan)
            a = coord.store.get_approval(state["pending_approval_id"])
            state = await coord.resume(plan.mission_id, decision(a))
            return coord.store.get_approval(state["pending_approval_id"])

    thread, out = background(first())
    qtbot.waitUntil(lambda: len(c.calls) == 1)
    c.finish()
    qtbot.waitUntil(lambda: not thread.is_alive())
    old = out[0]

    async def second():
        async with MissionCoordinator(tmp_path / "store", r, experimental_gui=True) as coord:
            with pytest.raises(ValueError, match="fresh"):
                await coord.resume(plan.mission_id, decision(old))
            state = await coord.continue_mission(plan.mission_id)
            fresh = coord.store.get_approval(state["pending_approval_id"])
            assert fresh.generation > old.generation
            assert fresh.approval_id != old.approval_id
            return await coord.resume(plan.mission_id, decision(fresh))

    thread, out = background(second())
    qtbot.waitUntil(lambda: len(c.calls) == 2)
    c.finish()
    qtbot.waitUntil(lambda: not thread.is_alive())
    assert out[0]["status"] == "completed"
    b.shutdown()


def test_stop_before_runtime_loop_or_queued_start_cannot_submit(qtbot, tmp_path):
    from monster.mission.gui_bridge import Handoff
    from monster.mission.runtime import MissionRuntime

    c, gate, b, r = setup(qtbot, tmp_path)
    runtime = MissionRuntime(tmp_path / "store", r, b, plan=None, resume_id="nonexistent")
    b.start_requested.emit(Handoff(request(), threading.Event()))
    runtime.stop()
    qtbot.wait(30)
    assert c.calls == []
    assert not gate.busy
    runtime.close()


def test_retired_bridge_disconnects_controller(qtbot, tmp_path):
    c, _gate, b, _r = setup(qtbot, tmp_path)
    b.shutdown()
    # An old terminal listener must not retain every retired dispatcher forever.
    assert c.receivers("2terminal_ready(PyObject)") == 0


def test_cleanup_watchdog_quarantines_without_terminal_signal(qtbot, tmp_path):
    c, gate, b, r = setup(qtbot, tmp_path)
    thread, out = background(r.run(request()))
    qtbot.waitUntil(lambda: bool(c.calls))
    b.halt()
    assert c.cancels == 1 and gate.busy
    b.current["stop_at"] = time.monotonic() - 6
    b._watchdog()
    qtbot.waitUntil(lambda: not thread.is_alive())
    assert out[0].status == "uncertain" and not out[0].cleanup_confirmed
    assert gate.quarantined
    b.shutdown()


def test_bridge_ledger_does_not_persist_transient_instruction(qtbot, tmp_path):
    from monster.mission.contracts import Mission
    from monster.mission.coordinator import MissionCoordinator

    c, gate, b, r = setup(qtbot, tmp_path)

    async def job():
        from monster.mission.contracts import ApprovalDecision

        async with MissionCoordinator(tmp_path / "store", r, experimental_gui=True) as coord:
            state = await coord.start(
                Mission(
                    mission_id="privacy-gui",
                    objective_ref="editor-a",
                    workspace_id="desktop",
                    authorized=True,
                    deadline_utc=time.time() + 600,
                    subtasks=[
                        Subtask(
                            subtask_id="s1",
                            objective_ref="editor-a",
                            success_refs=["v"],
                            category="local_reversible",
                        )
                    ],
                )
            )
            a = coord.store.get_approval(state["pending_approval_id"])
            return await coord.resume(
                "privacy-gui",
                ApprovalDecision(
                    approval_id=a.approval_id,
                    effect_id=a.effect_id,
                    generation=a.generation,
                    decision="approve",
                ),
            )

    thread, out = background(job())
    qtbot.waitUntil(lambda: bool(c.calls))
    c.finish()
    qtbot.waitUntil(lambda: not thread.is_alive())
    assert out[0]["status"] == "completed"
    assert not gate.busy
    contents = b"".join(p.read_bytes() for p in (tmp_path / "store").glob("*.sqlite3*"))
    assert b"Type harmless sentence" not in contents
    b.shutdown()


def test_runtime_close_tolerates_loop_already_closed(qtbot, tmp_path):
    from monster.mission.runtime import MissionRuntime

    _c, _gate, b, r = setup(qtbot, tmp_path)
    runtime = MissionRuntime(tmp_path / "store", r, b, plan=None, resume_id="none")

    class ClosedLoop:
        def call_soon_threadsafe(self, callback):
            raise RuntimeError("Event loop is closed")

    runtime.loop = ClosedLoop()
    runtime._exit = asyncio.Event()
    runtime.close()
    assert runtime.closed and b.closed


def test_phase4_simulation_runtime_consent_progress_and_shutdown(qtbot,tmp_path):
    from monster.mission.plan_templates import editor_plan
    from monster.mission.runner import FakeGuiSubtaskRunner
    from monster.mission.runtime import MissionRuntime
    runtime=MissionRuntime(tmp_path,FakeGuiSubtaskRunner(),None,scheduled_plan=editor_plan())
    updates=[]
    runtime.changed.connect(updates.append)
    runtime.start()
    qtbot.waitUntil(lambda:bool(updates))
    assert updates[-1].get('consent') is not None
    runtime.answer('approve')
    qtbot.waitUntil(lambda:any(u['state']['status']=='completed' for u in updates))
    assert runtime.last_report['provenance']=='simulated'
    assert len(runtime.last_report['verified_subtask_ids'])==6
    runtime.close()
    qtbot.waitUntil(lambda:not runtime.thread.is_alive())
