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


def test_companion_accessible_states_and_request_activity_restore_without_visible_text(qtbot):
    companion = Companion(Preferences(reduced_motion=True))
    qtbot.addWidget(companion)
    labels = []
    for state in State:
        companion.set_state(state)
        labels.append(companion.accessibleName())
    assert len(set(labels)) == len(State)
    companion.set_state(State.WAITING)
    companion.set_activity("Vision test")
    assert companion.accessibleName() == "Monster · Vision test"
    assert companion.visual_state == State.THINKING
    companion.set_activity("")
    assert companion.accessibleName() == "Monster · Review"
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
    assert companion.width() == 120
    assert companion.height() == 120
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
    assert companion.accessibleName() == "Monster · Capturing"
    assert companion.visual_state == State.WORKING
    companion.set_state(State.THINKING)
    assert companion.accessibleName() == "Monster · Thinking"
    companion.set_paused(True)
    assert companion.accessibleName() == "Monster · Paused"
    companion.set_activity("Vision test")
    companion.set_activity("")
    assert companion.accessibleName() == "Monster · Paused"


def test_reduced_motion_poses_are_static_and_distinct():
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

    for state in State:
        assert render(state, 0) == render(state, 3)
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
    assert companion.pos() == QPoint(-446, 474)


def test_companion_has_no_visible_text_or_status_panel(qtbot):
    from PySide6.QtWidgets import QLabel
    companion = Companion(Preferences())
    qtbot.addWidget(companion)
    assert not companion.findChildren(QLabel)
    assert companion.toolTip() == ""


@pytest.mark.parametrize("state", list(State))
def test_character_draws_neither_text_nor_spinning_arc(state):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from monster.ui.companion import draw_monster

    class CharacterPainter(QPainter):
        def drawArc(self, *args):
            raise AssertionError("Thinking must use a pose, not a spinning arc")

        def drawText(self, *args):
            raise AssertionError("Companion must not draw text")

    image = QImage(80, 80, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = CharacterPainter(image)
    try:
        draw_monster(painter, QRectF(0, 0, 80, 80), state, 1.5)
    finally:
        painter.end()


def test_paused_companion_uses_waiting_pose_and_request_restores_pause(qtbot):
    companion = Companion(Preferences())
    qtbot.addWidget(companion)
    companion.set_state(State.WORKING)
    companion.set_paused(True)
    assert companion.visual_state == State.WAITING
    companion.set_activity("Vision test")
    assert companion.visual_state == State.THINKING
    companion.set_activity("")
    assert companion.visual_state == State.WAITING


@pytest.mark.parametrize("state", [State.THINKING, State.WORKING, State.COMPLETED])
def test_active_character_animates_smoothly_while_parked(qtbot, state):
    companion = Companion(Preferences(companion_position="top_right"))
    qtbot.addWidget(companion)
    companion.show()
    companion.set_state(state)
    companion.tick()
    assert companion.timer.interval() <= 34


def test_working_animation_moves_whole_body_instead_of_only_small_details():
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from monster.ui.character import draw_monster

    def center(phase):
        image = QImage(80, 80, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        draw_monster(painter, QRectF(0, 0, 80, 80), State.WORKING, phase)
        painter.end()
        # Opaque body pixels exclude the stationary translucent shadow/bloom.
        pixels = [(x, y) for y in range(80) for x in range(80)
                  if image.pixelColor(x, y).alpha() > 240]
        return sum(y for _, y in pixels) / len(pixels)

    assert abs(center(0) - center(.35)) > .8


@pytest.mark.parametrize("character", ["monster", "monkey", "rabbit", "tiger", "cat", "pigeon", "ox"])
def test_character_choice_roundtrips_and_reaches_settings(qtbot, tmp_path, character):
    prefs = Preferences(companion_character=character)
    path = tmp_path / 'prefs.json'
    save(path, prefs)
    assert load(path).companion_character == character
    settings = Settings(prefs)
    qtbot.addWidget(settings)
    assert settings.companion_character.currentData() == character
    saved = []
    settings.saved.connect(saved.append)
    settings.apply()
    assert saved[0].companion_character == character


def test_legacy_preferences_keep_original_character_and_reject_unknown(tmp_path):
    path = tmp_path / 'prefs.json'
    path.write_text('{}')
    assert load(path).companion_character == 'monster'
    with pytest.raises(ValueError):
        Preferences(companion_character='unknown').validate()


def test_animal_characters_are_distinct_animated_and_respect_reduced_motion():
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from monster.ui.character import draw_monster

    def render(character, state, phase, reduced=False):
        image = QImage(80, 80, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        try:
            draw_monster(painter, QRectF(0, 0, 80, 80), state, phase,
                         reduced=reduced, character=character)
        finally:
            painter.end()
        return image

    characters = ['monster', 'monkey', 'rabbit', 'tiger', 'cat', 'pigeon', 'ox']
    images = [render(name, State.LISTENING, 0) for name in characters]
    assert all(images[i] != images[j] for i in range(7) for j in range(i))
    for name in characters:
        for state in State:
            assert render(name, state, 0) != render(name, state, .7)
            assert render(name, state, 0, True) == render(name, state, .7, True)


def test_character_picker_previews_without_saving_and_stops_when_hidden(qtbot):
    prefs = Preferences()
    settings = Settings(prefs)
    qtbot.addWidget(settings)
    settings.show()
    settings.companion_character.setCurrentIndex(settings.companion_character.findData('rabbit'))
    assert settings.character_preview.character == 'rabbit'
    assert prefs.companion_character == 'monster'
    assert settings.character_preview.timer.isActive()
    settings.reduced.setChecked(True)
    assert not settings.character_preview.timer.isActive()
    settings.reduced.setChecked(False)
    assert settings.character_preview.timer.isActive()
    settings.reject()
    assert not settings.character_preview.timer.isActive()
    assert prefs.companion_character == 'monster'
