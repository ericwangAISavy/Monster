"""Serial allowlisted actions with expiring permission and owned-input cleanup."""

import threading
import time

from monster.computer.contracts import validate_action


class Disarmed(RuntimeError):
    pass


class InputExecutor:
    def __init__(self, backend, clock=time.monotonic):
        self.backend, self.clock = backend, clock
        self.lock = threading.RLock()
        self.generation = 0
        self.armed = False
        self.heartbeat_at = 0.0
        self.arm_until = 0.0
        self.geometry = []
        self.owned = {}  # (kind, value) -> monotonic down time
        self.cleanup_ok = True
        self.last_reason = "not armed"
        self.expected_focus = None

    def arm(self):
        with self.lock:
            if not self.cleanup_ok or self.owned:
                raise RuntimeError("Input cleanup is unconfirmed; restart and inspect held input.")
            if not self.backend.desktop_ready() or self.backend.physically_held():
                raise RuntimeError(
                    "Unlock the desktop and release all modifiers/buttons before arming."
                )
            self.generation += 1
            self.geometry = self.backend.displays()
            if not self.geometry:
                raise RuntimeError("No displays available.")
            self.heartbeat_at = self.clock()
            self.arm_until = self.clock() + 30
            self.expected_focus = None
            self.armed = True
            return self.generation

    def heartbeat(self):
        with self.lock:
            self.heartbeat_at = self.clock()

    def _check(self, generation):
        if (
            not self.armed
            or generation != self.generation
            or self.clock() - self.heartbeat_at > 1
            or self.clock() >= self.arm_until
        ):
            raise Disarmed("Input is disarmed or its lease expired.")
        if not self.backend.desktop_ready() or self.backend.displays() != self.geometry:
            raise Disarmed("Desktop or display geometry changed.")
        if self.expected_focus and self.backend.foreground() != self.expected_focus:
            raise Disarmed("Keyboard focus changed.")

    def _release(self):
        for kind, value in list(self.owned):
            try:
                getattr(self.backend, kind)(value, False)
            except Exception:  # noqa: BLE001 - Attempt every owned release even after one fails.
                self.cleanup_ok = False
            else:
                self.owned.pop((kind, value), None)

    def stop(self, reason="stopped"):
        with self.lock:
            self.armed = False
            self.generation += 1
            self.last_reason = reason
            self._release()
            return self.cleanup_ok and not self.owned

    def watchdog(self):
        with self.lock:
            expired_hold = any(self.clock() - started >= 2 for started in self.owned.values())
            try:
                if self.armed:
                    self._check(self.generation)
                if expired_hold:
                    raise Disarmed("Held-input limit reached.")
            except Disarmed as exc:
                self.stop(str(exc))

    def event(self, generation, kind, value, down=None):
        with self.lock:
            self._check(generation)
            if down is True:
                if (kind, value) in self.owned:
                    raise ValueError("Input is already held.")
                # Track before insertion: a partial/ambiguous failure still requires release.
                self.owned[(kind, value)] = self.clock()
            if down is False and (kind, value) not in self.owned:
                raise ValueError("Cannot release input not owned by this worker.")
            args = value if kind in ("move", "scroll") else (value, down)
            getattr(self.backend, kind)(*args)
            if down is False:
                self.owned.pop((kind, value), None)

    def wait(self, generation, seconds):
        end = self.clock() + seconds
        while self.clock() < end:
            with self.lock:
                self._check(generation)
            time.sleep(min(0.01, max(0, end - self.clock())))

    def execute(self, action, generation, expected_focus=None, expected_geometry=None):
        # Never include text payloads in receipts/errors.
        events = 0
        attempted = False
        try:
            with self.lock:
                self.expected_focus = expected_focus
                self._check(generation)
                if expected_geometry is not None and self.geometry != expected_geometry:
                    raise Disarmed("Observation geometry changed.")
                action = validate_action(action, self.geometry)
            op = action["op"]
            button = action.get("button", "left")

            def emit(kind, value, down=None):
                nonlocal events, attempted
                attempted = True
                self.event(generation, kind, value, down)
                events += 1

            if "x" in action:
                emit("move", (action["x"], action["y"]))
            if op == "click":
                for i in range(action.get("count", 1)):
                    emit("mouse", button, True)
                    emit("mouse", button, False)
                    if i == 0 and action.get("count") == 2:
                        self.wait(generation, 0.08)
            elif op in ("down", "up"):
                emit("mouse", button, op == "down")
            elif op == "scroll":
                emit("scroll", (action["delta"], action.get("horizontal", False)))
            elif op == "drag":
                emit("mouse", button, True)
                duration = action.get("duration", 0.4)
                steps = max(2, round(duration / 0.02))
                for index in range(1, steps + 1):
                    self.wait(generation, duration / steps)
                    x = round(action["x"] + (action["end_x"] - action["x"]) * index / steps)
                    y = round(action["y"] + (action["end_y"] - action["y"]) * index / steps)
                    # Reject paths crossing a physical gap between monitors.
                    if not any(b.contains(x, y) for b in self.geometry):
                        raise ValueError("Drag path crosses a monitor gap.")
                    emit("move", (x, y))
                emit("mouse", button, False)
            elif op == "text":
                for character in action["text"]:
                    data = character.encode("utf-16-le")
                    for index in range(0, len(data), 2):
                        unit = int.from_bytes(data[index : index + 2], "little")
                        emit("unicode", unit, True)
                        emit("unicode", unit, False)
                    self.wait(generation, 0.005)
            elif op == "press":
                with self.lock:
                    emit("key", action["key"], True)
                    # A deliberate key press can itself change foreground (e.g.
                    # Start/Enter). Release the owned key without treating that
                    # expected transition as an unrelated focus theft.
                    self.expected_focus = None
                emit("key", action["key"], False)
            elif op == "chord":
                for key in action["keys"][:-1]:
                    emit("key", key, True)
                with self.lock:
                    emit("key", action["keys"][-1], True)
                    # A completed chord may close/switch its target. Only owned
                    # releases remain; do not misreport that expected transition.
                    self.expected_focus = None
                for key in reversed(action["keys"]):
                    emit("key", key, False)
            elif op in ("key_down", "key_up"):
                emit("key", action["key"], op == "key_down")
            with self.lock:
                self.expected_focus = None
            return {"status": "inserted", "events": events}
        except Exception as exc:  # noqa: BLE001 - Input failures must always disarm and clean up.
            self.stop(type(exc).__name__)
            return {
                "status": "partial_or_unknown" if attempted else "rejected",
                "events": events,
                "error": type(exc).__name__,
                "cleanup_ok": self.cleanup_ok,
            }
