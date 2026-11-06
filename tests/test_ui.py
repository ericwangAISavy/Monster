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
