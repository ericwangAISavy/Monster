import asyncio
import base64
import json
import struct
import zlib
from dataclasses import replace

import httpx
import pytest

from monster.providers.base import ProviderError
from monster.providers.config import OllamaConfig, load, save
from monster.providers.decisions import parse_decision
from monster.providers.ollama import OllamaProvider, strict_json


def config():
    return OllamaConfig(model="vision:small", local_server_confirmed=True)


def image_colors(png):
    offset, compressed = 8, b""
    while offset < len(png):
        length = struct.unpack(">I", png[offset : offset + 4])[0]
        if png[offset + 4 : offset + 8] == b"IDAT":
            compressed += png[offset + 8 : offset + 8 + length]
        offset += length + 12
    raw = zlib.decompress(compressed)
    names = {b"\xff\x00\x00": "red", b"\x00\xff\x00": "green", b"\x00\x00\xff": "blue"}
    return [names[raw[1 + index * 96 : 4 + index * 96]] for index in range(3)]


def transport(requests, decision=None, show=None):
    def handle(request):
        requests.append(request)
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "test-1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "vision:small", "digest": "abc"}]})
        if request.url.path == "/api/show":
            return httpx.Response(
                200, json=show if show is not None else {"capabilities": ["vision", "completion"]}
            )
        payload = json.loads(request.content)
        assert payload["stream"] is False and payload["format"]
        assert len(payload["messages"][1]["images"]) == 1
        png = base64.b64decode(payload["messages"][1]["images"][0])
        content = decision or {"colors": image_colors(png)}
        return httpx.Response(200, json={"done": True, "message": {"content": json.dumps(content)}})

    return httpx.MockTransport(handle)


def test_native_discovery_vision_probe_and_decision():
    requests = []
    adapter = OllamaProvider(config(), transport(requests))
    probe = asyncio.run(adapter.probe("vision:small"))
    assert probe.vision_verified and probe.structured_verified and probe.digest == "abc"
    assert [r.url.path for r in requests] == ["/api/version", "/api/tags", "/api/show", "/api/chat"]
    proposal = {
        "kind": "action",
        "summary": "Click the editor",
        "action": {"op": "click", "x_1000": 40, "y_1000": 60, "button": "left", "count": 1},
        "risk": "routine",
    }
    adapter = OllamaProvider(config(), transport([], decision=proposal))
    result = asyncio.run(adapter.decide("vision:small", "Write a note", b"PNG", 100, 100))
    assert result.action == {"op": "click", "x": 4, "y": 6, "button": "left", "count": 1}
    assert result.risk == "routine"


@pytest.mark.parametrize(
    "show",
    [
        {"capabilities": ["completion"]},
        {"remote_host": "cloud.example", "capabilities": ["vision"]},
    ],
)
def test_incompatible_models_never_receive_images(show):
    requests = []
    adapter = OllamaProvider(config(), transport(requests, show=show))
    with pytest.raises(ProviderError):
        asyncio.run(adapter.probe("vision:small"))
    assert all(request.url.path != "/api/chat" for request in requests)


def test_unknown_capabilities_require_real_probe_and_wrong_answer_fails():
    adapter = OllamaProvider(config(), transport([], show={}))
    assert asyncio.run(adapter.capabilities("vision:small")).vision is None
    assert asyncio.run(adapter.probe("vision:small")).vision_verified
    adapter = OllamaProvider(config(), transport([], decision={"colors": ["red"]}))
    with pytest.raises(ProviderError, match="probe failed"):
        asyncio.run(adapter.probe("vision:small"))


@pytest.mark.parametrize(
    "value",
    [
        {"kind": ["action"], "summary": "bad"},
        {"kind": "action", "summary": "bad", "action": {"op": "exec", "code": "anything"}},
        {"kind": "action", "summary": "bad", "action": {"op": ["move"]}},
        {"kind": "action", "summary": "bad", "action": {"op": "move", "x": 100, "y": 2}},
        {"kind": "action", "summary": "bad", "action": {"op": "move", "x": True, "y": 2}},
        {"kind": "complete", "summary": "Done", "approved": True},
        {"kind": "complete", "summary": ""},
        {"kind": "action", "summary": "bad", "action": None},
    ],
)
def test_invalid_decisions_rejected(value):
    with pytest.raises(ProviderError):
        parse_decision(value, 100, 100)


