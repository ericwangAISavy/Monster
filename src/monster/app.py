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
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
)

from monster.agent.controller import SupervisedController
from monster.agent.diagnostics import exception_evidence
from monster.capture import CaptureIsolation
from monster.config import Preferences, load, save
from monster.geometry import beside_cursor
from monster.lifecycle import MockTaskController, State, TaskController
from monster.native import GlobalHotkeys, approval_keys_released, focus_window, foreground_window
from monster.preview import PreviewTaskController
from monster.providers import config as provider_config
from monster.ui.companion import Companion, draw_monster
from monster.ui.control_panel import ControlPanel
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
        from monster.mission.workspace import WorkspaceGate, session_lock_path
        self.workspace_gate = WorkspaceGate(session_lock_path())
        self.manual_lease = None
        self.manual_generation = None
        self.mission_runtime = None
        self.strategic_review = None
        self.workspace_mode = "personal_desktop"
        self.workspace_runtime = None
        self.workspace_window = None
        self.workspace_registry = None
        self.phase4_qualification = None
        self.reconciliation_effect = None
        self.mission_review_timer = QTimer()
        self.mission_review_timer.setSingleShot(True)
        self.mission_review_timer.timeout.connect(self.stop)
        self.input_cleanup_blocked = False
        self.active = False
        self.paused = False
        self.enabled = True
        self.current_state = State.IDLE
        self.pending_question = None
        self.keyboard_approval = None
        self.approval_hotkey_available = False
        self.approval_key_timer = QTimer()
        self.approval_key_timer.setInterval(20)
        self.approval_key_timer.timeout.connect(self._finish_keyboard_approval)
        self.settings_window = None
        self.provider_window = None
        self.preview_window = None
        self.last_preview = None
        self.last_failure = None
        self.last_failure_snapshot = None
        self.details_snapshot = None
        self.report_export_buttons = []
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
        self.isolation.diagnostic.connect(self._capture_diagnostic)
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
        self.control_panel = None
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
            self._cancel_keyboard_approval()
            if not self.closing:
                self.hotkey_errors = self.register_hotkeys()
            if self.pill.mode in {"approval", "clarification"}:
                self.pill.hide()

    def bind_controller(self, controller):
        self.controller = controller
        bindings = [
            (controller.state_changed, self.set_state),
            (controller.stage_changed, self.companion.set_stage),
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
            from monster.agent.standalone_journal import StandaloneJournal
            controller.standalone_journal = StandaloneJournal(self.config_path.parent / 'missions', controller)
            controller.standalone_journal.replied.connect(controller.standalone_reply)
            controller.standalone_lease_valid = lambda: (
                self.manual_lease is not None and self.workspace_gate.current == self.manual_lease
                and not self.workspace_gate.quarantined and self.manual_generation == controller.run_generation)
            bindings.append((controller.worker_owned, self._worker_ownership))
            bindings.append((controller.terminal_ready, self._manual_terminal))
        for signal, callback in bindings:
            signal.connect(callback)
        self.controller_bindings = bindings

    def switch_controller(self, controller):
        if self.workspace_gate.busy or (self.mission_runtime and self.mission_runtime.engaged):
            if hasattr(controller, "close"):
                controller.close()
            self.notice("Stop the mission and confirm cleanup before changing modes.")
            return
        if self.mission_runtime:
            self.mission_runtime.close()
            self.mission_runtime = None
        self._cancel_keyboard_approval()
        previous = self.controller
        for signal, callback in self.controller_bindings:
            signal.disconnect(callback)
        previous.cancel()
        if hasattr(previous, "close"):
            previous.close()
        self.input_cleanup_blocked |= getattr(previous, "cleanup_blocked", False)
        self.input_owns_stop = False
        self.pending_question = None
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
        self.companion.set_paused(paused)
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
        conflicts = []
        if self.pending_question and self.pending_question[2]:
            shortcut = "Ctrl+Alt+Y"
            if any(parse_hotkey(item[0]) == parse_hotkey(shortcut) for item in bindings.values()):
                conflicts.append("Approve request: Ctrl+Alt+Y conflicts with another Monster shortcut.")
            else:
                controller, rid = self.controller, self.pending_question[0]
                bindings["Approve request"] = (
                    shortcut, lambda: self._queue_keyboard_approval(controller, rid)
                )
        return self.hotkeys.register(bindings) + conflicts

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
        self.add_action("Last run report…", self.show_run_report)
        self.add_action("Experimental Mission · two Notepad drafts…", self.start_experimental_mission)
        self.add_action("Resume latest experimental mission…", lambda: self.start_experimental_mission(resume=True))
        self.add_action("Mission effects · observe-only review…", self.review_mission_effects)
        self.add_action("Phase 4 · six-stage simulation…", lambda: self.start_phase4("simulation"))
        self.add_action("Phase 4 · observe-only rehearsal…", lambda: self.start_phase4("observe_only"))
        self.add_action("Phase 4 · qualified native pilot…", lambda: self.start_phase4("supervised_pilot"))
        self.add_action("Phase 4 · resume simulation…", lambda: self.start_phase4("simulation", resume=True))
        self.add_action("Phase 4 · resume qualified pilot…", lambda: self.start_phase4("supervised_pilot", resume=True))
        self.add_action("Phase 5 · plan and simulate…", self.start_phase5)
        self.add_action("Phase 5 · safe replan simulation…", lambda: self.start_phase5(replan=True))
        self.add_action("Phase 5 · review safe revision…", self.propose_phase5_revision)
        self.add_action("Phase 5 · resume simulation…", lambda: self.start_phase5(resume=True))
        self.add_action("Mission progress…", self.show_mission_progress)
        self.add_action("Mission · pause after stage", self.pause_mission_boundary)
        self.add_action("Mission · resume / request consent", self.resume_mission_boundary)
        self.add_action("Workspaces…", self.show_workspaces)
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
        self.control_panel = ControlPanel()
        self.isolation.windows.append(self.control_panel)
        self._build_control_panel()
        self.tray.activated.connect(
            lambda reason: self.toggle_control_panel()
            if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                          QSystemTrayIcon.ActivationReason.Context) else None
        )
        self.tray.show()

    def _build_control_panel(self):
        panel = self.control_panel
        sections = {
            "task": panel.section("Your assistant", "Give Monster a task. Stay in control."),
            "settings": panel.section("Make it yours"),
            "activity": panel.section("Activity & diagnostics"),
            "missions": panel.section("Missions & workspaces", "Experimental workflows and workspace controls.", collapsed=True),
            "app": panel.section("Application"),
        }
        task_secondary = QHBoxLayout()
        sections["task"].addLayout(task_secondary)
        for action in self.menu.actions():
            if action.isSeparator():
                continue
            text = action.text()
            if action.menu():
                demo = panel.section("Demo tools", collapsed=True)
                demo_frame = demo.parentWidget().parentWidget()
                action.changed.connect(
                    lambda current=action, frame=demo_frame: frame.setVisible(current.isVisible())
                )
                demo_frame.setVisible(action.isVisible())
                for child in action.menu().actions():
                    panel.action_button(demo, child)
                continue
            if text in ("Talk to Monster", "Pause", "Stop task"):
                group = "task"
            elif text in ("Show companion", "Settings…", "AI model settings…"):
                group = "settings"
            elif text.startswith("Last "):
                group = "activity"
            elif text == "Quit Monster":
                group = "app"
            else:
                group = "missions"
            label = {"Talk to Monster": "＋  New task", "Settings…": "Companion & shortcuts  ›",
                     "AI model settings…": "AI model & automation  ›"}.get(text)
            target = task_secondary if text in ("Pause", "Stop task") else sections[group]
            if text == "Talk to Monster":
                sections[group].removeItem(task_secondary)
            panel.action_button(target, action, label,
                                "primary" if text == "Talk to Monster" else
                                "danger" if text == "Stop task" else None)
            if text == "Talk to Monster":
                sections[group].addLayout(task_secondary)
        panel.content.addStretch()

    def toggle_control_panel(self):
        if self.isolation.busy:
            return
        if self.control_panel.isVisible():
            self.control_panel.hide()
            return
        self._pause_for_secondary()
        self.control_panel.open()

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
            if self.control_panel:
                owned.add(int(self.control_panel.winId()))
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
        if self.workspace_mode != "personal_desktop":
            if self.workspace_runtime and instruction.strip().lower() in {"start guest simulation", "start simulation"}:
                self.workspace_runtime.start_mission()
            else:
                self.notice("Independent Workspace · simulation only. Use Workspaces to start the reviewed fixture.")
            return
        if self.mission_runtime and self.mission_runtime.engaged:
            self.notice("Mission owns this task · stop it before starting another.")
            return
        if self.mission_runtime and getattr(self.controller, 'is_supervised', False):
            self.mission_runtime.close()
            if self.mission_runtime.thread.is_alive():
                self.notice('Previous mission journal is closing · retry shortly.')
                return
            self.mission_runtime = None
        if (self.isolation.busy or self.active or not instruction.strip()
                or getattr(self.controller, "phase", None) == "closing"):
            return
        if getattr(self.controller, "cleanup_blocked", False):
            reason = "Input cleanup unconfirmed. Restart Monster before another task."
            self.controller.record_start_failure(instruction, reason, stage="startup_policy")
            self.failed(reason)
            return
        target = self.activation_target
        lease = None
        if getattr(self.controller, "is_supervised", False):
            from uuid import uuid4
            lease = self.workspace_gate.acquire("manual-" + uuid4().hex, 1)
            if lease is None:
                self.notice("Desktop busy or cleanup unconfirmed · no input started.")
                return
            from monster.computer.windows import WindowsInput

            self.pill.hide()
            try:
                if not target or not WindowsInput().restore_foreground(target):
                    raise RuntimeError("Cannot restore the initial target window.")
            except RuntimeError as exc:
                self.activation_target = None
                reason = (
                    "Stopped incomplete: Cannot restore the initial target window. "
                    "Focus the intended application and activate Monster again. No task was started."
                )
                self.workspace_gate.release(lease, cleanup_confirmed=True)
                self.controller.record_start_failure(instruction, reason)
                self._log_ui("initial_focus_exception", evidence=exception_evidence(exc))
                self.failed(reason)
                return
        if self.controller.submit(instruction):
            if getattr(self.controller, "is_supervised", False):
                # Pin the current-composer task before any asynchronous capture.
                self.controller.task_foreground = target
                self._log_ui("initial_focus_restore", restored=True,
                             target=self.controller.run_diagnostics.focus_alias(target))
            if lease:
                self.manual_lease = lease
                self.manual_generation = self.controller.run_generation
            self.activation_target = None
            self.last_task_outcome = "running"
            self.pill.input.clear()
            self.pill.hide()
        elif lease:
            self.workspace_gate.release(lease, cleanup_confirmed=True)

    def start_experimental_mission(self, *, resume=False, reconciliation_only=False):
        if self.workspace_mode != "personal_desktop":
            self.notice("Select Personal Desktop for a local mission.")
            return
        if (not isinstance(self.controller, SupervisedController) or self.active
                or self.workspace_gate.busy or self.controller.phase != "idle"
                or self.input_cleanup_blocked
                or (self.mission_runtime and self.mission_runtime.engaged)):
            self.notice("Mission mode requires idle supervised input with confirmed cleanup.", 5000)
            return
        from monster.native import IS_WINDOWS
        if not IS_WINDOWS:
            self.notice("Real experimental missions require Windows; offline tests remain available.")
            return
        if self.mission_runtime:
            self.mission_runtime.close()
            self.notice("Previous mission runtime is closing · retry shortly.")
            if self.mission_runtime.thread.is_alive():
                return
        from monster.mission.gui_bridge import MissionGuiBridge
        from monster.mission.gui_subtask_adapter import AsyncGuiSubtaskRunner
        from monster.mission.runtime import EDITOR_OBJECTIVES, MissionRuntime, editor_mission
        bridge = MissionGuiBridge(self.controller, self.workspace_gate,
            preflight=self._mission_preflight, objective_catalog=EDITOR_OBJECTIVES, parent=self.app)
        runner = AsyncGuiSubtaskRunner(bridge)
        self.mission_runtime = MissionRuntime(self.config_path.parent / "missions", runner, bridge,
                                              plan=None if resume else editor_mission(),
                                              reconciliation_only=reconciliation_only, parent=self.app)
        runtime = self.mission_runtime
        runtime.changed.connect(lambda update, source=runtime: self._mission_changed(update, source=source))
        runtime.reconciliation_ready.connect(lambda records, source=runtime:
                                             self._reconciliation_changed(records, source=source))
        runtime.reconciliation_error.connect(lambda text, source=runtime:
            self.notice(text, 5000) if source is self.mission_runtime else None)
        self.mission_runtime.start()

    def start_phase4(self, mode="simulation", *, resume=False):
        if self.workspace_mode != "personal_desktop":
            self.notice("Select Personal Desktop for the local Phase 4 pilot.")
            return
        if mode == "supervised_pilot" and self.phase4_qualification is None:
            self.notice("Phase 4 native pilot disabled · qualify Phases 2/3 and supply reviewed evidence.",5000)
            return
        if self.active or self.workspace_gate.busy or (self.mission_runtime and self.mission_runtime.engaged):
            self.notice("Finish or stop the current task before starting a mission.")
            return
        if self.mission_runtime:
            self.mission_runtime.close()
            if self.mission_runtime.thread.is_alive():
                self.notice("Mission runtime closing · retry shortly.")
                return
        from monster.mission.plan_templates import PILOT_OBJECTIVES, editor_plan
        from monster.mission.runner import FakeGuiSubtaskRunner
        from monster.mission.runtime import MissionRuntime
        from monster.mission.scheduling import provider_binding
        bridge = None
        runner = FakeGuiSubtaskRunner()
        arguments = {}
        if mode == "supervised_pilot":
            from monster.native import IS_WINDOWS
            if not IS_WINDOWS or not isinstance(self.controller,SupervisedController):
                self.notice("Qualified pilot requires Windows supervised mode.")
                return
            from monster.mission.gui_bridge import MissionGuiBridge
            from monster.mission.gui_subtask_adapter import AsyncGuiSubtaskRunner
            bridge = MissionGuiBridge(self.controller,self.workspace_gate,preflight=self._mission_preflight,
                                      objective_catalog=PILOT_OBJECTIVES,parent=self.app)
            runner = AsyncGuiSubtaskRunner(bridge)
            import hashlib
            import re
            model = self.model_config.model
            model_ref = model if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}",model) else hashlib.sha256(model.encode()).hexdigest()
            arguments = {"provider_ref":"configured-ollama","model_ref":model_ref,
                         "configuration_digest":provider_binding(self.model_config)}
        runtime = MissionRuntime(self.config_path.parent/"missions",runner,bridge,
            scheduled_plan=None if resume else editor_plan(mode=mode,**arguments),
            scheduled_resume_mode=mode if resume else None,
            native_qualification=self.phase4_qualification,parent=self.app)
        self.mission_runtime = runtime
        runtime.changed.connect(lambda update,source=runtime:self._mission_changed(update,source=source))
        runtime.start()

    def start_phase5(self, *, resume=False, replan=False):
        if self.workspace_mode != "personal_desktop":
            self.notice("Select Personal Desktop for local strategic planning.")
            return
        if self.active or self.workspace_gate.busy or (self.mission_runtime and self.mission_runtime.engaged):
            self.notice("Finish or stop the current task before planning.")
            return
        if self.mission_runtime:
            self.mission_runtime.close()
            if self.mission_runtime.thread.is_alive():
                self.notice("Previous mission is closing · retry shortly.")
                return
        from monster.mission.runner import FakeGuiSubtaskRunner
        from monster.mission.runtime import MissionRuntime

        text = None
        if not resume:
            text, accepted = QInputDialog.getText(None, "Monster · fixture planning",
                "Simulation only. Supported goal: Prepare a short technical design proposal.",
                text="Prepare a short technical design proposal.")
            if not accepted:
                return
        runner = FakeGuiSubtaskRunner()
        if replan:
            runner.preflight_faults = ['ready', 'ready', 'blocked_preflight']
        runtime = MissionRuntime(self.config_path.parent / "missions", runner, None,
                                 strategic_text=text, strategic_resume=resume, parent=self.app)
        self.mission_runtime = runtime
        runtime.changed.connect(lambda update, source=runtime: self._mission_changed(update, source=source))
        runtime.start()

    def propose_phase5_revision(self):
        if self.mission_runtime and not self.mission_runtime.engaged:
            self.mission_runtime.request_strategic_revision()

    def show_workspaces(self):
        if self.workspace_mode == 'personal_desktop' and (
            self.active or self.workspace_gate.busy or (self.mission_runtime and self.mission_runtime.engaged)
        ):
            self.stop()
            self.notice('Finish personal input cleanup before opening workspace settings.')
            return
        from monster.ui.workspace_settings import WorkspaceSettings
        from monster.workspaces.registry import ManualGuestWorkspace

        if self.workspace_window:
            if self.control_panel:
                self.control_panel.present(self.workspace_window)
            else:
                self.workspace_window.show()
                self.workspace_window.raise_()
            return
        try:
            self.workspace_registry = ManualGuestWorkspace(self.config_path.parent / 'workspaces.json')
        except (ValueError, OSError):
            self.notice('Workspace settings unavailable; no guest connection started.')
            return
        descriptor = next(iter(self.workspace_registry.record.guests), None)
        window = WorkspaceSettings(descriptor)
        self.workspace_window = window
        window.mode_requested.connect(self.select_workspace_mode)
        window.register_requested.connect(self.register_workspace)
        window.forget_requested.connect(self.forget_workspace)
        window.pair_requested.connect(lambda: self._workspace_command('begin_pairing'))
        window.start_requested.connect(lambda: self._workspace_command('start_mission'))
        window.resume_requested.connect(lambda: self._workspace_command('resume_mission'))
        window.preview_requested.connect(lambda: self._workspace_command('preview'))
        window.stop_requested.connect(lambda: self._workspace_command('stop'))
        window.revoke_requested.connect(lambda: self._workspace_command('revoke'))
        window.reconcile_requested.connect(lambda: self._workspace_command('reconcile'))
        if self.control_panel:
            self.control_panel.present(window)
        else:
            window.show()

    def register_workspace(self):
        if not self.workspace_registry:
            return
        try:
            descriptor = self.workspace_registry.register('Disposable guest')
            self.workspace_window.registration.setText(descriptor.display_name)
        except (ValueError, OSError):
            self.notice('Only one registered guest is supported; inspect existing workspace settings.')

    def forget_workspace(self):
        if self.workspace_mode != 'personal_desktop' or (self.workspace_runtime and self.workspace_runtime.engaged):
            self.notice('Stop the guest and select Personal Desktop before forgetting it.')
            return
        if self.workspace_runtime:
            self.workspace_runtime.close()
            self.workspace_runtime = None
        try:
            self.workspace_registry.forget()
            self.workspace_window.registration.setText('No registered guest')
        except (ValueError, OSError):
            self.notice('Workspace registration could not be removed.')

    def select_workspace_mode(self, mode):
        allowed = mode in ('personal_desktop', 'guest_simulation')
        busy = self.active or self.workspace_gate.busy or (self.mission_runtime and self.mission_runtime.engaged)
        remote_busy = self.workspace_runtime and (self.workspace_runtime.engaged or
            (self.workspace_runtime.last_projection.get('status') or {}).get('cleanup') in ('active', 'unknown'))
        if not allowed or busy or remote_busy:
            if remote_busy:
                self.workspace_runtime.stop()
            self.notice('Finish input cleanup and guest stop confirmation before switching workspaces.')
            self._reset_workspace_selector()
            return False
        if mode == 'guest_simulation':
            if not self.workspace_registry or not self.workspace_registry.record.guests:
                self.notice('Register one guest in Workspaces before selecting the simulation.')
                self._reset_workspace_selector()
                return False
            if self.workspace_runtime is None:
                from monster.workspaces.runtime import WorkspaceRuntime

                descriptor = self.workspace_registry.record.guests[0]
                runtime = WorkspaceRuntime(self.config_path.parent / 'guest-simulation' / descriptor.workspace_id,
                                           descriptor, parent=self.app)
                self.workspace_runtime = runtime
                runtime.changed.connect(lambda status, source=runtime: self._workspace_changed(status, source=source))
                runtime.pairing_offer.connect(lambda offer, source=runtime: self._workspace_pairing(offer, source=source))
                runtime.review_requested.connect(lambda challenge, source=runtime: self._workspace_review(challenge, source=source))
                runtime.preview_ready.connect(lambda frame, source=runtime: self._workspace_preview(frame, source=source))
                runtime.start()
        elif self.workspace_runtime:
            self.workspace_runtime.stop()
        self.workspace_mode = mode
        self.notice('Personal Desktop selected' if mode == 'personal_desktop' else
                    'Independent Workspace · simulation selected; no real VM input.')
        return True

    def _reset_workspace_selector(self):
        if self.workspace_window:
            self.workspace_window.mode.blockSignals(True)
            self.workspace_window.mode.setCurrentIndex(0 if self.workspace_mode == 'personal_desktop' else 1)
            self.workspace_window.mode.blockSignals(False)

    def _workspace_command(self, operation):
        if self.workspace_mode != 'guest_simulation' or self.workspace_runtime is None:
            self.notice('Select Independent Workspace · simulation first.')
            return
        # Application-owned button wiring only; no model/network string is dispatched here.
        methods = {name: getattr(self.workspace_runtime, name) for name in
                   ('begin_pairing', 'start_mission', 'resume_mission', 'preview', 'stop', 'revoke', 'reconcile')}
        if operation in methods:
            methods[operation]()

    def _workspace_pairing(self, offer, *, source):
        if source is not self.workspace_runtime or self.workspace_mode != 'guest_simulation' or self.closing:
            return
        answer = QMessageBox.question(self.workspace_window, 'Verify SIMULATED pairing',
            'In-memory simulation only; this is not a TLS or Windows pairing test.\n'
            + 'Simulated console code: ' + offer.code + '\nHost fingerprint: ' + offer.host_fingerprint
            + '\nGuest fingerprint: ' + offer.guest_fingerprint + '\nConfirm this simulated identity pair?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            source.confirm_pairing(offer)

    def _workspace_changed(self, projection, *, source):
        if source is not self.workspace_runtime or self.closing:
            return
        if self.workspace_window:
            self.workspace_window.status.setText(projection['message'])
        if self.workspace_mode == 'guest_simulation':
            state = (projection.get('status') or {}).get('state')
            self.set_state(State.WORKING if state == 'running' else State.COMPLETED if state == 'completed' else State.WAITING)
            if (projection['stale'] or (projection.get('status') or {}).get('cleanup') == 'unknown') and self.pending_question and self.pending_question[0].startswith('workspace-'):
                self.pending_question = None
                self._cancel_keyboard_approval()
                self.pill.hide()

    def _workspace_review(self, challenge, *, source):
        if source is not self.workspace_runtime or self.workspace_mode != 'guest_simulation' or self.closing:
            return
        self.question('workspace-' + challenge.challenge_id,
            'Independent Workspace · SIMULATION\n' + challenge.summary
            + '\nPlan ' + challenge.fingerprint[:12] + ' · guest generation ' + str(challenge.binding.generation)
            + '\nNo real screenshot, model request or desktop input.', True)
        import time
        self.mission_review_timer.start(max(1, int((challenge.expires_at - time.time()) * 1000)))

    def _workspace_preview(self, frame, *, source):
        if source is not self.workspace_runtime or self.workspace_mode != 'guest_simulation' or self.closing:
            return
        import base64
        dialog = QDialog(self.workspace_window)
        dialog.setWindowTitle('Synthetic guest preview · never used for input')
        layout = QVBoxLayout(dialog)
        label = QLabel()
        pixmap = QPixmap()
        pixmap.loadFromData(base64.b64decode(frame.png_base64), 'PNG')
        label.setPixmap(pixmap)
        layout.addWidget(label)
        dialog.show()

    def pause_mission_boundary(self):
        self._close_strategic_review()
        if self.mission_runtime and not self.mission_runtime.closed:
            self.mission_runtime.request_boundary_pause()

    def resume_mission_boundary(self):
        if self.mission_runtime and not self.mission_runtime.closed:
            self.mission_runtime.request_resume()

    def show_mission_progress(self):
        if self.mission_runtime and not self.mission_runtime.closed:
            self.mission_runtime.request_progress()

    def _show_phase4_progress(self,report):
        if self.pending_question or (self.pill.isVisible() and self.pill.mode=="input"):
            return
        used=report['budget_used']
        budget=report['budget']
        title = "SIMULATED" if report['provenance']=='simulated' else report['provenance'].replace('_',' ')
        total = len(report['verified_subtask_ids']) + len(report['remaining_subtask_ids'])
        strategic = report.get('strategic')
        strategy_text = ("\nStrategy revisions: " + str(strategic['context']['replans'])
                         + " · goal: " + strategic['evaluation']['status'].replace('_', ' ')
                         + " · synthetic evidence only") if strategic else ""
        self.question("mission-progress",title+" · "+str(len(report['verified_subtask_ids']))
            +"/"+str(total)+" verified · "+str(len(report['remaining_subtask_ids']))+" remaining\n"
            +"Active budget charged: "+str(round(used['active_ms']/1000,1))+"s / "
            +str(budget['max_active_seconds'])+"s\nInputs charged: "+str(used['input_charged'])
            +" / "+str(budget['max_total_input_transactions'])+" · planning calls: "
            +str(used['models_charged'])+" / "+str(budget['max_total_model_calls'])
            +"\n"+report.get('status','unknown').replace('_',' ')
            + (" · "+report['reason_code'].replace('_',' ') if report.get('reason_code') else "")
            + strategy_text,True)
        self.pill.show_effect_controls()
        self.pill.approve.setText("Pause after stage")
        self.pill.reject.setText("Stop")
        self.pill.edit_effect.setText("Resume / consent")
        self.pill.pause_effect.setText("Close")

    def review_mission_effects(self):
        if self.mission_runtime and not self.mission_runtime.closed:
            if self.mission_runtime.engaged or self.workspace_gate.busy:
                self.notice("Stop and finish input cleanup before reviewing uncertain effects.")
                return
            self.mission_runtime.review_effects()
        else:
            self.start_experimental_mission(resume=True, reconciliation_only=True)

    def _reconciliation_changed(self, records, *, source):
        if source is not self.mission_runtime or self.closing:
            return
        if not records:
            self.reconciliation_effect = None
            self.notice("No unresolved action effects. Attestation never restarts input.", 5000)
            return
        effect = records[0]
        self.reconciliation_effect = effect
        self.question("reconcile-" + effect.effect_id,
            "Observe only · inspect the desktop yourself. No input or retry.\n"
            + str(effect.action_kind) + " · " + effect.target_ref + " · " + effect.category
            + "\nObjective: " + str(effect.objective_ref) + " · fingerprint "
            + effect.fingerprint[:12] + "\nLast state: " + effect.status
            + ". Confirm only what you can independently observe; this records human attestation.", True)
        self.pill.show_effect_controls()
        self.pill.approve.setText("Confirm occurred")
        self.pill.reject.setText("Confirm did not occur")
        self.pill.edit_effect.setText("Leave unresolved")
        self.pill.pause_effect.setText("Abandon mission")

    def _mission_preflight(self):
        from monster.computer.windows import WindowsInput
        windows = WindowsInput()
        if not windows.desktop_ready() or self.isolation.busy:
            raise RuntimeError("Desktop unavailable")
        if any(w and w.isVisible() for w in (self.settings_window, self.provider_window, self.preview_window)):
            raise RuntimeError("Close secondary windows")
        target = windows.foreground()
        owned = {int(self.pill.winId()), int(self.companion.winId())}
        if not target or target in owned or (self.menu and self.menu.isVisible()):
            raise RuntimeError("Focus disposable editor before authorizing the subtask")
        return target

    def _mission_changed(self, update, *, source=None):
        if self.closing or (source is not None and source is not self.mission_runtime):
            return
        if update.get("strategic_consent"):
            from monster.ui.strategic_review import StrategicReviewDialog

            self._close_strategic_review()
            runtime = self.mission_runtime
            dialog = StrategicReviewDialog(update['strategic_preview'])
            self.strategic_review = dialog
            dialog.simulate.connect(lambda: runtime.answer('approve'))
            dialog.rejected.connect(lambda: runtime.answer('reject'))
            dialog.edit_goal.connect(lambda: self.notice("Cancel this mission, then choose Phase 5 · plan and simulate to edit the goal."))
            dialog.show()
            return
        state, approval = update["state"], update.get("approval")
        if update.get("requested_progress"):
            self._show_phase4_progress(update["report"])
            return
        if update.get("consent"):
            consent = update["consent"]
            label = "SIMULATION · no desktop input" if consent.mode=="simulation" else (
                "Observe-only · no input" if consent.mode=="observe_only" else "EXPERIMENTAL · human supervision required")
            budget = update["report"]["budget"]
            used = update["report"].get("budget_used", {})
            data_scope = ("Desktop screenshots to saved provider " + self.model_config.url
                          + " · model " + consent.model_ref) if consent.mode=="supervised_pilot" else "No screenshots, model requests or native input."
            stages = ("\nReviewed strategic fixture objectives; no real document is created. Plan "
                      if update['report'].get('strategic') else
                      "\nSix fixed scratch sections A–F; no save, replace or send. Plan ")
            text = (label + stages
                    + consent.plan_digest[:12] + " · workspace " + consent.workspace_id
                    + "\nWall " + str(budget['max_wall_seconds']) + "s; active "
                    + str(budget['max_active_seconds']) + "s; inputs "
                    + str(budget['max_total_input_transactions']) + "; planning calls "
                    + str(budget['max_total_model_calls']) + "; reviews "
                    + str(budget['max_total_reviews']) + "; starts " + str(budget['max_subtask_starts'])
                    + ". Inputs already charged: " + str(used.get('input_charged',0))
                    + ".\n" + data_scope + "\nConsent covers sequence only. Each native action still needs exact review. "
                    "Pause does not extend the absolute deadline or reset counters. Ctrl+Alt+X stops.")
            self.question("mission-consent-"+consent.mission_id,text,True)
            import time
            self.mission_review_timer.start(max(1,int((consent.expires_at-time.time())*1000)))
            return
        if approval and self.mission_runtime.stopped.is_set():
            return
        if state["status"] == "waiting_approval" and approval:
            import time
            index = "A" if approval.subtask_id == "editor-a" else "B"
            self.question("mission-" + approval.approval_id,
                "Experimental mission · " + index + "/2. Focus disposable Notepad, then Ctrl+Alt+Y. "
                "Type the labeled test sentence without replacing text. Each action still needs approval.", True)
            self.mission_review_timer.start(max(1, int((approval.expires_at-time.time())*1000)))
        else:
            self.mission_review_timer.stop()
            if self.pending_question and self.pending_question[0].startswith("mission-"):
                self.pending_question = None
                self._cancel_keyboard_approval()
                self.pill.hide()
            report = update.get("report")
            progress = update.get("progress")
            simulated = (report and report.get("provenance")=="simulated") or (
                progress and progress.get("provenance")=="simulated")
            prefix = "SIMULATED mission · " if simulated else "Mission · "
            self.notice(prefix + state["status"].replace("_", " ")
                + (" · pause after stage pending" if state.get("reason")=="pause_pending" else (
                    " · "+state['reason'].replace('_',' ') if state.get('reason') not in (None,'verified') else "")), 5000)

    def _manual_terminal(self, report):
        if self.manual_lease and report.run_generation == self.manual_generation:
            self.workspace_gate.release(self.manual_lease, cleanup_confirmed=report.cleanup_confirmed)
            self.manual_lease = None

    def dismiss(self):
        if self.workspace_mode != 'personal_desktop':
            self.stop()
            return
        if self.mission_runtime and self.mission_runtime.engaged:
            self.stop()
            return
        self._cancel_keyboard_approval()
        self.activation_target = None
        self.isolation.suppress_restore(self.pill)
        if self.pending_question:
            # Cancel directly: "reject" is valid free text in a clarification,
            # so sending it as a response could schedule another observation.
            self.controller.cancel()
            self.pending_question = None
        self.hotkey_errors = self.register_hotkeys()
        self.pill.hide()
        if not self.active:
            self.set_state(State.IDLE)

    def set_state(self, state):
        self.current_state = State(state)
        self.companion.set_state(state)
        if self.control_panel:
            mode = "Demo" if self.controller.is_mock else (
                "Hands-free" if self.model_config.hands_free else
                "Real input" if getattr(self.controller, "is_supervised", False) else "Preview"
            )
            self.control_panel.status.setText(f"●  {str(state).replace('_', ' ').title()}  ·  {mode}")
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
        snapshot = self.controller.diagnostic_snapshot() if hasattr(self.controller, "diagnostic_snapshot") else None
        self.last_failure_snapshot = snapshot
        if snapshot is not None:
            self.last_failure += "\n\n" + snapshot.text()
        self.pending_question = None
        self.set_state(State.ERROR)
        self.notice("Stopped incomplete · open tray → Last failure for details", 5000)
        self.state_timer.start(5100)

    def question(self, request_id, text, approval):
        self._cancel_keyboard_approval()
        self.pending_question = (request_id, text, approval)
        if self.isolation.busy:
            return
        self.hotkey_errors = self.register_hotkeys()
        self.approval_hotkey_available = approval and not self.hotkey_errors
        passive = self.approval_hotkey_available and getattr(self.controller, "is_supervised", False)
        if self.approval_hotkey_available:
            stop_key = "Ctrl+Alt+X" if getattr(self.controller, "is_supervised", False) else self.preferences.stop
            text += f"\nApprove: Ctrl+Alt+Y, then release all keys. Stop: {stop_key}."
        self.set_state(State.WAITING)
        self.pill.show_question(request_id, text, approval, take_focus=not passive)
        if approval and getattr(self.controller, 'phase', None) == 'launch_query_review':
            self.pill.approve.setText('Confirm query')
        if getattr(self.controller, "phase", None) == "effect_approval":
            self.pill.show_effect_controls()
        self._log_ui("review_surface", request_id=request_id, passive=passive,
                     keyboard_available=self.approval_hotkey_available,
                     hotkey_errors=list(self.hotkey_errors))
        self.place_pill()
        if not passive:
            focus_window(self.pill)
        if not approval:
            self.pill.input.setFocus()

    def _log_ui(self, event, **details):
        details["overlay_visibility"] = self.companion.visibility_status()
        diagnostics = getattr(self.controller, "run_diagnostics", None)
        if diagnostics is None:
            return
        point = QCursor.pos()
        screen = self.app.screenAt(point) or self.app.primaryScreen()
        geometry = screen.geometry() if screen else None
        diagnostics.add(event, getattr(self.controller, "phase", "ui"),
                        pointer_qt_logical={"x": point.x(), "y": point.y()},
                        screen_qt_logical=({"x": geometry.x(), "y": geometry.y(),
                                            "width": geometry.width(), "height": geometry.height()}
                                           if geometry else None),
                        device_pixel_ratio=screen.devicePixelRatio() if screen else None, **details)

    def _cancel_keyboard_approval(self, *, released=False):
        if self.keyboard_approval is not None:
            self._log_ui("approval_keys_released" if released else "approval_gesture_cancelled",
                         request_id=self.keyboard_approval[1])
        self.approval_key_timer.stop()
        self.keyboard_approval = None
        self.approval_hotkey_available = False

    def _queue_keyboard_approval(self, controller=None, request_id=None):
        if not self.approval_hotkey_available or not self.pending_question:
            return
        rid, _, approval = self.pending_question
        if (
            not approval or not self.pill.isVisible() or not self.pill.approve.isEnabled()
            or (controller is not None and controller is not self.controller)
            or (request_id is not None and request_id != rid)
        ):
            return
        self.keyboard_approval = (self.controller, rid)
        self._log_ui("approval_gesture", request_id=rid, source="keyboard", awaiting_release=True)
        self.approval_key_timer.start()

    def _finish_keyboard_approval(self):
        pending = self.keyboard_approval
        if (
            pending is None or not self.pending_question or not self.pending_question[2]
            or pending != (self.controller, self.pending_question[0])
            or not self.pill.isVisible() or not self.pill.approve.isEnabled()
        ):
            self._cancel_keyboard_approval()
            return
        if approval_keys_released():
            self._cancel_keyboard_approval(released=True)
            self.answer(pending[1], "approve", source="keyboard")

    def answer(self, request_id, answer, *, source="mouse_or_text"):
        if request_id.startswith('workspace-'):
            if self.workspace_mode == 'guest_simulation' and self.workspace_runtime and self.pending_question and self.pending_question[0] == request_id:
                self.mission_review_timer.stop()
                self.pending_question = None
                self._cancel_keyboard_approval()
                self.pill.hide()
                self.workspace_runtime.answer(request_id.removeprefix('workspace-'), answer)
            return
        if request_id == "mission-progress" and self.pending_question and self.pending_question[0]==request_id:
            self.pending_question=None
            self._cancel_keyboard_approval()
            self.pill.hide()
            if answer=="approve":self.pause_mission_boundary()
            elif answer=="reject":self.stop()
            elif answer=="edit":self.resume_mission_boundary()
            self.hotkey_errors=self.register_hotkeys()
            return
        if (self.reconciliation_effect and self.pending_question
                and request_id == "reconcile-" + self.reconciliation_effect.effect_id
                and self.pending_question[0] == request_id):
            effect = self.reconciliation_effect
            self.reconciliation_effect = None
            self.pending_question = None
            self._cancel_keyboard_approval()
            self.pill.hide()
            self.hotkey_errors = self.register_hotkeys()
            if answer == "pause":
                self.mission_runtime.stop()
            else:
                outcome = {"approve": "success", "reject": "failure"}.get(answer, "unresolved")
                self.mission_runtime.attest_effect(effect.effect_id, outcome)
            return
        if (self.mission_runtime and self.pending_question
                and self.pending_question[0] == request_id and request_id.startswith("mission-")):
            self.mission_review_timer.stop()
            self.pending_question = None
            self._cancel_keyboard_approval()
            self.pill.hide()
            self.hotkey_errors = self.register_hotkeys()
            self.mission_runtime.answer(answer)
            return
        if not self.pending_question or self.pending_question[0] != request_id:
            return
        self._log_ui("review_gesture_response", request_id=request_id, source=source)
        self.pending_question = None
        self._cancel_keyboard_approval()
        self.hotkey_errors = self.register_hotkeys()
        self.pill.hide()
        self.controller.respond(request_id, answer)

    def toggle_pause(self):
        if self.workspace_mode != 'personal_desktop':
            if self.workspace_runtime:
                self.workspace_runtime.pause()
            return
        if self.mission_runtime and self.mission_runtime.engaged:
            self.stop()
            return
        self.controller.toggle_pause()
        if self.active and not self.paused:
            self.pill.hide()

    def _close_strategic_review(self):
        if self.strategic_review:
            self.strategic_review.blockSignals(True)
            self.strategic_review.close()
            self.strategic_review.deleteLater()
            self.strategic_review = None

    def stop(self):
        if self.workspace_mode != 'personal_desktop':
            self.mission_review_timer.stop()
            self._cancel_keyboard_approval()
            self.pending_question = None
            self.pill.hide()
            if self.workspace_runtime:
                self.workspace_runtime.stop()
            self.notice('Guest stop requested; acknowledgement pending.')
            return
        self._close_strategic_review()
        self.mission_review_timer.stop()
        if self.mission_runtime:
            self.mission_runtime.stop()
        self._cancel_keyboard_approval()
        self.activation_target = None
        # A real controller must synchronously disarm its input worker here.
        self.pending_question = None
        self.isolation.suppress_restore(self.pill)
        self.state_timer.stop()
        self.pill.hide()
        self.controller.cancel()
        self.hotkey_errors = self.register_hotkeys()

    def toggle_companion(self, visible):
        self.enabled = visible
        if not visible:
            self.isolation.suppress_restore(self.companion)
        self.companion.setVisible(visible and not self.isolation.busy)

    def _capture_ended(self):
        if self.closing:
            self.pill.hide()
            self.companion.hide()
            if self.control_panel:
                self.control_panel.hide()
            return
        self.companion.setVisible(self.enabled)
        if self.pending_question:
            self.question(*self.pending_question)
        elif self._deferred_notice:
            text, duration = self._deferred_notice
            self._deferred_notice = None
            self.notice(text, duration)

    def show_settings(self):
        if self.mission_runtime and self.mission_runtime.engaged:
            self.stop()
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
        if self.control_panel:
            self.control_panel.present(self.settings_window)
        else:
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
                restored = WindowsInput().restore_foreground(target)
                self._log_ui("focus_restore", restored=restored,
                             target=self.controller.run_diagnostics.focus_alias(target))
                if not restored:
                    raise RuntimeError("Cannot restore the approved target's focus.")
            self.capture_desktop(lambda: capture_display(None))
        except RuntimeError as exc:
            self._log_ui("capture_request_exception", evidence=exception_evidence(exc))
            self.preview_capture = None
            self.controller.capture_failed(str(exc))

    def _preview_observation(self, observation):
        if self.preview_capture:
            controller, generation = self.preview_capture
            self.preview_capture = None
            controller.observation_ready(observation, generation)

    def _capture_diagnostic(self, evidence):
        if self.preview_capture:
            controller, generation = self.preview_capture
            if controller is self.controller and generation == controller.generation:
                self._log_ui(evidence.get("event", "capture_backend_exception"), evidence=evidence)

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
        if self.last_preview.coordinate_source is not None:
            text += "\n\nModel coordinate mapping: " + json.dumps(
                self.last_preview.coordinate_source, ensure_ascii=False
            )
        if self.last_preview.kind == "action":
            expected = self.last_preview.expected_result or self.last_preview.summary
            text += f"\n\nExpected visible result: {expected}"
        verification = getattr(self.controller, "last_verification", None)
        if verification:
            verified = getattr(self.controller, "last_verified_proposal", None)
            if verified is not None:
                text += (
                    "\n\nLast verified action: "
                    + json.dumps(verified.action, ensure_ascii=False)
                    + "\nExpected visible result for that action: "
                    + (verified.expected_result or verified.summary)
                )
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
                "Full run reports include task/action text; review before sharing.",
                snapshot=self.last_failure_snapshot,
            )

    def show_run_report(self):
        controller = self.controller
        diagnostics = getattr(controller, "run_diagnostics", None)
        if diagnostics is None:
            self.notice("No supervised run report is available yet.")
            return
        if self.active:
            self.stop()
        self._show_run_report_ready(controller, diagnostics.run_id)

    def _show_run_report_ready(self, controller, run_id, attempt=0):
        diagnostics = getattr(controller, "run_diagnostics", None)
        if controller is not self.controller or diagnostics is None or diagnostics.run_id != run_id:
            return
        if (getattr(controller, "phase", None) == "closing" or self.isolation.busy) and attempt < 100:
            QTimer.singleShot(50, lambda: self._show_run_report_ready(controller, run_id, attempt + 1))
            return
        snapshot = controller.diagnostic_snapshot()
        self._show_details("Monster · last run report", snapshot.text(),
                           "Includes task/action text. Review before sharing. Screenshots remain in RAM "
                           "unless you choose Save with screenshots. Images may contain personal desktop content. "
                           "Retention limits are in the report.",
                           snapshot=snapshot)

    def save_diagnostic_report(self, *, include_screenshots=False):
        snapshot = self.details_snapshot
        if snapshot is None:
            return
        extension = "zip" if include_screenshots else "json"
        label = "bundle with screenshots" if include_screenshots else "report (task/action text)"
        path, _ = QFileDialog.getSaveFileName(
            self.preview_window, f"Save diagnostic {label}",
            f"monster-run-{snapshot.data['run_id']}.{extension}",
            "ZIP (*.zip)" if include_screenshots else "JSON (*.json)",
        )
        if not path:
            return
        try:
            snapshot.export(path, include_screenshots=include_screenshots)
        except (OSError, ValueError):
            self.preview_window.findChild(QLabel).setText(
                "Could not save. Choose a new filename in a writable directory; existing files are preserved."
            )
        else:
            self.preview_window.findChild(QLabel).setText("Diagnostic export saved. Review its contents before sharing.")

    def _show_details(self, title, text, description, *, snapshot=None):
        if self.isolation.busy or self._focus_secondary_except(self.preview_window):
            return
        self._pause_for_secondary()
        self.details_snapshot = snapshot
        for button in self.report_export_buttons:
            button.setEnabled(snapshot is not None)
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
        plain = buttons.addButton("Save report…", QDialogButtonBox.ButtonRole.ActionRole)
        images = buttons.addButton("Save with screenshots…", QDialogButtonBox.ButtonRole.ActionRole)
        self.report_export_buttons = [plain, images]
        for button in self.report_export_buttons:
            button.setEnabled(snapshot is not None)
        plain.clicked.connect(lambda: self.save_diagnostic_report())
        images.clicked.connect(lambda: self.save_diagnostic_report(include_screenshots=True))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.finished.connect(self._close_preview_details)
        dialog.show()
        focus_window(dialog)

    def _close_preview_details(self):
        if self.preview_window:
            self.preview_window.deleteLater()
            self.preview_window = None
            self.details_snapshot = None
            self.report_export_buttons = []

    def _pause_for_secondary(self):
        if self.active and not self.paused:
            self.controller.toggle_pause()
        self.pill.hide()

    def _focus_secondary_except(self, current):
        for window in (self.settings_window, self.provider_window, self.preview_window):
            if window is not None and window is not current and window.isVisible():
                focus_window(window.window())
                return True
        return False

    def show_provider_settings(self):
        if self.mission_runtime and self.mission_runtime.engaged:
            self.stop()
        if self.isolation.busy:
            return
        if self._focus_secondary_except(self.provider_window):
            return
        self._pause_for_secondary()
        if not self.provider_window:
            self.provider_window = ProviderSettings(self.model_config)
            self.provider_window.saved.connect(self.save_provider_settings)
            self.provider_window.activity_changed.connect(self.companion.set_activity)
            self.provider_window.finished.connect(self._discard_provider_settings)
        if self.control_panel:
            self.control_panel.present(self.provider_window)
        else:
            self.provider_window.show()
            focus_window(self.provider_window)

    def _discard_provider_settings(self):
        self.companion.set_activity("")
        if self.provider_window:
            self.provider_window.deleteLater()
            self.provider_window = None

    def save_provider_settings(self, config):
        if self.workspace_gate.busy or (self.mission_runtime and self.mission_runtime.engaged):
            self.provider_window.status.setText("Wait for mission stop and confirmed cleanup.")
            return
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
        self.companion.apply_preferences(prefs)
        self.settings_window.accept()
        self.settings_window.deleteLater()
        self.settings_window = None

    def shutdown(self):
        if self.workspace_runtime:
            self.workspace_runtime.close()
        if self.workspace_window:
            self.workspace_window.close()
        self._close_strategic_review()
        self._cancel_keyboard_approval()
        if self.closing:
            return
        self.closing = True
        self.mission_review_timer.stop()
        if self.mission_runtime:
            self.mission_runtime.close()
        self.isolation.suppress_restore(self.pill)
        self.isolation.suppress_restore(self.companion)
        self.state_timer.stop()
        self.controller.cancel()
        if hasattr(self.controller, "close"):
            self.controller.close()
        self.hotkeys.close()
        self.companion.timer.stop()
        self.pill._hide_timer.stop()
        if self.control_panel:
            self.isolation.suppress_restore(self.control_panel)
            self.control_panel.hide()
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
    parser.add_argument("--overlay-helper", type=Path,
                        help="Explicit signed UIAccess MonsterOverlay.exe installed in Program Files")
    parser.add_argument("--phase4-qualification",type=Path,
                        help="Explicit local Phase2/3 PASS evidence metadata for the supervised pilot")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Monster")
    app.setOrganizationName("Monster")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(monster_icon())
    path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation))
    ui = CompanionApp(app, MockTaskController(), path / "companion.json")
    if args.phase4_qualification:
        from monster.mission.scheduling import NativePilotQualification
        try:
            ui.phase4_qualification=NativePilotQualification.model_validate_json(
                args.phase4_qualification.read_text(encoding="utf-8"))
        except (OSError,ValueError):
            print("Phase4 qualification unavailable/invalid; native pilot remains disabled.",file=sys.stderr)
    if args.overlay_helper:
        from monster.ui.overlay_client import OverlayClient
        try:
            renderer = OverlayClient(args.overlay_helper)
        except (RuntimeError, OSError, ValueError):
            print("UIAccess renderer unavailable; ordinary companion remains active.", file=sys.stderr)
        else:
            ui.companion.attach_renderer(renderer)
            app.aboutToQuit.connect(renderer.close)
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
