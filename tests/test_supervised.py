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
from monster.providers.base import Decision, ProbeResult
from monster.providers.config import OllamaConfig


def observation(color="#ffffff", foreground=77):
    image = QImage(100, 100, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
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