@pytest.mark.parametrize(
    "url",
    [
        "http://user:secret@localhost:11434",
        "http://localhost:11434/api",
        "ftp://localhost",
        "http://0.0.0.0",
        "http://8.8.8.8",
        "http://localhost?key=secret",
        "http://localhost:0",
    ],
)
def test_invalid_endpoint(url):
    with pytest.raises(ValueError):
        OllamaConfig(url=url).validate()


def test_private_lan_http_requires_confirmation_and_settings_roundtrip(tmp_path):
    with pytest.raises(ValueError, match="unencrypted"):
        OllamaConfig(url="http://192.168.1.100:11434").validate()
    value = OllamaConfig(url="http://192.168.1.100:11434", allow_lan_http=True)
    path = tmp_path / "ollama.json"
    save(path, value)
    assert load(path) == value
    with pytest.raises(ValueError):
        replace(value, mode="preview").validate()


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_http_errors_do_not_follow_redirects_or_leak_bodies(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="private diagnostic secret",
            headers={"location": "https://unapproved.example/"},
        )

    adapter = OllamaProvider(config(), httpx.MockTransport(handle))
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.health())
    assert "secret" not in str(error.value) and len(calls) == 1


def test_timeout_malformed_truncated_and_oversized_responses():
    def timeout(request):
        raise httpx.ReadTimeout("secret error")

    with pytest.raises(ProviderError, match="timed out"):
        asyncio.run(OllamaProvider(config(), httpx.MockTransport(timeout)).health())
    for body in (b"not json", b'{"version":"a","version":"b"}', b" " * (2 * 1024 * 1024 + 1)):
        adapter = OllamaProvider(
            config(), httpx.MockTransport(lambda _, body=body: httpx.Response(200, content=body))
        )
        with pytest.raises(ProviderError):
            asyncio.run(adapter.health())
    adapter = OllamaProvider(
        config(),
        httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"done": True, "done_reason": "length", "message": {"content": "{}"}}
            )
        ),
    )
    with pytest.raises(ProviderError, match="incomplete"):
        asyncio.run(adapter.decide("vision:small", "test", b"PNG", 10, 10))
    with pytest.raises(ValueError):
        strict_json('{"x":NaN}')


def test_no_screenshot_transmission_without_confirmation():
    calls = []
    adapter = OllamaProvider(replace(config(), local_server_confirmed=False), transport(calls))
    with pytest.raises(ProviderError, match="Confirm"):
        asyncio.run(adapter.decide("vision:small", "test", b"PNG", 100, 100))
    assert calls == []


def test_inference_settings_are_sent_explicitly_and_length_is_diagnostic():
    requests = []
    cfg = replace(
        config(), context_size=16384, max_output_tokens=8192, keep_alive="5m", thinking=False
    )
    adapter = OllamaProvider(
        cfg, transport(requests, decision={"kind": "complete", "summary": "Visible"})
    )
    asyncio.run(adapter.decide("vision:small", "test", b"PNG", 100, 100))
    payload = json.loads(requests[-1].content)
    assert payload["think"] is False and payload["keep_alive"] == "5m"
    assert payload["options"]["num_ctx"] == 16384
    assert payload["options"]["num_predict"] == 8192
    adapter = OllamaProvider(
        cfg,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "done": True,
                    "done_reason": "length",
                    "message": {
                        "content": '{"kind":"action"',
                        "thinking": "private model reasoning",
                    },
                },
            )
        ),
    )
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.decide("vision:small", "test", b"PNG", 100, 100))
    assert "8192" in str(error.value) and "output-token limit" in str(error.value)
    assert "private" not in str(error.value)


def test_legacy_config_gets_inference_defaults_and_roundtrips(tmp_path):
    path = tmp_path / "ollama.json"
    path.write_text(
        json.dumps(
            {
                "url": "http://localhost:11434",
                "model": "gemma4:e4b",
                "local_server_confirmed": True,
                "mode": "supervised",
            }
        )
    )
    cfg = load(path)
    assert cfg.max_output_tokens == 4096 and not cfg.thinking
    assert cfg.context_size == 16384 and cfg.keep_alive == "5m"
    save(path, replace(cfg, max_output_tokens=8192))
    assert load(path).max_output_tokens == 8192


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_output_tokens": 0},
        {"max_output_tokens": True},
        {"context_size": -1},
        {"context_size": 999999},
        {"thinking": "false"},
        {"keep_alive": "forever"},
    ],
)
def test_invalid_inference_configuration_is_rejected(overrides):
    with pytest.raises(ValueError):
        replace(config(), **overrides).validate()


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"done": True, "done_reason": "error"}, "server error"),
        ({"done": False}, "completion flag missing/false"),
    ],
)
def test_incomplete_response_distinguishes_server_error_from_missing_completion(reply, expected):
    reply = {**reply, "message": {"content": "private incomplete payload"}}
    adapter = OllamaProvider(
        config(), httpx.MockTransport(lambda _: httpx.Response(200, json=reply))
    )
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.decide("vision:small", "test", b"PNG", 100, 100))
    assert expected in str(error.value)
    assert "private" not in str(error.value)


