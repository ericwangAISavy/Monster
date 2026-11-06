"""Two independent overlay surfaces, with secondary settings in the tray only."""

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from monster.capture import CaptureIsolation
from monster.config import Preferences, load, save
from monster.geometry import beside_cursor
from monster.lifecycle import MockTaskController, State, TaskController
from monster.native import GlobalHotkeys, focus_window
from monster.ui.companion import Companion, draw_monster
from monster.ui.pill import Pill
from monster.ui.settings import Settings


def monster_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    draw_monster(painter, pixmap.rect(), State.IDLE, 0, True)
    painter.end()
    return QIcon(pixmap)


class CompanionApp:
    def __init__(
        self, app: QApplication, controller: TaskController, config_path: Path, enable_tray=True
    ):
        self.app, self.controller, self.config_path = app, controller, config_path
        self.active = False
        self.paused = False
        self.enabled = True
        self.current_state = State.IDLE
        self.pending_question = None
        self.settings_window = None
        self.closing = False
        self.startup_error = ""
        try:
            self.preferences = load(config_path)
        except (OSError, ValueError, TypeError):
            self.preferences = Preferences()
            self.startup_error = "Could not load settings; defaults are in use."
        self.companion = Companion(self.preferences)
        self.pill = Pill()
        self.isolation = CaptureIsolation([self.companion, self.pill])
        self.isolation.ended.connect(self._capture_ended)
        self._deferred_notice = None
        self.hotkeys = GlobalHotkeys(app)
        self.state_timer = QTimer()
        self.state_timer.setSingleShot(True)
        self.state_timer.timeout.connect(self._return_idle)
        self.pill.submitted.connect(self.submit)
        self.pill.dismissed.connect(self.dismiss)
        self.pill.answered.connect(self.answer)
        controller.state_changed.connect(self.set_state)
        controller.active_changed.connect(self._active_changed)
        controller.paused_changed.connect(self._paused_changed)
        controller.status.connect(self.notice)
        controller.finished.connect(self.completed)
        controller.failed.connect(self.failed)
        controller.approval_requested.connect(lambda rid, text: self.question(rid, text, True))
        controller.clarification_requested.connect(
            lambda rid, text: self.question(rid, text, False)
        )
        self.tray = None
        self.menu = None
        if enable_tray:
            self.create_tray()
        self.companion.show()
        self.isolation.apply_native_hints()
        self.hotkey_errors = self.register_hotkeys()
        self.app.aboutToQuit.connect(self.shutdown)
        # Tray activation is essential when native shortcuts aren't available.
        if enable_tray:
            QTimer.singleShot(300, self.welcome)

    def _active_changed(self, active):
        self.active = active

    def _paused_changed(self, paused):
        self.paused = paused
        if hasattr(self, "pause_action"):
            self.pause_action.setText("Resume" if paused else "Pause")

    def register_hotkeys(self):
        return self.hotkeys.register(
            {
                "Activate": (self.preferences.activate, self.activate),
                "Pause / resume": (self.preferences.pause, self.toggle_pause),
                "Emergency stop": (self.preferences.stop, self.stop),
            }
        )

    def create_tray(self):
        self.tray = QSystemTrayIcon(monster_icon(), self.app)
        self.tray.setToolTip("Monster · GUI demo · no computer control")
        self.menu = QMenu()
        self.add_action("Talk to Monster", self.activate)
        self.pause_action = self.add_action("Pause", self.toggle_pause)
        self.add_action("Stop task", self.stop)
        self.menu.addSeparator()
        toggle = self.add_action("Show companion", self.toggle_companion)
        toggle.setCheckable(True)
        toggle.setChecked(True)
        self.add_action("Settings…", self.show_settings)
        if self.controller.is_mock:
            demo = self.menu.addMenu("GUI demo")
            for kind in ("approval", "clarification"):
                action = demo.addAction(f"Test {kind} pill")
                action.triggered.connect(
                    lambda checked=False, value=kind: self.controller.request_demo(value)
                )
        self.menu.addSeparator()
        self.add_action("Quit Monster", self.app.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(
            lambda reason: (
                self.activate() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
            )
        )
        self.tray.show()

    def add_action(self, text, callback):
        action = QAction(text, self.menu)
        action.triggered.connect(callback)
        self.menu.addAction(action)
        return action

    def welcome(self):
        if not QSystemTrayIcon.isSystemTrayAvailable() and self.hotkey_errors:
            self.notice("No tray or global shortcuts · close with Ctrl+C in the terminal", 6000)
            return
        if self.tray:
            text = "GUI demo only — no model or computer actions. "
            text += self.startup_error or "Activate from the tray or " + self.preferences.activate
            if self.hotkey_errors:
                text += "\n" + "\n".join(self.hotkey_errors)
            self.tray.showMessage("Monster", text)

    def place_pill(self):
        cursor = QCursor.pos()
        screen = self.app.screenAt(cursor) or self.app.primaryScreen()
        if screen:
            from PySide6.QtCore import QPoint

            self.pill.move(
                beside_cursor(cursor, self.pill.size(), screen.availableGeometry(), QPoint(72, -48))
            )

    def activate(self):
        if self.isolation.busy or (self.settings_window and self.settings_window.isVisible()):
            return
        if self.pending_question:
            rid, text, approval = self.pending_question
            self.question(rid, text, approval)
            return
        if self.active:
            # Explicit manual takeover: pause engine before accepting new interaction.
            if not self.paused:
                self.controller.toggle_pause()
            self.notice("Paused · resume or stop from the tray")
            return
        self.state_timer.stop()
        self.pill.show_input()
        self.place_pill()
        self.pill.show()
        focus_window(self.pill)
        self.pill.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.set_state(State.LISTENING)

    def submit(self, instruction):
        if self.isolation.busy:
            return
        if self.controller.submit(instruction):
            self.pill.input.clear()
            self.pill.hide()

    def dismiss(self):
        self.isolation.suppress_restore(self.pill)
        if self.pending_question:
            # Dismissal never grants approval. Explicitly reject/cancel the request.
            self.controller.respond(self.pending_question[0], "reject")
            self.controller.cancel()
            self.pending_question = None
        self.pill.hide()
        if not self.active:
            self.set_state(State.IDLE)

    def set_state(self, state):
        self.current_state = State(state)
        self.companion.set_state(state)
        if self.tray:
            self.tray.setToolTip(
                f"Monster · {'GUI demo · ' if self.controller.is_mock else ''}{state}"
            )

    def _return_idle(self):
        if not self.active and self.pill.mode not in ("input", "clarification", "approval"):
            self.set_state(State.IDLE)

    def notice(self, text, duration_ms=2600):
        if self.closing:
            return
        if self.isolation.busy:
            self._deferred_notice = (text, duration_ms)
            return
        # Never replace a focused prompt or a pending safety request with a timer notice.
        if self.pending_question or (self.pill.isVisible() and self.pill.mode == "input"):
            return
        self.pill.show_status(text, duration_ms)
        self.place_pill()

    def completed(self, text):
        self.pending_question = None
        self.notice(text)
        self.state_timer.start(2700)

    def failed(self, text):
        self.pending_question = None
        self.set_state(State.ERROR)
        self.notice(text, 5000)
        self.state_timer.start(5100)

    def question(self, request_id, text, approval):
        self.pending_question = (request_id, text, approval)
        if self.isolation.busy:
            return
        self.set_state(State.WAITING)
        self.pill.show_question(request_id, text, approval)
        self.place_pill()
        focus_window(self.pill)
        if not approval:
            self.pill.input.setFocus()

    def answer(self, request_id, answer):
        if not self.pending_question or self.pending_question[0] != request_id:
            return
        self.pending_question = None
        self.pill.hide()
        self.controller.respond(request_id, answer)

    def toggle_pause(self):
        self.controller.toggle_pause()
        if self.active and not self.paused:
            self.pill.hide()

    def stop(self):
        # A real controller must synchronously disarm its input worker here.
        self.pending_question = None
        self.isolation.suppress_restore(self.pill)
        self.state_timer.stop()
        self.pill.hide()
        self.controller.cancel()

    def toggle_companion(self, visible):
        self.enabled = visible
        if not visible:
            self.isolation.suppress_restore(self.companion)
        self.companion.setVisible(visible and not self.isolation.busy)

    def _capture_ended(self):
        if self.closing:
            self.pill.hide()
            self.companion.hide()
            return
        self.companion.setVisible(self.enabled)
        if self.pending_question:
            self.question(*self.pending_question)
        elif self._deferred_notice:
            text, duration = self._deferred_notice
            self._deferred_notice = None
            self.notice(text, duration)

    def show_settings(self):
        if self.isolation.busy:
            return
        if self.active and not self.paused:
            self.controller.toggle_pause()
        self.pill.hide()
        if not self.active:
            self.set_state(State.IDLE)
        if not self.settings_window:
            self.settings_window = Settings(self.preferences)
            self.settings_window.saved.connect(self.save_settings)
            self.settings_window.rejected.connect(self._discard_settings)
        self.settings_window.show()
        focus_window(self.settings_window)

    def _discard_settings(self):
        if self.settings_window:
            self.settings_window.deleteLater()
            self.settings_window = None

    def capture_desktop(self, backend):
        """Future screenshot service entry: never include secondary Monster surfaces."""
        if self.settings_window and self.settings_window.isVisible():
            raise RuntimeError("Close Monster settings before taking an agent screenshot.")
        if self.menu and self.menu.isVisible():
            raise RuntimeError("Close the tray menu before taking an agent screenshot.")
        self.isolation.capture(backend)

    def save_settings(self, prefs):
        previous = self.preferences
        self.preferences = prefs
        errors = self.register_hotkeys()
        # On Windows reject conflicts and restore previous working bindings.
        from monster.native import IS_WINDOWS

        if IS_WINDOWS and errors:
            self.preferences = previous
            self.hotkey_errors = self.register_hotkeys()
            self.settings_window.error.setText("\n".join(errors))
            return
        try:
            save(self.config_path, prefs)
        except OSError:
            self.preferences = previous
            self.hotkey_errors = self.register_hotkeys()
            self.settings_window.error.setText(
                "Could not save preferences. Previous settings restored."
            )
            return
        self.hotkey_errors = errors
        self.companion.preferences = prefs
        self.settings_window.accept()
        self.settings_window.deleteLater()
        self.settings_window = None

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.isolation.suppress_restore(self.pill)
        self.isolation.suppress_restore(self.companion)
        self.state_timer.stop()
        self.controller.cancel()
        self.hotkeys.close()
        self.companion.timer.stop()
        self.pill._hide_timer.stop()
        if self.tray:
            self.tray.hide()
        self.pill.close()
        self.companion.close()
        if self.settings_window:
            self.settings_window.close()


def main():
    parser = argparse.ArgumentParser(description="Monster cursor companion — GUI demo only")
    parser.add_argument("--smoke-test", action="store_true", help="Exit after one second")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Monster")
    app.setOrganizationName("Monster")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(monster_icon())
    path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
    ui = CompanionApp(app, MockTaskController(), path / "companion.json")
    if args.smoke_test:
        QTimer.singleShot(1000, app.quit)
    # Native console interrupt is supported without relying on an invisible main window.
    import signal

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(250)
    exit_code = app.exec()
    ui.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
