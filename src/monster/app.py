"""Two independent overlay surfaces, with secondary settings in the tray only."""

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
)

from monster.agent.controller import SupervisedController
from monster.capture import CaptureIsolation
from monster.config import Preferences, load, save
from monster.geometry import beside_cursor
from monster.lifecycle import MockTaskController, State, TaskController
from monster.native import GlobalHotkeys, focus_window, foreground_window
from monster.preview import PreviewTaskController
from monster.providers import config as provider_config
from monster.ui.companion import Companion, draw_monster
from monster.ui.pill import Pill
from monster.ui.provider_settings import ProviderSettings
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
        self.input_owns_stop = False
        self.input_cleanup_blocked = False
        self.active = False
        self.paused = False
        self.enabled = True
        self.current_state = State.IDLE
        self.pending_question = None
        self.settings_window = None
        self.provider_window = None
        self.preview_window = None
        self.last_preview = None
        self.last_failure = None
        self.last_task_outcome = None
        self.preview_capture = None
        self.activation_target = None
        self.controller_bindings = []
        self.startup_error = ""
        self.provider_path = config_path.with_name("ollama.json")
        try:
            self.model_config = provider_config.load(self.provider_path)
        except (OSError, ValueError, TypeError):
            self.model_config = provider_config.OllamaConfig()
            self.startup_error = "Could not load model settings; GUI demo is in use."
        self.closing = False
        try:
            self.preferences = load(config_path)
        except (OSError, ValueError, TypeError):
            self.preferences = Preferences()
            self.startup_error = "Could not load settings; defaults are in use."
        self.companion = Companion(self.preferences)
        self.pill = Pill()
        self.isolation = CaptureIsolation([self.companion, self.pill])
        self.isolation.ended.connect(self._capture_ended)
        self.isolation.completed.connect(self._preview_observation)
        self.isolation.failed.connect(self._preview_capture_failed)
        self._deferred_notice = None
        self.hotkeys = GlobalHotkeys(app)
        self.state_timer = QTimer()
        self.state_timer.setSingleShot(True)
        self.state_timer.timeout.connect(self._return_idle)
        self.pill.submitted.connect(self.submit)
        self.pill.dismissed.connect(self.dismiss)
        self.pill.answered.connect(self.answer)
        self.bind_controller(controller)
        self.tray = None
        self.menu = None
        if enable_tray:
            self.create_tray()
        self.companion.show()
        self.isolation.apply_native_hints()
        self.hotkey_errors = self.register_hotkeys()
        self.app.aboutToQuit.connect(self.shutdown)
        if self.model_config.mode in {"preview", "supervised"}:
            factory = (
                SupervisedController
                if self.model_config.mode == "supervised"
                else PreviewTaskController
            )
            self.switch_controller(factory(self.model_config))
        # Tray activation is essential when native shortcuts aren't available.
        if enable_tray:
            QTimer.singleShot(300, self.welcome)

    def _active_changed(self, active):
        self.active = active
        if not active:
            self.pending_question = None
            if self.pill.mode in {"approval", "clarification"}:
                self.pill.hide()

    def bind_controller(self, controller):
        self.controller = controller
        bindings = [
            (controller.state_changed, self.set_state),
            (controller.active_changed, self._active_changed),
            (controller.paused_changed, self._paused_changed),
            (controller.status, self.notice),
            (controller.finished, self.completed),
            (controller.failed, self.failed),
            (controller.approval_requested, lambda rid, text: self.question(rid, text, True)),
            (controller.clarification_requested, lambda rid, text: self.question(rid, text, False)),
        ]
        if isinstance(controller, (PreviewTaskController, SupervisedController)):
            bindings.extend(
                [
                    (controller.capture_requested, self._capture_for_preview),
                    (controller.preview_ready, self._show_preview),
                ]
            )
        if isinstance(controller, SupervisedController):
            bindings.append((controller.worker_owned, self._worker_ownership))
        for signal, callback in bindings:
            signal.connect(callback)
        self.controller_bindings = bindings

    def switch_controller(self, controller):
        previous = self.controller
        for signal, callback in self.controller_bindings:
            signal.disconnect(callback)
        previous.cancel()
        if hasattr(previous, "close"):
            previous.close()
        self.input_cleanup_blocked |= getattr(previous, "cleanup_blocked", False)
        self.input_owns_stop = False
        self.register_hotkeys()
        self.bind_controller(controller)
        self.pending_question = None
        self.activation_target = None
        self.last_preview = None
        self.last_task_outcome = None
        self.active, self.paused = False, False
        self._paused_changed(False)
        self.pill.hide()
        self.pill.input.setToolTip(
            "Preview only · sends one screenshot to your configured LAN server"
            if not controller.is_mock
            else "GUI demo · no input or inference"
        )
        self.set_state(State.IDLE)
        if hasattr(self, "demo_menu"):
            self.demo_menu.menuAction().setVisible(controller.is_mock)

    def _paused_changed(self, paused):
        self.paused = paused
        if hasattr(self, "pause_action"):
            self.pause_action.setText(
                "Stop / manual takeover"
                if getattr(self.controller, "is_supervised", False)
                else "Resume"
                if paused
                else "Pause"
            )

    def _worker_ownership(self, owned):
        self.input_owns_stop = owned
        if not owned:
            self.input_cleanup_blocked |= getattr(self.controller, "cleanup_blocked", False)
        self.hotkey_errors = self.register_hotkeys()

    def register_hotkeys(self):
        from monster.native import parse_hotkey

        bindings = {
            "Activate": (self.preferences.activate, self.activate),
            "Pause / resume": (self.preferences.pause, self.toggle_pause),
        }
        if not self.input_owns_stop or parse_hotkey(self.preferences.stop) != (3, ord("X")):
            bindings["Emergency stop"] = (self.preferences.stop, self.stop)
        return self.hotkeys.register(bindings)

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
        self.add_action("AI model settings…", self.show_provider_settings)
        self.add_action("Last action preview…", self.show_preview_details)
        self.add_action("Last failure…", self.show_failure_details)
        if self.controller.is_mock:
            demo = self.menu.addMenu("GUI demo")
            self.demo_menu = demo
            for kind in ("approval", "clarification"):
                action = demo.addAction(f"Test {kind} pill")
                action.triggered.connect(
                    lambda checked=False, value=kind: (
                        self.controller.request_demo(value) if self.controller.is_mock else None
                    )
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
            text = (
                "GUI demo only — no model or computer actions. "
                if self.controller.is_mock
                else (
                    (
                        "Real input — "
                        + (
                            "approve each action"
                            if self.model_config.approve_each_step
                            else "routine steps automatic"
                        )
                        + "; Ctrl+Alt+X stops. "
                    )
                    if getattr(self.controller, "is_supervised", False)
                    else "Ollama preview only — one screenshot per task; no input actions. "
                )
            )
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
        if (self.provider_window and self.provider_window.isVisible()) or (
            self.preview_window and self.preview_window.isVisible()
        ):
            return
        if self.pending_question:
            rid, text, approval = self.pending_question
            self.question(rid, text, approval)
            return
        if self.active:
            if getattr(self.controller, "is_supervised", False):
                self.stop()
                return
            # Explicit manual takeover: pause engine before accepting new interaction.
            if not self.paused:
                self.controller.toggle_pause()
            self.notice("Paused · resume or stop from the tray")
            return
        if getattr(self.controller, "is_supervised", False) and not (
            self.pill.isVisible() and self.pill.mode == "input"
        ):
            target = foreground_window()
            owned = {int(self.pill.winId()), int(self.companion.winId())}
            self.activation_target = target if target and target not in owned else None
        self.state_timer.stop()
        self.pill.show_input()
        if not self.controller.is_mock:
            self.pill.input.setToolTip(
                (
                    "SUPERVISED REAL INPUT · "
                    if getattr(self.controller, "is_supervised", False)
                    else "Preview only · "
                )
                + "screenshot sent to "
                + self.model_config.url
            )
        self.place_pill()
        self.pill.show()
        focus_window(self.pill)
        self.pill.input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.set_state(State.LISTENING)

    def submit(self, instruction):
        if self.isolation.busy or self.active or not instruction.strip():
            return
        target = self.activation_target
        if getattr(self.controller, "is_supervised", False):
            from monster.computer.windows import WindowsInput

            self.pill.hide()
            try:
                if not target or not WindowsInput().restore_foreground(target):
                    raise RuntimeError("Cannot restore the initial target window.")
            except RuntimeError:
                self.activation_target = None
                self.failed(
                    "Stopped incomplete: Cannot restore the initial target window. "
                    "Focus the intended composer and activate Monster again. No task was started."
                )
                return
        if self.controller.submit(instruction):
            if getattr(self.controller, "is_supervised", False):
                # Pin the current-composer task before any asynchronous capture.
                self.controller.task_foreground = target
            self.activation_target = None
            self.last_task_outcome = "running"
            self.pill.input.clear()
            self.pill.hide()

    def dismiss(self):
        self.activation_target = None
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
            mode = "GUI demo" if self.controller.is_mock else "Ollama preview"
            if getattr(self.controller, "is_supervised", False):
                mode = "Real input · " + (
                    "step approval ON"
                    if self.model_config.approve_each_step
                    else "routine steps automatic"
                )
            outcome = (
                "stopped incomplete · Last failure"
                if state == State.IDLE and self.last_task_outcome == "incomplete"
                else state
            )
            self.tray.setToolTip(f"Monster · {mode} · {outcome}")

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
        self.last_task_outcome = "completed"
        self.pending_question = None
        self.notice(text)
        self.state_timer.start(2700)

    def failed(self, text):
        self.last_task_outcome = "incomplete"
        self.last_failure = (
            text
            + "\n\nConfirmed input transactions: "
            + str(getattr(self.controller, "actions", 0))
            + "\nInspect the desktop: failed or partial input may have occurred. "
            "No failed or ambiguous input transaction was automatically replayed."
        )
        if hasattr(self.controller, "failure_diagnostics"):
            self.last_failure += "\n\n" + self.controller.failure_diagnostics()
        self.pending_question = None
        self.set_state(State.ERROR)
        self.notice("Stopped incomplete · open tray → Last failure for details", 5000)
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
        self.activation_target = None
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
        if self._focus_secondary_except(self.settings_window):
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
        if (self.provider_window and self.provider_window.isVisible()) or (
            self.preview_window and self.preview_window.isVisible()
        ):
            raise RuntimeError("Close Monster model settings/preview before taking a screenshot.")
        if self.menu and self.menu.isVisible():
            raise RuntimeError("Close the tray menu before taking an agent screenshot.")
        self.isolation.capture(backend)

    def _capture_for_preview(self):
        from monster.computer.screenshot import capture_display

        self.preview_capture = (self.controller, self.controller.generation)
        try:
            target = getattr(self.controller, "restore_focus", None)
            if target:
                from monster.computer.windows import WindowsInput

                self.controller.restore_focus = None
                if not WindowsInput().restore_foreground(target):
                    raise RuntimeError("Cannot restore the approved target's focus.")
            self.capture_desktop(lambda: capture_display(None))
        except RuntimeError as exc:
            self.preview_capture = None
            self.controller.capture_failed(str(exc))

    def _preview_observation(self, observation):
        if self.preview_capture:
            controller, generation = self.preview_capture
            self.preview_capture = None
            controller.observation_ready(observation, generation)

    def _preview_capture_failed(self, error):
        if self.preview_capture:
            controller, generation = self.preview_capture
            self.preview_capture = None
            if generation == controller.generation:
                controller.capture_failed(error)

    def _show_preview(self, decision):
        self.last_preview = decision

    def show_preview_details(self):
        if not self.last_preview:
            return
        import json

        text = (
            f"{self.last_preview.kind}: {self.last_preview.summary}\n"
            f"Risk: {self.last_preview.risk or 'unclassified'}\n\n"
            + json.dumps(self.last_preview.action, ensure_ascii=False, indent=2)
        )
        verification = getattr(self.controller, "last_verification", None)
        if verification:
            text += (
                f"\n\nLast executed action visual check: {verification.status}\n"
                f"Evidence: {verification.evidence}"
            )
        timings = getattr(self.controller, "timings", None)
        if timings is not None:
            text += "\n\n" + timings.report()
        self._show_details(
            "Monster · action proposal",
            text,
            "Model proposal only. Inspect the desktop to verify the result.\n"
            "Coordinates refer to the captured target display's image.",
        )

    def show_failure_details(self):
        if self.last_failure:
            self._show_details(
                "Monster · last failure",
                self.last_failure,
                "Review and copy these details when reporting the failure. "
                "Unexpected-error diagnostics omit exception messages and payloads.",
            )

    def _show_details(self, title, text, description):
        if self.isolation.busy or self._focus_secondary_except(self.preview_window):
            return
        self._pause_for_secondary()
        if self.preview_window:
            self.preview_window.setWindowTitle(title)
            self.preview_window.findChild(QTextEdit).setPlainText(text)
            self.preview_window.findChild(QLabel).setText(description)
            focus_window(self.preview_window)
            return
        dialog = QDialog()
        self.preview_window = dialog
        dialog.setWindowTitle(title)
        dialog.resize(480, 290)
        layout = QVBoxLayout(dialog)
        label = QLabel(description)
        label.setWordWrap(True)
        layout.addWidget(label)
        details = QTextEdit()
        details.setReadOnly(True)
        details.setPlainText(text)
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.finished.connect(self._close_preview_details)
        dialog.show()
        focus_window(dialog)

    def _close_preview_details(self):
        if self.preview_window:
            self.preview_window.deleteLater()
            self.preview_window = None

    def _pause_for_secondary(self):
        if self.active and not self.paused:
            self.controller.toggle_pause()
        self.pill.hide()

    def _focus_secondary_except(self, current):
        for window in (self.settings_window, self.provider_window, self.preview_window):
            if window is not None and window is not current and window.isVisible():
                focus_window(window)
                return True
        return False

    def show_provider_settings(self):
        if self.isolation.busy:
            return
        if self._focus_secondary_except(self.provider_window):
            return
        self._pause_for_secondary()
        if not self.provider_window:
            self.provider_window = ProviderSettings(self.model_config)
            self.provider_window.saved.connect(self.save_provider_settings)
            self.provider_window.finished.connect(self._discard_provider_settings)
        self.provider_window.show()
        focus_window(self.provider_window)

    def _discard_provider_settings(self):
        if self.provider_window:
            self.provider_window.deleteLater()
            self.provider_window = None

    def save_provider_settings(self, config):
        if config.mode == "supervised":
            if self.input_cleanup_blocked:
                self.provider_window.status.setText(
                    "Input cleanup unconfirmed. Inspect input and restart Monster."
                )
                return
            from monster.native import parse_hotkey

            if any(
                parse_hotkey(key) == (3, ord("X"))
                for key in (self.preferences.activate, self.preferences.pause)
            ):
                self.provider_window.status.setText(
                    "Ctrl+Alt+X is reserved for the real input worker."
                )
                return
        try:
            provider_config.save(self.provider_path, config)
        except OSError:
            self.provider_window.status.setText("Could not save provider settings.")
            return
        self.model_config = config
        factory = {
            "demo": lambda _: MockTaskController(),
            "preview": PreviewTaskController,
            "supervised": SupervisedController,
        }[config.mode]
        self.switch_controller(factory(config))
        self.provider_window.accept()

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
        if hasattr(self.controller, "close"):
            self.controller.close()
        self.hotkeys.close()
        self.companion.timer.stop()
        self.pill._hide_timer.stop()
        if self.tray:
            self.tray.hide()
        self.pill.close()
        self.companion.close()
        if self.settings_window:
            self.settings_window.close()
        if self.provider_window:
            self.provider_window.close()
        if self.preview_window:
            self.preview_window.close()


def main():
    parser = argparse.ArgumentParser(
        description="Monster companion — demo, LAN preview or supervised Windows input"
    )
    parser.add_argument("--smoke-test", action="store_true", help="Exit after one second")
    parser.add_argument(
        "--capture-test",
        type=Path,
        help="Windows diagnostic: save an isolated PNG, then keep the GUI open",
    )
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Monster")
    app.setOrganizationName("Monster")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(monster_icon())
    path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
    ui = CompanionApp(app, MockTaskController(), path / "companion.json")
    if args.capture_test:
        from monster.computer.screenshot import capture_display

        def save_observation(observation):
            try:
                with args.capture_test.open("xb") as output:
                    output.write(observation.png)
            except OSError:
                ui.notice("Capture not saved · choose a new writable output path", 5000)
            else:
                ui.notice("Screenshot saved · inspect it for overlay contamination", 5000)

        ui.isolation.completed.connect(save_observation)
        ui.isolation.failed.connect(lambda text: ui.notice(text, 5000))

        def diagnostic_capture():
            ui.activate()
            QTimer.singleShot(500, lambda: ui.capture_desktop(lambda: capture_display(None)))

        QTimer.singleShot(1000, diagnostic_capture)
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