def test_step_approval_defaults_enabled_and_persists_explicit_choice(tmp_path):
    path = tmp_path / "ollama.json"
    path.write_text("{}")
    assert load(path).approve_each_step is True
    cfg = replace(config(), approve_each_step=False)
    save(path, cfg)
    assert load(path).approve_each_step is False
    with pytest.raises(ValueError):
        replace(cfg, approve_each_step="false").validate()


def test_action_risk_is_normalized_and_invalid_classifications_rejected():
    action = {
        "kind": "action",
        "summary": "Focus editor",
        "action": {"op": "click", "x": 10, "y": 10},
        "risk": "routine",
    }
    assert parse_decision(action, 100, 100).risk == "routine"
    for invalid in (True, None, [], "approved", "safe"):
        with pytest.raises(ProviderError):
            parse_decision({**action, "risk": invalid}, 100, 100)
    legacy = {k: v for k, v in action.items() if k != "risk"}
    assert parse_decision(legacy, 100, 100).risk is None


def test_visual_verification_request_checks_actual_composer_result():
    requests = []
    adapter = OllamaProvider(
        config(),
        transport(
            requests,
            decision={"status": "failed", "evidence": "Text is in search, not the composer"},
        ),
    )
    result = asyncio.run(
        adapter.verify_action(
            "vision:small",
            "Type Hello in current Slack composer",
            {"op": "text", "text": "Hello"},
            "Hello appears in the unsent composer",
            b"PNG",
            100,
            100,
        )
    )
    assert result.status == "failed"
    payload = json.loads(requests[-1].content)
    assert '"text": "Hello"' in payload["messages"][1]["content"]
    assert "not an existing conversation or search field" in payload["messages"][1]["content"]
    assert payload["format"]["properties"]["status"]["enum"] == ["succeeded", "failed", "uncertain"]
    adapter = OllamaProvider(
        config(), transport([], decision={"status": "succeeded", "evidence": "ok", "execute": True})
    )
    with pytest.raises(ProviderError, match="verification"):
        asyncio.run(
            adapter.verify_action(
                "vision:small",
                "test",
                {"op": "press", "key": "TAB"},
                "Focus visible",
                b"PNG",
                100,
                100,
            )
        )


def test_action_expectations_and_allowed_keys_are_constrained():
    from monster.providers.decisions import ACTION_SCHEMA

    value = {
        "kind": "action",
        "summary": "Type draft",
        "risk": "routine",
        "action": {"op": "text", "text": "Hello"},
        "expected_result": "Hello is visible in the editable composer, unsent",
    }
    assert "unsent" in parse_decision(value, 100, 100).expected_result
    with pytest.raises(ProviderError, match="expected result"):
        parse_decision({**value, "expected_result": ""}, 100, 100)
    press = next(s for s in ACTION_SCHEMA["oneOf"] if s["properties"]["op"]["const"] == "press")
    assert "CTRL+A" not in press["properties"]["key"]["enum"]
    chord = next(s for s in ACTION_SCHEMA["oneOf"] if s["properties"]["op"]["const"] == "chord")
    assert ["SHIFT", "ENTER"] in chord["properties"]["keys"]["enum"]


def test_provider_diagnostics_include_finish_metrics_but_never_raw_thinking():
    events = []

    def respond(request):
        return httpx.Response(200, json={
            "done": True, "done_reason": "stop", "eval_count": 14, "load_duration": 123,
            "message": {"thinking": "PRIVATE_RAW_REASONING_CANARY", "content": json.dumps({
                "kind": "action", "summary": "Focus editor", "risk": "routine",
                "action": {"op": "click", "x_1000": 40, "y_1000": 60},
                "expected_result": "Editor focused",
            })},
        })

    provider = OllamaProvider(config(), httpx.MockTransport(respond),
                              diagnostic_callback=lambda event, details, content: events.append(
                                  {"event": event, "details": details, "content": content}
                              ))
    asyncio.run(provider.decide("vision:small", "Open Sticky Notes", b"PNG", 100, 100))
    assert "PRIVATE_RAW_REASONING_CANARY" not in json.dumps(events)
    response = next(item for item in events if item["event"] == "provider_model_response")
    assert response["details"]["server_metrics"]["eval_count"] == 14
    parsed = next(item for item in events if item["event"] == "provider_structured_response")
    assert parsed["content"]["action"] == {"op": "click", "x_1000": 40, "y_1000": 60}


