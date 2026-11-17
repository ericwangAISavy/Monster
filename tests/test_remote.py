import asyncio
import base64
import hashlib
import socket
import time
from concurrent.futures import Future
from typing import ClassVar

import httpx
import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QTimer, Signal
from PySide6.QtGui import QColor, QImage

from monster.computer.contracts import Bounds
from monster.computer.screenshot import Observation
from monster.remote.bridge import RemoteError


def observation(width=3200, height=1800):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    png = bytes(data)
    bounds = Bounds(-width, 0, width, height)
    return Observation("frame", time.monotonic(), bounds, (bounds,), png,
                       hashlib.sha256(png).hexdigest(), 4242)


class FakeInput(QObject):
    completed = Signal(int, object, str)
    interrupted = Signal()
    diagnostic = Signal(object)
    instances: ClassVar[list] = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self.next_id = 0
        self.ops = []
        self.shut = False
        FakeInput.instances.append(self)

    def submit(self, operation, action=None, focus=None, geometry=None):
        self.next_id += 1
        job = self.next_id
        self.ops.append((operation, action, focus, geometry))
        value = {"status": "inserted", "events": 2} if operation == "execute" else True
        QTimer.singleShot(1, lambda: self.completed.emit(job, value, ""))
        return job

    def close(self):
        return self.submit("close")

    def stop(self):
        self.ops.append(("stop", None, None, None))

    def shutdown(self):
        self.shut = True


class FakeWindows:
    restored: ClassVar[list] = []

    def restore_foreground(self, handle):
        FakeWindows.restored.append(handle)
        return True


@pytest.fixture
def remote(qapp, tmp_path, monkeypatch):
    from monster import native
    from monster.app import CompanionApp
    from monster.lifecycle import MockTaskController
    from monster.remote.bridge import RemoteBridge

    monkeypatch.setattr(native, "foreground_window", lambda: 4242)
    FakeInput.instances.clear()
    FakeWindows.restored.clear()
    ui = CompanionApp(qapp, MockTaskController(), tmp_path / "companion.json", enable_tray=False)
    ui.register_hotkeys = list
    owners = {}  # hwnd -> pid; everything else belongs to another process.
    under = {}  # physical point -> hwnd
    bridge = RemoteBridge(ui, input_factory=FakeInput, windows_factory=FakeWindows,
                          auto_approve=False,  # Pin laptop approval; tests opt in below.
                          capture=lambda display: observation(),
                          window_pid=lambda hwnd: owners.get(hwnd, 1),
                          window_at=lambda x, y: under.get((x, y), 5555))
    bridge.test_owners, bridge.test_under = owners, under
    ui.remote_bridge = bridge
    yield ui, bridge
    ui.shutdown()


def call(qtbot, bridge, name, **kwargs):
    future = bridge.call(name, **kwargs)
    qtbot.waitUntil(future.done, timeout=3000)
    return future.result()


def test_run_task_requires_supervised_mode(qtbot, remote):
    _, bridge = remote
    with pytest.raises(RemoteError, match="supervised"):
        call(qtbot, bridge, "run_task", instruction="open notepad")
    status = call(qtbot, bridge, "status")
    assert status["mode"] == "demo" and not status["task_running"]


def test_screenshot_then_approved_click_maps_to_physical_pixels(qtbot, remote):
    ui, bridge = remote
    shot = call(qtbot, bridge, "screenshot")
    assert (shot["width"], shot["height"]) == (1600, 900)  # Downscaled from 3200x1800.
    future = bridge.call("act", kind="click", screenshot_id=shot["screenshot_id"], x=800, y=450,
                         button="left", double=False)
    qtbot.waitUntil(lambda: ui.pending_question is not None, timeout=3000)
    request_id, text, approval = ui.pending_question
    assert request_id.startswith("remote-") and approval and "screen (-1600, 900)" in text
    assert not future.done() and not FakeInput.instances  # Nothing runs before approval.
    ui.answer(request_id, "approve")
    qtbot.waitUntil(future.done, timeout=3000)
    assert future.result()["status"] == "executed"
    worker = FakeInput.instances[0]
    executed = [op for op in worker.ops if op[0] == "execute"]
    assert executed[0][1] == {"op": "click", "x": -1600, "y": 900, "button": "left"}
    assert executed[0][2] == 4242 and FakeWindows.restored == [4242]
    assert worker.shut and not ui.workspace_gate.busy and not ui.input_owns_stop


