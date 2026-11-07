import asyncio
import threading
import time

import pytest
from test_supervised import controller, observation

from monster.mission.contracts import ApprovalDecision, Mission, Subtask


@pytest.mark.parametrize("journal_failure", [False, True])
def test_real_controller_gate_uses_journal_before_fake_input(qtbot, tmp_path, journal_failure):
    from PySide6.QtCore import QTimer

    from monster.mission.coordinator import MissionCoordinator
    from monster.mission.gui_bridge import MissionGuiBridge
    from monster.mission.gui_subtask_adapter import AsyncGuiSubtaskRunner
    from monster.mission.runtime import EDITOR_OBJECTIVES, EXPECTED_TEXT
    from monster.mission.workspace import WorkspaceGate
    from monster.providers.base import Decision

    engine, native, _ = controller(
        [
            Decision(
                "action",
                "type",
                {"op": "text", "text": EXPECTED_TEXT["editor-a"]},
                "benign",
                expected_result="Text visible",
            ),
            Decision("done", "visible"),
        ]
    )
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    reviews = []

    def review(rid, text):
        reviews.append(engine.phase)
        QTimer.singleShot(1, lambda: engine.respond(rid, "approve"))

    engine.approval_requested.connect(review)
    gate = WorkspaceGate(tmp_path / "desktop.lock")
    bridge = MissionGuiBridge(
        engine,
        gate,
        preflight=lambda: 77,
        objective_catalog=EDITOR_OBJECTIVES,
        workspace_id="desktop",
    )
    runner = AsyncGuiSubtaskRunner(bridge)
    output = []

    async def scenario():
        async with MissionCoordinator(tmp_path / "state", runner, experimental_gui=True) as c:
            if journal_failure:
                c.store.connection.execute(
                    "CREATE TRIGGER fail_intent BEFORE UPDATE ON action_effects "
                    "WHEN json_extract(NEW.record,'$.status')='dispatch_intent_recorded' "
                    "BEGIN SELECT RAISE(ABORT,'simulated failure'); END"
                )
            plan = Mission(
                mission_id="integrated",
                objective_ref="editor-a",
                workspace_id="desktop",
                authorized=True,
                deadline_utc=time.time() + 300,
                subtasks=[
                    Subtask(
                        subtask_id="editor-a",
                        objective_ref="editor-a",
                        success_refs=["text-visible"],
                        category="local_reversible",
                    )
                ],
            )
            state = await c.start(plan)
            a = c.store.get_approval(state["pending_approval_id"])
            state = await c.resume(
                plan.mission_id,
                ApprovalDecision(
                    approval_id=a.approval_id,
                    effect_id=a.effect_id,
                    generation=a.generation,
                    decision="approve",
                ),
            )
            effects = c.effects.records(plan.mission_id)
            output.append((state, effects, [c.effects.history(e.effect_id) for e in effects]))

    def work():
        try:
            asyncio.run(scenario())
        except BaseException as exc:  # noqa: BLE001 -- transport a test-thread failure to assertions.
            output.append(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=15000)
    assert output and not isinstance(output[0], BaseException), output
    state, effects, history = output[0]
    assert reviews[0] == "effect_approval"
    if journal_failure:
        assert native.executed == []
        assert state["status"] == "paused"
        assert not any(e.status == "observed_success" for e in effects)
    else:
        assert len(native.executed) == 1
        assert state["status"] == "completed"
        assert effects[0].status == "observed_success"
        codes = [e["code"] for e in history[0]]
        assert (
            codes.index("approved")
            < codes.index("dispatch_intent_recorded")
            < codes.index("input_inserted")
        )
        assert codes.index("input_inserted") < codes.index("observed_success")
        data = b"".join(p.read_bytes() for p in (tmp_path / "state").glob("*.sqlite3*"))
        assert EXPECTED_TEXT["editor-a"].encode() not in data
    bridge.shutdown()
    runner.detach()
    engine.close()