@pytest.mark.parametrize("width,height,point,expected", [
    (1920, 1080, (585, 955), (1123, 1030)),
    (1920, 1080, (570, 972), (1094, 1049)),
    (1920, 1080, (1000, 1000), (1919, 1079)),
    (2560, 1440, (500, 500), (1280, 720)),
    (1, 1, (1000, 1000), (0, 0)),
])
def test_normalized_coordinate_contract_maps_once_to_native_image_pixels(width, height, point, expected):
    decision = parse_decision({
        "kind": "action", "summary": "Focus target", "risk": "routine",
        "expected_result": "Target foreground",
        "action": {"op": "click", "x_1000": point[0], "y_1000": point[1]},
    }, width, height, coordinate_space="normalized_1000")
    assert (decision.action["x"], decision.action["y"]) == expected
    assert "x_1000" not in decision.action
    assert decision.coordinate_source["model_action"]["x_1000"] == point[0]
    assert decision.coordinate_source["image_size"] == {"width": width, "height": height}


@pytest.mark.parametrize("action", [
    {"op": "click", "x": 585, "y": 955},
    {"op": "click", "x_1000": 585, "y_1000": 955, "x": 585},
    {"op": "click", "x_1000": 1001, "y_1000": 500},
    {"op": "click", "x_1000": True, "y_1000": 500},
    {"op": "click", "x_1000": -1, "y_1000": 500},
])
def test_normalized_coordinate_protocol_rejects_ambiguous_or_invalid_points(action):
    with pytest.raises(ProviderError):
        parse_decision({"kind": "action", "summary": "Target", "action": action},
                       1920, 1080, coordinate_space="normalized_1000")


def test_normalized_model_coordinates_add_negative_monitor_origin_only_at_dispatch():
    from types import SimpleNamespace

    from monster.agent.validation import prepare_action
    from monster.computer.contracts import Bounds

    decision = parse_decision({"kind": "action", "summary": "Focus target", "action": {
        "op": "click", "x_1000": 570, "y_1000": 972,
    }}, 1920, 1080, coordinate_space="normalized_1000")
    bounds = Bounds(-1920, 0, 1920, 1080)
    observation = SimpleNamespace(bounds=bounds, displays=(bounds,), foreground=77)
    actual = prepare_action(decision.action, observation)
    assert actual["x"] == -826 and actual["y"] == 1049


def test_live_provider_schema_requires_normalized_keys_without_legacy_fallback():
    requests = []
    provider = OllamaProvider(config(), transport(requests, decision={
        "kind": "action", "summary": "Slack taskbar icon", "risk": "routine",
        "expected_result": "Slack foreground", "action": {"op": "click", "x": 585, "y": 955},
    }))
    with pytest.raises(ProviderError, match="x_1000/y_1000"):
        asyncio.run(provider.decide("vision:small", "Go to Slack", b"PNG", 1920, 1080))
    payload = json.loads(requests[-1].content)
    action_schema = payload["format"]["oneOf"][0]["properties"]["action"]
    click = next(item for item in action_schema["oneOf"] if item["properties"]["op"]["const"] == "click")
    assert click["properties"]["y_1000"]["maximum"] == 1000
    assert "y" not in click["properties"]
    assert "already-converted native image pixels" in payload["messages"][1]["content"]


def test_probe_reports_stage_and_image_evidence_without_reasoning():
    events, requests = [], []
    adapter = OllamaProvider(config(), transport(requests),
                             diagnostic_callback=lambda event, details, content=None: events.append((event, details)))
    asyncio.run(adapter.probe("vision:small"))
    stages = [details["stage"] for event, details in events if event == "probe_stage"]
    assert stages == ["server_version", "model_list", "model_metadata", "vision_inference", "vision_validation"]
    image = next(details for event, details in events if event == "probe_image")
    sent = base64.b64decode(json.loads(requests[-1].content)["messages"][1]["images"][0])
    import hashlib
    assert image["sha256"] == hashlib.sha256(sent).hexdigest()
    assert image["expected_colors"] == image_colors(sent)
    assert image["width"] == 96 and image["height"] == 32
    assert any(event == "probe_passed" for event, _ in events)


