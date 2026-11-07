import pytest
from PySide6.QtCore import QPoint, Qt

from monster.config import Preferences, load, save
from monster.lifecycle import State
from monster.ui.companion import Companion
from monster.ui.settings import Settings


def test_companion_layout_preferences_roundtrip_and_old_files(tmp_path):
    path = tmp_path / "prefs.json"
    path.write_text('{"reduced_motion": true}')
    assert load(path).companion_position == "cursor"
    prefs = Preferences(companion_scale=125, companion_position="bottom_right")
    save(path, prefs)
    assert load(path) == prefs
    for field, value in [("companion_scale", 74), ("companion_scale", 151),
                         ("companion_position", "invalid")]:
        prefs = Preferences()
        setattr(prefs, field, value)
        with pytest.raises(ValueError):
            prefs.validate()


def test_companion_labels_are_distinct_and_request_activity_restores_state(qtbot):
    companion = Companion(Preferences(reduced_motion=True))
    qtbot.addWidget(companion)
    labels = []
    for state in State:
        companion.set_state(state)
        labels.append(companion.status_label.text())
        assert companion.status_label.text() in companion.accessibleName()
    assert len(set(labels)) == len(State)
    companion.set_state(State.WAITING)
    companion.set_activity("Vision test")
    assert companion.status_label.text() == "Vision test"
    assert companion.visual_state == State.THINKING
    companion.set_activity("")
    assert companion.status_label.text() == "Review"
    assert companion.visual_state == State.WAITING
    assert companion.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert companion.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus


def test_scaled_corner_companion_does_not_follow_cursor(qtbot, monkeypatch):
    from monster.ui import companion as module

    companion = Companion(Preferences(companion_scale=150, companion_position="top_right",
                                      reduced_motion=True))
    qtbot.addWidget(companion)
    companion.show()
    monkeypatch.setattr(module.QCursor, "pos", lambda: QPoint(10, 10))
    companion.tick()
    first = companion.pos()
    assert companion.width() == 168
    assert companion.height() == 132
    monkeypatch.setattr(module.QCursor, "pos", lambda: QPoint(300, 300))
    companion.tick()
    assert companion.pos() == first
    assert module.QApplication.primaryScreen().availableGeometry().contains(companion.frameGeometry())


def test_companion_settings_save_size_and_placement(qtbot):
    settings = Settings(Preferences())
    qtbot.addWidget(settings)
    saved = []
    settings.saved.connect(saved.append)
    settings.companion_scale.setValue(125)
    settings.companion_position.setCurrentIndex(settings.companion_position.findData("bottom_right"))
    settings.apply()
    assert saved[0].companion_scale == 125
    assert saved[0].companion_position == "bottom_right"


def test_capture_stage_and_pause_label_do_not_claim_model_thinking(qtbot):
    companion = Companion(Preferences())
    qtbot.addWidget(companion)
    companion.set_state(State.WORKING)
    companion.set_stage("Capturing")
    assert companion.status_label.text() == "Capturing"
    assert companion.visual_state == State.WORKING
    companion.set_state(State.THINKING)
    assert companion.status_label.text() == "Thinking"
    companion.set_paused(True)
    assert companion.status_label.text() == "Paused"
    companion.set_activity("Vision test")
    companion.set_activity("")
    assert companion.status_label.text() == "Paused"


def test_reduced_motion_thinking_ring_is_static_and_distinct():
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from monster.ui.companion import draw_monster

    def render(state, phase):
        image = QImage(64, 64, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        draw_monster(painter, QRectF(0, 0, 64, 64), state, phase, reduced=True)
        painter.end()
        return image

    assert render(State.THINKING, 0) == render(State.THINKING, 3)
    assert render(State.THINKING, 0) != render(State.WORKING, 0)


def test_scaled_corner_stays_inside_negative_monitor(qtbot, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtCore import QRect

    from monster.ui import companion as module

    area = QRect(-1920, -300, 1600, 900)
    monkeypatch.setattr(module.QApplication, "primaryScreen",
                        lambda: SimpleNamespace(availableGeometry=lambda: area))
    companion = Companion(Preferences(companion_scale=150, companion_position="bottom_right"))
    qtbot.addWidget(companion)
    companion.show()
    companion.tick()
    assert area.contains(companion.frameGeometry())
    assert companion.pos() == QPoint(-494, 462)
