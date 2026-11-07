"""Bounded per-run evidence in RAM; persistence only through explicit export."""

import hashlib
import json
import platform
import re
import sys
import threading
import time
import traceback
import uuid
import zipfile
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import wraps
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlsplit


def exception_evidence(exc):
    """Project stack locations only, never messages, source lines or locals."""
    root = Path(__file__).resolve().parents[1]
    frames = []
    for frame, line in traceback.walk_tb(exc.__traceback__):
        path = Path(frame.f_code.co_filename).resolve()
        if path.is_relative_to(root):
            frames.append({"file": path.relative_to(root).as_posix(), "line": line,
                           "function": frame.f_code.co_name})
    causes, seen = [], {id(exc)}
    cause = exc.__cause__ or exc.__context__
    while cause is not None and id(cause) not in seen and len(causes) < 8:
        seen.add(id(cause))
        causes.append(type(cause).__name__)
        cause = cause.__cause__ or cause.__context__
    return {"exception_class": type(exc).__name__, "project_stack": frames,
            "cause_classes": causes}


def runtime_identity():
    package = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    source_files = source_errors = 0
    try:
        for path in sorted(package.rglob("*.py")):
            try:
                data = path.read_bytes()
            except OSError:
                source_errors += 1
                continue
            digest.update(str(path.relative_to(package)).encode())
            digest.update(data)
            source_files += 1
    except OSError:
        source_errors += 1
    revision = None
    git = package.parent.parent / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            if re.fullmatch(r"refs/[A-Za-z0-9_./-]+", ref) and ".." not in ref.split("/"):
                try:
                    head = (git / ref).read_text().strip()
                except FileNotFoundError:
                    for line in (git / "packed-refs").read_text().splitlines():
                        if line.endswith(" " + ref):
                            head = line.split()[0]
                            break
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
            revision = head
    except OSError:
        pass
    packages = {}
    for name in ("monster-companion", "PySide6-Essentials", "mss", "httpx"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "unknown"
    return {
        "git_revision_at_startup": revision, "source_sha256": digest.hexdigest() if source_files else None,
        "source_fingerprint_complete": bool(source_files and not source_errors),
        "python": sys.version.split()[0], "os": platform.system(),
        "os_release": platform.release(), "machine": platform.machine(), "packages": packages,
    }


# Capture at process startup, not after a running checkout is updated on disk.
RUNTIME = runtime_identity()


@dataclass(frozen=True)
class DiagnosticSnapshot:
    data: dict
    images: dict

    def text(self):
        return (
            "Monster run diagnostics — contains task/action text when included. "
            "Review before sharing. No raw model reasoning.\n\n"
            + json.dumps(self.data, ensure_ascii=False, indent=2)
        )

    def export(self, path, *, include_screenshots=False):
        data = deepcopy(self.data)
        data["screenshots_included"] = bool(include_screenshots and self.images)
        data["screenshot_export_requested"] = include_screenshots
        if include_screenshots:
            for observation in data["observations"]:
                if observation["id"] in self.images:
                    observation["file"] = f"screenshots/{observation['id']}.png"
            with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("report.json", json.dumps(data, ensure_ascii=False, indent=2))
                for identifier, png in self.images.items():
                    bundle.writestr(f"screenshots/{identifier}.png", png)
        else:
            with Path(path).open("x", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)


def synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class RunDiagnostics:
    def __init__(self, instruction, config, *, max_events=512, max_images=16,
                 max_image_bytes=24 * 1024 * 1024, clock=time.monotonic, content_free=False):
        self.content_free = content_free
        self.lock = threading.RLock()
        self.clock, self.started = clock, clock()
        self.run_id = uuid.uuid4().hex
        self.started_at = datetime.now(UTC).isoformat()
        self.instruction = "[omitted mission content]" if content_free else instruction[:8000]
        self.instruction_truncated = len(instruction) > 8000
        self.configuration = {name: getattr(config, name) for name in (
            "model", "mode", "timeout", "context_size", "max_output_tokens", "thinking",
            "keep_alive", "approve_each_step", "allow_lan_http", "local_server_confirmed",
        )}
        try:
            origin = urlsplit(config.url)
            host = origin.hostname or "unknown"
            host = f"[{host}]" if ":" in host else host
            self.configuration["url"] = f"{origin.scheme}://{host}" + (
                f":{origin.port}" if origin.port is not None else ""
            )
        except ValueError:
            self.configuration["url"] = "[invalid origin]"
        self.events, self.observations = [], []
        self.images = OrderedDict()
        self.focus = {}
        self.outcome = {"status": "running", "stage": "opening", "reason": None}
        self.max_events, self.max_images = max_events, 0 if content_free else max_images
        self.max_image_bytes = max_image_bytes
        self.image_bytes = self.sequence = self.frame_sequence = 0
        self.events_dropped = self.images_dropped = self.observations_dropped = 0

    @synchronized
    def focus_alias(self, handle):
        if not handle:
            return None
        return self.focus.setdefault(handle, f"window-{len(self.focus) + 1}")

    @synchronized
    def add(self, kind, phase, *, content=None, **details):
        self.sequence += 1
        event = {
            "sequence": self.sequence, "elapsed_s": round(self.clock() - self.started, 6),
            "event": kind, "stage": phase, "details": deepcopy(details),
        }
        if content and not self.content_free:
            event["content"] = deepcopy(content)
        self.events.append(event)
        if len(self.events) > self.max_events:
            self.events.pop(0)
            self.events_dropped += 1

    @synchronized
    def observe(self, observation, phase):
        self.frame_sequence += 1
        identifier = f"frame-{self.frame_sequence:04d}"
        metadata = {
            "id": identifier, "source_id": observation.id,
            "bounds": asdict(observation.bounds),
            "displays": [asdict(bounds) for bounds in observation.displays],
            "foreground": self.focus_alias(observation.foreground),
            "age_s": max(0, round(self.clock() - observation.captured_at, 6)),
            "sha256": hashlib.sha256(observation.png).hexdigest(),
        }
        self.observations.append(metadata)
        if len(self.observations) > 128:
            self.observations.pop(0)
            self.observations_dropped += 1
        png = observation.png
        if len(png) <= self.max_image_bytes and self.max_images > 0:
            self.images[identifier] = png
            self.image_bytes += len(png)
        else:
            self.images_dropped += 1
        while len(self.images) > self.max_images or self.image_bytes > self.max_image_bytes:
            _, old = self.images.popitem(last=False)
            self.image_bytes -= len(old)
            self.images_dropped += 1
        self.add("observation", phase, observation=identifier, **metadata_without_id(metadata))
        return identifier

    @synchronized
    def end(self, status, stage, reason, *, cleanup_confirmed=None):
        self.outcome = {
            "status": status, "stage": stage, "reason": reason,
            "cleanup_confirmed": cleanup_confirmed,
        }

    @synchronized
    def snapshot(self, *, include_content=True, summary=None):
        include_content = include_content and not self.content_free
        observations = deepcopy(self.observations)
        for observation in observations:
            observation["retained"] = observation["id"] in self.images
        events = deepcopy(self.events)
        if not include_content:
            for event in events:
                event.pop("content", None)
        worker_hashes = set()
        for event in events:
            if event["event"] != "worker_event":
                continue
            worker = event["details"].get("worker")
            runtime = worker.get("runtime") if isinstance(worker, dict) else None
            source = runtime.get("source_sha256") if isinstance(runtime, dict) else None
            if type(source) is str and re.fullmatch(r"[0-9a-f]{64}", source):
                worker_hashes.add(source)
        worker_hashes = sorted(worker_hashes)
        parent_hash = RUNTIME["source_sha256"]
        data = {
            "format": "monster.run.v1", "run_id": self.run_id, "started_at_utc": self.started_at,
            "snapshot_at_utc": datetime.now(UTC).isoformat(), "start_monotonic_s": self.started,
            "elapsed_s_at_snapshot": round(self.clock() - self.started, 6),
            "runtime": deepcopy(RUNTIME),
            "runtime_consistency": {
                "worker_source_hashes": worker_hashes,
                "source_mismatch": any(item != parent_hash for item in worker_hashes)
                                   if worker_hashes and parent_hash else None,
            }, "configuration": deepcopy(self.configuration),
            "task": self.instruction if include_content else "[omitted]",
            "content_included": include_content, "screenshots_included": False,
            "original_instruction_truncated": self.instruction_truncated,
            "outcome": deepcopy(self.outcome), "summary": deepcopy(summary or {}),
            "events": events, "observations": observations,
            "retention": {
                "events_dropped": self.events_dropped, "images_dropped": self.images_dropped,
                "observation_records_dropped": self.observations_dropped,
                "max_images": self.max_images, "max_image_bytes": self.max_image_bytes,
                "retained_image_bytes": self.image_bytes,
            },
            "limits": "Accepted input and model verification do not prove task success. "
                      "Screenshots are recent bounded RAM evidence; export is explicit. "
                      "No raw model reasoning, environment dump or credential files collected.",
        }
        if not include_content:
            data["outcome"]["reason"] = "[omitted; use content report for failure detail]"
            if "primary" in data["outcome"]:
                data["outcome"]["primary"]["reason"] = "[omitted]"
        return DiagnosticSnapshot(data, dict(self.images) if include_content else {})


def metadata_without_id(metadata):
    return {key: value for key, value in metadata.items() if key != "id"}
