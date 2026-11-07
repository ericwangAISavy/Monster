from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from monster.app import CompanionApp
from monster.capture import CaptureIsolation
from monster.config import Preferences, load, save
from monster.lifecycle import MockTaskController, State
from monster.ui.pill import Pill


def test_input_submit_escape_and_status_do_not_steal_input(qtbot):
    pill = Pill()
    qtbot.addWidget(pill)
    received, dismissed = [], []
    pill.submitted.connect(received.append)
    pill.dismissed.connect(lambda: dismissed.append(True))
    pill.show()
    pill.input.setText("  Open Notepad  ")
    qtbot.keyClick(pill.input, Qt.Key.Key_Return)
    assert received == ["Open Notepad"]
    qtbot.keyClick(pill.input, Qt.Key.Key_Escape)
    assert dismissed
    pill.show_status("Demo complete", 10)
    assert pill.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    qtbot.waitUntil(lambda: not pill.isVisible())
    pill.show_input()
    pill.show()
    assert not pill.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    qtbot.wait(30)
    assert pill.isVisible()  # Old completion timer cannot hide a new prompt.


def test_approval_requires_explicit_choice(qtbot):
    pill = Pill()
    qtbot.addWidget(pill)
    answers = []
    pill.answered.connect(lambda rid, answer: answers.append((rid, answer)))
    pill.show_question("a1", "Approve this simulated action?", True)
    qtbot.keyClick(pill, Qt.Key.Key_Return)
    assert answers == []
    qtbot.mouseClick(pill.reject, Qt.MouseButton.LeftButton)
    assert answers == [("a1", "reject")]


def test_controller_integration_pill_is_stable_and_collapses(qtbot, qapp, tmp_path):
    engine = MockTaskController(interval_ms=15)
    ui = CompanionApp(qapp, engine, tmp_path / "prefs.json", enable_tray=False)
    try:
        assert not ui.pill.isVisible()
        assert ui.companion.windowFlags() & Qt.WindowType.WindowTransparentForInput
        ui.activate()
        assert ui.pill.isVisible()
        position = ui.pill.pos()
        for _ in range(3):
            ui.companion.tick()
        assert ui.pill.pos() == position
        ui.pill.input.setText("Hello")
        qtbot.keyClick(ui.pill.input, Qt.Key.Key_Return)
        assert not ui.pill.isVisible()
        assert ui.current_state == State.THINKING
        qtbot.waitUntil(lambda: not engine.active)
        assert ui.pill.isVisible()
        assert "Demo complete" in ui.pill.message.text()
        ui.activate()
        assert ui.current_state == State.LISTENING
        ui.stop()
        assert not engine.active
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_isolation_restores_visibility_even_on_failure(qtbot):
    visible, hidden = QWidget(), QWidget()
    qtbot.addWidget(visible)
    qtbot.addWidget(hidden)
    visible.show()
    hidden.hide()
    isolation = CaptureIsolation([visible, hidden])
    errors = []
    isolation.failed.connect(errors.append)

    def backend():
        assert not visible.isVisible() and not hidden.isVisible()
        raise RuntimeError("sensitive provider data must not leak")

    isolation.capture(backend, settle_ms=50)
    assert not visible.isVisible()
    import pytest

    with pytest.raises(RuntimeError):
        isolation.capture(backend)
    qtbot.waitUntil(lambda: not isolation.busy)
    assert visible.isVisible() and not hidden.isVisible()
    assert errors == ["Capture failed: RuntimeError"]


def test_preferences_roundtrip_and_duplicate_keys(tmp_path):
    import pytest

    path = tmp_path / "settings.json"
    prefs = Preferences(reduced_motion=True, offset_x=30)
    save(path, prefs)
    assert load(path) == prefs
    with pytest.raises(ValueError):
        Preferences(stop="Ctrl+Alt+M").validate()