def test_declined_and_stale_actions_send_no_input(qtbot, remote):
    ui, bridge = remote
    with pytest.raises(RemoteError, match="screenshot"):
        call(qtbot, bridge, "act", kind="click", screenshot_id="nope", x=1, y=1)
    with pytest.raises(RemoteError, match="screenshot"):
        call(qtbot, bridge, "act", kind="type_text", text="hello")
    shot = call(qtbot, bridge, "screenshot")
    future = bridge.call("act", kind="press_keys", keys=["ctrl", "s"])
    qtbot.waitUntil(lambda: ui.pending_question is not None, timeout=3000)
    assert "CTRL+S" in ui.pending_question[1]
    ui.answer(ui.pending_question[0], "reject")
    qtbot.waitUntil(future.done, timeout=3000)
    assert future.result()["status"] == "declined" and not FakeInput.instances
    future = bridge.call("act", kind="scroll", screenshot_id=shot["screenshot_id"], x=5, y=5,
                         delta=-3)
    qtbot.waitUntil(lambda: ui.pending_question is not None, timeout=3000)
    ui.stop()  # Laptop emergency stop cancels the pending remote review.
    qtbot.waitUntil(future.done, timeout=3000)
    assert future.result()["status"] == "declined" and ui.pending_question is None


def test_invalid_keys_are_refused(qtbot, remote):
    _, bridge = remote
    call(qtbot, bridge, "screenshot")
    with pytest.raises(RemoteError, match="keys"):
        call(qtbot, bridge, "act", kind="press_keys", keys=["CTRL", "NOTAKEY"])


class InstantBridge:
    def __init__(self):
        self.calls = []

    def call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        future = Future()
        if name == "screenshot":
            future.set_result({"screenshot_id": "abc", "width": 2, "height": 1,
                               "jpeg_base64": base64.b64encode(b"jpegbytes").decode()})
        elif name == "act":
            future.set_exception(RemoteError("Take a screenshot first."))
        else:
            future.set_result({"mode": "demo", "ok": True})
        return future


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_mcp_server_exposes_annotated_tools_behind_secret_path():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from monster.remote.server import RemoteServer

    bridge, port = InstantBridge(), free_port()
    server = RemoteServer(bridge, "t" * 40, port)
    server.start()
    try:
        assert httpx.post(f"http://127.0.0.1:{port}/mcp/wrong", json={}).status_code == 404

        async def session():
            async with (
                streamable_http_client(server.url) as (read, write, _),
                ClientSession(read, write) as client,
            ):
                await client.initialize()
                tools = {t.name: t for t in (await client.list_tools()).tools}
                result = await client.call_tool("monster_status", {})
                shot = await client.call_tool("screenshot", {})
                refused = await client.call_tool("type_text", {"text": "hi"})
                return tools, result, shot, refused

        tools, result, shot, refused = asyncio.run(session())
        assert shot.content[0].type == "image" and shot.content[0].mimeType == "image/jpeg"
        assert base64.b64decode(shot.content[0].data) == b"jpegbytes"
        assert '"screenshot_id": "abc"' in shot.content[1].text
        assert '"status": "refused"' in refused.content[0].text
        assert set(tools) == {"monster_status", "run_task", "task_status", "task_diagnostics",
                              "stop", "screenshot",
                              "click", "scroll", "drag", "type_text", "press_keys"}
        assert tools["screenshot"].annotations.readOnlyHint is True
        assert tools["click"].annotations.readOnlyHint is False
        assert tools["run_task"].annotations.readOnlyHint is False
        assert not result.isError and bridge.calls[0] == ("status", {})
    finally:
        server.close()


def test_run_task_starts_supervised_controller_and_tracks_outcome(qtbot, remote, monkeypatch):
    from test_supervised import controller

    from monster.computer import windows as windows_module

    ui, bridge = remote
    from monster.computer import screenshot as screenshot_module

    monkeypatch.setattr(windows_module, "WindowsInput", FakeWindows)
    # The controller's first step requests a capture; never grab the real desktop in tests.
    monkeypatch.setattr(screenshot_module, "capture_display", lambda display=None: observation())
    engine, _, _ = controller([])
    ui.switch_controller(engine)
    started = call(qtbot, bridge, "run_task", instruction="Open Notepad")
    assert started["state"] == "running" and engine.active
    assert engine.instruction == "Open Notepad" and FakeWindows.restored == [4242]
    with pytest.raises(RemoteError, match="already running"):
        call(qtbot, bridge, "run_task", instruction="again")
    assert call(qtbot, bridge, "stop")["stopped"] == ["task"]
    qtbot.waitUntil(lambda: call(qtbot, bridge, "task_status")["state"] != "running",
                    timeout=3000)
    assert call(qtbot, bridge, "task_status", task_id=started["task_id"])["state"] ==         "stopped_incomplete"


