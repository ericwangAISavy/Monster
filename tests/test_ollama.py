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
        "action": {"op": "click", "x": 4, "y": 6, "button": "left", "count": 1},
    }
    adapter = OllamaProvider(config(), transport([], decision=proposal))
    result = asyncio.run(adapter.decide("vision:small", "Write a note", b"PNG", 100, 100))
    assert result.action == proposal["action"]


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
