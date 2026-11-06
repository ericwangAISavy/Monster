import hashlib
import time
from dataclasses import replace

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer, Signal
from PySide6.QtGui import QColor, QImage

from monster.agent.controller import SupervisedController
from monster.agent.validation import prepare_action, scene_matches
from monster.computer.contracts import Bounds
from monster.computer.screenshot import Observation
from monster.providers.base import Decision, ProbeResult, Verification
from monster.providers.config import OllamaConfig


def observation(color="#ffffff", foreground=77, blink=None):
    image = QImage(100, 100, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    if blink is not None:
        image.setPixelColor(99, 99, QColor(blink % 255, 0, 0))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    png = bytes(data)
    bounds = Bounds(-100, 0, 100, 100)
    return Observation(
        "frame",
        time.monotonic(),
        bounds,
        (bounds,),
        png,
        hashlib.sha256(png).hexdigest(),
        foreground,
    )


class FakeInput(QObject):
    completed = Signal(int, object, str)
    interrupted = Signal()

    def __init__(self, cleanup=True):
        super().__init__()
        self.next_id = 0
        self.executed = []
        self.cleanup = cleanup
        self.stops = 0

    def submit(self, operation, action=None, focus=None, geometry=None):
        self.next_id += 1
        job = self.next_id
        if operation == "execute":
            self.executed.append((action, focus, geometry))
        value = {"status": "inserted", "events": 4} if operation == "execute" else True
        QTimer.singleShot(1, lambda: self.completed.emit(job, value, ""))
        return job

    def stop(self):
        self.stops += 1

    def close(self):
        self.next_id += 1
        job = self.next_id
        QTimer.singleShot(1, lambda: self.completed.emit(job, self.cleanup, ""))
        return job

    def shutdown(self):
        self.stop()


class FakeModel:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.history = []

    async def probe(self, model):
        return ProbeResult(model, "abc", "test", True, True)

    async def verify_action(self, *args):
        return Verification("succeeded", "Simulated expected visual result is present")

    async def decide(self, *args, history=()):
        self.history.append(history)
        return self.decisions.pop(0) if self.decisions else Decision("unable", "No more steps")


def controller(decisions, cleanup=True):
    input_bridge = FakeInput(cleanup)
    model = FakeModel(decisions)
    config = OllamaConfig(model="vision", local_server_confirmed=True, mode="supervised")
    engine = SupervisedController(
        config, provider_factory=lambda _: model, input_bridge=input_bridge
    )
    return engine, input_bridge, model


def test_supervised_approval_execution_and_user_verified_completion(qtbot):
    engine, bridge, model = controller(
        [
            Decision("action", "Type hello", {"op": "text", "text": "Hello"}),
            Decision("complete", "The note is visible"),
        ]
    )
    questions, finished, owners = [], [], []
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.approval_requested.connect(lambda rid, text: questions.append((rid, text)))
    engine.finished.connect(finished.append)
    engine.worker_owned.connect(owners.append)
    try:
        assert engine.submit("Write hello in the open Notepad")
        qtbot.waitUntil(lambda: len(questions) == 1)
        assert not bridge.executed
        engine.respond("stale-id", "approve")
        assert not bridge.executed
        engine.respond(questions[-1][0], "approve")
        qtbot.waitUntil(lambda: len(questions) == 2)
        assert len(bridge.executed) == 1
        assert bridge.executed[0][1] == 77
        assert model.history[-1] and "text" in model.history[-1][0]
        assert not finished
        engine.respond(questions[-1][0], "approve")
        qtbot.waitUntil(lambda: bool(finished))
        assert "confirmed by you" in finished[0]
        assert owners == [True, False]
    finally:
        engine.close()


def test_decline_and_expiry_never_execute(qtbot):
    for decline in (True, False):
        engine, bridge, _ = controller(
            [Decision("action", "Click", {"op": "click", "x": 30, "y": 30})]
        )
        questions = []
        engine.capture_requested.connect(
            lambda e=engine: e.observation_ready(observation(), e.generation)
        )
        engine.approval_requested.connect(lambda rid, text, q=questions: q.append(rid))
        try:
            engine.submit("test")
            qtbot.waitUntil(lambda q=questions: bool(q))
            if decline:
                engine.respond(questions[0], "reject")
            else:
                engine.approval_timer.start(1)
            qtbot.waitUntil(lambda e=engine: e.phase == "idle")
            engine.respond(questions[0], "approve")
            assert not bridge.executed
        finally:
            engine.close()


def test_screen_change_discards_approved_action(qtbot):
    engine, bridge, _ = controller(
        [
            Decision("action", "Click", {"op": "click", "x": 30, "y": 30}),
            Decision("unable", "Screen changed"),
        ]
    )
    questions = []
    captures = []

    def capture():
        captures.append(True)
        engine.observation_ready(
            observation("white" if len(captures) == 1 else "black"), engine.generation
        )

    engine.capture_requested.connect(capture)
    engine.approval_requested.connect(lambda rid, text: questions.append(rid))
    try:
        engine.submit("test")
        qtbot.waitUntil(lambda: bool(questions))
        engine.respond(questions[0], "approve")
        qtbot.waitUntil(lambda: engine.phase == "idle")
        assert not bridge.executed and engine.stale == 1
    finally:
        engine.close()


def test_cancel_during_capture_and_cleanup_failure_blocks_rearming(qtbot):
    engine, bridge, _ = controller([], cleanup=False)
    requested = []
    engine.capture_requested.connect(lambda: requested.append(engine.generation))
    try:
        engine.submit("test")
        qtbot.waitUntil(lambda: bool(requested))
        engine.cancel()
        engine.observation_ready(observation(), requested[-1])
        qtbot.waitUntil(lambda: engine.phase == "idle")
        assert engine.cleanup_blocked and not engine.submit("new task")
        assert not bridge.executed
    finally:
        engine.close()


def test_image_binding_focus_and_policy():
    before = observation()
    assert scene_matches(before, observation(), {"op": "text", "text": "a"})
    assert not scene_matches(before, observation("black"), {"op": "click", "x": 30, "y": 30})
    assert not scene_matches(before, observation(foreground=88), {"op": "text", "text": "a"})
    mapped = prepare_action({"op": "click", "x": 30, "y": 20}, before)
    assert mapped["x"] == -70 and mapped["y"] == 20
    for action in (
        {"op": "text", "text": "powershell"},
        {"op": "chord", "keys": ["WIN", "R"]},
        {"op": "press", "key": "DELETE"},
    ):
        with pytest.raises(ValueError):
            prepare_action(action, before)
    with pytest.raises(ValueError):
        prepare_action({"op": "text", "text": "a"}, replace(before, foreground=0))


def test_gui_worker_shortcut_handoff_and_stale_prompt_cleanup(qtbot, qapp, tmp_path):
    from monster.app import CompanionApp
    from monster.lifecycle import MockTaskController
    from monster.native import parse_hotkey

    ui = CompanionApp(qapp, MockTaskController(), tmp_path / "companion.json", enable_tray=False)
    engine, _, _ = controller([])
    registrations = []
    ui.hotkeys.register = lambda bindings: registrations.append(bindings) or []
    try:
        ui.switch_controller(engine)
        engine.worker_owned.emit(True)
        assert all(parse_hotkey(value[0]) != (3, ord("X")) for value in registrations[-1].values())
        ui.question("old", "Approve?", True)
        engine.active_changed.emit(False)
        assert ui.pending_question is None and not ui.pill.isVisible()
        engine.worker_owned.emit(False)
        assert "Emergency stop" in registrations[-1]
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_real_bridge_discards_cancelled_startup_and_disarms_after_action(qtbot):
    import threading

    from monster.agent.input_bridge import InputBridge

    created = []
    gate = threading.Event()

    class Session:
        def __init__(self, event_callback=None):
            self.callback = event_callback
            self.executions = self.disarms = self.closes = 0
            created.append(self)
            gate.wait(1)

        def arm(self):
            pass

        def execute(self, action, **kwargs):
            self.executions += 1
            return {"status": "inserted"}

        def disarm(self):
            self.disarms += 1

        def stop(self):
            pass

        def close(self):
            self.closes += 1
            return True

    bridge = InputBridge(factory=Session)
    receipts = []
    bridge.completed.connect(lambda job, value, error: receipts.append((job, value, error)))
    try:
        bridge.submit("open")
        qtbot.waitUntil(lambda: bool(created))
        bridge.stop()
        closing = bridge.close()
        gate.set()
        qtbot.waitUntil(lambda: any(r[0] == closing for r in receipts))
        assert created[0].executions == 0 and created[0].closes == 1
        opening = bridge.submit("open")
        qtbot.waitUntil(lambda: any(r[0] == opening for r in receipts))
        action = bridge.submit("execute", {"op": "press", "key": "A"})
        qtbot.waitUntil(lambda: any(r[0] == action for r in receipts))
        assert created[-1].executions == 1 and created[-1].disarms == 1
    finally:
        gate.set()
        bridge.shutdown()
    assert not bridge.thread.is_alive()


def test_post_approval_exception_has_stage_and_never_dispatches(qtbot, monkeypatch):
    import monster.agent.controller as module

    engine, bridge, _ = controller([Decision("action", "Type", {"op": "text", "text": "Hello"})])
    questions, failures = [], []
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.approval_requested.connect(lambda rid, text: questions.append(rid))
    engine.failed.connect(failures.append)

    def broken_compare(*args):
        raise TypeError("PRIVATE SCREEN OR TYPED CONTENT must never be displayed")

    monkeypatch.setattr(module, "scene_matches", broken_compare)
    try:
        engine.submit("test")
        qtbot.waitUntil(lambda: bool(questions))
        engine.respond(questions[0], "approve")
        qtbot.waitUntil(lambda: bool(failures))
        assert "Screen recheck after approval" in failures[0]
        assert "TypeError" in failures[0]
        assert "PRIVATE" not in failures[0]
        assert not bridge.executed
        assert engine.phase == "idle"
    finally:
        engine.close()


def test_automatic_routine_step_rechecks_then_executes_without_step_prompt(qtbot):
    engine, bridge, _ = controller(
        [
            Decision("action", "Type hello", {"op": "text", "text": "Hello"}),
            Decision("complete", "Hello is visible"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    captures, questions, finished = [], [], []

    def capture():
        captures.append(engine.phase)
        # Automatic mode must not restore a window over human focus changes.
        assert engine.restore_focus is None
        engine.observation_ready(observation(), engine.generation)

    engine.capture_requested.connect(capture)
    engine.approval_requested.connect(lambda rid, text: questions.append((rid, text)))
    engine.finished.connect(finished.append)
    try:
        engine.submit("Write hello")
        qtbot.waitUntil(lambda: engine.phase == "confirming")
        assert len(bridge.executed) == 1
        assert captures[:2] == ["observing", "rechecking"]
        assert len(questions) == 1 and "Confirm the visible result" in questions[0][1]
        assert not finished
        engine.respond(questions[0][0], "approve")
        qtbot.waitUntil(lambda: bool(finished))
    finally:
        engine.close()


@pytest.mark.parametrize(
    "decision",
    [
        Decision("action", "Click the editor", {"op": "click", "x": 30, "y": 30}),
        Decision("action", "Submit", {"op": "press", "key": "ENTER"}),
        Decision("action", "Send a message", {"op": "text", "text": "Hello"}),
    ],
)
def test_automatic_mode_retains_ambiguous_and_sensitive_review(qtbot, decision):
    engine, bridge, _ = controller([decision])
    engine.config = replace(engine.config, approve_each_step=False)
    questions = []
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.approval_requested.connect(lambda rid, text: questions.append(rid))
    try:
        engine.submit("test")
        qtbot.waitUntil(lambda: bool(questions))
        assert not bridge.executed
        engine.respond(questions[0], "reject")
        qtbot.waitUntil(lambda: engine.phase == "idle")
    finally:
        engine.close()


def test_automatic_mode_cannot_dispatch_when_fresh_screen_focus_changed(qtbot):
    engine, bridge, _ = controller(
        [
            Decision("action", "Type hello", {"op": "text", "text": "Hello"}),
            Decision("unable", "Need user intervention"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    frames = []

    def capture():
        frames.append(True)
        engine.observation_ready(
            observation(foreground=77 if len(frames) == 1 else 88), engine.generation
        )

    engine.capture_requested.connect(capture)
    try:
        engine.submit("test")
        qtbot.waitUntil(lambda: engine.phase == "idle")
        assert not bridge.executed and engine.stale == 1
    finally:
        engine.close()


@pytest.mark.parametrize(
    "action",
    [
        {"op": "click", "x": 30, "y": 30, "button": "left", "count": 1},
        {"op": "press", "key": "ENTER"},
    ],
)
def test_automatic_mode_runs_classified_routine_click_and_enter(qtbot, action):
    engine, bridge, _ = controller(
        [
            Decision(
                "action",
                "Focus the editor" if action["op"] == "click" else "Insert a newline",
                action,
                risk="routine",
            ),
            Decision("complete", "Visible result"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    questions = []
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.approval_requested.connect(lambda rid, text: questions.append(text))
    try:
        engine.submit("Work in the open Notepad")
        qtbot.waitUntil(lambda: engine.phase == "confirming")
        assert len(bridge.executed) == 1
        assert len(questions) == 1 and "Confirm the visible result" in questions[0]
    finally:
        engine.close()


@pytest.mark.parametrize(
    "risk, summary",
    [
        ("sensitive", "Click the control"),
        ("uncertain", "Click the control"),
        ("routine", "Send the email"),
        ("routine", "Confirm payment"),
    ],
)
def test_automatic_mode_explains_retained_risk_review(qtbot, risk, summary):
    engine, bridge, _ = controller(
        [Decision("action", summary, {"op": "click", "x": 30, "y": 30}, risk=risk)]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    questions = []
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.approval_requested.connect(lambda rid, text: questions.append(text))
    try:
        engine.submit("test")
        qtbot.waitUntil(lambda: bool(questions))
        assert not bridge.executed
        assert any(word in questions[0] for word in ("sensitive", "uncertain"))
    finally:
        engine.close()


def test_slack_draft_blocks_send_and_never_types_same_message_twice(qtbot):
    content = "Hello, this is a draft for the team."
    engine, bridge, model = controller(
        [
            Decision("action", "Press Enter", {"op": "press", "key": "ENTER"}, risk="routine"),
            Decision(
                "action",
                "Type draft",
                {"op": "text", "text": content},
                risk="routine",
                expected_result="Requested text is visible in the Slack composer, unsent.",
            ),
            Decision("action", "Type draft again", {"op": "text", "text": content}, risk="routine"),
            Decision("complete", "The draft is visible"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    questions, finished = [], []
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.approval_requested.connect(lambda rid, text: questions.append(rid))
    engine.finished.connect(finished.append)
    try:
        engine.submit(f'Type "{content}" in the current Slack message panel. Do not send.')
        qtbot.waitUntil(lambda: engine.phase == "confirming")
        assert len(bridge.executed) == 1
        assert bridge.executed[0][0] == {"op": "text", "text": content}
        feedback = " ".join(model.history[-1])
        assert content in feedback and "succeeded" in feedback
        assert "already inserted" in feedback
        assert not finished
        engine.respond(questions[-1], "approve")
        qtbot.waitUntil(lambda: bool(finished))
    finally:
        engine.close()


def test_wrong_click_cannot_repeat_despite_changed_screenshot_and_coordinate_jitter(qtbot):
    engine, bridge, model = controller(
        [
            Decision("action", "Focus composer", {"op": "click", "x": 10, "y": 10}, risk="routine"),
            Decision("action", "Focus composer", {"op": "click", "x": 13, "y": 12}, risk="routine"),
            Decision("action", "Focus composer", {"op": "click", "x": 11, "y": 10}, risk="routine"),
            Decision(
                "action", "Focus actual composer", {"op": "click", "x": 70, "y": 70}, risk="routine"
            ),
            Decision("action", "Type hello", {"op": "text", "text": "Hello"}, risk="routine"),
            Decision("complete", "Draft visible"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    checks = []
    frames = []

    async def verify(*args):
        checks.append(args)
        last_input = bridge.executed[-1][0]
        correct = last_input["op"] == "text" or last_input.get("x") == -30
        return Verification(
            "succeeded" if correct else "failed",
            "Expected result visible" if correct else "Composer unchanged",
        )

    model.verify_action = verify

    def capture():
        # Every screenshot's bytes/hash differ, as happens with clocks/carets.
        frames.append(True)
        engine.observation_ready(observation(blink=len(frames)), engine.generation)

    engine.capture_requested.connect(capture)
    try:
        engine.submit('Type "Hello" in the open Notepad editor')
        qtbot.waitUntil(lambda: engine.phase == "confirming")
        actions = [entry[0] for entry in bridge.executed]
        assert len(actions) == 3
        assert actions[0]["x"] == -90 and actions[1]["x"] == -30
        assert actions[2] == {"op": "text", "text": "Hello"}
        assert engine.verification_calls == 3
        assert any("different" in line or "rejected" in line for line in engine.history)
    finally:
        engine.close()


def test_uncertain_typing_needs_human_review_without_replay_or_private_diagnostics(qtbot):
    content = "PRIVATE_TYPED_CANARY_8372"
    engine, bridge, model = controller(
        [
            Decision("action", "Type draft", {"op": "text", "text": content}, risk="routine"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    failures, finished = [], []

    async def verify(*args):
        return Verification("uncertain", "PRIVATE_SCREEN_CANARY: cannot see intended composer")

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.failed.connect(failures.append)
    engine.finished.connect(finished.append)
    try:
        engine.submit("Type my supplied message in current Slack composer")
        qtbot.waitUntil(lambda: engine.phase == "confirming")
        assert not finished and not failures
        engine.respond(engine.question_id, "reject")
        qtbot.waitUntil(lambda: bool(failures))
        assert len(bridge.executed) == 1
        assert "Stopped incomplete" in failures[0]
        assert not finished
        diagnostics = engine.failure_diagnostics()
        assert content not in diagnostics and "PRIVATE_SCREEN_CANARY" not in diagnostics
        assert "uncertain" in diagnostics
    finally:
        engine.close()


def test_repeated_bad_proposals_stop_incomplete_before_action_limit(qtbot):
    bad = Decision(
        "action", "Click unchanged target", {"op": "click", "x": 20, "y": 20}, risk="routine"
    )
    engine, bridge, model = controller([bad] * 6)
    engine.config = replace(engine.config, approve_each_step=False)
    failures, finished = [], []

    async def verify(*args):
        return Verification("failed", "Target did not focus")

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.failed.connect(failures.append)
    engine.finished.connect(finished.append)
    try:
        engine.submit("Focus current Slack composer")
        qtbot.waitUntil(lambda: bool(failures))
        assert len(bridge.executed) == 1 and engine.calls < 20
        assert "Stopped incomplete" in failures[0] and not finished
    finally:
        engine.close()


def test_task_limit_is_failure_not_completed(qtbot):
    engine, bridge, _ = controller([])
    failures, finished = [], []

    def capture():
        engine.calls = 20
        engine.observation_ready(observation(), engine.generation)

    engine.capture_requested.connect(capture)
    engine.failed.connect(failures.append)
    engine.finished.connect(finished.append)
    try:
        engine.submit("Type in current composer")
        qtbot.waitUntil(lambda: bool(failures))
        assert "limit" in failures[0] and "incomplete" in failures[0]
        assert not finished and not bridge.executed
    finally:
        engine.close()


def test_uncertain_draft_can_only_complete_after_explicit_user_verification(qtbot):
    engine, bridge, model = controller(
        [
            Decision("action", "Type hello", {"op": "text", "text": "Hello"}, risk="routine"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    finished = []

    async def verify(*args):
        return Verification("uncertain", "Cannot read the small composer text")

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.finished.connect(finished.append)
    try:
        engine.submit('Type "Hello" in the current Slack message panel; leave unsent')
        qtbot.waitUntil(lambda: engine.phase == "confirming")
        assert len(bridge.executed) == 1 and not finished
        engine.respond(engine.question_id, "approve")
        qtbot.waitUntil(lambda: bool(finished))
        assert "confirmed by you" in finished[0] and len(bridge.executed) == 1
    finally:
        engine.close()


def test_draft_policy_is_revalidated_at_dispatch_after_approval(qtbot):
    engine, bridge, _ = controller(
        [Decision("action", "Type hello", {"op": "text", "text": "Hello"}, risk="routine")]
    )
    failures = []
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.failed.connect(failures.append)
    try:
        engine.submit('Type "Hello" in the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: engine.phase == "approval")
        request_id = engine.question_id
        # An altered pending proposal must not bypass dispatch-side policy checks.
        engine.pending = Decision(
            "action", "Press Enter", {"op": "press", "key": "ENTER"}, risk="routine"
        )
        engine.respond(request_id, "approve")
        qtbot.waitUntil(lambda: bool(failures))
        assert not bridge.executed
        assert "incomplete" in failures[0]
    finally:
        engine.close()


def test_current_slack_task_never_redirects_typing_to_new_foreground_window(qtbot):
    engine, bridge, _ = controller(
        [Decision("action", "Type hello", {"op": "text", "text": "Hello"}, risk="routine")]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    frames, failures = [], []

    def capture():
        frames.append(True)
        engine.observation_ready(
            observation(foreground=77 if len(frames) == 1 else 88), engine.generation
        )

    engine.capture_requested.connect(capture)
    engine.failed.connect(failures.append)
    try:
        engine.submit('Type "Hello" in the current Slack message panel')
        qtbot.waitUntil(lambda: bool(failures))
        assert not bridge.executed
        assert "target window changed" in failures[0]
    finally:
        engine.close()


def test_repeat_identity_normalizes_defaults_unicode_and_nearby_coordinates():
    from monster.agent.progress import action_signature, equivalent_action

    assert equivalent_action(
        {"op": "click", "x": 10, "y": 10},
        {"op": "click", "x": 12, "y": 14, "button": "left", "count": 1},
    )
    assert action_signature({"op": "text", "text": "café "}) == action_signature(
        {"op": "text", "text": "cafe\u0301"}
    )


@pytest.mark.parametrize("approve_each_step", [True, False])
@pytest.mark.parametrize(
    "followup",
    [
        {"op": "text", "text": "Hello from Monster!"},
        {"op": "text", "text": " from Monster"},
        {"op": "press", "key": "BACKSPACE"},
        {"op": "chord", "keys": ["SHIFT", "ENTER"]},
    ],
)
def test_draft_insertion_cannot_be_followed_by_more_input(qtbot, approve_each_step, followup):
    engine, bridge, model = controller(
        [
            Decision("action", "Type draft", {"op": "text", "text": "Hello from Monster"}),
            Decision("action", "Adjust draft", followup, risk="routine"),
            Decision("complete", "Requested draft is visible and unsent"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=approve_each_step)
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    # Approve input reviews as a user would. Final confirmation stays unanswered.
    engine.approval_requested.connect(
        lambda rid, text: QTimer.singleShot(0, lambda: engine.respond(rid, "approve"))
        if engine.phase == "approval" else None
    )
    try:
        engine.submit('Type "Hello from Monster" into the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: engine.phase == "confirming")
        assert [entry[0] for entry in bridge.executed] == [
            {"op": "text", "text": "Hello from Monster"}
        ]
        assert engine.actions == 1 and engine.verification_calls == 1
        assert "rejected" in " ".join(model.history[-1]).lower()
    finally:
        engine.close()


@pytest.mark.parametrize("target", [77, 0, "owned"])
@pytest.mark.parametrize("restored", [True, False])
def test_initial_pill_focus_handoff_is_explicit_before_task_start(
    qtbot, qapp, tmp_path, monkeypatch, restored, target
):
    import monster.app as app_module
    import monster.computer.windows as windows_module
    from monster import native
    from monster.lifecycle import MockTaskController

    engine, bridge, _ = controller([])
    ui = app_module.CompanionApp(
        qapp, MockTaskController(), tmp_path / "companion.json", enable_tray=False
    )
    ui.switch_controller(engine)
    foreground = [int(ui.pill.winId()) if target == "owned" else target]
    restores = []
    starts = []

    class Api:
        GetForegroundWindow = staticmethod(lambda: foreground[0])

    class Windows:
        def restore_foreground(self, target):
            assert not ui.pill.isVisible()
            restores.append(target)
            if restored:
                foreground[0] = target
            return restored

    # Stand in for native focus infrastructure, never target text or UI semantics.
    monkeypatch.setattr(native, "user32", lambda: Api())
    monkeypatch.setattr(windows_module, "WindowsInput", Windows)
    monkeypatch.setattr(app_module, "focus_window", lambda window: foreground.__setitem__(0, 999))
    monkeypatch.setattr(ui, "register_hotkeys", list)
    real_submit = engine.submit

    def submit(instruction):
        starts.append(foreground[0])
        return real_submit(instruction)

    monkeypatch.setattr(engine, "submit", submit)
    try:
        ui.activate()
        assert foreground[0] == 999
        # Reopening an already-visible input pill must not replace the saved target.
        ui.activate()
        ui.submit('Type "Hello from Monster" into the current Slack composer. Do not send.')
        assert restores == ([77] if target == 77 else [])
        assert starts == ([77] if restored and target == 77 else [])
        if restored and target == 77:
            assert engine.task_foreground == 77
        else:
            assert "No task was started" in ui.last_failure
        assert not bridge.executed
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


@pytest.mark.parametrize("approve_each_step", [True, False])
def test_pre_pill_target_is_enforced_on_first_capture_after_probe(qtbot, approve_each_step):
    import asyncio
    import threading

    engine, bridge, model = controller(
        [Decision("action", "Type draft", {"op": "text", "text": "Hello from Monster"})]
    )
    engine.config = replace(engine.config, approve_each_step=approve_each_step)
    probing = threading.Event()
    release_probe = threading.Event()
    failures, questions = [], []

    async def probe(model_name):
        probing.set()
        while not release_probe.is_set():
            await asyncio.sleep(0.001)
        return ProbeResult(model_name, "abc", "test", True, True)

    model.probe = probe
    # The GUI pins the pre-pill target immediately after submit. While its model
    # probe is pending, a user changes foreground to another window.
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(foreground=88), engine.generation)
    )
    engine.failed.connect(failures.append)
    engine.approval_requested.connect(lambda rid, text: questions.append(rid))
    try:
        assert engine.submit(
            'Type "Hello from Monster" into the currently open Slack composer. Do not send.'
        )
        engine.task_foreground = 77
        qtbot.waitUntil(probing.is_set)
        release_probe.set()
        qtbot.waitUntil(lambda: engine.phase == "idle")
        assert len(failures) == 1 and "target window changed" in failures[0]
        assert not model.history  # No planning request went to the new foreground.
        assert not bridge.executed and not questions
        assert engine.calls == 0 and engine.actions == 0
    finally:
        release_probe.set()
        engine.close()


def test_task_timings_survive_completion_without_private_payloads(qtbot):
    content = "PRIVATE_DRAFT_CANARY_2918"
    engine, bridge, _ = controller(
        [
            Decision("action", "Type draft", {"op": "text", "text": content}),
            Decision("complete", "Draft is visible"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    finished = []
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.finished.connect(finished.append)
    try:
        engine.submit(f'Type "{content}" into the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: engine.phase == "confirming")
        engine.respond(engine.question_id, "approve")
        qtbot.waitUntil(lambda: bool(finished))
        report = engine.failure_diagnostics()
        assert engine.last_verified_proposal.action == {"op": "text", "text": content}
        assert "Planning: completed 2, failed 0, cancelled 0" in report
        assert "Visual verification: completed 1" in report
        assert "Input dispatch: completed 1" in report
        assert "Input startup: completed 1" in report
        assert "Screenshot capture: completed 4" in report
        assert "Input cleanup: completed 1" in report
        assert content not in report and "Simulated expected" not in report
        assert len(bridge.executed) == 1
    finally:
        engine.close()


def test_cancelled_model_wait_is_reported_and_does_not_finish_late(qtbot):
    import asyncio
    import threading

    engine, bridge, model = controller([])
    probing = threading.Event()

    async def probe(model_name):
        probing.set()
        await asyncio.sleep(30)
        return ProbeResult(model_name, "abc", "test", True, True)

    model.probe = probe
    try:
        assert engine.submit("Type a harmless draft in Slack. Do not send.")
        qtbot.waitUntil(probing.is_set)
        engine.cancel()
        qtbot.waitUntil(lambda: engine.phase == "idle")
        report = engine.failure_diagnostics()
        assert "Model compatibility check: completed 0, failed 0, cancelled 1" in report
        assert "Input cleanup: completed 1" in report
        assert not bridge.executed
    finally:
        engine.close()


@pytest.mark.parametrize("scheduled_from", ["screenshot", "inserted"])
def test_cancelled_capture_timer_cannot_capture_during_next_probe(qtbot, scheduled_from):
    import asyncio
    import threading

    decisions = (
        [Decision("action", "Observe again", {"op": "screenshot"})]
        if scheduled_from == "screenshot"
        else [Decision("action", "Type", {"op": "text", "text": "Hello"})]
    )
    engine, bridge, model = controller(decisions)
    engine.config = replace(engine.config, approve_each_step=False)
    captures = []
    pending = threading.Event()

    def capture():
        captures.append((engine.generation, engine.phase))
        engine.observation_ready(observation(), engine.generation)

    async def slow_probe(model_name):
        pending.set()
        await asyncio.sleep(30)
        return ProbeResult(model_name, "abc", "test", True, True)

    engine.capture_requested.connect(capture)
    try:
        engine.submit("Write Hello in the open Notepad")
        if scheduled_from == "screenshot":
            qtbot.waitUntil(lambda: bool(engine.pending))
        else:
            qtbot.waitUntil(lambda: engine.phase == "verifying_capture")
        engine.cancel()
        qtbot.waitUntil(lambda: engine.phase == "idle")
        model.probe = slow_probe
        engine.submit("Write Hello in the open Notepad")
        qtbot.waitUntil(pending.is_set)
        count = len(captures)
        input_count = len(bridge.executed)
        qtbot.wait(450)  # Pass the old task's 350ms capture deadline.
        assert len(captures) == count
        assert len(bridge.executed) == input_count
        assert engine.phase == "probing"
    finally:
        engine.close()


@pytest.mark.parametrize("cancel", [True, False])
def test_capture_wait_distinguishes_cancel_from_backend_failure(qtbot, cancel):
    engine, bridge, _ = controller([])
    captures = []
    engine.capture_requested.connect(lambda: captures.append(engine.generation))
    try:
        engine.submit("Write a harmless note")
        qtbot.waitUntil(lambda: bool(captures))
        if cancel:
            engine.cancel()
        else:
            engine.capture_failed("Capture backend unavailable")
        qtbot.waitUntil(lambda: engine.phase == "idle")
        expected = "completed 0, failed 0, cancelled 1" if cancel else (
            "completed 0, failed 1, cancelled 0"
        )
        assert f"Screenshot capture: {expected}" in engine.failure_diagnostics()
        assert not bridge.executed
        report = engine.failure_diagnostics()
        engine.observation_ready(observation(), captures[0])
        assert engine.failure_diagnostics() == report
    finally:
        engine.close()


@pytest.mark.parametrize("approve_each_step", [True, False])
@pytest.mark.parametrize("focus_result", ["failed", "uncertain"])
def test_human_focus_confirmation_recovers_without_more_clicks_or_false_completion(
    qtbot, approve_each_step, focus_result
):
    engine, bridge, model = controller(
        [
            Decision("action", "Focus composer", {"op": "click", "x": 10, "y": 10}, risk="routine"),
            Decision("action", "Focus elsewhere", {"op": "click", "x": 70, "y": 70}, risk="routine"),
            Decision("complete", "Focus means done"),
            Decision("action", "Type draft", {"op": "text", "text": "Hello from Monster"}),
            Decision("complete", "Draft visible"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=approve_each_step)
    captures, finished = [], []

    async def verify(*args):
        if bridge.executed[-1][0]["op"] == "click":
            return Verification(focus_result, "Cannot recognize the caret")
        return Verification("succeeded", "Requested text is visible in the editable composer")

    def capture():
        captures.append((engine.phase, engine.restore_focus))
        engine.restore_focus = None  # Simulate the GUI's one-time focus restoration.
        engine.observation_ready(observation(), engine.generation)

    def approve_input(rid, text):
        if engine.phase == "approval":
            QTimer.singleShot(0, lambda: engine.respond(rid, "approve"))

    model.verify_action = verify
    engine.capture_requested.connect(capture)
    engine.approval_requested.connect(approve_input)
    engine.finished.connect(finished.append)
    try:
        engine.submit('Type "Hello from Monster" into the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: engine.phase == "focus_review")
        assert len(bridge.executed) == 1 and not finished
        engine.respond("stale-focus-question", "approve")
        assert engine.phase == "focus_review"
        engine.respond(engine.question_id, "approve")
        qtbot.waitUntil(lambda: engine.phase == "confirming")
        assert [item[0]["op"] for item in bridge.executed] == ["click", "text"]
        assert ("observing", 77) in captures
        assert any("user confirmed" in " ".join(history).lower() for history in model.history)
        assert "visual verification " + focus_result in engine.failure_diagnostics()
        assert "User confirmed composer focus" in engine.failure_diagnostics()
        assert not finished
        engine.respond(engine.question_id, "approve")
        qtbot.waitUntil(lambda: bool(finished))
    finally:
        engine.close()


@pytest.mark.parametrize("intervention", ["decline", "expiry", "stop"])
def test_focus_review_never_continues_without_explicit_confirmation(qtbot, intervention):
    engine, bridge, model = controller(
        [Decision("action", "Focus composer", {"op": "click", "x": 10, "y": 10}, risk="routine")]
    )
    engine.config = replace(engine.config, approve_each_step=False)

    async def verify(*args):
        return Verification("failed", "Cannot see caret")

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit('Type "Hello" in the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: engine.phase == "focus_review")
        rid = engine.question_id
        if intervention == "decline":
            engine.respond(rid, "reject")
        elif intervention == "expiry":
            engine.approval_timer.start(1)
        else:
            engine.cancel()
        qtbot.waitUntil(lambda: engine.phase == "idle")
        engine.respond(rid, "approve")
        assert len(bridge.executed) == 1 and not finished
        assert engine.calls == 1
    finally:
        engine.close()


def test_focus_confirmation_does_not_override_failed_text_verification(qtbot):
    engine, bridge, model = controller(
        [
            Decision("action", "Focus composer", {"op": "click", "x": 10, "y": 10}, risk="routine"),
            Decision("action", "Type draft", {"op": "text", "text": "Hello"}),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    failures, finished = [], []

    async def verify(*args):
        return Verification("failed", "Expected result not visible")

    def review(rid, text):
        if engine.phase == "focus_review":
            QTimer.singleShot(0, lambda: engine.respond(rid, "approve"))

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.approval_requested.connect(review)
    engine.failed.connect(failures.append)
    engine.finished.connect(finished.append)
    try:
        engine.submit('Type "Hello" in the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: bool(failures))
        assert [entry[0]["op"] for entry in bridge.executed] == ["click", "text"]
        assert "Typed text could not be verified" in failures[0]
        assert not finished
    finally:
        engine.close()


def test_focus_confirmation_still_rejects_changed_foreground(qtbot):
    engine, bridge, model = controller(
        [Decision("action", "Focus composer", {"op": "click", "x": 10, "y": 10}, risk="routine")]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    failures = []

    async def verify(*args):
        return Verification("failed", "Caret not recognized")

    def capture():
        engine.observation_ready(
            observation(foreground=88 if engine.human_confirmed_focus else 77), engine.generation
        )

    model.verify_action = verify
    engine.capture_requested.connect(capture)
    engine.failed.connect(failures.append)
    try:
        engine.submit('Type "Hello" in the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: engine.phase == "focus_review")
        engine.respond(engine.question_id, "approve")
        qtbot.waitUntil(lambda: bool(failures))
        assert "target window changed" in failures[0]
        assert len(bridge.executed) == 1 and engine.calls == 1
    finally:
        engine.close()


@pytest.mark.parametrize("mutation", ["replace", "in_place"])
@pytest.mark.parametrize("when", ["before_approval", "during_recheck"])
def test_approval_cannot_authorize_a_changed_valid_text_proposal(qtbot, mutation, when):
    engine, bridge, _ = controller(
        [Decision("action", "Type reviewed text", {"op": "text", "text": "Hello"})]
    )
    questions, failures = [], []

    def alter():
        if mutation == "replace":
            engine.pending = Decision("action", "Type different text", {"op": "text", "text": "Other"})
        else:
            engine.pending.action["text"] = "Other"

    def capture():
        if engine.phase == "rechecking" and when == "during_recheck":
            alter()
        engine.observation_ready(observation(), engine.generation)

    engine.capture_requested.connect(capture)
    engine.approval_requested.connect(lambda rid, text: questions.append(rid))
    engine.failed.connect(failures.append)
    try:
        engine.submit('Type "Hello" into the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: engine.phase == "approval")
        if when == "before_approval":
            alter()
        engine.respond(questions[-1], "approve")
        qtbot.waitUntil(lambda: bool(failures))
        assert not bridge.executed
        assert "proposal changed" in failures[0].lower()
    finally:
        engine.close()


def test_clarification_restores_reviewed_window_before_fresh_observation(qtbot):
    engine, bridge, _ = controller(
        [
            Decision("clarify", "What exact text should I type?"),
            Decision("action", "Type reply", {"op": "text", "text": "Hello"}),
            Decision("complete", "Draft is visible"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    current = [77]
    restores, failures = [], []

    def capture():
        if engine.restore_focus is not None:
            restores.append(engine.restore_focus)
            current[0] = engine.restore_focus
            engine.restore_focus = None
        engine.observation_ready(observation(foreground=current[0]), engine.generation)

    engine.capture_requested.connect(capture)
    engine.clarification_requested.connect(lambda rid, text: current.__setitem__(0, 999))
    engine.failed.connect(failures.append)
    try:
        engine.submit("Type a message into the current Slack composer. Do not send.")
        qtbot.waitUntil(lambda: engine.phase == "clarifying")
        engine.respond(engine.question_id, "Hello")
        qtbot.waitUntil(lambda: engine.phase == "confirming" or bool(failures))
        assert not failures
        assert restores == [77]
        assert [item[0] for item in bridge.executed] == [{"op": "text", "text": "Hello"}]
    finally:
        engine.close()


def test_dismissing_clarification_cancels_without_a_reply_or_new_capture(
    qtbot, qapp, tmp_path, monkeypatch
):
    from monster.app import CompanionApp
    from monster.lifecycle import MockTaskController

    engine, bridge, _ = controller([Decision("clarify", "What text should I type?")])
    ui = CompanionApp(qapp, MockTaskController(), tmp_path / "prefs.json", enable_tray=False)
    captures = []

    def capture():
        captures.append(engine.generation)
        engine.observation_ready(observation(), engine.generation)

    monkeypatch.setattr(ui, "_capture_for_preview", capture)
    ui.switch_controller(engine)
    try:
        engine.submit("Type a message in the current Slack composer. Do not send.")
        qtbot.waitUntil(lambda: engine.phase == "clarifying")
        count = len(captures)
        ui.dismiss()
        qtbot.waitUntil(lambda: engine.phase == "idle")
        assert len(captures) == count
        assert not bridge.executed
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_automatic_step_keeps_exact_proposal_binding_through_recheck(qtbot):
    engine, bridge, _ = controller(
        [Decision("action", "Type planned text", {"op": "text", "text": "Hello"})]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    failures = []

    def capture():
        if engine.phase == "rechecking":
            engine.pending.action["text"] = "Other"
        engine.observation_ready(observation(), engine.generation)

    engine.capture_requested.connect(capture)
    engine.failed.connect(failures.append)
    try:
        engine.submit('Type "Hello" into the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: bool(failures))
        assert not bridge.executed
        assert "proposal changed" in failures[0].lower()
    finally:
        engine.close()
