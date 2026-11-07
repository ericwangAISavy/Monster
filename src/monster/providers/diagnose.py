"""Explicit synthetic inference comparison, without GUI jobs or desktop access.

Run: uv run python -m monster.providers.diagnose
Reads saved settings, never writes them. Console output contains metadata only.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

import httpx

from monster.agent.diagnostics import RUNTIME
from monster.providers.config import load
from monster.providers.ollama import PROBE_PROMPT, PROBE_SCHEMA, probe_image, strict_json


async def diagnose(config, *, case="all", transport=None, progress=None):
    config.validate()
    if not config.model or not config.local_server_confirmed:
        raise ValueError("Save a model and confirm local-only server operation first.")
    if "cloud" in config.model.lower():
        raise ValueError("Known cloud-backed models are not used in local-only diagnostics.")
    image = probe_image(["blue", "green", "red"])
    encoded = base64.b64encode(image).decode("ascii")
    cases = (
        ("text", 'Return {"ready":true} only.', False, None, "general_inference"),
        ("text_schema", 'Return {"ready":true} only.', False, {
            "type": "object", "properties": {"ready": {"const": True}},
            "required": ["ready"], "additionalProperties": False,
        }, "structured_output"),
        ("image", PROBE_PROMPT, True, None, "vision_path"),
        ("image_schema", PROBE_PROMPT, True, PROBE_SCHEMA, "vision_with_structured_output"),
    )
    if case != "all":
        cases = tuple(entry for entry in cases if entry[0] == case)
        if not cases:
            raise ValueError("Unknown diagnostic case")
    report = {
        "runtime": RUNTIME, "configuration": asdict(config),
        "synthetic_image": {"sha256": hashlib.sha256(image).hexdigest(),
                            "width": 96, "height": 32, "png_bytes": len(image),
                            "expected_colors": ["blue", "green", "red"]},
        "cases": [], "suspect_path": None, "qualified_for_control": False,
        "note": "Response completion only, not answer accuracy or control qualification. "
                "No desktop capture, input, raw answers, reasoning, retries or settings writes. "
                "Earlier cases may warm the server; all completing does not prove a GUI defect. "
                "Server routing relies on saved local-only confirmation and is not independently rechecked.",
    }
    for name, prompt, with_image, schema, suspect in cases:
        if progress:
            progress(f"Starting {name}; total request limit {config.timeout}s")
        user = {"role": "user", "content": prompt}
        if with_image:
            user["images"] = [encoded]
        payload = {
            "model": config.model, "stream": False, "think": config.thinking,
            "keep_alive": config.keep_alive,
            "options": {"temperature": 0, "num_ctx": config.context_size,
                        "num_predict": config.max_output_tokens},
            "messages": [{"role": "system", "content": "Return only the requested JSON."}, user],
        }
        if schema is not None:
            payload["format"] = schema
        started = time.monotonic()
        result = {"case": name, "status": "pending", "headers_received": False}
        try:
            async with asyncio.timeout(config.timeout):
                async with (
                    httpx.AsyncClient(base_url=config.url.rstrip("/"), follow_redirects=False,
                                      timeout=httpx.Timeout(config.timeout, connect=5),
                                      transport=transport) as client,
                    client.stream("POST", "/api/chat", json=payload) as response,
                ):
                    result.update(headers_received=True, status_code=response.status_code,
                                  headers_wait_s=round(time.monotonic() - started, 3))
                    if response.status_code != 200:
                        result["status"] = "http_error"
                    else:
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 2 * 1024 * 1024:
                                raise ValueError("Response size limit")
                        value = strict_json(data)
                        if type(value) is not dict:
                            raise ValueError("Response envelope")
                        message = value.get("message")
                        complete = (value.get("done") is True and type(message) is dict
                                    and type(message.get("content")) is str
                                    and not message.get("tool_calls")
                                    and value.get("done_reason") not in {"length", "error"})
                        result["status"] = "completed" if complete else "incomplete_response"
                        result["metrics"] = {
                            key: value[key] for key in ("total_duration", "load_duration",
                                "prompt_eval_duration", "eval_duration", "prompt_eval_count", "eval_count")
                            if type(value.get(key)) is int and 0 <= value[key] < 2**63
                        }
        except (TimeoutError, httpx.TimeoutException):
            result["status"] = "timeout"
        except httpx.HTTPError as exc:
            result.update(status="transport_error", error_type=type(exc).__name__)
        except (ValueError, TypeError):
            result["status"] = "invalid_response"
        result["elapsed_s"] = round(time.monotonic() - started, 3)
        report["cases"].append(result)
        if progress:
            progress(f"{name}: {result['status']} in {result['elapsed_s']}s")
        if result["status"] != "completed":
            report["suspect_path"] = suspect
            break
    return report


def main():
    import sys

    from PySide6.QtCore import QCoreApplication, QStandardPaths

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Optional saved Ollama JSON path")
    parser.add_argument("--case", choices=("all", "text", "text_schema", "image", "image_schema"),
                        default="all", help="Choose one case to avoid warming other paths first")
    args = parser.parse_args()
    app = QCoreApplication.instance() or QCoreApplication([])
    app.setApplicationName("Monster")
    app.setOrganizationName("Monster")
    path = args.config or Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation)) / "ollama.json"
    try:
        if not path.is_file():
            raise ValueError("Saved settings were not found.")
        config = load(path)
        print(f"Saved configuration: {config.model} at {config.url}; context={config.context_size}, "
              f"output={config.max_output_tokens}, thinking={config.thinking}, "
              f"timeout={config.timeout}s per request", file=sys.stderr, flush=True)
        report = asyncio.run(diagnose(config, case=args.case,
            progress=lambda message: print(message, file=sys.stderr, flush=True)))
    except (ValueError, TypeError, OSError) as exc:
        print(f"Cannot start diagnostic: {type(exc).__name__}. Check saved settings.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Diagnostic cancelled; no input was sent.", file=sys.stderr)
        return 130
    print(json.dumps(report, indent=2))
    return 1 if report["suspect_path"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