def test_stop_during_capture_never_restores_approval(qtbot, qapp, tmp_path):
    engine = MockTaskController(interval_ms=100)
    ui = CompanionApp(qapp, engine, tmp_path / "prefs.json", enable_tray=False)
    try:
        engine.request_demo("approval")
        assert ui.pill.mode == "approval" and ui.pill.isVisible()
        ui.isolation.capture(lambda: "pixels", settle_ms=50)
        ui.stop()
        qtbot.waitUntil(lambda: not ui.isolation.busy)
        assert ui.pending_question is None
        assert not engine.active
        assert ui.pill.mode != "approval" or not ui.pill.isVisible()
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_new_question_survives_capture(qtbot, qapp, tmp_path):
    engine = MockTaskController(interval_ms=100)
    ui = CompanionApp(qapp, engine, tmp_path / "prefs.json", enable_tray=False)
    try:
        ui.isolation.capture(lambda: "pixels", settle_ms=50)
        engine.request_demo("clarification")
        qtbot.waitUntil(lambda: not ui.isolation.busy)
        assert ui.pending_question is not None
        assert ui.pill.isVisible() and ui.pill.mode == "clarification"
        ui.dismiss()
        assert not engine.active
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_oversized_approval_cannot_be_approved(qtbot):
    pill = Pill()
    qtbot.addWidget(pill)
    answers = []
    pill.answered.connect(lambda rid, value: answers.append(value))
    pill.show_question("oversized", "Detailed action " * 300, True)
    assert not pill.approve.isEnabled()
    pill._answer("approve")
    assert not answers
    qtbot.mouseClick(pill.reject, Qt.MouseButton.LeftButton)
    assert answers == ["reject"]


def test_settings_cancel_discards_edits_and_blocks_capture(qtbot, qapp, tmp_path):
    import pytest

    ui = CompanionApp(qapp, MockTaskController(), tmp_path / "prefs.json", enable_tray=False)
    try:
        ui.show_settings()
        ui.settings_window.fields["activate"].setText("Ctrl+Alt+K")
        with pytest.raises(RuntimeError, match="Close Monster settings"):
            ui.capture_desktop(lambda: "must not execute")
        ui.settings_window.reject()
        assert ui.settings_window is None
        ui.show_settings()
        assert ui.settings_window.fields["activate"].text() == "Ctrl+Alt+M"
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_failure_details_survive_transient_notice_and_are_copyable(qtbot, qapp, tmp_path):
    from PySide6.QtWidgets import QTextEdit

    ui = CompanionApp(qapp, MockTaskController(), tmp_path / "prefs.json", enable_tray=False)
    try:
        ui.failed("Screen recheck failed: Unexpected TypeError at agent/validation.py:10")
        ui.pill.hide()
        ui.show_failure_details()
        details = ui.preview_window.findChild(QTextEdit)
        assert "TypeError" in details.toPlainText()
        assert "Confirmed input transactions" in details.toPlainText()
        assert details.isReadOnly()
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_incomplete_outcome_remains_visible_in_tray_after_notice_hides(qtbot, qapp, tmp_path):
    from types import SimpleNamespace

    tips = []
    ui = CompanionApp(qapp, MockTaskController(), tmp_path / "prefs.json", enable_tray=False)
    ui.tray = SimpleNamespace(setToolTip=tips.append, hide=lambda: None)
    try:
        ui.failed("Stopped incomplete: Task limit reached")
        ui.pill.hide()
        ui._return_idle()
        assert ui.last_task_outcome == "incomplete"
        assert "stopped incomplete" in tips[-1]
        assert "completed" not in tips[-1]
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_passive_approval_pill_can_be_read_without_taking_focus(qtbot):
    pill = Pill()
    qtbot.addWidget(pill)
    pill.show_question("review", "Review this action", True, take_focus=False)
    assert pill.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert pill.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert not pill.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    pill.show_question("clarify", "What text?", False)
    assert not pill.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus


