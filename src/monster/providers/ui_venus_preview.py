"""Explicit offline or trusted-LAN UI-Venus previews. Never captures or injects input."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import struct
import time
from pathlib import Path

from monster.providers.base import ProviderError
from monster.providers.ollama import strict_json
from monster.providers.ui_venus import DecisionRequest, UIVenusComputerAdapter, UIVenusConfig
from monster.providers.venus_actions import (
    MAX_PNG_BYTES,
    FrameBinding,
    UIVenusActionError,
    parse_preview,
)


def _read(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise UIVenusActionError("input_file_size")
    return data


def _origin(value):
    if (type(value) not in (tuple, list) or len(value) != 2
            or any(type(n) is not int for n in value)):
        raise UIVenusActionError("explicit_origin_required")
    return value


def load_observation(path, origin, frame_id, displays=None):
    """The caller supplies image geometry; no Windows discovery or capture occurs."""
    from monster.computer.contracts import Bounds
    from monster.computer.screenshot import Observation

    png = _read(path, MAX_PNG_BYTES)
    if len(png) < 33 or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise UIVenusActionError("png_header")
    width, height = struct.unpack(">II", png[16:24])
    bounds = Bounds(*_origin(origin), width, height)
    geometry = (bounds,) if displays is None else tuple(Bounds(*item) for item in displays)
    obs = Observation(frame_id, time.monotonic(), bounds, geometry, png,
                      hashlib.sha256(png).hexdigest(), 0)
    FrameBinding.from_observation(obs)
    return obs


def replay_fixture(path, *, include_content=False):
    manifest = strict_json(_read(path, 1024 * 1024))
    if (type(manifest) is not dict or type(manifest.get("version")) is not int
            or manifest["version"] != 1 or manifest.get("synthetic") is not True
            or type(manifest.get("cases")) is not list or not 1 <= len(manifest["cases"]) <= 128):
        raise UIVenusActionError("fixture_manifest")
    report = {"mode": "offline_fixture", "real_input": False, "live_model_tested": False,
              "qualification": "synthetic_protocol_only", "cases": []}
    seen = set()
    for case in manifest["cases"]:
        if (type(case) is not dict or type(case.get("id")) is not str
                or not 1 <= len(case["id"]) <= 128 or case["id"] in seen
                or type(case.get("screenshot")) is not str
                or type(case.get("response")) is not str
                or ("expected" in case) == ("expect_error" in case)):
            raise UIVenusActionError("fixture_case")
        seen.add(case["id"])
        screenshot = (path.parent / case["screenshot"]).resolve()
        if not screenshot.is_relative_to(path.parent.resolve()):
            raise UIVenusActionError("fixture_image_path")
        obs = load_observation(screenshot, case.get("origin_px"), case["id"], case.get("displays"))
        row = {"id": case["id"], "matched": False}
        try:
            candidate = parse_preview(case["response"], FrameBinding.from_observation(obs), "fixture-replay", 1)
            expected = case.get("expected")
            actual = candidate.to_dict(include_content=True)
            row["matched"] = (type(expected) is dict and bool(expected)
                              and all(key in {"kind", "image_action", "desktop_action", "verification", "execution_allowed"}
                                      and actual.get(key) == value for key, value in expected.items()))
            row["preview"] = candidate.to_dict(include_content=include_content)
        except UIVenusActionError as exc:
            row.update(error=exc.code, matched=case.get("expect_error") == exc.code)
        report["cases"].append(row)
    return report


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture", type=Path, help="Replay a generated fixture manifest; no network")
    mode.add_argument("--response", type=Path, help="Parse a saved native response; no network")
    mode.add_argument("--server", help="Explicit trusted server origin, e.g. http://127.0.0.1:8000")
    result.add_argument("--screenshot", type=Path, help="Existing PNG; never captures the desktop")
    result.add_argument("--origin-x", type=int)
    result.add_argument("--origin-y", type=int)
    result.add_argument("--model", help="Explicit served UI-Venus checkpoint/alias; never selected automatically")
    result.add_argument("--task")
    result.add_argument("--frame-id", default="offline-frame-1")
    result.add_argument("--generation", type=int, default=1)
    result.add_argument("--local-server-confirmed", action="store_true")
    result.add_argument("--allow-lan-http", action="store_true")
    result.add_argument("--timeout", type=int, default=60)
    result.add_argument("--max-output-tokens", type=int, default=4096)
    result.add_argument("--include-content", action="store_true", help="Explicitly show action payload/terminal text; never reasoning")
    return result


def main():
    import sys

    cli = parser()
    args = cli.parse_args()
    if args.fixture:
        if args.screenshot or args.server or args.model or args.origin_x is not None or args.origin_y is not None:
            cli.error("Fixture geometry is supplied by the manifest; other mode arguments cannot override it.")
    elif not args.screenshot or args.origin_x is None or args.origin_y is None:
        cli.error("Supply --screenshot and explicit --origin-x / --origin-y; desktop origin is not inferred.")
    if args.server and (not args.model or not args.task or not args.local_server_confirmed):
        cli.error("Server previews require explicit --model, --task and --local-server-confirmed.")
    if not args.server and args.model:
        cli.error("--model is only used in explicit server mode.")
    try:
        if args.fixture:
            report = replay_fixture(args.fixture, include_content=args.include_content)
            passed = all(row["matched"] for row in report["cases"])
        else:
            obs = load_observation(args.screenshot, (args.origin_x, args.origin_y), args.frame_id)
            req = DecisionRequest.from_observation("preview-session", args.generation,
                                                   args.task or "Offline native-format preview", obs)
            if args.server:
                cfg = UIVenusConfig(args.server, args.model, args.local_server_confirmed,
                                    args.allow_lan_http, args.timeout, args.max_output_tokens)
                candidate = asyncio.run(UIVenusComputerAdapter(cfg).next_decision(req))
            else:
                raw = _read(args.response, 256 * 1024).decode("utf-8-sig")
                candidate = parse_preview(raw, req.frame, req.task_id, req.generation)
            report = {"mode": "server_preview" if args.server else "offline_response",
                      "real_input": False, "live_model_tested": bool(args.server),
                      "model_qualified": False, "preview": candidate.to_dict(include_content=args.include_content)}
            passed = True
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0 if passed else 1
    except (ProviderError, ValueError, TypeError, OSError, UnicodeError, RecursionError) as exc:
        # Provider errors contain fixed safe categories; never emit file contents,
        # arbitrary OS exception strings, HTTP bodies, or raw model responses.
        detail = str(exc) if isinstance(exc, ProviderError) else type(exc).__name__
        print("Preview stopped: " + detail, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Preview cancelled; no input was sent.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
