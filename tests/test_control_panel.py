from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from monster.app import CompanionApp
from monster.lifecycle import MockTaskController
from monster.ui.control_panel import ControlPanel


def test_panel_docks_and_stays_open_until_dismissed(qtbot, qapp):
    panel = ControlPanel()
    qtbot.addWidget(panel)
    panel.open()
    assert panel.isVisible()
    assert panel.grab().toImage().pixelColor(10, 50).alpha() == 255
    assert panel.screen().availableGeometry().contains(panel.frameGeometry())
    other = QDialog()
    qtbot.addWidget(other)
    other.show()
    other.activateWindow()
    qapp.processEvents()
    assert panel.isVisible()
    qtbot.keyClick(panel, Qt.Key.Key_Escape)
    assert not panel.isVisible()


def test_tray_actions_open_embedded_settings_and_preserve_save(qtbot, qapp, tmp_path):
    ui = CompanionApp(qapp, MockTaskController(), tmp_path / 'prefs.json', enable_tray=False)
    ui.create_tray()
    panel = ui.control_panel
    try:
        assert ui.tray.contextMenu() is None
        ui.toggle_control_panel()
        assert panel.isVisible()
        ui.show_settings()
        settings = ui.settings_window
        assert settings.isVisible() and settings.window() is panel
        settings.reduced.setChecked(True)
        settings.apply()
        assert ui.preferences.reduced_motion
        assert panel.stack.currentWidget() is panel.home_page
        ui.show_provider_settings()
        assert ui.provider_window.isVisible()
        assert ui.provider_window.window() is panel
        ui.provider_window.reject()
        qapp.processEvents()
        assert panel.stack.currentWidget() is panel.home_page
    finally:
        ui.shutdown()


def test_action_buttons_follow_real_action_state_and_hide_before_invocation(qtbot):
    from PySide6.QtGui import QAction

    panel = ControlPanel()
    qtbot.addWidget(panel)
    action = QAction('Pause')
    seen = []
    action.triggered.connect(lambda: seen.append(panel.isVisible()))
    button = panel.action_button(panel.section('Task'), action)
    panel.open()
    action.setText('Resume')
    assert button.text() == 'Resume'
    action.setEnabled(False)
    assert not button.isEnabled()
    action.setEnabled(True)
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert seen == [False]


def test_panel_is_excluded_from_agent_capture(qtbot, qapp, tmp_path):
    ui = CompanionApp(qapp, MockTaskController(), tmp_path / 'prefs.json', enable_tray=False)
    ui.create_tray()
    panel = ui.control_panel
    completed = []
    try:
        panel.open()
        ui.isolation.completed.connect(completed.append)
        ui.capture_desktop(lambda: panel.isVisible())
        qtbot.waitUntil(lambda: bool(completed), timeout=2000)
        assert completed == [False]
        assert panel.isVisible()
    finally:
        ui.shutdown()


def test_back_navigation_preserves_embedded_form(qtbot):
    panel = ControlPanel()
    qtbot.addWidget(panel)
    form = QDialog()
    layout = QVBoxLayout(form)
    layout.addWidget(QLabel('Settings'))
    panel.present(form)
    panel.home()
    assert panel.stack.currentWidget() is panel.home_page
    assert not form.isVisible()
    panel.present(form)
    assert form.isVisible() and len(panel.detail_pages) == 1


def test_companion_switch_uses_real_action_without_closing_panel(qtbot, qapp, tmp_path):
    from monster.ui.control_panel import PanelSwitch

    ui = CompanionApp(qapp, MockTaskController(), tmp_path / 'prefs.json', enable_tray=False)
    ui.create_tray()
    try:
        ui.control_panel.open()
        switch = ui.control_panel.findChild(PanelSwitch)
        assert switch.isChecked() and ui.companion.isVisible()
        qtbot.mouseClick(switch, Qt.MouseButton.LeftButton)
        assert not ui.enabled and not ui.companion.isVisible()
        assert ui.control_panel.isVisible()
        qtbot.keyClick(switch, Qt.Key.Key_Space)
        assert ui.enabled and ui.companion.isVisible()
    finally:
        ui.shutdown()
