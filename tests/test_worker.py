"""Exercise real JSON/pipe/worker scheduling with a fake Win32 backend."""

import multiprocessing
import threading
import time
from types import SimpleNamespace

from test_computer import FakeWindows

from monster.computer.worker import receive, run_worker, send


def wait_event(connection, names, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if connection.poll(0.02):
            value = receive(connection)
            if value["event"] in names:
                return value
    raise AssertionError(f"Missing worker event: {names}")


def worker_fixture(monkeypatch, register=True):
    import monster.computer.worker as module

    backend = FakeWindows()
    hotkey_pending = threading.Event()

    def peek(pointer, *_):
        if hotkey_pending.is_set():
            hotkey_pending.clear()
            pointer._obj.message = 0x0312
            pointer._obj.wParam = 0x4D20
            return 1
        return 0

    backend.api = SimpleNamespace(
        RegisterHotKey=lambda *_: register, UnregisterHotKey=lambda *_: True, PeekMessageW=peek
    )
    monkeypatch.setattr(module, "WindowsInput", lambda: backend)
    parent, child = multiprocessing.Pipe()
    thread = threading.Thread(target=run_worker, args=(child,), daemon=True)
    thread.start()
    return backend, hotkey_pending, parent, thread


def test_worker_hotkey_interrupts_execution_and_confirms_cleanup(monkeypatch):
    backend, hotkey, connection, thread = worker_fixture(monkeypatch)
    try:
        assert wait_event(connection, {"ready"})
        send(connection, {"op": "arm"})
        generation = wait_event(connection, {"armed"})["generation"]
        started = threading.Event()
        backend.on_event = started.set
        send(
            connection,
            {
                "op": "execute",
                "id": 1,
                "generation": generation,
                "action": {"op": "text", "text": "A" * 1000},
            },
        )
        assert started.wait(1)
        hotkey.set()
        event = wait_event(connection, {"stopped"})
        assert event["reason"] == "emergency hotkey" and event["cleanup_ok"]
        count = len(backend.events)
        receipt = wait_event(connection, {"receipt"})
        assert receipt["status"] == "partial_or_unknown"
        assert len(backend.events) == count
        send(connection, {"op": "shutdown"})
        assert wait_event(connection, {"closed"})["cleanup_ok"]
    finally:
        connection.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_worker_fails_startup_when_stop_hotkey_conflicts(monkeypatch):
    backend, _, connection, thread = worker_fixture(monkeypatch, register=False)
    try:
        assert "unavailable" in wait_event(connection, {"startup_error"})["error"]
    finally:
        connection.close()
        thread.join(timeout=2)
    assert not backend.events and not thread.is_alive()


def test_worker_heartbeat_loss_disarms_and_releases(monkeypatch):
    backend, _, connection, thread = worker_fixture(monkeypatch)
    try:
        wait_event(connection, {"ready"})
        send(connection, {"op": "arm"})
        generation = wait_event(connection, {"armed"})["generation"]
        send(
            connection,
            {
                "op": "execute",
                "id": 1,
                "generation": generation,
                "action": {"op": "key_down", "key": "CTRL"},
            },
        )
        assert wait_event(connection, {"receipt"})["status"] == "inserted"
        event = wait_event(connection, {"disarmed"})
        assert event["cleanup_ok"]
        assert backend.events[-1] == ("key", "CTRL", False)
        send(connection, {"op": "shutdown"})
        wait_event(connection, {"closed"})
    finally:
        connection.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_explicit_disarm_has_one_ack_and_allows_rearm(monkeypatch):
    _, _, connection, thread = worker_fixture(monkeypatch)
    try:
        wait_event(connection, {"ready"})
        send(connection, {"op": "arm"})
        first = wait_event(connection, {"armed"})["generation"]
        send(connection, {"op": "stop"})
        assert wait_event(connection, {"stopped"})["cleanup_ok"]
        send(connection, {"op": "arm"})
        # A stale duplicate 'disarmed' event must not break the next arm.
        event = wait_event(connection, {"armed", "disarmed"})
        assert event["event"] == "armed" and event["generation"] != first
        send(connection, {"op": "shutdown"})
        wait_event(connection, {"closed"})
    finally:
        connection.close()
        thread.join(timeout=2)
