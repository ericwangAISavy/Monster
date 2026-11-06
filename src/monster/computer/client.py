"""Trusted parent client. No automated model loop is connected in this milestone."""

import multiprocessing
import queue
import threading
import time

from monster.computer.worker import receive, run_worker, send


class InputSession:
    def __init__(self, event_callback=None):
        self.event_callback = event_callback
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe(duplex=True)
        self.process = context.Process(target=run_worker, args=(child,), daemon=True)
        self.events = queue.Queue()
        self.commands = queue.Queue()
        self.closed = threading.Event()
        self.stop_requested = threading.Event()
        self.generation = None
        self.next_id = 0
        self.cleanup_confirmed = False
        self._fully_closed = False
        self.process.start()
        child.close()
        self.thread = threading.Thread(target=self._supervise, daemon=True)
        self.thread.start()
        try:
            ready = self.wait_for({"ready", "startup_error"}, 10)
        except (RuntimeError, TimeoutError):
            self.close()
            raise
        if ready["event"] != "ready":
            self.close()
            raise RuntimeError(ready.get("error", "Worker failed to start."))

    def _supervise(self):
        heartbeat = 0
        try:
            while not self.closed.is_set():
                if self.stop_requested.is_set():
                    # Discard unsent input before delivering stop.
                    while not self.commands.empty():
                        self.commands.get_nowait()
                    send(self.connection, {"op": "stop"})
                    self.stop_requested.clear()
                now = time.monotonic()
                if now - heartbeat >= 0.1:
                    send(self.connection, {"op": "heartbeat"})
                    heartbeat = now
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    command = None
                if command:
                    send(self.connection, command)
                if self.connection.poll(0.01):
                    event = receive(self.connection)
                    self.events.put(event)
                    if self.event_callback:
                        self.event_callback(event)
        except (OSError, EOFError, ValueError):
            event = {"event": "worker_lost"}
            self.events.put(event)
            if self.event_callback:
                self.event_callback(event)

    def wait_for(self, names, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            try:
                event = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                self.stop()
                raise TimeoutError(
                    "Worker response timed out; action outcome may be unknown."
                ) from None
            if (
                event["event"] in {"worker_lost", "startup_error", "closed"}
                and event["event"] not in names
            ):
                raise RuntimeError("Input worker unavailable; no automatic retry.")
            if event["event"] in names:
                return event
            if event["event"] in {"stopped", "disarmed"}:
                self.generation = None
                raise RuntimeError(
                    "Input was stopped or disarmed; inspect outcome before rearming."
                )

    def arm(self):
        self.commands.put({"op": "arm"})
        event = self.wait_for({"armed", "arm_error"})
        if event["event"] != "armed":
            raise RuntimeError(event["error"])
        self.generation = event["generation"]

    def execute(self, action, expected_focus=None, expected_geometry=None):
        if self.generation is None:
            raise RuntimeError("Explicitly arm before input.")
        self.next_id += 1
        request = {
            "op": "execute",
            "id": self.next_id,
            "generation": self.generation,
            "action": action,
        }
        if expected_focus is not None:
            request["focus"] = expected_focus
        if expected_geometry is not None:
            request["geometry"] = [[b.x, b.y, b.width, b.height] for b in expected_geometry]
        self.commands.put(request)
        event = self.wait_for({"receipt"}, 15)
        if event["id"] != self.next_id:
            self.stop()
            raise RuntimeError("Mismatched receipt; input stopped.")
        if event["status"] != "inserted":
            self.generation = None
        return event

    def disarm(self):
        self.generation = None
        self.commands.put({"op": "stop"})
        event = self.wait_for({"stopped"})
        if not event.get("cleanup_ok"):
            raise RuntimeError("Input cleanup not confirmed.")

    def stop(self):
        self.generation = None
        self.stop_requested.set()

    def close(self):
        if self._fully_closed:
            return self.cleanup_confirmed
        self.stop()
        self.closed.set()
        self.thread.join(timeout=1)
        try:
            send(self.connection, {"op": "shutdown"})
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if self.connection.poll(0.05):
                    event = receive(self.connection)
                    if event["event"] == "closed":
                        self.cleanup_confirmed = bool(event.get("cleanup_ok"))
                        break
        except (OSError, EOFError):
            pass
        # The supervisor may have received the final acknowledgment before it stopped.
        while not self.events.empty():
            event = self.events.get_nowait()
            if event["event"] == "closed":
                self.cleanup_confirmed = bool(event.get("cleanup_ok"))
        self.process.join(timeout=2)
        # Never terminate a healthy worker ahead of its cleanup. Broken workers
        # may need forced teardown; caller must inspect input state in that case.
        if self.process.is_alive():
            self.cleanup_confirmed = False
            self.process.terminate()
            self.process.join(timeout=1)
        self.connection.close()
        self._fully_closed = True
        return self.cleanup_confirmed

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
