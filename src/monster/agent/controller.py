"""Experimental visual loop with configurable step review and human-confirmed completion."""

import json
import time
import uuid
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict, is_dataclass

from PySide6.QtCore import QTimer, Signal

from monster.agent.diagnostics import RunDiagnostics
from monster.agent.input_bridge import InputBridge
from monster.agent.metrics import OperationTimings
from monster.agent.progress import (
    action_description,
    action_signature,
    draft_only,
    equivalent_action,
    history_window,
    validate_draft_action,
)
from monster.agent.validation import (
    approval_reason,
    prepare_action,
    scene_matches,
    single_click_requested,
    window_close_requested,
)
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
        self.timings = OperationTimings()
        self.run_diagnostics = None
        self.log_frame = self.planned_frame = None
        self.scene_details = {}
        self.model_diagnostic_origins = OrderedDict()
        self.input_diagnostic_origins = OrderedDict()
        self.jobs = ProviderJobs(self)
        self.jobs.completed.connect(self._model_result)
        self.jobs.diagnostic.connect(self._background_diagnostic)
        self.input = input_bridge or InputBridge(parent=self)
        self.input.completed.connect(self._input_result)
        self.input.interrupted.connect(self.cancel)
        if hasattr(self.input, "diagnostic"):
            self.input.diagnostic.connect(self._worker_diagnostic)
        self.approval_requested.connect(
            lambda rid, text: self._record("review_requested", request_id=rid,
                                          content={"question": text})
        )
        self.clarification_requested.connect(
            lambda rid, text: self._record("clarification_requested", request_id=rid,
                                          content={"question": text})
        )
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
        self.last_verified_proposal = None
        self.reviewed_proposal = None
        self.task_foreground = None
        self.human_confirmed_focus = False
        self.close_dispatched = False
        self.single_click_policy = False
        self.single_click_dispatched = False
        self.recovery_attempts = self.rejected_proposals = self.verification_calls = 0
        self.trace = []
        self.restore_focus = None
        self.scheduled_capture = None
        self.capture_timer = QTimer(self)
        self.capture_timer.setSingleShot(True)
        self.capture_timer.timeout.connect(self._delayed_capture)
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
        self.timings.reset()
        self.instruction = instruction.strip()[:2000]
        self.single_click_policy = single_click_requested(self.instruction)
        self.run_diagnostics = RunDiagnostics(instruction.strip(), self.config)
        self.log_frame = self.planned_frame = None
        self.run_diagnostics.add(
            "task_started", "opening", draft_policy=draft_only(self.instruction),
            content={"effective_instruction": self.instruction},
            instruction_truncated_for_execution=instruction.strip() != self.instruction,
            close_policy=window_close_requested(self.instruction),
            single_click_policy=self.single_click_policy,
            limits={"input_transactions": 10, "planning_calls": 20, "task_seconds": 300,
                    "review_seconds": 60, "invalid_proposals": 3},
        )
        self.active, self.paused = True, False
        self.generation += 1
        self.history = []
        self.failed_actions = []
        self.inserted_text = set()
        self.last_verification = None
        self.last_verified_proposal = None
        self.reviewed_proposal = None
        self.task_foreground = None
        self.human_confirmed_focus = False
        self.close_dispatched = False
        self.single_click_dispatched = False
        self.recovery_attempts = self.rejected_proposals = self.verification_calls = 0
        self.trace = []
        self.actions = self.calls = self.stale = 0
        self.terminal = None
        self.active_changed.emit(True)
        self.paused_changed.emit(False)
        self.state_changed.emit(State.THINKING)
        self.stage_changed.emit("Starting")
        self.deadline.start(300000)
        self.phase = "opening"
        # GUI releases its stop registration before the child registers it. No
        # automated input can be armed before the child reports ready.
        self.worker_owned.emit(True)
        self.input_job = self._start_input("open")
        return True

    def _record(self, event, *, content=None, **details):
        if self.run_diagnostics is not None:
            self.run_diagnostics.add(event, self.phase, content=content,
                                     planning_call=self.calls, step=self.actions,
                                     generation=self.generation, **details)

    def _remember_diagnostic_origin(self, origins, job):
        if self.run_diagnostics is None:
            return
        origins[job] = (self.run_diagnostics.run_id, self.phase, self.calls,
                        self.actions, self.generation)
        while len(origins) > 128:
            origins.popitem(last=False)

    def _bound_diagnostic(self, event, origins, source_job, channel):
        if self.run_diagnostics is None:
            return
        origin = origins.get(source_job)
        if origin is None or origin[0] != self.run_diagnostics.run_id:
            self._record("diagnostic_ignored", channel=channel, source_job=source_job,
                         source_run_id=origin[0] if origin else None,
                         reason="stale_run" if origin else "unknown_source_job")
            return
        run_id, stage, call, step, generation = origin
        field = "evidence" if channel == "model" else "worker"
        name = "background_exception" if channel == "model" else "worker_event"
        self.run_diagnostics.add(
            name, stage if channel == "model" else self.phase,
            source_run_id=run_id, source_job=source_job, source_stage=stage,
            planning_call=call, step=step, generation=generation,
            delivery_phase=self.phase, **{field: event},
        )

    def _background_diagnostic(self, event):
        self._bound_diagnostic(event, self.model_diagnostic_origins, event.get("job"), "model")

    def _worker_diagnostic(self, event):
        self._bound_diagnostic(event, self.input_diagnostic_origins,
                               event.get("job", event.get("source_open_job")), "input")

    def diagnostic_snapshot(self, *, include_content=True):
        if self.run_diagnostics is None:
            return None
        return self.run_diagnostics.snapshot(include_content=include_content, summary={
            "accepted_transactions": self.actions, "planning_calls": self.calls,
            "visual_checks": self.verification_calls, "scene_rejections": self.stale,
            "phase_at_snapshot": self.phase, "active": self.active,
            "cleanup_blocked": self.cleanup_blocked, "timings": deepcopy(self.timings.totals),
            "pending_model_job": self.job, "pending_input_job": self.input_job,
            "pending_review_id": self.question_id,
        })

    def record_start_failure(self, instruction, reason, *, stage="initial_focus"):
        if self.active or self.phase == "closing":
            return
        self.timings.reset()
        self.actions = self.calls = self.verification_calls = self.stale = 0
        self.trace = []
        self.run_diagnostics = RunDiagnostics(instruction.strip(), self.config)
        self.run_diagnostics.add("startup_failed", stage, content={"reason": reason}, worker_started=False)
        self.run_diagnostics.end("error", stage, reason, cleanup_confirmed=None)

    def _start_model(self, operation):
        stage = {
            "probing": "probe", "deciding": "planning",
            "comparing": "recheck", "verifying": "verification",
        }[self.phase]
        job = self.jobs.start(operation)
        self._remember_diagnostic_origin(self.model_diagnostic_origins, job)
        self.timings.begin(("model", job), stage)
        self._record("model_requested", job=job, operation=stage)
        return job

    def _start_input(self, operation, *args):
        job = self.input.submit(operation, *args)
        self._remember_diagnostic_origin(self.input_diagnostic_origins, job)
        self.timings.begin(("input", job), "startup" if operation == "open" else "dispatch")
        self._record("input_requested", job=job, operation=operation)
        return job

    def _input_result(self, job, result, error):
        if job != self.input_job:
            self._record("input_result_ignored", source_job=job)
            return
        receipt = (
            {key: result[key] for key in ("status", "events", "error", "error_source", "cleanup_ok", "cursor_physical_after", "cursor_observation_error") if key in result}
            if isinstance(result, dict) else {"confirmed": bool(result)}
        )
        self._record("input_result", job=job, receipt=receipt,
                     content={"error": error} if error else None)
        successful = not error and bool(result)
        if self.phase == "executing":
            successful = successful and result.get("status") == "inserted"
        self.timings.finish(("input", job), "completed" if successful else "failed")
        self.input_job = None
        if self.phase == "closing":
            self.phase = "idle"
            self.cleanup_blocked = bool(error or not result)
            if self.run_diagnostics is not None:
                self.run_diagnostics.outcome["cleanup_confirmed"] = not self.cleanup_blocked
                self.run_diagnostics.outcome["cleanup_failed"] = self.cleanup_blocked
                if self.cleanup_blocked:
                    self.run_diagnostics.outcome["primary"] = {
                        key: self.run_diagnostics.outcome[key] for key in ("status", "stage", "reason")
                    }
                    self.run_diagnostics.outcome.update(
                        status="error", stage="closing", reason="Input cleanup unconfirmed. Inspect held input and restart Monster."
                    )
            self._record("cleanup_result", confirmed=not self.cleanup_blocked)
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
            if action == {"op": "chord", "keys": ["ALT", "F4"]}:
                self.close_dispatched = True
            if action["op"] == "click":
                self.single_click_dispatched = True
            if action["op"] == "text":
                self.inserted_text.add(action_signature(action))
            self.history.append(
                f"Step {self.actions}: input inserted, not yet visually verified. "
                f"Action: {json.dumps(action, ensure_ascii=False)}. "
                f"Expected: {self.pending.expected_result or self.pending.summary}"
            )
            self.trace.append(f"Step {self.actions}: {action_description(action)}; input inserted.")
            self.phase = "verifying_capture"
            self._schedule_capture()

    def _provider(self, diagnostics, stage, call=0, step=0):
        provider = self.provider_factory(self.config)
        if isinstance(provider, OllamaProvider):
            provider.diagnostic_callback = lambda event, details, content=None: diagnostics.add(
                event, stage, content=content, planning_call=call, step=step, **details
            )
        return provider

    def _probe(self):
        self.phase = "probing"
        self.state_changed.emit(State.THINKING)
        self.stage_changed.emit("Model check")
        diagnostics = self.run_diagnostics
        self.job = self._start_model(
            lambda: self._provider(diagnostics, "probing").probe(self.config.model)
        )

    def _schedule_capture(self):
        self.scheduled_capture = (self.generation, self.phase)
        self.capture_timer.start(350)

    def _delayed_capture(self):
        expected = self.scheduled_capture
        self.scheduled_capture = None
        if expected == (self.generation, self.phase):
            self._capture()

    def _capture(self):
        if self.active and not self.paused and self.phase in {
            "observing", "rechecking", "verifying_capture"
        }:
            self.timings.begin(("capture", self.generation), "capture")
            self.state_changed.emit(State.WORKING)
            self.stage_changed.emit("Capturing")
            self.capture_requested.emit()

    def observation_ready(self, observation, generation):
        if not self.active or self.paused or generation != self.generation:
            self._record("observation_ignored", source_generation=generation)
            return
        previous_frame = self.log_frame
        self.log_frame = self.run_diagnostics.observe(observation, self.phase)
        self.timings.finish(("capture", generation), "completed")
        if draft_only(self.instruction) or (
            window_close_requested(self.instruction) and not self.close_dispatched
        ):
            if self.task_foreground is None:
                self.task_foreground = observation.foreground
            elif self.task_foreground != observation.foreground:
                self.fail(
                    "Current-composer target window changed. Focus the intended composer "
                    "and start a new task; no input was redirected to another window."
                    if draft_only(self.instruction) else
                    "Close target window changed before dispatch. Focus the intended window "
                    "and start a new task; no close shortcut was redirected."
                )
                return
        if self.phase == "rechecking":
            before, action = self.observation, dict(self.pending.action)
            self.observation = observation
            self.phase = "comparing"
            self.stage_changed.emit("Rechecking")
            details = {}
            self.scene_details = details
            self._record("scene_recheck_requested", before=previous_frame, after=self.log_frame)

            async def compare():
                # ProviderJobs already executes off the Qt thread. A second
                # thread pool is unnecessary for this bounded comparison.
                return scene_matches(before, observation, action, diagnostics=details)

            self.job = self._start_model(compare)
            return
        if self.phase == "verifying_capture":
            self.observation = observation
            self.phase = "verifying"
            action = dict(self.pending.action)
            expected = self.pending.expected_result or self.pending.summary
            instruction = self._task_context()
            self.verification_calls += 1
            self._record("verification_requested", observation=self.log_frame,
                         content={"action": action, "expected_result": expected,
                                  "task_context": instruction})
            self.state_changed.emit(State.THINKING)
            self.stage_changed.emit("Verifying")
            diagnostics, call, step = self.run_diagnostics, self.calls, self.actions
            self.job = self._start_model(
                lambda: self._provider(diagnostics, "verifying", call, step).verify_action(
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
        self.planned_frame = self.log_frame
        self.phase = "deciding"
        instruction, history = self._task_context(), history_window(self.history)
        self._record("planning_requested", observation=self.log_frame,
                     draft_policy=draft_only(self.instruction),
                     close_policy=window_close_requested(self.instruction),
                     single_click_policy=self.single_click_policy,
                     content={"task_context": instruction, "history": history})
        self.state_changed.emit(State.THINKING)
        self.stage_changed.emit("Thinking")
        diagnostics, call, step = self.run_diagnostics, self.calls, self.actions
        self.job = self._start_model(
            lambda: self._provider(diagnostics, "deciding", call, step).decide(
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
            self._record("model_result_ignored", source_job=job)
            return
        self.jobs.futures.pop(job, None)
        self.timings.finish(("model", job), "failed" if error else "completed")
        self._record("model_result", job=job, completed=not bool(error),
                     content={"error": error} if error else None)
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
                self._reject_proposal(error, "model_response")
                return
            stage = {
                "probing": "Model compatibility check",
                "comparing": "Screen recheck after approval",
                "deciding": "Model decision",
                "verifying": "Visual action verification",
            }.get(self.phase, "Background operation")
            self.fail(f"{stage} failed: {error}")
        elif self.phase == "probing":
            self._record("model_compatibility", metadata=asdict(result) if is_dataclass(result) else {})
            self.phase = "observing"
            self._capture()
        elif self.phase == "verifying":
            self.last_verification = result
            self.last_verified_proposal = deepcopy(self.pending)
            self._record("verification_result", observation=self.log_frame, content={
                "action": self.pending.action, "expected_result": self.pending.expected_result or self.pending.summary,
                "status": result.status, "evidence": result.evidence,
            })
            self.trace.append(f"Step {self.actions}: visual verification {result.status}.")
            self.history.append(
                f"Step {self.actions} visual verification: {result.status}. "
                f"Evidence: {result.evidence}"
            )
            if (
                result.status != "succeeded"
                and self.single_click_policy
                and self.single_click_dispatched
            ):
                self.fail(
                    "The requested one-click input was dispatched, but its visible result "
                    "was not verified. Inspect the desktop; no second input will be attempted."
                )
                return
            if result.status != "succeeded":
                self.failed_actions.append(dict(self.pending.action))
                self.recovery_attempts += 1
                self.history.append(
                    "The last action did not achieve a verified result. Do not "
                    "repeat it or jitter its coordinates. Choose a materially "
                    "different strategy or ask the user to identify the intended target."
                )
                if (
                    self.pending.action["op"] == "click"
                    and draft_only(self.instruction)
                    and not self.inserted_text
                ):
                    self.phase = "focus_review"
                    self.question_id = uuid.uuid4().hex
                    self.state_changed.emit(State.WAITING)
                    self.approval_requested.emit(
                        self.question_id,
                        "The model could not verify composer focus after the click. "
                        "Inspect the intended editable message composer. Approve ONLY if "
                        "that composer gained keyboard focus and is ready for the requested "
                        "draft. If the click missed, focus is unclear, or another field is "
                        "active, Decline and focus the composer manually before a new task. "
                        "This confirms focus only, not typing or task completion. "
                        "No further click will be tried automatically.",
                    )
                    self.approval_timer.start(60000)
                    return
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
                    advice = (
                        "Focus the intended composer manually and start a new task."
                        if draft_only(self.instruction) else
                        "Inspect or focus the intended application manually before starting a new task."
                    )
                    self.fail("No verified progress after three attempts. " + advice)
                    return
            else:
                self.recovery_attempts = self.rejected_proposals = 0
            self.phase = "observing"
            # Verification can take seconds. Plan from a fresh frame rather than
            # the now-stale image that was submitted to the verifier.
            self._capture()
        elif self.phase == "comparing":
            age = time.monotonic() - self.observation.captured_at
            self._record("scene_recheck_result", matched=bool(result), age_s=age,
                         comparison=self.scene_details, observation=self.log_frame)
            if not self._reviewed_proposal_matches():
                return
            if not result or age > 2:
                self.stale += 1
                if self.stale > 2:
                    self.fail("Screen changed repeatedly. Stop and stabilize the test desktop.")
                else:
                    self.phase = "observing"
                    self._capture()
                return
            try:
                action = self._prepare_action(self.pending.action, self.observation)
                self._validate_draft_proposal()
            except (ValueError, TypeError):
                self.fail("Action is outside the supervised demo policy.")
                return
            self.phase = "executing"
            self.state_changed.emit(State.WORKING)
            self.stage_changed.emit("Acting")
            keyboard = action["op"] in {"text", "press", "chord"}
            self._record("dispatch_requested", transaction=self.actions + 1,
                         observation=self.log_frame, planned_observation=self.planned_frame,
                         expected_foreground=self.run_diagnostics.focus_alias(self.observation.foreground) if keyboard else None,
                         observed_foreground=self.run_diagnostics.focus_alias(self.observation.foreground),
                         expected_geometry=[asdict(bounds) for bounds in self.observation.displays],
                         observation_age_s=time.monotonic() - self.observation.captured_at,
                         content={"image_action": self.pending.action, "physical_action": action})
            self.input_job = self._start_input(
                "execute",
                action,
                self.observation.foreground if keyboard else None,
                list(self.observation.displays),
            )
        elif self.phase == "deciding":
            self.pending = result
            self._record("proposal", observation=self.planned_frame,
                         content={"proposal": asdict(result)})
            self.preview_ready.emit(result)
            if result.kind == "action" and result.action == {"op": "screenshot"}:
                self.phase = "observing"
                self._schedule_capture()
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
                        "This action already failed or its text was already inserted.", "repeat"
                    )
                    return
                try:
                    self._prepare_action(action, self.observation)
                    self._validate_draft_proposal()
                except (ValueError, TypeError) as exc:
                    self._reject_proposal(str(exc), "action_validation")
                    return
                reason = (
                    "Closing a window can discard unsaved work. Confirm the intended window "
                    "is the current foreground target."
                    if window_close_requested(self.instruction) else
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
                self.reviewed_proposal = deepcopy(result)
                self.phase = "approval"
                if not self.config.approve_each_step and reason is None:
                    self._begin_recheck(restore=False)
                    return
            else:
                if self.last_verification and self.last_verification.status != "succeeded":
                    self._reject_proposal(
                        "Completion is unsupported: the last action failed visual verification.", "completion"
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
            self._record("review_response_ignored", source_request_id=request_id,
                         active=self.active, paused=self.paused,
                         matching_request=request_id == self.question_id,
                         timer_active=self.approval_timer.isActive())
            return
        self._record("review_response", request_id=request_id, content={"answer": answer})
        self.approval_timer.stop()
        self.question_id = None
        if self.phase == "clarifying":
            self.instruction = (self.instruction + "\nUser clarification: " + answer[:1000])[:3000]
            self.restore_focus = self.observation.foreground
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
        elif self.phase == "focus_review":
            self.human_confirmed_focus = True
            self.history.append(
                "The user confirmed keyboard focus in the intended editable composer. "
                "This is human focus evidence, not successful model verification or task "
                "completion. Do not click or navigate again; propose the requested text "
                "only if the fresh screenshot still supports the intended target."
            )
            self.trace.append("User confirmed composer focus; text remains unverified.")
            self.recovery_attempts = self.rejected_proposals = 0
            self.restore_focus = self.observation.foreground
            self.phase = "observing"
            self._capture()
        elif self.phase == "confirming":
            self._terminate("complete", "Completed · visible result confirmed by you")
        elif self.phase == "approval":
            self._begin_recheck(restore=True)

    def _validate_draft_proposal(self):
        validate_draft_action(self.pending, self.instruction, bool(self.inserted_text))
        if self.human_confirmed_focus and self.pending.action.get("op") != "text":
            raise ValueError(
                "The user already confirmed composer focus. Do not click or navigate again; "
                "propose the requested draft text or ask for help."
            )

    def _prepare_action(self, action, observation):
        if self.single_click_policy:
            if self.single_click_dispatched:
                raise ValueError("The requested one-click input was already dispatched; no further input.")
            if action.get("op") not in {"move", "click"}:
                raise ValueError("A one-click request permits pointer movement and one click only; no substitutes.")
            if action.get("op") == "click" and action.get("count", 1) != 1:
                raise ValueError("The request permits one click only, not a double click.")
        closing = window_close_requested(self.instruction)
        if closing and (self.close_dispatched or action != {"op": "chord", "keys": ["ALT", "F4"]}):
            raise ValueError(
                "Current-window closing supports one reviewed Alt+F4 only; no guessed clicks or retry."
            )
        return prepare_action(action, observation, allow_window_close=closing)

    def _reviewed_proposal_matches(self):
        if self.reviewed_proposal is None or self.pending != self.reviewed_proposal:
            self.fail("Action proposal changed during review/recheck. No changed input was authorized.")
            return False
        return True

    def _begin_recheck(self, restore):
        if not self._reviewed_proposal_matches():
            return
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
                "requested text is visibly present in that editable composer, not in chat history. "
                "Type the complete requested draft in one text transaction. After text insertion, "
                "only observe and report completion or ask for help; do not append, edit or send."
            )
        if self.single_click_policy:
            text += (
                "\nTrusted task boundary: at most one single click. Locate the target from "
                "the screenshot, never assume a taskbar layout. After the click, only observe "
                "and report completion or ask for help; do not click again or substitute keys."
            )
        if window_close_requested(self.instruction):
            text += (
                "\nClose the current foreground window only. Propose Alt+F4, not clicks "
                "at guessed title-bar coordinates. Human review is mandatory because "
                "closing can discard unsaved work. One close transaction is permitted. "
                "Do not close other windows or dismiss unsaved-work confirmation dialogs. "
                "Verify the visible result, then request final confirmation; ask for help if uncertain."
            )
        if self.human_confirmed_focus:
            text += (
                "\nHuman focus confirmation: the user confirmed the intended editable "
                "composer had focus. This is not task completion or text verification. "
                "Use the fresh screenshot; do not click or navigate again. Propose the "
                "requested draft text, or ask for help if the target changed."
            )
        return text

    def _reject_proposal(self, reason, category="unspecified"):
        self.rejected_proposals += 1
        label = {
            "model_response": "invalid model response",
            "repeat": "repeated/previously failed input",
            "action_validation": "tool/action validation",
            "completion": "unsupported completion",
        }.get(category, "unspecified validation")
        self.trace.append(f"Proposal rejected: {label}.")
        self._record("proposal_rejected", category=label, content={
            "reason": reason,
            "proposal": asdict(self.pending) if category != "model_response" and is_dataclass(self.pending) else None,
        })
        self.history.append(
            f"Controller rejected the proposed step: {reason} "
            "No input was executed. Choose a different valid approach or ask for help."
        )
        if self.rejected_proposals >= 3:
            advice = (
                "Focus the current message composer manually and give a fresh typing instruction."
                if draft_only(self.instruction) else
                "Inspect the target application and Last action preview before starting a new task."
            )
            self.fail("Three repeated/invalid proposals rejected without input. " + advice)
            return
        self.phase = "observing"
        self._capture()

    def failure_diagnostics(self):
        return (
            f"Planning calls: {self.calls}; visual checks: {self.verification_calls}.\n"
            + "\n".join(self.trace[-12:])
            + "\n\n" + self.timings.report()
        )

    def capture_failed(self, error):
        self._record("capture_failed", content={"error": error})
        self.timings.finish(("capture", self.generation), "failed")
        self.fail(error)

    def fail(self, message):
        if self.active:
            self._terminate("error", "Stopped incomplete: " + message)

    def _terminate(self, kind, message):
        self._record("task_terminated", status=kind, model_job=self.job, input_job=self.input_job,
                     review_id=self.question_id, scheduled_capture=self.scheduled_capture,
                     content={"reason": message})
        if self.run_diagnostics is not None:
            self.run_diagnostics.end(kind, self.phase, message)
        self.capture_timer.stop()
        self.scheduled_capture = None
        self.timings.cancel_pending()
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
        self._remember_diagnostic_origin(self.input_diagnostic_origins, self.input_job)
        self.timings.begin(("input", self.input_job), "cleanup")
        self.instruction = ""
        self.observation = self.pending = None
        self.reviewed_proposal = None

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
