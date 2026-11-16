"""Qt-thread side of the remote MCP surface.

The MCP server runs on its own thread. It never touches Qt objects directly: each tool
call is posted here through a queued signal and answered through a concurrent Future.
All policy (preconditions, laptop approval, frame binding, worker ownership) lives here.
"""

import base64
import os
import time
import uuid
from concurrent.futures import Future

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage

from monster.computer.contracts import KEYS, MAX_DRAG_PATH, validate_action

APPROVAL_SECONDS = 60
FRAME_MAX_AGE_S = 120
MAX_IMAGE_EDGE = 1600
MAX_TEXT = 2000
MAX_DIAGNOSTIC_EVENTS = 300
# Events that explain why a task advanced or stopped; the rest is HTTP/worker plumbing.
KEY_EVENTS = frozenset({
    "task_started", "proposal", "proposal_rejected", "verification_result",
    "verification_reobserve_requested", "scene_recheck_result", "input_result",
    "completion_verification", "launch_state_observed", "app_launch_stage",
    "draft_target_result", "drawing_target_result", "review_response", "capture_failed",
    "model_result", "task_terminated", "startup_failed", "dispatch_requested",
    "review_requested", "clarification_requested", "launch_query_confirmed",
})


class RemoteError(RuntimeError):
    """A refusal that is safe to show to the remote chat client verbatim."""


def _resolve(future, value=None, error=None):
    if future.done():
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(value)


