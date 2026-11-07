"""Synthetic/offline adapter qualification; never real desktop input."""

import hashlib
import time

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage

from monster.computer.contracts import Bounds
from monster.computer.screenshot import Observation


def observation(width=1920, height=1080, origin=(-1920, 0), frame_id="frame-1"):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#eef2f9"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    png = bytes(data)
    bounds = Bounds(*origin, width, height)
    return Observation(frame_id, time.monotonic(), bounds, (bounds,), png,
                       hashlib.sha256(png).hexdigest(), 77)


def preview(response, obs=None):
    from monster.providers.venus_actions import FrameBinding, parse_preview
    return parse_preview(response, FrameBinding.from_observation(obs or observation()), "task-1", 1)


@pytest.mark.parametrize("point, size, expected", [
    ((0, 0), (1920, 1080), (0, 0)),
    ((999, 999), (1920, 1080), (1919, 1079)),
    ((500, 500), (1920, 1080), (960, 540)),
    ((500, 500), (2560, 1440), (1281, 720)),
    ((999, 999), (1, 1), (0, 0)),
])
def test_venus_999_coordinates_follow_pinned_upstream_formula(point, size, expected):
    from monster.providers.venus_actions import normalized_point
    assert normalized_point(point, *size) == expected


def test_venus_click_preview_binds_frame_and_negative_origin():
    result = preview("<think>PRIVATE_REASONING</think><action>Click(box=(999, 999))</action>")
    assert dict(result.image_action) == {"op": "click", "x": 1919, "y": 1079, "button": "left", "count": 1}
    assert result.desktop_action["x"] == -1 and result.desktop_action["y"] == 1079
    assert result.frame.observation_id == "frame-1"
    assert result.source["coordinate_space"] == "ui_venus_normalized_999"
    assert result.source["normalized_points"] == (("box", (999, 999)),)
    assert result.execution_allowed is False and result.verification == "unverified"
    assert "PRIVATE_REASONING" not in repr(result)
    with pytest.raises(TypeError):
        result.desktop_action["x"] = 0


@pytest.mark.parametrize("native, op", [
    ("DoubleClick(box=(100, 200))", "click"),
    ("RightClick(box=(100, 200))", "click"),
    ("MiddleClick(box=(100, 200))", "click"),
    ("Hover(box=(100, 200))", "move"),
    ("Drag(start=(0, 0), end=(999, 999))", "drag"),
    ("Type(content='Hello 世界')", "text"),
    ("Hotkey(keys=['ctrl', 'c'])", "chord"),
    ("Hotkey(keys=['enter'])", "press"),
])
def test_venus_supported_atomic_preview_actions(native, op):
    assert preview(native).image_action["op"] == op


@pytest.mark.parametrize("native, kind", [
    ("Wait()", "observe"), ("CallUser(content='Need help')", "needs_user"),
    ("Finished(content='Done')", "completion_claim"),
])
def test_venus_terminal_signals_never_qualify_success(native, kind):
    result = preview(native)
    assert result.kind == kind and result.verification == "unverified"
    assert not result.execution_allowed and result.image_action is None


@pytest.mark.parametrize("native", [
    "Click()", "Drag(end=(10, 10))", "TripleClick(box=(1, 1))",
    "Swipe(amount=-5, axis='vertical')", "KeyDown(keys=['shift'])",
    "KeyUp(keys=['shift'])", "MouseDown()", "MouseUp()",
    "Sequence(actions=[Click(box=(1, 1)), Finished()])",
    "Type(content='hello\\n')", "Type()", "Hotkey(keys=['down'], repeat=2)",
    "Hotkey(keys=['ctrl+c'])",
])
def test_venus_unsupported_semantics_are_not_guessed_or_executed(native):
    from monster.providers.venus_actions import UIVenusActionError
    with pytest.raises(UIVenusActionError):
        preview(native)


