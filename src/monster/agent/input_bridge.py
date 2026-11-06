"""Serial, off-UI worker client. Cancellation invalidates queued bridge operations."""

import queue
import threading

from PySide6.QtCore import QObject, Signal

from monster.computer.client import InputSession


class InputBridge(QObject):
    completed = Signal(int, object, str)
    interrupted = Signal()

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
            try:
                if operation == "open":
                    session = self.factory(event_callback=self._worker_event)
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
                    session.arm()
                    if epoch != self.epoch:
                        session.stop()
                        continue
                    result = session.execute(
                        action, expected_focus=focus, expected_geometry=geometry
                    )
                    if result.get("status") == "inserted":
                        session.disarm()
                elif operation == "close":
                    result = True if not self.session else self.session.close()
                    with self.lock:
                        self.session = None
                else:
                    raise RuntimeError("Unknown input operation.")
            except Exception:  # noqa: BLE001 - Never expose typed payloads/native error bodies.
                error = "Input worker failed or was stopped; inspect the desktop before retrying."
                if self.session:
                    self.session.stop()
            self.completed.emit(job, result, error)

    def _worker_event(self, event):
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