class StuckInput(FakeInput):
    """Never completes execute, like InputBridge after stop() discards queued work."""

    def submit(self, operation, action=None, focus=None, geometry=None):
        if operation != "execute":
            return super().submit(operation, action, focus, geometry)
        self.next_id += 1
        self.ops.append((operation, action, focus, geometry))
        return self.next_id


def test_stop_during_running_raw_action_cleans_up_and_releases_desktop(qtbot, remote):
    ui, bridge = remote
    bridge.input_factory = StuckInput
    call(qtbot, bridge, "screenshot")
    future = bridge.call("act", kind="press_keys", keys=["ENTER"])
    qtbot.waitUntil(lambda: ui.pending_question is not None, timeout=3000)
    ui.answer(ui.pending_question[0], "approve")
    qtbot.waitUntil(lambda: any(op[0] == "execute" for op in FakeInput.instances[0].ops),
                    timeout=3000)
    assert call(qtbot, bridge, "stop")["stopped"] == ["raw_action"]
    qtbot.waitUntil(future.done, timeout=3000)
    result = future.result()
    assert result["status"] == "failed" and result["cleanup_confirmed"]
    assert bridge.action is None and not ui.workspace_gate.busy and not ui.input_owns_stop
    assert FakeInput.instances[0].shut


def test_raw_input_may_not_operate_monster_windows(qtbot, remote, monkeypatch):
    import os

    from monster import native

    ui, bridge = remote
    shot = call(qtbot, bridge, "screenshot")
    bridge.test_under[(-1600, 900)] = 777
    bridge.test_owners[777] = os.getpid()  # e.g. Monster's settings dialog
    with pytest.raises(RemoteError, match="Monster window"):
        call(qtbot, bridge, "act", kind="click", screenshot_id=shot["screenshot_id"],
             x=800, y=450)
    monkeypatch.setattr(native, "foreground_window", lambda: int(ui.pill.winId()))
    with pytest.raises(RemoteError, match="belongs to Monster"):
        call(qtbot, bridge, "act", kind="type_text", text="disable approvals")
    assert ui.pending_question is None and not FakeInput.instances


def test_abandoned_request_is_not_executed(qtbot, remote):
    ui, bridge = remote
    future = bridge.call("screenshot")
    future.cancel()  # What asyncio.wait_for does when the MCP call times out.
    qtbot.wait(50)
    assert bridge.frame is None and not ui.isolation.busy


def test_late_signal_from_previous_worker_is_ignored(qtbot, remote):
    ui, bridge = remote
    call(qtbot, bridge, "screenshot")
    future = bridge.call("act", kind="press_keys", keys=["ENTER"])
    qtbot.waitUntil(lambda: ui.pending_question is not None, timeout=3000)
    ui.answer(ui.pending_question[0], "approve")
    qtbot.waitUntil(future.done, timeout=3000)
    old = FakeInput.instances[0]
    bridge.input_factory = StuckInput
    second = bridge.call("act", kind="press_keys", keys=["TAB"])
    qtbot.waitUntil(lambda: ui.pending_question is not None, timeout=3000)
    ui.answer(ui.pending_question[0], "approve")
    qtbot.waitUntil(lambda: len(FakeInput.instances) == 2 and any(
        op[0] == "execute" for op in FakeInput.instances[1].ops), timeout=3000)
    new_close = FakeInput.instances[1].next_id + 1
    old.completed.emit(new_close, True, "")  # Same job number, wrong worker.
    old.completed.emit(2, {"status": "inserted"}, "")
    qtbot.wait(20)
    assert not second.done() and bridge.action is not None
    bridge.cancel_action("test cleanup")
    qtbot.waitUntil(second.done, timeout=3000)