def test_keyboard_approval_waits_for_release_and_is_bound_to_request(
    qtbot, qapp, tmp_path, monkeypatch
):
    import monster.app as module

    engine = MockTaskController()
    ui = CompanionApp(qapp, engine, tmp_path / "prefs.json", enable_tray=False)
    responses = []
    held = [True]
    monkeypatch.setattr(module, "approval_keys_released", lambda: not held[0], raising=False)
    monkeypatch.setattr(ui, "register_hotkeys", list)
    monkeypatch.setattr(engine, "respond", lambda rid, answer: responses.append((rid, answer)))
    try:
        ui.question("first", "Review action", True)
        ui._queue_keyboard_approval()
        qtbot.wait(50)
        assert not responses
        ui.question("second", "Review different action", True)
        held[0] = False
        qtbot.wait(50)
        assert not responses  # Old gesture cannot approve a newer request.
        ui._queue_keyboard_approval()
        qtbot.waitUntil(lambda: bool(responses))
        assert responses == [("second", "approve")]
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_supervised_keyboard_review_does_not_request_focus_or_move_pointer(
    qtbot, qapp, tmp_path, monkeypatch
):
    from PySide6.QtGui import QCursor

    import monster.app as module

    class ReviewedMock(MockTaskController):
        is_supervised = True

    ui = CompanionApp(qapp, ReviewedMock(), tmp_path / "prefs.json", enable_tray=False)
    calls = []
    monkeypatch.setattr(ui, "register_hotkeys", list)
    monkeypatch.setattr(module, "focus_window", lambda window: calls.append(window))
    try:
        before = QCursor.pos()
        ui.question("review", "Approve one action", True)
        assert not calls
        assert QCursor.pos() == before
        assert ui.pill.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        assert "Ctrl+Alt+Y" in ui.pill.message.text()
        ui.question("text", "What text?", False)
        assert calls == [ui.pill]  # Text entry still intentionally takes focus.
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_keyboard_approval_cannot_approve_disabled_or_cancelled_requests(
    qtbot, qapp, tmp_path, monkeypatch
):
    import monster.app as module

    engine = MockTaskController()
    ui = CompanionApp(qapp, engine, tmp_path / "prefs.json", enable_tray=False)
    responses = []
    monkeypatch.setattr(ui, "register_hotkeys", list)
    monkeypatch.setattr(module, "approval_keys_released", lambda: True)
    monkeypatch.setattr(engine, "respond", lambda rid, answer: responses.append((rid, answer)))
    try:
        ui.question("oversized", "x" * 3501, True)
        ui._queue_keyboard_approval()
        qtbot.wait(30)
        assert not responses
        ui.question("clarify", "Which app?", False)
        ui._queue_keyboard_approval()
        qtbot.wait(30)
        assert not responses
        ui.question("cancelled", "Review action", True)
        ui._queue_keyboard_approval()
        ui.stop()
        qtbot.wait(30)
        assert not responses
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_native_approval_waits_for_trigger_key_and_modifiers(monkeypatch):
    from types import SimpleNamespace

    from monster import native

    held = {ord("Y")}
    api = SimpleNamespace(GetAsyncKeyState=lambda key: 0x8000 if key in held else 0)
    monkeypatch.setattr(native, "user32", lambda: api)
    assert not native.approval_keys_released()
    held.clear()
    held.add(0x11)
    assert not native.approval_keys_released()
    held.clear()
    assert native.approval_keys_released()


def test_native_hotkey_ids_are_not_reused_for_new_requests(qapp, monkeypatch):
    from types import SimpleNamespace

    from monster import native

    api = SimpleNamespace(RegisterHotKey=lambda *args: True, UnregisterHotKey=lambda *args: True)
    monkeypatch.setattr(native, "user32", lambda: api)
    hotkeys = native.GlobalHotkeys(qapp)
    try:
        assert not hotkeys.register({"Review": ("Ctrl+Alt+Y", lambda: None)})
        old_ids = set(hotkeys.ids)
        assert not hotkeys.register({"Review": ("Ctrl+Alt+Y", lambda: None)})
        assert old_ids.isdisjoint(hotkeys.ids)
        assert old_ids.isdisjoint(hotkeys.callbacks)
    finally:
        hotkeys.close()


