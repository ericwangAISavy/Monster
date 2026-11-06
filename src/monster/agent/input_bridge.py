"""Serial, off-UI worker client. Cancellation invalidates queued bridge operations."""

import queue
import threading
import time

from PySide6.QtCore import QObject, Signal

from monster.agent.diagnostics import exception_evidence
from monster.computer.client import InputSession


class InputBridge(QObject):
    completed = Signal(int, object, str)
    interrupted = Signal()
    diagnostic = Signal(object)

    def __init__(self, factory=InputSession, parent=None):
        super().__init__(parent)
        self.factory = factory
        self.session = None
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.epoch = 0
        self.next_id = 0
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="monster-input-client")
        self.thread.start()

    def submit(self, operation, action=None, focus=None, geometry=None):
        with self.lock:
            self.next_id += 1
            job = self.next_id
            self.queue.put((job, self.epoch, operation, action, focus, geometry))
            return job

    def stop(self):
        with self.lock:
            self.epoch += 1
            if self.session:
                self.session.stop()

    def _run(self):
        while True:
            request = self.queue.get()
            if request is None:
                return
            job, epoch, operation, action, focus, geometry = request
            if operation != "close" and epoch != self.epoch:
                continue
            result, error = None, ""
            stage = operation
            try:
                if operation == "open":
                    session = self.factory(event_callback=lambda event, opened=job, source_epoch=epoch:
                                           self._worker_event(event, opened, source_epoch))
                    with self.lock:
                        self.session = session
                    if epoch != self.epoch:
                        session.close()
                        with self.lock:
                            self.session = None
                        continue
                    result = True
                elif operation == "execute":
                    session = self.session
                    if not session:
                        raise RuntimeError("No input worker.")
                    stage = "arm"
                    self.diagnostic.emit({"event": "arm_requested", "job": job, "monotonic": time.monotonic()})
                    session.arm()
                    self.diagnostic.emit({"event": "arm_ready", "job": job, "monotonic": time.monotonic()})
                    if epoch != self.epoch:
                        session.stop()
                        continue
                    stage = "execute"
                    self.diagnostic.emit({"event": "dispatch_to_worker", "job": job, "monotonic": time.monotonic()})
                    result = session.execute(
                        action, expected_focus=focus, expected_geometry=geometry
                    )
                    if result.get("status") == "inserted":
                        stage = "disarm"
                        session.disarm()
                        self.diagnostic.emit({"event": "disarm_ready", "job": job, "monotonic": time.monotonic()})
                elif operation == "close":
                    result = True if not self.session else self.session.close()
                    self.diagnostic.emit({"event": "cleanup_summary", "job": job, "confirmed": bool(result),
                                          "exit_code": getattr(getattr(self.session, "process", None), "exitcode", None)})
                    with self.lock:
                        self.session = None
                else:
                    raise RuntimeError("Unknown input operation.")
            except Exception as exc:  # noqa: BLE001 - Only exception type, never values/locals.
                self.diagnostic.emit({"event": "input_exception", "job": job,
                                      "operation": operation, "input_stage": stage, "evidence": exception_evidence(exc)})
                error = "Input worker failed or was stopped; inspect the desktop before retrying."
                if self.session:
                    self.session.stop()
            self.completed.emit(job, result, error)

    def _worker_event(self, event, source_job=None, source_epoch=None):
        if event.get("event") in {"ready", "startup_error", "stopped", "disarmed", "worker_lost", "arm_error"}:
            details = {"event": event.get("event"), "source_open_job": source_job,
                       "source_epoch": source_epoch, "cleanup_ok": event.get("cleanup_ok")}
            reason = event.get("reason")
            known_reasons = {
                "emergency hotkey", "Input is disarmed or its lease expired.",
                "Desktop or display geometry changed.", "Keyboard focus changed.",
                "Held-input limit reached.", "parent stop", "worker exit",
                "protocol or connection failure", "Disarmed", "RuntimeError", "OSError",
                "ValueError", "TypeError",
            }
            if type(reason) is str:
                details["reason"] = reason if reason in known_reasons else "unclassified worker stop reason"
            if event.get("event") == "arm_error":
                known = {
                    "Input cleanup is unconfirmed; restart and inspect held input.",
                    "Unlock the desktop and release all modifiers/buttons before arming.",
                    "No displays available.",
                }
                error = event.get("error")
                details["reason"] = error if type(error) is str and error in known else "unclassified arm error"
            if event.get("event") == "startup_error":
                details["reason"] = "worker startup refused (check stop-shortcut ownership)"
            if event.get("event") == "ready":
                details["runtime"] = event.get("runtime")
            self.diagnostic.emit(details)
        if event.get("reason") == "emergency hotkey" or event.get("event") in {
            "disarmed",
            "worker_lost",
        }:
            self.interrupted.emit()

    def close(self):
        self.stop()
        job = self.submit("close")
        return job

    def shutdown(self):
        self.closed = True
        self.close()
        self.queue.put(None)
        self.thread.join(timeout=3)
