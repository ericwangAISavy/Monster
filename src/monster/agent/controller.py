"""Experimental visual loop with configurable step review and human-confirmed completion."""

import json
import time
import uuid

from PySide6.QtCore import QTimer, Signal

from monster.agent.input_bridge import InputBridge
from monster.agent.validation import needs_human_approval, prepare_action, scene_matches
from monster.lifecycle import State, TaskController
from monster.providers.jobs import ProviderJobs
from monster.providers.ollama import OllamaProvider


class SupervisedController(TaskController):
    capture_requested = Signal()
    preview_ready = Signal(object)
    worker_owned = Signal(bool)
    is_supervised = True

    def __init__(self, config, provider_factory=OllamaProvider, input_bridge=None, parent=None):
        super().__init__(parent)
        self.config, self.provider_factory = config, provider_factory
        self.jobs = ProviderJobs(self)
        self.jobs.completed.connect(self._model_result)
        self.input = input_bridge or InputBridge(parent=self)
        self.input.completed.connect(self._input_result)
        self.input.interrupted.connect(self.cancel)
        self.active = self.paused = False
        self.generation = 0
        self.phase = "idle"
        self.job = self.input_job = None
        self.question_id = None
        self.observation = self.pending = None
        self.history = []
        self.actions = self.calls = self.stale = 0
        self.cleanup_blocked = False
        self.repeats = {}
        self.restore_focus = None
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(lambda: self.fail("Five-minute task limit reached."))
        self.approval_timer = QTimer(self)
        self.approval_timer.setSingleShot(True)
        self.approval_timer.timeout.connect(
            lambda: self.fail("Approval expired; no action executed.")
        )
        self.terminal = None
        self.instruction = ""

    def submit(self, instruction):
        if (
            self.active
            or self.phase == "closing"
            or self.cleanup_blocked
            or not instruction.strip()
        ):
            return False
        self.instruction = instruction.strip()[:2000]
        self.active, self.paused = True, False
        self.generation += 1
        self.history = []
        self.repeats = {}
        self.actions = self.calls = self.stale = 0
        self.terminal = None
        self.active_changed.emit(True)
        self.paused_changed.emit(False)
        self.state_changed.emit(State.THINKING)
        self.deadline.start(300000)
        self.phase = "opening"
        # GUI releases its stop registration before the child registers it. No
        # automated input can be armed before the child reports ready.
        self.worker_owned.emit(True)
        self.input_job = self.input.submit("open")
        return True

    def _input_result(self, job, result, error):
        if job != self.input_job:
            return
        self.input_job = None
        if self.phase == "closing":
            self.phase = "idle"
            self.cleanup_blocked = bool(error or not result)
            self.worker_owned.emit(False)
            if self.cleanup_blocked:
                self.state_changed.emit(State.ERROR)
                self.failed.emit(
                    "Input cleanup unconfirmed. Inspect held input and restart Monster."
                )
            elif self.terminal:
                kind, text = self.terminal
                self.state_changed.emit(
                    State.COMPLETED
                    if kind == "complete"
                    else State.ERROR
                    if kind == "error"
                    else State.IDLE
                )
                (
                    self.finished
                    if kind == "complete"
                    else self.failed
                    if kind == "error"
                    else self.status
                ).emit(text)
            return
        if not self.active or self.paused:
            return
        if error:
            self.fail(error)
        elif self.phase == "opening":
            self._probe()
        elif self.phase == "executing":
            if result.get("status") != "inserted":
                self.fail(
                    "Input outcome is partial/unknown. Inspect the desktop; no automatic retry."
                )
                return
            self.actions += 1
            # Record only categories, not text payloads, in the bounded in-memory history.
            self.history.append(
                f"Input transaction {self.actions}: {self.pending.action['op']} inserted; "
                "inspect the next screenshot to determine its visible result."
            )
            self.phase = "observing"
            QTimer.singleShot(350, self, self._capture)

    def _probe(self):
        self.phase = "probing"
        self.job = self.jobs.start(
            lambda: self.provider_factory(self.config).probe(self.config.model)
        )

    def _capture(self):
        if self.active and not self.paused:
            self.state_changed.emit(State.WORKING)
            self.capture_requested.emit()

    def observation_ready(self, observation, generation):
        if not self.active or self.paused or generation != self.generation:
            return
        if self.phase == "rechecking":
            before, action = self.observation, dict(self.pending.action)
            self.observation = observation
            self.phase = "comparing"

            async def compare():
                # ProviderJobs already executes off the Qt thread. A second
                # thread pool is unnecessary for this bounded comparison.
                return scene_matches(before, observation, action)

            self.job = self.jobs.start(compare)
            return
        if self.phase != "observing":
            return
        if self.calls >= 20:
            self.fail("Supervised task budget reached (10 actions / 20 model calls).")
            return
        self.observation = observation
        self.calls += 1
        self.phase = "deciding"
        instruction, history = self.instruction, tuple(self.history[-10:])
        self.state_changed.emit(State.THINKING)
        self.job = self.jobs.start(
            lambda: self.provider_factory(self.config).decide(
                self.config.model,
                instruction,
                observation.png,
                observation.bounds.width,
                observation.bounds.height,
                history=history,
            )
        )

    def _model_result(self, job, result, error):
        if job != self.job or not self.active or self.paused:
            return
        self.jobs.futures.pop(job, None)
        self.job = None
        if error:
            stage = {
                "probing": "Model compatibility check",
                "comparing": "Screen recheck after approval",
                "deciding": "Model decision",
            }.get(self.phase, "Background operation")
            self.fail(f"{stage} failed: {error}")
        elif self.phase == "probing":
            self.phase = "observing"
            self._capture()
        elif self.phase == "comparing":
            if not result or time.monotonic() - self.observation.captured_at > 2:
                self.stale += 1
                if self.stale > 2:
                    self.fail("Screen changed repeatedly. Stop and stabilize the test desktop.")
                else:
                    self.phase = "observing"
                    self._capture()
                return
            try:
                action = prepare_action(self.pending.action, self.observation)
            except (ValueError, TypeError):
                self.fail("Action is outside the supervised demo policy.")
                return
            self.phase = "executing"
            self.state_changed.emit(State.WORKING)
            keyboard = action["op"] in {"text", "press", "chord"}
            self.input_job = self.input.submit(
                "execute",
                action,
                self.observation.foreground if keyboard else None,
                list(self.observation.displays),
            )
        elif self.phase == "deciding":
            self.pending = result
            self.preview_ready.emit(result)
            if result.kind == "action" and result.action == {"op": "screenshot"}:
                self.phase = "observing"
                QTimer.singleShot(350, self, self._capture)
                return
            if result.kind == "unable":
                self.fail("Model could not continue. Open Last action preview for its explanation.")
                return
            if result.kind == "clarify":
                self.phase = "clarifying"
                self.question_id = uuid.uuid4().hex
                self.state_changed.emit(State.WAITING)
                self.clarification_requested.emit(self.question_id, result.summary)
                self.approval_timer.start(60000)
                return
            if result.kind == "action":
                if self.actions >= 10:
                    self.fail("Ten-action budget reached; inspect the result manually.")
                    return
                fingerprint = (json.dumps(result.action, sort_keys=True), self.observation.sha256)
                self.repeats[fingerprint] = self.repeats.get(fingerprint, 0) + 1
                if self.repeats[fingerprint] >= 3:
                    self.fail("Repeated action without visual progress; inspect manually.")
                    return
                try:
                    prepare_action(result.action, self.observation)
                except (ValueError, TypeError):
                    self.fail("Proposed action is outside the supervised demo policy.")
                    return
                text = (
                    "REAL INPUT · review target desktop.\n"
                    + result.summary
                    + "\n"
                    + json.dumps(result.action, ensure_ascii=False)
                    + "\nCtrl+Alt+X stops."
                )
                self.phase = "approval"
                if not self.config.approve_each_step and not needs_human_approval(result):
                    self._begin_recheck(restore=False)
                    return
            else:
                text = (
                    "Confirm the visible result meets your task:\n"
                    + self.instruction
                    + "\nModel reports: "
                    + result.summary
                    + "\nApprove only after inspecting it."
                )
                self.phase = "confirming"
            self.question_id = uuid.uuid4().hex
            self.state_changed.emit(State.WAITING)
            self.approval_requested.emit(self.question_id, text)
            self.approval_timer.start(60000)

    def respond(self, request_id, answer):
        if (
            not self.active
            or self.paused
            or request_id != self.question_id
            or not self.approval_timer.isActive()
        ):
            return
        self.approval_timer.stop()
        self.question_id = None
        if self.phase == "clarifying":
            self.instruction = (self.instruction + "\nUser clarification: " + answer[:1000])[:3000]
            self.phase = "observing"
            self._capture()
        elif answer != "approve":
            self.cancel()
        elif self.phase == "confirming":
            self._terminate("complete", "Completed · visible result confirmed by you")
        elif self.phase == "approval":
            self._begin_recheck(restore=True)

    def _begin_recheck(self, restore):
        self.phase = "rechecking"
        # Automatic steps never steal focus back after manual takeover.
        self.restore_focus = self.observation.foreground if restore else None
        self._capture()

    def capture_failed(self, error):
        self.fail(error)

    def fail(self, message):
        if self.active:
            self._terminate("error", message)

    def _terminate(self, kind, message):
        self.input.stop()
        self.generation += 1
        if self.job:
            self.jobs.cancel(self.job)
        self.job = None
        self.deadline.stop()
        self.approval_timer.stop()
        self.question_id = None
        self.active, self.paused = False, False
        self.active_changed.emit(False)
        self.paused_changed.emit(False)
        self.phase = "closing"
        self.terminal = (kind, message)
        self.input_job = self.input.close()
        self.instruction = ""
        self.observation = self.pending = None

    def cancel(self):
        if self.phase != "closing" and self.active:
            self._terminate("cancel", "Stopped · inspect any actions already performed")

    def toggle_pause(self):
        # Do not retain an approval across manual takeover. A new task is required.
        self.cancel()

    def close(self):
        self.cancel()
        self.jobs.close()
        self.input.shutdown()