def test_keyboard_registration_failure_keeps_mouse_approval_available(
    qtbot, qapp, tmp_path, monkeypatch
):
    import monster.app as module

    class ReviewedMock(MockTaskController):
        is_supervised = True

    ui = CompanionApp(qapp, ReviewedMock(), tmp_path / "prefs.json", enable_tray=False)
    calls = []
    monkeypatch.setattr(ui, "register_hotkeys", lambda: ["Approve request: unavailable"])
    monkeypatch.setattr(module, "focus_window", lambda window: calls.append(window))
    try:
        ui.question("review", "Review action", True)
        assert calls == [ui.pill]
        assert ui.pill.approve.isEnabled()
        assert not ui.approval_hotkey_available
        assert "Ctrl+Alt+Y" not in ui.pill.message.text()
        ui._queue_keyboard_approval()
        assert ui.keyboard_approval is None
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_report_export_uses_displayed_snapshot_and_requires_explicit_save(
    qtbot, qapp, tmp_path, monkeypatch
):
    import json

    from monster.agent.diagnostics import RunDiagnostics
    from monster.providers.config import OllamaConfig

    ui = CompanionApp(qapp, MockTaskController(), tmp_path / "prefs.json", enable_tray=False)
    first = RunDiagnostics("FIRST TASK", OllamaConfig()).snapshot()
    second = RunDiagnostics("SECOND TASK", OllamaConfig()).snapshot()
    output = tmp_path / "report.json"
    try:
        ui._show_details("Run report", first.text(), "Review before sharing", snapshot=first)
        assert not output.exists()
        ui.last_failure_snapshot = second  # A later failure must not change the displayed export.
        monkeypatch.setattr("monster.app.QFileDialog.getSaveFileName", lambda *args: (str(output), "JSON"))
        ui.save_diagnostic_report()
        assert json.loads(output.read_text())["task"] == "FIRST TASK"
        ui.save_diagnostic_report()  # Existing files are preserved.
        assert json.loads(output.read_text())["task"] == "FIRST TASK"
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_repeated_passive_approval_does_not_hide_or_reconfigure_window(qtbot):
    from PySide6.QtCore import QEvent, QObject

    class Events(QObject):
        def __init__(self):
            super().__init__()
            self.hidden = 0

        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Hide:
                self.hidden += 1
            return False

    pill = Pill()
    qtbot.addWidget(pill)
    pill.show_question("first", "Review this action", True, take_focus=False)
    pill.winId()
    events = Events()
    pill.installEventFilter(events)
    pill.show_question("second", "Review the next action", True, take_focus=False)
    assert events.hidden == 0
    assert pill.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert not pill.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert pill.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert pill.request_id == "second"


def test_experimental_mission_requires_supervised_mode(qtbot,qapp,tmp_path):
    ui=CompanionApp(qapp,MockTaskController(),tmp_path/'prefs.json',enable_tray=False)
    try:
        ui.start_experimental_mission()
        assert ui.mission_runtime is None
        assert ui.controller.is_mock
    finally:
        ui.shutdown()


def test_mission_pending_blocks_manual_input_and_controller_switch(qtbot,qapp,tmp_path):
    from types import SimpleNamespace
    ui=CompanionApp(qapp,MockTaskController(),tmp_path/'prefs.json',enable_tray=False)
    try:
        ui.mission_runtime=SimpleNamespace(engaged=True,stop=lambda:None,close=lambda:None)
        ui.submit('test')
        assert not ui.controller.active
        old=ui.controller
        ui.switch_controller(MockTaskController())
        assert ui.controller is old
    finally:
        ui.shutdown()


def test_retired_mission_update_cannot_hide_current_review(qtbot,qapp,tmp_path):
    from types import SimpleNamespace
    ui=CompanionApp(qapp,MockTaskController(),tmp_path/'prefs.json',enable_tray=False)
    try:
        current=SimpleNamespace(engaged=True,stop=lambda:None,close=lambda:None)
        ui.mission_runtime=current
        ui.pending_question=('mission-new','Current review',True)
        old=object()
        ui._mission_changed({'state':{'status':'cancelled'},'approval':None},source=old)
        assert ui.pending_question[0]=='mission-new'
    finally:
        ui.shutdown()
