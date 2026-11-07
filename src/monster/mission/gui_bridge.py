"""App-owned Qt dispatcher. Nothing here creates a controller, input worker or capture loop."""

import threading
import time
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot

from monster.agent.terminal import ExecutionReport

from .contracts import GuiSubtaskRequest, TaskResult


@dataclass(frozen=True)
class Handoff:
    request: GuiSubtaskRequest
    cancelled: threading.Event


class MissionGuiBridge(QObject):
    start_requested = Signal(object)
    cancel_requested = Signal(object)
    resolved = Signal(object)
    policy_requested = Signal(object)
    policy_completed = Signal(object)

    def __init__(
        self,
        controller,
        gate,
        *,
        preflight,
        objective_catalog,
        workspace_id="interactive-desktop",
        parent=None,
    ):
        super().__init__(parent)
        self.controller, self.gate = controller, gate
        self.preflight, self.catalog = preflight, dict(objective_catalog)
        self.workspace_id = workspace_id
        self.current = None
        self.seen = set()
        self.closed = False
        self._terminal_connected = True
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._watchdog)
        self.start_requested.connect(self._start, Qt.ConnectionType.QueuedConnection)
        self.cancel_requested.connect(self._cancel, Qt.ConnectionType.QueuedConnection)
        self.controller.terminal_ready.connect(self._terminal)
        self._policy_connected = hasattr(controller, "effect_requested")
        if self._policy_connected:
            controller.effect_requested.connect(self._policy_request)
        self.policy_completed.connect(self._policy_reply, Qt.ConnectionType.QueuedConnection)
        self.timer.start()

    def affinity(self):
        if QThread.currentThread() != self.thread():
            raise RuntimeError("Mission dispatcher called outside Qt affinity thread")

    @staticmethod
    def result(req, *, status="blocked", cleanup=True, report=None, cancelled=False):
        verified = bool(
            report
            and report.cleanup_confirmed
            and report.visual_verified
            and report.human_confirmed
            and report.terminal_kind == "complete"
            and not cancelled
        )
        return TaskResult(
            mission_id=req.mission_id,
            subtask_id=req.subtask.subtask_id,
            effect_id=req.effect_id,
            generation=req.generation,
            status="verified_success" if verified else status,
            cleanup_confirmed=cleanup,
            confirmed_input_transactions=report.confirmed_input_transactions if report else 0,
            visual_verified=bool(report and report.visual_verified),
            human_confirmed=bool(report and report.human_confirmed and not cancelled),
            observation_ref=report.observation_ref if report else None,
            evidence_ref=f"gui:{req.effect_id}" if verified else None,
            evidence_kind="human" if verified else "none",
            reason="verified" if verified else "cancelled" if cancelled else "not_observed",
        )

    @Slot(object)
    def _start(self, handoff):
        self.affinity()
        req = GuiSubtaskRequest.model_validate(handoff.request.model_dump())
        if (
            self.closed
            or req.workspace_id != self.workspace_id
            or handoff.cancelled.is_set()
            or req.effect_id in self.seen
            or len(self.seen) >= 512
            or self.current
            or self.gate.busy
            or self.controller.active
            or self.controller.phase != "idle"
            or self.controller.cleanup_blocked
            or req.authorization_expires_at is None
            or req.authorization_expires_at <= time.time()
            or req.subtask.objective_ref not in self.catalog
            or not req.subtask.requires_approval
            or req.subtask.max_actions != 10
            or req.subtask.max_model_calls != 20
            or req.subtask.deadline_seconds != 300
            or req.subtask.category not in ("read_only", "local_reversible")
        ):
            self.resolved.emit(self.result(req))
            return
        self.seen.add(req.effect_id)
        try:
            token = self.gate.acquire(req.effect_id, req.generation)
        except OSError:
            token = None
        if token is None:
            self.resolved.emit(self.result(req))
            return
        try:
            target = self.preflight()
            if (
                not target
                or handoff.cancelled.is_set()
                or req.authorization_expires_at <= time.time()
            ):
                raise RuntimeError("No foreground target")
        except (OSError, RuntimeError, ValueError):
            self.gate.release(token, cleanup_confirmed=True)
            self.resolved.emit(self.result(req))
            return
        old = self.controller.config
        self.controller.config = replace(old, approve_each_step=True)
        # Install binding before submit emits any signals. Generation is assigned by submit.
        self.current = {
            "handoff": handoff,
            "token": token,
            "config": old,
            "controller_generation": self.controller.generation + 1,
            "deadline": time.monotonic() + req.subtask.deadline_seconds,
            "stop_at": None,
        }
        self.controller.action_gate = self._policy_connected
        if not self.controller.submit(self.catalog[req.subtask.objective_ref]):
            self._finish(self.result(req))
            return
        self.controller.action_gate = self._policy_connected
        self.controller.task_foreground = target
        self.controller.mission_cancel_event = handoff.cancelled

    @Slot(object)
    def _cancel(self, key):
        self.affinity()
        if self.current:
            req = self.current["handoff"].request
            if (req.effect_id, req.generation) == key:
                self.current["handoff"].cancelled.set()
                self.current["stop_at"] = self.current["stop_at"] or time.monotonic()
                self.controller.cancel()

    @Slot(object)
    def _terminal(self, report):
        self.affinity()
        if not self.current or not isinstance(report, ExecutionReport):
            return
        if report.run_generation != self.current["controller_generation"]:
            return
        handoff = self.current["handoff"]
        import hashlib
        import re

        model = self.controller.config.model
        model_ref = (
            model
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", model)
            else ("model:" + hashlib.sha256(model.encode()).hexdigest())
        )
        result = self.result(
            handoff.request,
            status="uncertain",
            cleanup=report.cleanup_confirmed,
            report=report,
            cancelled=handoff.cancelled.is_set(),
        )
        self._finish(result.model_copy(update={"model_id": model_ref}))

    def _finish(self, result):
        current, self.current = self.current, None
        self.controller.config = current["config"]
        self.controller.mission_cancel_event = None
        self.controller.action_gate = False
        self.gate.release(current["token"], cleanup_confirmed=result.cleanup_confirmed)
        self.resolved.emit(result)

    def _watchdog(self):
        if not self.current:
            return
        now = time.monotonic()
        if now >= self.current["deadline"] and self.current["stop_at"] is None:
            req = self.current["handoff"].request
            self._cancel((req.effect_id, req.generation))
        if (
            self.current
            and self.current["stop_at"] is not None
            and now - self.current["stop_at"] >= 5
        ):
            self._finish(
                self.result(
                    self.current["handoff"].request,
                    status="uncertain",
                    cleanup=False,
                    cancelled=True,
                )
            )

    @Slot(object)
    def _policy_request(self, packet):
        self.affinity()
        if not self.current:
            return
        req = self.current["handoff"].request
        observation = getattr(self.controller, "observation", None)
        # Only non-sensitive references/digests persist; actual action stays in RAM.
        import hashlib
        import json

        geometry = (
            (
                [vars(b) for b in observation.displays],
                vars(observation.bounds),
                observation.foreground,
            )
            if observation
            else None
        )
        scene = hashlib.sha256(json.dumps(geometry, sort_keys=True).encode()).hexdigest()
        context = {
            "mission_id": req.mission_id,
            "subtask_id": req.subtask.subtask_id,
            "attempt_id": req.effect_id,
            "workspace_id": req.workspace_id,
            "objective_ref": req.subtask.objective_ref,
            "generation": req.generation,
            "target_ref": "disposable-editor",
            "observation_ref": observation.id if observation else "unavailable",
            "scene_ref": scene,
            "active": not self.current["handoff"].cancelled.is_set(),
            "lease_valid": self.gate.current == self.current["token"] and not self.gate.quarantined,
            "cleanup_healthy": not self.controller.cleanup_blocked,
        }
        self.policy_requested.emit(
            {
                **packet,
                "context": context,
                "request_key": (req.effect_id, req.generation),
                "objective_ref": req.subtask.objective_ref,
            }
        )

    @Slot(object)
    def _policy_reply(self, reply):
        self.affinity()
        if not self.current:
            return
        req = self.current["handoff"].request
        if reply.get("request_key") != (req.effect_id, req.generation):
            return
        self.controller.effect_reply(reply)

    def halt(self):
        self.affinity()
        self.closed = True
        if self.current:
            req = self.current["handoff"].request
            self._cancel((req.effect_id, req.generation))

    def shutdown(self):
        self.affinity()
        self.closed = True
        self.timer.stop()
        if self._policy_connected:
            self.controller.effect_requested.disconnect(self._policy_request)
            self._policy_connected = False
        if self._terminal_connected:
            self.controller.terminal_ready.disconnect(self._terminal)
            self._terminal_connected = False
        if self.current:
            self.current["handoff"].cancelled.set()
            self.controller.cancel()
            self._finish(
                self.result(
                    self.current["handoff"].request,
                    status="uncertain",
                    cleanup=False,
                    cancelled=True,
                )
            )
