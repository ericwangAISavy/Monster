"""Event-driven engine boundary. The bundled controller is explicitly a UI simulation.

A real engine must implement this port and invalidate its own queued input before
emitting cancellation. UI cancellation alone is not an input safety mechanism.
"""

from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal


class State(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    WORKING = "working"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"


class TaskController(QObject):
    state_changed = Signal(str)
    stage_changed = Signal(str)  # Short content-free label; never model reasoning.
    status = Signal(str)
    finished = Signal(str)
    failed = Signal(str)
    approval_requested = Signal(str, str)  # request ID and concrete action summary
    clarification_requested = Signal(str, str)
    active_changed = Signal(bool)
    paused_changed = Signal(bool)
    is_mock = False

    def submit(self, instruction: str) -> bool:
        raise NotImplementedError

    def cancel(self) -> None:
        raise NotImplementedError

    def toggle_pause(self) -> None:
        raise NotImplementedError

    def respond(self, request_id: str, answer: str) -> None:
        raise NotImplementedError


class MockTaskController(TaskController):
    """Timers simulate latency; no model requests, captures, or input are performed."""

    is_mock = True

    def __init__(self, parent=None, interval_ms: int = 1800):
        super().__init__(parent)
        self.active = False
        self.paused = False
        self._step = 0
        self._request: tuple[str, str] | None = None
        self._generation = 0
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self._advance)

    def submit(self, instruction: str) -> bool:
        if self.active or not instruction.strip():
            return False
        self._generation += 1
        self._step = 0
        self._request = None
        self.active, self.paused = True, False
        self.active_changed.emit(True)
        self.paused_changed.emit(False)
        self.state_changed.emit(State.THINKING)
        self.timer.start()
        return True

    def _advance(self):
        if not self.active or self.paused or self._request:
            return
        self._step += 1
        if self._step == 1:
            self.state_changed.emit(State.WORKING)
        else:
            self.timer.stop()
            self.active = False
            self.active_changed.emit(False)
            self.state_changed.emit(State.COMPLETED)
            self.finished.emit("Demo complete · no computer actions performed")

    def toggle_pause(self):
        if not self.active:
            return
        self.paused = not self.paused
        self.paused_changed.emit(self.paused)
        if self.paused:
            self.timer.stop()
            self.state_changed.emit(State.WAITING)
            self.status.emit("Demo paused · resume from the tray")
        else:
            self.state_changed.emit(State.WAITING if self._request else State.WORKING)
            if not self._request:
                self.timer.start()

    def cancel(self):
        self._generation += 1
        self.timer.stop()
        self._request = None
        was_active = self.active
        self.active, self.paused = False, False
        self.active_changed.emit(False)
        self.paused_changed.emit(False)
        self.state_changed.emit(State.IDLE)
        if was_active:
            self.status.emit("Demo stopped · no computer actions performed")

    def request_demo(self, kind: str):
        """Explicit tray-only fixtures for exercising the same approval/clarification pill."""
        if not self.active:
            self.submit("UI interaction demo")
        self.timer.stop()
        request_id = f"demo-{self._generation}-{kind}"
        self._request = (request_id, kind)
        self.state_changed.emit(State.WAITING)
        if kind == "approval":
            self.approval_requested.emit(
                request_id,
                "Demo only: approve a simulated action? Nothing will be sent or changed.",
            )
        else:
            self.clarification_requested.emit(
                request_id, "Demo only: what should the note be titled?"
            )

    def respond(self, request_id: str, answer: str):
        if not self.active or not self._request or self._request[0] != request_id:
            return
        kind = self._request[1]
        if kind == "approval" and answer != "approve":
            self.cancel()
            return
        if not answer.strip():
            return
        self._request = None
        if not self.paused:
            self.state_changed.emit(State.WORKING)
            self.timer.start()