@pytest.mark.parametrize("native", [
    "__import__('os').system('PRIVATE_COMMAND')", "Click(box=print('PRIVATE_COMMAND'))",
    "Click((1, 2))", "Click(**{'box': (1, 2)})", "Click(box=(1, 2), box=(3, 4))",
    "Click(box=[1,2])", "Click(box=(True, 0))", "Click(box=(1000, 0))",
    "Click(box=(-1, 0))", "Click(box=(1e309, 0))", "Click(box=(1+2, 3))",
    "Click(box=(1, 2)); Finished()", "[Click(box=(1,2)) for x in range(2)]",
    "<action>Click(box=(1,2))", "<action>Wait()</action><action>Finished()</action>",
    "<think>incomplete<action>Finished()</action>", "<action>Finished()</action>untrusted tail",
])
def test_venus_rejects_hostile_malformed_and_ambiguous_output(native, tmp_path):
    from monster.providers.venus_actions import UIVenusActionError
    with pytest.raises(UIVenusActionError) as error:
        preview(native)
    assert "PRIVATE_COMMAND" not in str(error.value)
    assert not list(tmp_path.iterdir())


def test_venus_default_preview_redacts_payload_and_discards_thought():
    result = preview("<think>PRIVATE_REASONING</think><action>Type(content='PRIVATE_DRAFT')</action>")
    assert "PRIVATE" not in str(result.to_dict())
    assert result.to_dict(include_content=True)["image_action"]["text"] == "PRIVATE_DRAFT"
    assert "PRIVATE_REASONING" not in str(result.to_dict(include_content=True))


def test_venus_frame_rejects_forged_hash_and_dimensions():
    from dataclasses import replace

    from monster.providers.venus_actions import FrameBinding, UIVenusActionError
    obs = observation()
    for bad in (replace(obs, sha256="0" * 64), replace(obs, bounds=Bounds(0, 0, 10, 10))):
        with pytest.raises(UIVenusActionError):
            FrameBinding.from_observation(bad)


def test_venus_hostile_resource_limits_and_large_numbers_fail_safely():
    from monster.providers.venus_actions import UIVenusActionError
    for raw in ("X" * 65537, "Click(box=(" + "9" * 400 + ",0))",
                "Type(content=" + "[" * 20 + "'x'" + "]" * 20 + ")"):
        with pytest.raises(UIVenusActionError):
            preview(raw)


def config():
    from monster.providers.ui_venus import UIVenusConfig
    return UIVenusConfig(url="http://127.0.0.1:8000", model="UI-Venus-2-9B",
                         local_server_confirmed=True)


def request(obs=None, task_id="task-1", generation=1):
    from monster.providers.ui_venus import DecisionRequest
    return DecisionRequest.from_observation(task_id, generation, "Click the visible test button", obs or observation())


def response(content="<action>Click(box=(500, 500))</action>", finish="stop"):
    import httpx
    return httpx.Response(200, json={"model": "UI-Venus-2-9B", "choices": [{
        "finish_reason": finish, "message": {"role": "assistant", "content": content,
        "reasoning_content": "PRIVATE_REASONING"}}]})


def test_venus_transport_uses_native_format_and_never_forces_generic_json():
    import asyncio
    import base64
    import json

    import httpx

    from monster.providers.ui_venus import UIVenusComputerAdapter
    requests = []

    def handle(req):
        requests.append(req)
        return response()

    adapter = UIVenusComputerAdapter(config(), transport=httpx.MockTransport(handle))
    req = request()
    result = asyncio.run(adapter.next_decision(req))
    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/chat/completions"
    assert body["model"] == "UI-Venus-2-9B" and body["stream"] is False
    assert "format" not in body and "response_format" not in body
    assert "0–999" in body["messages"][0]["content"]
    assert "sudo_password" not in body["messages"][0]["content"]
    uri = body["messages"][-1]["content"][-1]["image_url"]["url"]
    assert base64.b64decode(uri.split(",", 1)[1]) == req.image_png
    assert result.execution_allowed is False
    assert "PRIVATE_REASONING" not in repr(adapter.history_metadata())