class RemoteBridge(QObject):
    _posted = Signal(object)

    def __init__(self, ui, input_factory=None, windows_factory=None, capture=None,
                 clock=time.monotonic, window_pid=None, window_at=None, parent=None,
                 auto_approve=None):
        super().__init__(parent)
        self.ui = ui
        # None follows the user's live Approval setting; True/False pins it (tests).
        self._auto_approve = auto_approve
        if input_factory is None:
            from monster.agent.input_bridge import InputBridge
            input_factory = InputBridge
        if windows_factory is None:
            from monster.computer.windows import WindowsInput
            windows_factory = WindowsInput
        if capture is None:
            from monster.computer.screenshot import capture_display
            capture = capture_display
        self.input_factory, self.windows_factory, self.capture_backend = (
            input_factory, windows_factory, capture)
        self.clock = clock
        if window_pid is None or window_at is None:
            from monster.native import root_window_at, window_process_id
            window_pid = window_pid or window_process_id
            window_at = window_at or root_window_at
        self.window_pid, self.window_at = window_pid, window_at
        self.task = None
        self.frame = None
        self.capture_future = None
        self.action = None  # Pending or running raw action.
        self._posted.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)
        ui.isolation.completed.connect(self._capture_completed)
        ui.isolation.failed.connect(self._capture_failed)
        self.approval_timer = QTimer(self)
        self.approval_timer.setSingleShot(True)
        self.approval_timer.timeout.connect(lambda: self._decline("Approval expired on the laptop."))

    @property
    def auto_approve(self):
        """With "No approval", raw actions skip the laptop review. Every other check still
        applies, and Stop / Ctrl+Alt+X still cancel."""
        if self._auto_approve is not None:
            return self._auto_approve
        return getattr(getattr(self.ui, "model_config", None), "hands_free", False) is True

    @auto_approve.setter
    def auto_approve(self, value):
        self._auto_approve = value

    # ---- thread-safe entry -------------------------------------------------
    def call(self, name, **kwargs) -> Future:
        future = Future()
        self._posted.emit((name, kwargs, future))
        return future

    def _dispatch(self, request):
        name, kwargs, future = request
        if future.done():  # The remote client gave up (timeout) before Qt got to it.
            return
        try:
            handler = getattr(self, "op_" + name)
            result = handler(future, **kwargs)
        except RemoteError as exc:
            _resolve(future, error=exc)
        except Exception as exc:  # noqa: BLE001 - Never leak internals to the remote client.
            _resolve(future, error=RemoteError(f"Monster refused the request ({type(exc).__name__})."))
        else:
            if result is not None:
                _resolve(future, result)

    # ---- shared state ------------------------------------------------------
    def _controller(self):
        return self.ui.controller

    def _busy_reason(self):
        ui = self.ui
        if ui.workspace_mode != "personal_desktop":
            return "Monster is in Independent Workspace mode."
        if ui.mission_runtime and ui.mission_runtime.engaged:
            return "A mission owns the desktop."
        if ui.active:
            return "A task is already running. Use task_status or stop."
        if ui.pending_question:
            return "Monster is waiting for a review on the laptop."
        if self.action is not None:
            return "A raw action is already pending or running."
        if ui.isolation.busy:
            return "A screenshot is in progress."
        if ui.workspace_gate.busy:
            return "The desktop is busy or input cleanup is unconfirmed."
        if getattr(self._controller(), "cleanup_blocked", False) or ui.input_cleanup_blocked:
            return "Input cleanup is unconfirmed. Restart Monster before more input."
        return None

    def _is_monster_window(self, handle):
        owned = {int(self.ui.pill.winId()), int(self.ui.companion.winId())}
        if self.ui.control_panel:
            owned.add(int(self.ui.control_panel.winId()))
        return handle in owned or self.window_pid(handle) == os.getpid()

    def _target_window(self):
        from monster.native import foreground_window

        target = foreground_window()
        if not target:
            raise RemoteError("No usable foreground window. Focus the target app on the laptop.")
        if self._is_monster_window(target):
            raise RemoteError("The focused window belongs to Monster. Remote input may not "
                              "operate Monster itself; focus another app on the laptop.")
        return target

    def op_status(self, future):
        controller = self._controller()
        ui = self.ui
        mode = ("supervised" if getattr(controller, "is_supervised", False)
                else "demo" if controller.is_mock else "preview")
        pending = ui.pending_question
        return {
            "mode": mode,
            "task_running": bool(ui.active),
            "phase": getattr(controller, "phase", None),
            "waiting_for_laptop_review": bool(pending),
            "review_text": pending[1] if pending else None,
            "raw_action_pending": self.action is not None,
            "last_task": self._task_view(),
            "busy_reason": self._busy_reason(),
            "approval": "none" if self.auto_approve else "laptop",
        }

    # ---- high-level tasks --------------------------------------------------
    def _task_view(self):
        task = self.task
        if task is None:
            return None
        controller = self._controller()
        if task["state"] == "running":
            if controller is not task["controller"] or (
                getattr(controller, "run_generation", None) != task["generation"]
            ):
                task["state"], task["message"] = "superseded", "Another task replaced this one."
            elif not controller.active and getattr(controller, "phase", "") != "closing":
                outcome = self.ui.last_task_outcome
                task["state"] = "completed" if outcome == "completed" else "stopped_incomplete"
        pending = self.ui.pending_question if task["state"] == "running" else None
        return {
            "task_id": task["id"],
            "instruction": task["instruction"],
            "state": task["state"],
            "phase": getattr(controller, "phase", None) if task["state"] == "running" else None,
            "waiting_for_laptop_review": bool(pending),
            "review_text": pending[1] if pending else None,
            "message": task["message"],
            "result": task.get("result"),
            "confirmed_input_transactions": getattr(controller, "actions", None),
        }

    def op_run_task(self, future, instruction):
        instruction = (instruction or "").strip()
        if not instruction:
            raise RemoteError("Instruction is empty.")
        if len(instruction) > MAX_TEXT:
            raise RemoteError(f"Instruction exceeds {MAX_TEXT} characters.")
        controller = self._controller()
        if not getattr(controller, "is_supervised", False):
            raise RemoteError(
                "Monster is not in supervised real-input mode. On the laptop open "
                "AI model & automation and choose 'Supervised real input'.")
        reason = self._busy_reason()
        if reason:
            raise RemoteError(reason)
        target = self._target_window()
        self.ui.activation_target = target
        self.ui.submit(instruction)
        if not controller.active or self.ui.last_task_outcome != "running":
            raise RemoteError(
                "Monster did not start the task. See Last failure on the laptop for details.")
        task = {"id": uuid.uuid4().hex[:12], "instruction": instruction, "state": "running",
                "message": None, "controller": controller,
                "generation": controller.run_generation}

        def finished(text, current=task):
            if current["state"] == "running" and self.task is current:
                current["state"], current["message"] = "completed", text
                current["result"] = getattr(controller, "completion_summary", None)

        def failed(text, current=task):
            if current["state"] == "running" and self.task is current:
                current["state"], current["message"] = "stopped_incomplete", text

        if self.task:
            self._disconnect_task(self.task)
        task["slots"] = ((controller.finished, finished), (controller.failed, failed))
        for signal, slot in task["slots"]:
            signal.connect(slot)
        self.task = task
        return self._task_view()

    def _disconnect_task(self, task):
        for signal, slot in task.get("slots", ()):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        task["slots"] = ()

    def op_task_status(self, future, task_id=None):
        if self.task is None or (task_id and task_id != self.task["id"]):
            raise RemoteError("Unknown task_id. Only the most recent remote task is tracked.")
        return self._task_view()

    def op_task_diagnostics(self, future, task_id=None, full=False):
        """Read-only event trail of the tracked task, without screenshots."""
        task = self.task
        if task is None or (task_id and task_id != task["id"]):
            raise RemoteError("Unknown task_id. Only the most recent remote task is tracked.")
        controller = self._controller()
        if (controller is not task["controller"]
                or getattr(controller, "run_generation", None) != task["generation"]):
            raise RemoteError("This task's diagnostics were replaced by a newer run.")
        snapshot = controller.diagnostic_snapshot(include_content=True)
        if snapshot is None:
            raise RemoteError("No diagnostics recorded for this task.")
        data = snapshot.data
        events = data["events"] if full else [e for e in data["events"] if e["event"] in KEY_EVENTS]
        dropped = max(0, len(events) - MAX_DIAGNOSTIC_EVENTS)
        return {
            "task_id": task["id"], "state": self._task_view()["state"],
            "outcome": data["outcome"], "summary": data["summary"],
            "configuration": {k: data["configuration"].get(k)
                              for k in ("model", "mode", "hands_free", "approve_each_step")},
            "events": events[-MAX_DIAGNOSTIC_EVENTS:], "events_omitted": dropped,
            "view": "full" if full else "key",
        }

    def op_stop(self, future):
        stopped = []
        if self.action is not None:
            self._decline("Stopped from the remote client.")
            stopped.append("raw_action")
        if self.ui.active or self.ui.pending_question:
            self.ui.stop()
            stopped.append("task")
        return {"stopped": stopped or ["nothing_running"]}

    # ---- screenshots -------------------------------------------------------
    def op_screenshot(self, future, display=None):
        reason = self._busy_reason()
        if reason:
            raise RemoteError(reason)
        if display is not None and (type(display) is not int or not 0 <= display <= 16):
            raise RemoteError("display must be an integer 0-16 (0 = all displays).")
        self.capture_future = future
        backend = self.capture_backend
        try:
            self.ui.capture_desktop(lambda: backend(display))
        except RuntimeError as exc:
            self.capture_future = None
            raise RemoteError(str(exc)) from None
        # Resolved asynchronously by the isolation signals.

    def _capture_completed(self, observation):
        future, self.capture_future = self.capture_future, None
        if future is None:
            return
        self.ui.notice("ChatGPT took a screenshot", 2000)
        try:
            _resolve(future, self._encode_frame(observation))
        except Exception as exc:  # noqa: BLE001
            _resolve(future, error=RemoteError(f"Could not encode screenshot ({type(exc).__name__})."))

    def _capture_failed(self, error):
        future, self.capture_future = self.capture_future, None
        if future is not None:
            _resolve(future, error=RemoteError(error))

    def _encode_frame(self, observation):
        image = QImage.fromData(observation.png, "PNG")
        if image.isNull():
            raise ValueError("bad png")
        scale = min(1.0, MAX_IMAGE_EDGE / max(image.width(), image.height()))
        if scale < 1.0:
            image = image.scaled(round(image.width() * scale), round(image.height() * scale),
                                 Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "JPEG", 80)
        buffer.close()
        frame_id = uuid.uuid4().hex[:10]
        self.frame = {
            "id": frame_id, "observation": observation, "captured_at": self.clock(),
            "width": image.width(), "height": image.height(),
            "scale_x": image.width() / observation.bounds.width,
            "scale_y": image.height() / observation.bounds.height,
        }
        return {"screenshot_id": frame_id, "width": image.width(), "height": image.height(),
                "jpeg_base64": base64.b64encode(bytes(data)).decode("ascii")}

    # ---- raw actions -------------------------------------------------------
    def _frame_point(self, screenshot_id, x, y):
        frame = self.frame
        if frame is None or frame["id"] != screenshot_id:
            raise RemoteError("Unknown or outdated screenshot_id. Take a new screenshot first.")
        if self.clock() - frame["captured_at"] > FRAME_MAX_AGE_S:
            raise RemoteError("Screenshot is older than two minutes. Take a new one.")
        for value, limit, name in ((x, frame["width"], "x"), (y, frame["height"], "y")):
            if type(value) is not int or not 0 <= value < limit:
                raise RemoteError(f"{name} must be an integer pixel inside the screenshot.")
        bounds = frame["observation"].bounds
        return (bounds.x + min(bounds.width - 1, round(x / frame["scale_x"])),
                bounds.y + min(bounds.height - 1, round(y / frame["scale_y"])))

    def op_act(self, future, kind, **params):
        reason = self._busy_reason()
        if reason:
            raise RemoteError(reason)
        if kind == "click":
            px, py = self._frame_point(params["screenshot_id"], params["x"], params["y"])
            button, count = params.get("button", "left"), 2 if params.get("double") else 1
            action = {"op": "click", "x": px, "y": py, "button": button}
            if count == 2:
                action["count"] = 2
            summary = (f"{'Double-click' if count == 2 else 'Click'} {button} at screenshot "
                       f"({params['x']}, {params['y']}) → screen ({px}, {py}).")
        elif kind == "scroll":
            px, py = self._frame_point(params["screenshot_id"], params["x"], params["y"])
            delta = params["delta"]
            if type(delta) is not int or not -10 <= delta <= 10 or delta == 0:
                raise RemoteError("delta must be a non-zero integer from -10 to 10 notches.")
            action = {"op": "scroll", "x": px, "y": py, "delta": delta * 120}
            summary = f"Scroll {'down' if delta < 0 else 'up'} {abs(delta)} at ({params['x']}, {params['y']})."
        elif kind == "drag":
            points = params["points"]
            if (type(points) is not list or not 2 <= len(points) <= MAX_DRAG_PATH + 2
                    or any(type(p) not in (list, tuple) or len(p) != 2 for p in points)):
                raise RemoteError(f"points must be 2-{MAX_DRAG_PATH + 2} [x, y] pairs.")
            duration = params.get("duration")
            if duration is None:
                duration = 0.5 if len(points) == 2 else 1.0
            if type(duration) not in (int, float) or not 0.1 <= duration <= 1.5:
                raise RemoteError("duration must be 0.1-1.5 seconds.")
            mapped = [self._frame_point(params["screenshot_id"], x, y) for x, y in points]
            (sx, sy), (ex, ey) = mapped[0], mapped[-1]
            action = {"op": "drag", "x": sx, "y": sy, "end_x": ex, "end_y": ey,
                      "button": "left", "duration": float(duration)}
            if len(mapped) > 2:
                action["path"] = [list(p) for p in mapped[1:-1]]
            summary = (f"Left-drag through {len(points)} points from screenshot "
                       f"({points[0][0]}, {points[0][1]}) to ({points[-1][0]}, {points[-1][1]}) "
                       f"over {float(duration):.1f}s. The button is held the whole time.")
        elif kind == "type_text":
            text = params["text"]
            if type(text) is not str or not text or len(text) > MAX_TEXT:
                raise RemoteError(f"text must be 1-{MAX_TEXT} characters.")
            action = {"op": "text", "text": text}
            summary = f"Type into the focused window:\n“{text}”"
        elif kind == "press_keys":
            keys = [str(k).upper() for k in params["keys"]]
            unknown = [k for k in keys if k not in KEYS]
            if not keys or len(keys) > 4 or unknown:
                raise RemoteError("keys must be 1-4 names from: " + ", ".join(sorted(KEYS)))
            action = {"op": "press", "key": keys[0]} if len(keys) == 1 else {"op": "chord", "keys": keys}
            summary = "Press " + "+".join(keys) + "."
        else:
            raise RemoteError("Unknown action kind.")
        frame = self.frame
        displays = list(frame["observation"].displays) if frame else None
        if displays is None:
            raise RemoteError("Take a screenshot first so Monster can bind the display layout.")
        try:
            validate_action(action, displays)
        except ValueError as exc:
            raise RemoteError(str(exc)) from None
        target = self._target_window()
        if "x" in action and self._is_monster_window(self.window_at(action["x"], action["y"])):
            raise RemoteError("That point is on a Monster window. Remote input may not operate "
                              "Monster's own controls or settings.")
        request_id = "remote-" + uuid.uuid4().hex[:10]
        self.action = {"id": request_id, "future": future, "action": action, "summary": summary,
                       "target": target, "displays": displays, "lease": None, "bridge": None,
                       "jobs": {}, "receipt": None, "error": None}
        if self.auto_approve:
            self.ui.notice("Remote action: " + summary.splitlines()[0], 2000)
            self._execute(self.action)
            return
        self.ui.question(
            request_id,
            "ChatGPT requests one raw action:\n" + summary +
            "\nApprove only if this is exactly what you want.", True)
        self.approval_timer.start(APPROVAL_SECONDS * 1000)
        # Resolved after the laptop review and worker cleanup.

    def answer(self, request_id, answer):
        """Called by the app when the pill answers a remote-* review."""
        current = self.action
        if current is None or current["id"] != request_id or current["bridge"] is not None:
            return
        self.approval_timer.stop()
        if answer != "approve":
            self._decline("Declined on the laptop.")
            return
        self._execute(current)

    def cancel_action(self, message):
        """Laptop stop/quit: drop a pending review or stop a running raw action."""
        self._decline(message)

    def _decline(self, message):
        current, self.action = self.action, None
        self.approval_timer.stop()
        if current is None:
            return
        if current["bridge"] is not None:
            # Keep ownership until cleanup completes. Stop discards queued jobs without a
            # completion signal, so close (which always completes) drives the finish.
            self.action = current
            current["error"] = current["error"] or message
            if "close" not in current["jobs"]:
                current["jobs"]["close"] = current["bridge"].close()
            return
        ui = self.ui
        if ui.pending_question and ui.pending_question[0] == current["id"]:
            ui.pending_question = None
            ui._cancel_keyboard_approval()
            ui.pill.hide()
            ui.hotkey_errors = ui.register_hotkeys()
        _resolve(current["future"], {"status": "declined", "message": message})

    def _execute(self, current):
        ui = self.ui
        lease = ui.workspace_gate.acquire("remote-" + uuid.uuid4().hex, 1)
        if lease is None:
            self.action = None
            _resolve(current["future"], error=RemoteError("Desktop busy; no input started."))
            return
        current["lease"] = lease
        ui._worker_ownership(True)
        bridge = self.input_factory(parent=self)
        current["bridge"] = bridge
        bridge.completed.connect(
            lambda job, result, error, source=bridge: self._input_result(source, job, result, error))
        current["jobs"]["open"] = bridge.submit("open")

    def _input_result(self, source, job, result, error):
        current = self.action
        if current is None or current["bridge"] is not source:
            return  # Late signal from an earlier worker session.
        jobs, bridge = current["jobs"], current["bridge"]
        if job == jobs.get("close"):
            self._finish(current, cleanup_confirmed=bool(result) and not error)
        elif "close" in jobs:
            if job == jobs.get("execute"):
                current["receipt"] = result  # Stopped mid-flight; keep the evidence.
        elif job == jobs.get("open"):
            if error or current["error"]:
                current["error"] = current["error"] or error
                jobs["close"] = bridge.close()
                return
            if not self.windows_factory().restore_foreground(current["target"]):
                current["error"] = "Could not restore the target window; no input sent."
                jobs["close"] = bridge.close()
                return
            jobs["execute"] = bridge.submit("execute", current["action"], current["target"],
                                            current["displays"])
        elif job == jobs.get("execute"):
            current["receipt"], current["error"] = result, current["error"] or (error or None)
            jobs["close"] = bridge.close()

    def _finish(self, current, *, cleanup_confirmed):
        ui = self.ui
        self.action = None
        ui.workspace_gate.release(current["lease"], cleanup_confirmed=cleanup_confirmed)
        if not cleanup_confirmed:
            ui.input_cleanup_blocked = True
        ui._worker_ownership(False)
        current["bridge"].shutdown()
        receipt = current["receipt"] or {}
        status = receipt.get("status") if receipt else "not_sent"
        outcome = {
            "status": "executed" if status == "inserted" and not current["error"] else "failed",
            "worker_status": status,
            "cleanup_confirmed": cleanup_confirmed,
            "message": current["error"] or "Input delivered. Take a screenshot to verify the result.",
        }
        if status == "partial_or_unknown":
            outcome["message"] = "Input may have partially occurred. Inspect before retrying."
        _resolve(current["future"], outcome)