def test_completed_task_status_includes_agent_result(qtbot, remote, monkeypatch):
    from test_supervised import controller

    from monster.computer import screenshot as screenshot_module
    from monster.computer import windows as windows_module
    from monster.providers.base import Decision

    ui, bridge = remote
    monkeypatch.setattr(windows_module, "WindowsInput", FakeWindows)
    monkeypatch.setattr(screenshot_module, "capture_display", lambda display=None: observation())
    engine, _, _ = controller([Decision("complete", "Yiwu: 61F, cloudy")])
    ui.switch_controller(engine)
    started = call(qtbot, bridge, "run_task", instruction="read today's forecast")
    qtbot.waitUntil(lambda: engine.phase == "confirming", timeout=5000)
    engine.respond(engine.question_id, "approve")
    qtbot.waitUntil(lambda: call(qtbot, bridge, "task_status")["state"] != "running",
                    timeout=3000)
    status = call(qtbot, bridge, "task_status", task_id=started["task_id"])
    assert status["state"] == "completed" and status["result"] == "Yiwu: 61F, cloudy"


def test_approved_drag_maps_every_point_to_physical_pixels(qtbot, remote):
    ui, bridge = remote
    shot = call(qtbot, bridge, "screenshot")
    future = bridge.call("act", kind="drag", screenshot_id=shot["screenshot_id"],
                         points=[[800, 450], [900, 450], [900, 550]], duration=None)
    qtbot.waitUntil(lambda: ui.pending_question is not None, timeout=3000)
    request_id, text, _ = ui.pending_question
    assert "Left-drag through 3 points" in text and not FakeInput.instances
    ui.answer(request_id, "approve")
    qtbot.waitUntil(future.done, timeout=3000)
    assert future.result()["status"] == "executed"
    executed = [op for op in FakeInput.instances[0].ops if op[0] == "execute"]
    assert executed[0][1] == {"op": "drag", "x": -1600, "y": 900, "end_x": -1400, "end_y": 1100,
                              "button": "left", "duration": 1.0, "path": [[-1400, 900]]}


@pytest.mark.parametrize("points,duration", [
    ([[1, 1]], None), ([[1, 1]] * 27, None), ([[1, 1], [2]], None), ([[1, 1], [2, 2]], 3.0),
])
def test_invalid_drag_requests_are_refused(qtbot, remote, points, duration):
    _, bridge = remote
    shot = call(qtbot, bridge, "screenshot")
    with pytest.raises(RemoteError):
        call(qtbot, bridge, "act", kind="drag", screenshot_id=shot["screenshot_id"],
             points=points, duration=duration)


def test_auto_approve_runs_raw_action_without_laptop_review(qtbot, remote):
    ui, bridge = remote
    bridge.auto_approve = True
    shot = call(qtbot, bridge, "screenshot")
    result = call(qtbot, bridge, "act", kind="click", screenshot_id=shot["screenshot_id"],
                  x=800, y=450, button="left", double=False)
    assert result["status"] == "executed" and ui.pending_question is None
    executed = [op for op in FakeInput.instances[0].ops if op[0] == "execute"]
    assert executed[0][1] == {"op": "click", "x": -1600, "y": 900, "button": "left"}
    assert not ui.workspace_gate.busy


def test_approval_follows_live_setting_by_default(qtbot, remote):
    from dataclasses import replace

    from monster.remote.bridge import RemoteBridge

    ui, _ = remote
    live = RemoteBridge(ui, input_factory=FakeInput, windows_factory=FakeWindows,
                        capture=lambda display: observation(),
                        window_pid=lambda hwnd: 1, window_at=lambda x, y: 5555)
    ui.model_config = replace(ui.model_config, hands_free=True)
    assert live.auto_approve is True
    assert call(qtbot, live, "status")["approval"] == "none"
    ui.model_config = replace(ui.model_config, hands_free=False)
    assert live.auto_approve is False
    assert call(qtbot, live, "status")["approval"] == "laptop"


def test_saved_settings_default_to_no_approval(tmp_path):
    import json

    from monster.providers.config import load

    assert load(tmp_path / "missing.json").hands_free is True
    path = tmp_path / "ollama.json"
    path.write_text(json.dumps({"mode": "demo"}), encoding="utf-8")
    assert load(path).hands_free is True
    path.write_text(json.dumps({"mode": "demo", "hands_free": False}), encoding="utf-8")
    assert load(path).hands_free is False