@pytest.mark.parametrize("finish", [None, "length", "tool_calls", "error"])
def test_venus_incomplete_results_never_enter_accepted_history(finish):
    import asyncio

    import httpx

    from monster.providers.base import ProviderError
    from monster.providers.ui_venus import UIVenusComputerAdapter
    calls = []

    def handle(req):
        calls.append(req)
        return response("<action>Finished()</action>", finish)

    adapter = UIVenusComputerAdapter(config(), transport=httpx.MockTransport(handle))
    with pytest.raises(ProviderError):
        asyncio.run(adapter.next_decision(request()))
    assert len(calls) == 1 and adapter.history_metadata() == ()


@pytest.mark.parametrize("change", ["invalidate", "frame", "task", "generation", "foreground", "model"])
def test_venus_stale_responses_are_discarded(change):
    import asyncio
    from dataclasses import replace

    import httpx

    from monster.providers.ui_venus import StalePreviewError, UIVenusComputerAdapter

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def handle(req):
            started.set()
            await release.wait()
            return response()

        adapter = UIVenusComputerAdapter(config(), transport=httpx.MockTransport(handle))
        original = request()
        pending = asyncio.create_task(adapter.next_decision(original))
        await started.wait()
        if change == "invalidate":
            adapter.invalidate()
        elif change == "model":
            adapter.configure(replace(config(), model="different-explicit-model"))
        elif change == "task":
            adapter.set_current_request(request(task_id="task-2"))
        elif change == "generation":
            adapter.set_current_request(request(generation=2))
        elif change == "foreground":
            adapter.set_current_request(replace(original, frame=replace(original.frame, foreground_guard=88)))
        else:
            adapter.set_current_request(request(observation(frame_id="frame-2")))
        release.set()
        with pytest.raises(StalePreviewError):
            await pending
        assert adapter.history_metadata() == ()

    asyncio.run(scenario())


def test_venus_concurrent_request_is_rejected_without_second_transfer():
    import asyncio

    import httpx

    from monster.providers.base import ProviderError
    from monster.providers.ui_venus import UIVenusComputerAdapter

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handle(req):
            calls.append(req)
            started.set()
            await release.wait()
            return response()

        adapter = UIVenusComputerAdapter(config(), transport=httpx.MockTransport(handle))
        pending = asyncio.create_task(adapter.next_decision(request()))
        await started.wait()
        with pytest.raises(ProviderError):
            await adapter.next_decision(request())
        release.set()
        await pending
        assert len(calls) == 1

    asyncio.run(scenario())


def test_venus_frame_binding_includes_foreground_guard():
    from dataclasses import replace

    from monster.providers.venus_actions import FrameBinding
    obs = observation()
    assert FrameBinding.from_observation(obs) != FrameBinding.from_observation(replace(obs, foreground=88))


def test_venus_crop_origin_and_display_gaps_are_not_guessed():
    from dataclasses import replace

    from monster.providers.venus_actions import UIVenusActionError
    crop = observation(100, 60, (-1880, 30))
    crop = replace(crop, displays=(Bounds(-1920, 0, 1920, 1080),))
    result = preview("Click(box=(999,999))", crop)
    assert (result.desktop_action["x"], result.desktop_action["y"]) == (-1781, 89)
    gap = replace(observation(300, 100, (0, 0)),
                  displays=(Bounds(0, 0, 100, 100), Bounds(200, 0, 100, 100)))
    with pytest.raises(UIVenusActionError):
        preview("Click(box=(500,500))", gap)


def test_venus_history_is_bounded_and_contains_only_accepted_action_context():
    import asyncio
    import json
    from dataclasses import replace

    import httpx

    from monster.providers.ui_venus import UIVenusComputerAdapter
    messages = []

    def handle(req):
        messages.append(json.loads(req.content)["messages"])
        return response("<think>SECRET_REASONING</think><action>Type(content='sample')</action>")

    adapter = UIVenusComputerAdapter(replace(config(), history_turns=2, history_frames=1),
                                     transport=httpx.MockTransport(handle))
    for i in range(3):
        asyncio.run(adapter.next_decision(request(observation(frame_id=f"frame-{i}"))))
    assert len(adapter.history_metadata()) == 2
    assert sum(turn["image_retained"] for turn in adapter.history_metadata()) == 1
    assert "SECRET_REASONING" not in json.dumps(messages)
    assert "NOT executed" in json.dumps(messages)
    assert [turn["observation_id"] for turn in adapter.history_metadata()] == ["frame-1", "frame-2"]