def test_probe_timeout_names_stage_without_claiming_model_incompatible():
    original = transport([])
    events = []

    async def handle(request):
        if request.url.path == "/api/chat":
            raise httpx.ReadTimeout("secret server internals")
        return original.handle_request(request)

    adapter = OllamaProvider(config(), httpx.MockTransport(handle),
                             diagnostic_callback=lambda event, details, content=None: events.append((event, details)))
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.probe("vision:small"))
    assert "Image inference + JSON" in str(error.value)
    assert "timed out" in str(error.value)
    assert "secret" not in str(error.value)
    failure = next(details for event, details in events if event == "probe_failed")
    assert failure["stage"] == "vision_inference"


@pytest.mark.parametrize("colors", [
    ["red", "green", "blue"], ["red", "blue", "green"],
    ["green", "red", "blue"], ["green", "blue", "red"],
    ["blue", "red", "green"], ["blue", "green", "red"],
])
def test_synthetic_probe_png_decodes_with_independent_image_reader(colors):
    from PySide6.QtGui import QImage

    from monster.providers.ollama import probe_image

    image = QImage.fromData(probe_image(colors), "PNG")
    assert not image.isNull()
    assert (image.width(), image.height()) == (96, 32)
    rgb = {"red": "#ff0000", "green": "#00ff00", "blue": "#0000ff"}
    assert [image.pixelColor(x, 16).name() for x in (16, 48, 80)] == [rgb[c] for c in colors]


def test_draft_schema_exposes_full_text_without_physical_character_keys():
    requests = []
    proposal = {"kind": "action", "summary": "Type hello", "risk": "routine",
                "expected_result": "hello is visible", "action": {"op": "text", "text": "hello"}}
    adapter = OllamaProvider(config(), transport(requests, decision=proposal))
    result = asyncio.run(adapter.decide("vision:small", "Write hello in the current Slack msg panel", b"PNG", 100, 100))
    assert result.action == {"op": "text", "text": "hello"}
    body = json.loads(requests[-1].content)
    variants = body["format"]["oneOf"][0]["properties"]["action"]["oneOf"]
    assert "press" not in [v["properties"]["op"]["const"] for v in variants]
    assert '{"op":"text","text":"hello"}' in body["messages"][1]["content"]


@pytest.mark.parametrize("status", ["ready", "needs_focus", "wrong_target", "uncertain"])
def test_draft_target_verification_uses_pixels_and_requested_application(status):
    requests = []
    adapter = OllamaProvider(config(), transport(requests, decision={"status": status, "evidence": "Visible target evidence"}))
    result = asyncio.run(adapter.verify_draft_target("vision:small", "Write hello in Slack", {"op": "text", "text": "hello"}, b"PNG", 100, 100))
    assert result.status == status
    prompt = json.loads(requests[-1].content)["messages"][1]["content"]
    assert "ChatGPT" in prompt and "Slack" in prompt
    assert "before" in prompt.lower()


@pytest.mark.parametrize("result", [
    {"status": "ready", "evidence": ""},
    {"status": "succeeded", "evidence": "Not a valid target status"},
    {"status": ["ready"], "evidence": "Wrong type"},
    {"status": "ready", "evidence": "x", "approved": True},
])
def test_draft_target_malformed_assessment_is_rejected(result):
    adapter = OllamaProvider(config(), transport([], decision=result))
    with pytest.raises(ProviderError, match="invalid draft target assessment"):
        asyncio.run(adapter.verify_draft_target("vision:small", "Write hello in Slack", {"op": "text", "text": "hello"}, b"PNG", 100, 100))


def test_terminal_window_generation_exposes_complete_chord_but_not_modifier_taps():
    from monster.providers.decisions import decision_schema_for_navigation

    def fields(instruction):
        variants = decision_schema_for_navigation(instruction)['oneOf'][0]['properties']['action']['oneOf']
        return {v['properties']['op']['const']: v['properties'] for v in variants}

    terminal = fields('open new fresh terminal window')
    ordinary = fields('write a note')
    assert ['CTRL', 'SHIFT', 'N'] in terminal['chord']['keys']['enum']
    assert ['CTRL', 'SHIFT', 'N'] not in ordinary['chord']['keys']['enum']
    assert not {'CTRL', 'SHIFT', 'ALT'} & set(terminal['press']['key']['enum'])
    assert 'WIN' in terminal['press']['key']['enum']
