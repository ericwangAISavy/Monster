"""Experimental visual loop with configurable step review and human-confirmed completion."""

import json
import time
import uuid

from PySide6.QtCore import QTimer, Signal

from monster.agent.input_bridge import InputBridge
from monster.agent.progress import (
    action_description,
    action_signature,
    draft_only,
    equivalent_action,
    history_window,
    validate_draft_action,
)
from monster.agent.validation import approval_reason, prepare_action, scene_matches
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
        self.failed_actions = []
        self.inserted_text = set()
        self.last_verification = None
        self.task_foreground = None
        self.recovery_attempts = self.rejected_proposals = self.verification_calls = 0
        self.trace = []
        self.restore_focus = None
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(lambda: self.fail("Five-minute task limit reached."))
        self.approval_timer = QTimer(self)
        self.approval_timer.setSingleShot(True)
        self.approval_timer.timeout.connect(
            lambda: self.fail("Review expired; no additional input was authorized.")
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
        self.failed_actions = []
        self.inserted_text = set()
        self.last_verification = None
        self.task_foreground = None
        self.recovery_attempts = self.rejected_proposals = self.verification_calls = 0
        self.trace = []
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
            action = dict(self.pending.action)
            if action["op"] == "text":
                self.inserted_text.add(action_signature(action))
            self.history.append(
                f"Step {self.actions}: input inserted, not yet visually verified. "
                f"Action: {json.dumps(action, ensure_ascii=False)}. "
                f"Expected: {self.pending.expected_result or self.pending.summary}"
            )
            self.trace.append(f"Step {self.actions}: {action_description(action)}; input inserted.")
            self.phase = "verifying_capture"
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
        if draft_only(self.instruction):
            if self.task_foreground is None:
                self.task_foreground = observation.foreground
            elif self.task_foreground != observation.foreground:
                self.fail(
                    "Current-composer target window changed. Focus the intended composer "
                    "and start a new task; no input was redirected to another window."
                )
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
        if self.phase == "verifying_capture":
            self.observation = observation
            self.phase = "verifying"
            action = dict(self.pending.action)
            expected = self.pending.expected_result or self.pending.summary
            instruction = self._task_context()
            self.verification_calls += 1
            self.state_changed.emit(State.THINKING)
            self.job = self.jobs.start(
                lambda: self.provider_factory(self.config).verify_action(
                    self.config.model,
                    instruction,
                    action,
                    expected,
                    observation.png,
                    observation.bounds.width,
                    observation.bounds.height,
                )
            )
            return
        if self.phase != "observing":
            return
        if self.calls >= 20:
            self.fail(
                "Task limit reached (20 planning calls). The task is incomplete, not completed."
            )
            return
        self.observation = observation
        self.calls += 1
        self.phase = "deciding"
        instruction, history = self._task_context(), history_window(self.history)
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
            if self.phase == "deciding" and error.startswith(
                (
                    "Model returned an invalid",
                    "Model decision contains",
                    "Unsupported model action",
                    "Model action failed validation",
                    "Model did not return valid structured JSON",
                )
            ):
                self._reject_proposal(error)
                return
            stage = {
                "probing": "Model compatibility check",
                "comparing": "Screen recheck after approval",
                "deciding": "Model decision",
                "verifying": "Visual action verification",
            }.get(self.phase, "Background operation")
            self.fail(f"{stage} failed: {error}")
        elif self.phase == "probing":
            self.phase = "observing"
            self._capture()
        elif self.phase == "verifying":
            self.last_verification = result
            self.trace.append(f"Step {self.actions}: visual verification {result.status}.")
            self.history.append(
                f"Step {self.actions} visual verification: {result.status}. "
                f"Evidence: {result.evidence}"
            )
            if result.status != "succeeded":
                self.failed_actions.append(dict(self.pending.action))
                self.recovery_attempts += 1
                self.history.append(
                    "The last action did not achieve a verified result. Do not "
                    "repeat it or jitter its coordinates. Choose a materially "
                    "different strategy or ask the user to focus the composer."
                )
                if self.pending.action["op"] == "text" and result.status == "uncertain":
                    self.phase = "confirming"
                    self.question_id = uuid.uuid4().hex
                    self.state_changed.emit(State.WAITING)
                    self.approval_requested.emit(
                        self.question_id,
                        "Text was inserted once, but the model cannot verify its location. "
                        "Inspect the editable composer and confirm your ENTIRE task is complete. "
                        "If it is missing, in the wrong field, or incomplete, Decline. "
                        "No automatic retyping will occur.\nTask: " + self.instruction[:2000],
                    )
                    self.approval_timer.start(60000)
                    return
                if self.pending.action["op"] == "text":
                    self.fail(
                        "Typed text could not be verified in the intended field. Inspect "
                        "the composer before retrying; no text was automatically retyped."
                    )
                    return
                if self.recovery_attempts >= 3:
                    self.fail(
                        "No verified progress after three attempts. Focus the intended "
                        "field manually and start a new task."
                    )
                    return
            else:
                self.recovery_attempts = self.rejected_proposals = 0
            self.phase = "observing"
            # Verification can take seconds. Plan from a fresh frame rather than
            # the now-stale image that was submitted to the verifier.
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
                validate_draft_action(self.pending, self.instruction, bool(self.inserted_text))
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
                    self.fail(
                        "Ten-action limit reached. The task is incomplete; inspect the result."
                    )
                    return
                action = result.action
                if (
                    action["op"] == "text" and action_signature(action) in self.inserted_text
                ) or any(equivalent_action(action, failed) for failed in self.failed_actions):
                    self._reject_proposal(
                        "This action already failed or its text was already inserted."
                    )
                    return
                try:
                    prepare_action(action, self.observation)
                    validate_draft_action(result, self.instruction, bool(self.inserted_text))
                except (ValueError, TypeError) as exc:
                    self._reject_proposal(str(exc))
                    return
                reason = (
                    "Per-step approval is enabled."
                    if self.config.approve_each_step
                    else approval_reason(result)
                )
                text = (
                    "REAL INPUT · "
                    + (reason or "Review target desktop.")
                    + "\n"
                    + result.summary
                    + "\n"
                    + json.dumps(result.action, ensure_ascii=False)
                    + "\nCtrl+Alt+X stops."
                )
                self.phase = "approval"
                if not self.config.approve_each_step and reason is None:
                    self._begin_recheck(restore=False)
                    return
            else:
                if self.last_verification and self.last_verification.status != "succeeded":
                    self._reject_proposal(
                        "Completion is unsupported: the last action failed visual verification."
                    )
                    return
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
            if (
                self.phase == "confirming"
                and self.last_verification
                and self.last_verification.status == "uncertain"
            ):
                self.fail(
                    "Typed result was not confirmed in the intended composer. No automatic retyping occurred."
                )
            else:
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

    def _task_context(self):
        text = self.instruction
        if draft_only(text):
            text += (
                "\nTrusted task boundary: draft/typing only in the currently open composer. "
                "Do not send, submit, switch applications or press Enter. Stop when the "
                "requested text is visibly present in that editable composer, not in chat history."
            )
        return text

    def _reject_proposal(self, reason):
        self.rejected_proposals += 1
        self.trace.append("Proposal rejected: repeated/invalid action or unsupported completion.")
        self.history.append(
            f"Controller rejected the proposed step: {reason} "
            "No input was executed. Choose a different valid approach or ask for help."
        )
        if self.rejected_proposals >= 3:
            self.fail(
                "Three repeated/invalid proposals rejected without input. Focus the current "
                "message composer manually and give a fresh typing instruction."
            )
            return
        self.phase = "observing"
        self._capture()

    def failure_diagnostics(self):
        return (
            f"Planning calls: {self.calls}; visual checks: {self.verification_calls}.\n"
            + "\n".join(self.trace[-12:])
        )

    def capture_failed(self, error):
        self.fail(error)

    def fail(self, message):
        if self.active:
            self._terminate("error", "Stopped incomplete: " + message)

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