def test_venus_changed_objective_discards_old_candidate_history():
    import asyncio
    from dataclasses import replace

    import httpx

    from monster.providers.ui_venus import UIVenusComputerAdapter
    adapter = UIVenusComputerAdapter(config(), transport=httpx.MockTransport(lambda req: response()))
    original = request()
    asyncio.run(adapter.next_decision(original))
    assert adapter.history_metadata()
    adapter.set_current_request(replace(original, instruction="Different objective"))
    assert adapter.history_metadata() == ()


@pytest.mark.parametrize("url, allow_http", [
    ("https://8.8.8.8", False), ("https://public.example", False),
    ("http://192.168.2.2:8000", False), ("http://user:secret@localhost:8000", False),
    ("http://localhost:8000/v1", False),
])
def test_venus_initial_transport_rejects_unsafe_or_unsupported_endpoint(url, allow_http):
    from dataclasses import replace

    from monster.providers.ui_venus import UIVenusComputerAdapter
    with pytest.raises(ValueError):
        UIVenusComputerAdapter(replace(config(), url=url, allow_lan_http=allow_http))


def test_venus_unconfirmed_server_never_receives_image():
    import asyncio
    from dataclasses import replace

    import httpx

    from monster.providers.base import ProviderError
    from monster.providers.ui_venus import UIVenusComputerAdapter
    calls = []
    adapter = UIVenusComputerAdapter(replace(config(), local_server_confirmed=False),
                                     transport=httpx.MockTransport(lambda req: calls.append(req)))
    with pytest.raises(ProviderError):
        asyncio.run(adapter.next_decision(request()))
    assert not calls


def test_venus_offline_fixture_cli_never_qualifies_or_executes():
    import json
    import subprocess
    import sys
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures/gui_synthetic/venus_cases.json"
    run = subprocess.run([sys.executable, "-m", "monster.providers.ui_venus_preview", "--fixture", str(fixture)],
                         capture_output=True, text=True, check=False, timeout=15)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["mode"] == "offline_fixture" and report["real_input"] is False
    assert report["live_model_tested"] is False
    assert len(report["cases"]) == 7 and all(case["matched"] for case in report["cases"])
    assert "Synthetic outcome" not in run.stdout
    assert "Need target clarification" not in run.stdout


def test_venus_offline_response_cli_redacts_text_and_requires_explicit_origin(tmp_path):
    import json
    import subprocess
    import sys

    image = tmp_path / "synthetic.png"
    image.write_bytes(observation(100, 100, (0, 0)).png)
    text = tmp_path / "response.txt"
    text.write_text("<think>PRIVATE_REASONING</think><action>Type(content='PRIVATE_DRAFT')</action>")
    cmd = [sys.executable, "-m", "monster.providers.ui_venus_preview", "--response", str(text),
           "--screenshot", str(image)]
    missing = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=10)
    assert missing.returncode != 0
    run = subprocess.run(cmd + ["--origin-x", "0", "--origin-y", "0"],
                         capture_output=True, text=True, check=False, timeout=10)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["preview"]["image_action"]["text_characters"] == len("PRIVATE_DRAFT")
    assert "PRIVATE" not in run.stdout
    assert sorted(p.name for p in tmp_path.iterdir()) == ["response.txt", "synthetic.png"]


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_venus_http_failures_do_not_retry_follow_redirects_or_leak_bodies(status):
    import asyncio

    import httpx

    from monster.providers.base import ProviderError
    from monster.providers.ui_venus import UIVenusComputerAdapter
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(status, text="PRIVATE_SERVER_BODY", headers={"location": "https://public.example"})

    adapter = UIVenusComputerAdapter(config(), transport=httpx.MockTransport(handle))
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.next_decision(request()))
    assert "PRIVATE_SERVER_BODY" not in str(error.value)
    assert len(calls) == 1 and adapter.history_metadata() == ()


