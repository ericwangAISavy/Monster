"""Spawned input worker with its own Win32 message pump and lease watchdog.

The anonymous inherited duplex pipe carries bounded JSON. It has no network
listener or pickle deserialization. The main worker thread owns the emergency
hotkey; a single executor thread handles bounded commands.
"""

import ctypes
import json
import queue
import threading
from ctypes import wintypes

from monster.computer.contracts import Bounds, integer
from monster.computer.executor import InputExecutor
from monster.computer.windows import WindowsInput

MAX_MESSAGE = 32768


def send(connection, value):
    connection.send_bytes(json.dumps(value, ensure_ascii=True).encode("ascii"))


def receive(connection):
    result = json.loads(connection.recv_bytes(MAX_MESSAGE))
    if type(result) is not dict:
        raise ValueError("Protocol requires an object.")
    return result


def run_worker(connection):
    executor = None
    jobs = queue.Queue(maxsize=1)
    results = queue.Queue()
    shutting_down = threading.Event()
    hotkey = False
    api = None
    try:
        backend = WindowsInput()
        api = backend.api
        api.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        api.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        api.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        # Ctrl+Alt+X, MOD_NOREPEAT. A conflict is a hard arming failure.
        hotkey = bool(api.RegisterHotKey(None, 0x4D20, 0x4003, ord("X")))
        if not hotkey:
            send(
                connection,
                {
                    "event": "startup_error",
                    "error": "Stop shortcut Ctrl+Alt+X unavailable. "
                    "Quit the companion/other owner before starting real tools.",
                },
            )
            return
        executor = InputExecutor(backend)

        def execute_jobs():
            while not shutting_down.is_set():
                try:
                    request = jobs.get(timeout=0.05)
                except queue.Empty:
                    continue
                geometry = request.get("geometry")
                geometry = [Bounds(*b) for b in geometry] if geometry is not None else None
                receipt = executor.execute(
                    request["action"], request["generation"], request.get("focus"), geometry
                )
                results.put({"event": "receipt", "id": request["id"], **receipt})

        thread = threading.Thread(target=execute_jobs, name="bounded-input", daemon=True)
        thread.start()
        send(connection, {"event": "ready"})
        busy = False
        seen = set()
        previous_armed = False
        while not shutting_down.is_set():
            msg = wintypes.MSG()
            while api.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == 0x0312 and msg.wParam == 0x4D20:
                    executor.stop("emergency hotkey")
                    previous_armed = False
                    send(
                        connection,
                        {
                            "event": "stopped",
                            "reason": "emergency hotkey",
                            "cleanup_ok": executor.cleanup_ok,
                        },
                    )
            executor.watchdog()
            if previous_armed and not executor.armed:
                send(
                    connection,
                    {
                        "event": "disarmed",
                        "reason": executor.last_reason,
                        "cleanup_ok": executor.cleanup_ok,
                    },
                )
            previous_armed = executor.armed
            while not results.empty():
                send(connection, results.get_nowait())
                busy = False
            if connection.poll(0.01):
                request = receive(connection)
                op = request.get("op")
                if op == "heartbeat" and set(request) == {"op"}:
                    executor.heartbeat()
                elif op == "stop" and set(request) == {"op"}:
                    executor.stop("parent stop")
                    previous_armed = False
                    send(connection, {"event": "stopped", "cleanup_ok": executor.cleanup_ok})
                elif op == "shutdown" and set(request) == {"op"}:
                    break
                elif op == "arm" and set(request) == {"op"}:
                    if busy:
                        raise ValueError("Cannot arm while an action is in flight.")
                    try:
                        generation = executor.arm()
                    except RuntimeError as exc:
                        send(connection, {"event": "arm_error", "error": str(exc)})
                    else:
                        send(connection, {"event": "armed", "generation": generation})
                elif (
                    op == "execute"
                    and {"op", "id", "generation", "action"} <= set(request)
                    and not (
                        set(request) - {"op", "id", "generation", "action", "focus", "geometry"}
                    )
                ):
                    if "focus" in request:
                        integer(request["focus"], 1, 2**64 - 1)
                    if "geometry" in request:
                        geometry = request["geometry"]
                        if type(geometry) is not list or not 1 <= len(geometry) <= 32:
                            raise ValueError("Invalid geometry")
                        for bounds in geometry:
                            if type(bounds) is not list or len(bounds) != 4:
                                raise ValueError("Invalid bounds")
                            for value in bounds:
                                integer(value, -100000, 100000)
                            integer(bounds[2], 1, 100000)
                            integer(bounds[3], 1, 100000)
                    action_id = integer(request["id"], 1, 10000)
                    integer(request["generation"], 1, 100000)
                    if busy or action_id in seen or len(seen) >= 1000:
                        raise ValueError("Busy, duplicate action ID, or session budget exhausted.")
                    seen.add(action_id)
                    busy = True
                    jobs.put_nowait(request)
                else:
                    raise ValueError("Unknown message or fields.")
    except (EOFError, OSError, ValueError, TypeError, RuntimeError):
        # No raw action payload or arbitrary exception text is logged.
        if executor:
            executor.stop("protocol or connection failure")
    finally:
        shutting_down.set()
        cleanup_ok = True
        if executor:
            cleanup_ok = executor.stop("worker exit")
        if api and hotkey:
            api.UnregisterHotKey(None, 0x4D20)
        try:
            send(connection, {"event": "closed", "cleanup_ok": cleanup_ok})
        except (OSError, EOFError):
            pass
        connection.close()
