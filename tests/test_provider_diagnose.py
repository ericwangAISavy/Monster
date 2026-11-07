import asyncio
import base64
import json

import httpx
import pytest

from monster.providers.config import OllamaConfig


def test_independent_check_preserves_config_and_never_captures_or_prints_content():
    from monster.providers.diagnose import diagnose

    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={
            "done": True, "done_reason": "stop", "eval_count": 12,
            "message": {"content": "private answer", "thinking": "private reasoning"},
            "total_duration": 1000000, "load_duration": 1000,
        })

    cfg = OllamaConfig(model="vision:small", local_server_confirmed=True)
    result = asyncio.run(diagnose(cfg, transport=httpx.MockTransport(handle)))
    assert [case["case"] for case in result["cases"]] == [
        "text", "text_schema", "image", "image_schema",
    ]
    assert all(case["status"] == "completed" for case in result["cases"])
    assert "private" not in json.dumps(result)
    for body in requests:
        assert body["model"] == cfg.model and body["think"] is cfg.thinking
        assert body["options"] == {"temperature": 0, "num_ctx": cfg.context_size,
                                    "num_predict": cfg.max_output_tokens}
        assert body["keep_alive"] == cfg.keep_alive and body["stream"] is False
    assert "images" not in requests[0]["messages"][1]
    assert "format" not in requests[0]
    assert "format" in requests[1]
    assert requests[0]["messages"] == requests[1]["messages"]
    assert "images" in requests[2]["messages"][1] and "format" not in requests[2]
    image = base64.b64decode(requests[2]["messages"][1]["images"][0])
    assert image.startswith(b"\x89PNG")
    assert requests[2]["messages"][1]["images"] == requests[3]["messages"][1]["images"]


@pytest.mark.parametrize("failure_case, expected", [
    (0, "general_inference"), (1, "structured_output"),
    (2, "vision_path"), (3, "vision_with_structured_output"),
])
def test_independent_check_stops_on_first_failure_without_retry(failure_case, expected):
    from monster.providers.diagnose import diagnose

    calls = []

    def handle(request):
        calls.append(request)
        if len(calls) == failure_case + 1:
            raise httpx.ReadTimeout("sensitive error body")
        return httpx.Response(200, json={"done": True, "message": {"content": "OK"}})

    cfg = OllamaConfig(model="vision:small", local_server_confirmed=True)
    result = asyncio.run(diagnose(cfg, transport=httpx.MockTransport(handle)))
    assert len(calls) == failure_case + 1
    assert result["cases"][-1]["status"] == "timeout"
    assert result["suspect_path"] == expected
    assert result["qualified_for_control"] is False
    assert "sensitive" not in json.dumps(result)


def test_direct_diagnostic_image_schema_matches_production_probe_request():
    from monster.providers.diagnose import diagnose
    from monster.providers.ollama import OllamaProvider

    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"done": True, "message": {"content": "{}"}})

    cfg = OllamaConfig(model="vision:small", local_server_confirmed=True)
    adapter = OllamaProvider(cfg, httpx.MockTransport(handle))
    from monster.providers.ollama import PROBE_PROMPT, PROBE_SCHEMA, probe_image
    asyncio.run(adapter.chat(cfg.model, PROBE_PROMPT,
                            probe_image(["blue", "green", "red"]), PROBE_SCHEMA))
    production = requests.pop()
    asyncio.run(diagnose(cfg, transport=httpx.MockTransport(handle)))
    assert requests[-1] == production


def test_single_case_can_repeat_report_request_without_warming_other_paths():
    from monster.providers.diagnose import diagnose

    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"done": True, "message": {"content": "{}"}})

    cfg = OllamaConfig(model="vision:small", local_server_confirmed=True)
    result = asyncio.run(diagnose(cfg, case="image_schema", transport=httpx.MockTransport(handle)))
    assert len(bodies) == 1
    import hashlib

    from PySide6.QtGui import QImage
    sent = base64.b64decode(bodies[0]["messages"][1]["images"][0])
    assert result["synthetic_image"]["sha256"] == hashlib.sha256(sent).hexdigest()
    image = QImage.fromData(sent, "PNG")
    assert [image.pixelColor(x, 16).name() for x in (16, 48, 80)] == ["#0000ff", "#00ff00", "#ff0000"]
    assert [row["case"] for row in result["cases"]] == ["image_schema"]


def test_headless_diagnostic_uses_saved_config_over_real_loopback_http(tmp_path):
    import subprocess
    import sys
    import threading
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from monster.providers.config import save

    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"done": True, "done_reason": "stop",
                               "message": {"content": "private output", "thinking": "private reasoning"}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = tmp_path / "ollama.json"
    cfg = replace(OllamaConfig(model="vision:small", local_server_confirmed=True),
                  url=f"http://127.0.0.1:{server.server_port}")
    save(path, cfg)
    original = path.read_bytes()
    try:
        run = subprocess.run([sys.executable, "-m", "monster.providers.diagnose", "--config", str(path),
                              "--case", "image_schema"], check=False, capture_output=True, text=True, timeout=10)
        assert run.returncode == 0, run.stderr
        report = json.loads(run.stdout)
        assert len(report["cases"]) == len(calls) == 1
        assert report["cases"][0]["status"] == "completed"
        assert report["qualified_for_control"] is False
        assert "private" not in run.stdout
        assert path.read_bytes() == original
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("model, confirmed", [("vision:small", False), ("vision:cloud", True)])
def test_diagnostic_rejects_unconfirmed_or_known_cloud_model_without_requests(model, confirmed):
    from monster.providers.diagnose import diagnose

    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"done": True, "message": {"content": "{}"}})

    cfg = OllamaConfig(model=model, local_server_confirmed=confirmed)
    with pytest.raises(ValueError):
        asyncio.run(diagnose(cfg, transport=httpx.MockTransport(handle)))
    assert not calls