@pytest.mark.parametrize("body", [
    {"model": "different-model", "choices": []},
    {"model": "UI-Venus-2-9B", "choices": []},
    {"model": "UI-Venus-2-9B", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Finished()", "tool_calls": [{"name": "PRIVATE_TOOL"}]}}]},
    {"model": "UI-Venus-2-9B", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Click(box=(1,2))", "refusal": "PRIVATE_REFUSAL"}}]},
])
def test_venus_mismatched_ambiguous_or_tool_messages_are_not_candidates(body):
    import asyncio

    import httpx

    from monster.providers.base import ProviderError
    from monster.providers.ui_venus import UIVenusComputerAdapter
    adapter = UIVenusComputerAdapter(config(), transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.next_decision(request()))
    assert "PRIVATE" not in str(error.value)
    assert adapter.history_metadata() == ()


def test_venus_cancellation_does_not_accept_history_and_releases_singleflight():
    import asyncio

    import httpx

    from monster.providers.ui_venus import UIVenusComputerAdapter

    async def scenario():
        started = asyncio.Event()
        calls = []

        async def handle(req):
            calls.append(req)
            if len(calls) == 1:
                started.set()
                await asyncio.sleep(60)
            return response()

        adapter = UIVenusComputerAdapter(config(), transport=httpx.MockTransport(handle))
        pending = asyncio.create_task(adapter.next_decision(request()))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert adapter.history_metadata() == ()
        result = await adapter.next_decision(request(observation(frame_id="frame-2")))
        assert result.kind == "action" and len(calls) == 2

    asyncio.run(scenario())


def test_venus_preview_cannot_capture_or_dispatch_input(monkeypatch):
    import asyncio

    import httpx

    import monster.computer.screenshot as screenshots
    from monster.computer.executor import InputExecutor
    from monster.providers.ui_venus import UIVenusComputerAdapter

    def forbidden(*args, **kwargs):
        raise AssertionError("Preview attempted desktop capture or input")

    monkeypatch.setattr(screenshots, "capture_display", forbidden)
    monkeypatch.setattr(InputExecutor, "execute", forbidden)
    result = asyncio.run(UIVenusComputerAdapter(config(), transport=httpx.MockTransport(lambda req: response())).next_decision(request()))
    assert result.execution_allowed is False


def test_venus_tag_like_payload_is_preserved_as_data_while_thought_is_discarded():
    result = preview("<think>SECRET_REASONING</think><action>Type(content='</action><think>literal payload</think>')</action>")
    assert result.image_action["text"] == "</action><think>literal payload</think>"
    assert "SECRET_REASONING" not in str(result.to_dict(include_content=True))


def test_venus_whole_request_deadline_rejects_late_result():
    import asyncio
    from dataclasses import replace

    import httpx

    from monster.providers.base import ProviderError
    from monster.providers.ui_venus import UIVenusComputerAdapter

    async def handle(req):
        await asyncio.sleep(10)
        return response()

    adapter = UIVenusComputerAdapter(replace(config(), timeout=5),
                                     transport=httpx.MockTransport(handle))
    with pytest.raises(ProviderError, match="timed out"):
        asyncio.run(adapter.next_decision(request()))
    assert adapter.history_metadata() == ()


def test_venus_oversized_response_is_rejected_without_body_disclosure():
    import asyncio

    import httpx

    from monster.providers.base import ProviderError
    from monster.providers.ui_venus import UIVenusComputerAdapter

    adapter = UIVenusComputerAdapter(config(), transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"PRIVATE" + b"x" * (2 * 1024 * 1024))))
    with pytest.raises(ProviderError, match="size limit") as error:
        asyncio.run(adapter.next_decision(request()))
    assert "PRIVATE" not in str(error.value)
    assert adapter.history_metadata() == ()