def test_task_diagnostics_explain_a_stopped_task(qtbot, remote, monkeypatch):
    from test_supervised import controller

    from monster.computer import screenshot as screenshot_module
    from monster.computer import windows as windows_module
    from monster.providers.base import Decision

    ui, bridge = remote
    monkeypatch.setattr(windows_module, "WindowsInput", FakeWindows)
    monkeypatch.setattr(screenshot_module, "capture_display", lambda display=None: observation())
    engine, _, _ = controller([Decision("unable", "Cannot find the Record button")])
    ui.switch_controller(engine)
    started = call(qtbot, bridge, "run_task", instruction="click Record")
    qtbot.waitUntil(lambda: call(qtbot, bridge, "task_status")["state"] != "running",
                    timeout=5000)
    key = call(qtbot, bridge, "task_diagnostics", task_id=started["task_id"])
    names = [event["event"] for event in key["events"]]
    assert key["view"] == "key" and "proposal" in names and "task_terminated" in names
    assert "provider_http_result" not in names and key["outcome"]["status"] == "error"
    proposal = next(e for e in key["events"] if e["event"] == "proposal")
    assert proposal["content"]["proposal"]["summary"] == "Cannot find the Record button"
    full = call(qtbot, bridge, "task_diagnostics", task_id=started["task_id"], full=True)
    assert len(full["events"]) >= len(key["events"]) and full["view"] == "full"
    with pytest.raises(RemoteError, match="Unknown task_id"):
        call(qtbot, bridge, "task_diagnostics", task_id="nope")


def _start_with_context(qtbot, remote, monkeypatch, decisions, **context):
    from test_supervised import controller

    from monster.computer import screenshot as screenshot_module
    from monster.computer import windows as windows_module

    ui, bridge = remote
    monkeypatch.setattr(windows_module, "WindowsInput", FakeWindows)
    monkeypatch.setattr(screenshot_module, "capture_display", lambda display=None: observation())
    engine, _, _ = controller(decisions)
    ui.switch_controller(engine)
    started = call(qtbot, bridge, "run_task", instruction="draw the design", **context)
    return ui, bridge, engine, started


def test_temporary_context_is_stored_and_deleted_when_the_task_succeeds(qtbot, remote, monkeypatch):
    from monster.providers.base import Decision

    ui, bridge, engine, started = _start_with_context(
        qtbot, remote, monkeypatch, [Decision("complete", "Drawn")],
        context_text="Requirements: payments service",
        context_files=[{"name": "notes.md", "data": base64.b64encode(b"# Notes").decode()}],
        context_images=[{"name": "sketch.png", "data": base64.b64encode(b"PNG").decode()}])
    store = ui.context_store
    bundle = engine.context_bundle_id
    assert started["temporary_context"]["items"] == 3 and len(store.documents(bundle_id=bundle)) == 3
    qtbot.waitUntil(lambda: engine.phase == "confirming", timeout=5000)
    engine.respond(engine.question_id, "approve")
    qtbot.waitUntil(lambda: call(qtbot, bridge, "task_status")["state"] == "completed", timeout=3000)
    assert store.bundle(bundle) is None and store.documents(bundle_id=bundle) == []


def test_temporary_context_is_kept_for_a_retry_when_the_task_fails(qtbot, remote, monkeypatch):
    from monster.providers.base import Decision

    ui, bridge, engine, started = _start_with_context(
        qtbot, remote, monkeypatch, [Decision("unable", "Cannot find the app")],
        context_text="Requirements: payments service")
    bundle = engine.context_bundle_id
    qtbot.waitUntil(lambda: call(qtbot, bridge, "task_status")["state"] != "running", timeout=5000)
    assert ui.context_store.bundle(bundle)["status"] == "kept"
    assert call(qtbot, bridge, "task_status")["temporary_context"]["kept_for_retry"] is True


def test_bad_temporary_context_is_refused_before_the_task_starts(qtbot, remote, monkeypatch):
    from test_supervised import controller

    ui, bridge = remote
    engine, _, _ = controller([])
    ui.switch_controller(engine)
    with pytest.raises(RemoteError, match="base64"):
        call(qtbot, bridge, "run_task", instruction="x",
             context_files=[{"name": "a.pdf", "data": "not base64!!"}])
    with pytest.raises(RemoteError, match="readable file"):
        call(qtbot, bridge, "run_task", instruction="x", context_paths=["C:/no/such/file.pdf"])
    assert not engine.active
