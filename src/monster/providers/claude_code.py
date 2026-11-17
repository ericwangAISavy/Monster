"""Claude Code CLI as Monster's reasoning provider.

Every prompt and validation rule is inherited from the Ollama provider; only the
transport differs. Each call runs the local `claude` CLI headless with no tools, no MCP
servers and no saved session, sends the screenshot(s) as image blocks over stdin, and
reads one JSON answer. The CLI uses this laptop's Claude Code login, so screenshots go
to Anthropic; that needs explicit consent in settings.

Latency: starting the CLI costs about 1.5-2 s, so a small pool keeps one process per
call type already started and waiting on stdin (a "warm" process). Answers are asked for
as plain JSON in the prompt (one model turn); the schema tool (--json-schema, two turns)
is only used to retry an answer that did not parse.
"""

import asyncio
import atexit
import base64
import glob
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from monster.providers.base import ProbeResult, ProviderError
from monster.providers.ollama import (
    PROBE_PROMPT,
    PROBE_SCHEMA,
    OllamaProvider,
    probe_image,
    strict_json,
    structured_diagnostic,
)

MAX_OUTPUT_BYTES = 4 * 1024 * 1024
CLI_ENV = "MONSTER_CLAUDE_CLI"
JSON_REPLY = (
    "\n\nReply with ONLY one JSON object, no prose and no code fence, that is valid "
    "against this JSON Schema:\n{schema}"
)


def find_cli():
    """The Claude Code CLI: $MONSTER_CLAUDE_CLI, `claude` on PATH, or the VS Code extension's."""
    explicit = os.environ.get(CLI_ENV)
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    found = shutil.which("claude")
    if found:
        return found
    home = os.path.expanduser("~")
    pattern = os.path.join(home, ".vscode", "extensions", "anthropic.claude-code-*",
                           "resources", "native-binary", "claude*")
    candidates = [p for p in glob.glob(pattern)
                  if os.path.basename(p).lower() in {"claude", "claude.exe"}]
    return max(candidates, key=os.path.getmtime) if candidates else None


def wrap_schema(schema):
    """The CLI needs an object at the top level; Monster's decision schema is a oneOf."""
    if schema.get("type") == "object":
        return schema, False
    return {"type": "object", "properties": {"answer": schema}, "required": ["answer"],
            "additionalProperties": False}, True


def parse_json_text(text):
    """The JSON object in a plain-text answer, tolerating a code fence; None if absent."""
    if type(text) is not str:
        return None
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = strict_json(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return value if type(value) is dict else None


MAX_IMAGE_EDGE = 1280  # Long edge sent to Claude; fewer image tokens, faster and cheaper.
JPEG_QUALITY = 80


def png_size(png):
    """(width, height) from a PNG header, or None."""
    if type(png) is bytes and len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
    return None


def image_scale(width, height):
    return min(1.0, MAX_IMAGE_EDGE / max(1, width, height))


def shrink_image(png):
    """A downscaled JPEG of a large PNG screenshot, or the PNG unchanged when small or unreadable."""
    size = png_size(png)
    scale = image_scale(*size) if size else 1.0
    if scale >= 1.0:
        return png, "image/png"
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PySide6.QtGui import QImage

        image = QImage.fromData(png, "PNG")
        if image.isNull():
            return png, "image/png"
        small = image.scaled(max(1, round(image.width() * scale)), max(1, round(image.height() * scale)),
                             Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        saved = small.save(buffer, "JPEG", JPEG_QUALITY)
        buffer.close()
        if saved and data.size():
            return bytes(data), "image/jpeg"
    except Exception:  # noqa: BLE001 - Any imaging problem just sends the original.
        pass
    return png, "image/png"


def scale_action(action, scale):
    """Pixel coordinates of an action scaled into (or back out of) the image Claude sees."""
    if scale == 1.0 or type(action) is not dict:
        return action
    scaled = dict(action)
    for key in ("x", "y", "end_x", "end_y"):
        if type(scaled.get(key)) is int:
            scaled[key] = round(scaled[key] * scale)
    if type(scaled.get("path")) is list:
        scaled["path"] = [[round(x * scale), round(y * scale)] for x, y in scaled["path"]]
    return scaled


def _matches(variant, value):
    for name, rule in variant.get("properties", {}).items():
        if name in value and type(rule) is dict:
            if "const" in rule and value[name] != rule["const"]:
                return False
            if "enum" in rule and value[name] not in rule["enum"]:
                return False
    return True


def fit_to_schema(value, schema):
    """Trim strings longer than their schema's maxLength.

    Plain JSON answers are not schema-enforced, and an over-long evidence or summary text
    should not cost a whole call: the provider's strict parsers still check everything else.
    """
    if type(schema) is not dict:
        return value
    if "oneOf" in schema:
        variants = [v for v in schema["oneOf"] if type(v) is dict]
        chosen = next((v for v in variants if type(value) is dict and _matches(v, value)), None)
        return fit_to_schema(value, chosen) if chosen else value
    if type(value) is str and type(schema.get("maxLength")) is int and len(value) > schema["maxLength"]:
        limit = schema["maxLength"]
        return value[:max(1, limit - 1)].rstrip() + "…"
    if type(value) is dict:
        properties = schema.get("properties", {})
        return {k: fit_to_schema(v, properties.get(k)) for k, v in value.items()}
    if type(value) is list:
        return [fit_to_schema(v, schema.get("items")) for v in value]
    return value


class WarmPool:
    """One started, waiting CLI process per argument list, bounded and reaped when idle."""

    MAX_READY = 6
    IDLE_S = 90

    def __init__(self, spawn=None, clock=time.monotonic):
        self.lock = threading.Lock()
        self.ready = {}  # tuple(argv) -> (process, started_at)
        self.clock = clock
        self.cwd = None
        self.spawn = spawn or self._spawn

    def _spawn(self, argv):
        if self.cwd is None:
            # An empty working directory keeps project CLAUDE.md files out of the prompt.
            self.cwd = tempfile.mkdtemp(prefix="monster-claude-")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        return subprocess.Popen(argv, cwd=self.cwd, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=flags)

    @staticmethod
    def _kill(process):
        try:
            process.kill()
            process.communicate(timeout=5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass

    def take(self, argv):
        """A ready process for argv (or a new one), and start the next one in advance."""
        key = tuple(argv)
        with self.lock:
            stale = self._reap()
            entry = self.ready.pop(key, None)
        for process in stale:
            self._kill(process)
        if entry is not None and entry[0].poll() is None:
            process = entry[0]
        else:
            process = self.spawn(argv)
        self._prewarm(key, argv)
        return process

    def _prewarm(self, key, argv):
        evicted = []
        with self.lock:
            if key in self.ready:
                return
            while len(self.ready) >= self.MAX_READY:
                oldest = min(self.ready, key=lambda k: self.ready[k][1])
                evicted.append(self.ready.pop(oldest)[0])
        for process in evicted:
            self._kill(process)
        try:
            process = self.spawn(argv)
        except OSError:
            return  # The next call simply starts cold.
        with self.lock:
            if key in self.ready:
                extra = process
            else:
                self.ready[key], extra = (process, self.clock()), None
        if extra is not None:
            self._kill(extra)

    def _reap(self):
        now = self.clock()
        stale = [k for k, (p, t) in self.ready.items() if now - t > self.IDLE_S or p.poll() is not None]
        return [self.ready.pop(k)[0] for k in stale]

    def close(self):
        with self.lock:
            processes = [p for p, _ in self.ready.values()]
            self.ready.clear()
        for process in processes:
            self._kill(process)
        if self.cwd:
            shutil.rmtree(self.cwd, ignore_errors=True)
            self.cwd = None


POOL = WarmPool()
atexit.register(POOL.close)


def _run_blocking(argv, stdin, timeout, pool=POOL):
    process = pool.take(argv) if "-p" in argv else pool.spawn(argv)
    try:
        out, err = process.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        WarmPool._kill(process)
        raise TimeoutError from None
    return process.returncode, out, err


async def pooled_runner(argv, stdin, timeout):
    # Plain subprocesses on a worker thread work across the per-job event loops. A bare
    # thread avoids the loop's default executor (concurrent.futures), which is not
    # reliably importable inside every job thread on this Python.
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(setter, value):
        if not future.done():
            setter(value)

    def work():
        try:
            value = _run_blocking(argv, stdin, timeout)
        except BaseException as exc:  # noqa: BLE001 - Re-raised in the awaiting task.
            loop.call_soon_threadsafe(deliver, future.set_exception, exc)
        else:
            loop.call_soon_threadsafe(deliver, future.set_result, value)

    threading.Thread(target=work, name="monster-claude-call", daemon=True).start()
    return await future


class ClaudeCodeProvider(OllamaProvider):
    def __init__(self, config, transport=None, diagnostic_callback=None, cli=None,
                 runner=None):
        super().__init__(config, transport, diagnostic_callback)
        self.cli = cli
        self.model_override = None  # Set while a yes/no check runs on the faster model.
        # runner(argv, stdin_bytes, timeout) -> (returncode, stdout_bytes, stderr_bytes)
        self.runner = runner or pooled_runner

    def may_transmit_screenshots(self):
        return self.config.claude_code_confirmed

    def _cli(self):
        path = self.cli or find_cli()
        if not path:
            raise ProviderError(
                "Claude Code CLI not found. Install Claude Code, or set "
                f"{CLI_ENV} to the path of claude.exe.")
        return path

    def _argv(self, system, schema=None):
        argv = [
            self._cli(), "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--tools", "", "--strict-mcp-config", "--no-session-persistence",
            "--model", self.model_override or self.config.claude_model,
            "--effort", "low", "--system-prompt", system,
        ]
        if schema is not None:
            index = argv.index("--tools")
            argv[index:index] = ["--json-schema", json.dumps(schema)]
        return argv

    async def _ask(self, argv, content):
        message = {"type": "user", "message": {"role": "user", "content": content}}
        started = time.monotonic()
        self.diagnostic("provider_request_started", {
            "method": "CLI", "route": "claude -p", "request_timeout_s": self.config.timeout,
            "model": self.model_override or self.config.claude_model,
            "structured_tool": "--json-schema" in argv})
        try:
            code, out, _ = await self.runner(
                argv, (json.dumps(message) + "\n").encode("utf-8"), self.config.timeout)
        except (TimeoutError, asyncio.TimeoutError):
            raise ProviderError("Claude Code timed out; increase the request timeout or retry.") from None
        except OSError:
            raise ProviderError("Could not start the Claude Code CLI.") from None
        if len(out) > MAX_OUTPUT_BYTES:
            raise ProviderError("Claude Code response exceeds the size limit.")
        result = None
        for line in out.decode("utf-8", "replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if type(event) is dict and event.get("type") == "result":
                result = event
        self.diagnostic("provider_model_response", {
            "exit_code": code, "elapsed_s": time.monotonic() - started,
            "subtype": result.get("subtype") if result else None,
            "is_error": result.get("is_error") if result else None,
            "cost_usd": result.get("total_cost_usd") if result else None,
            "turns": result.get("num_turns") if result else None,
        })
        if result is None:
            raise ProviderError("Claude Code returned no result. Check that you are logged in "
                                "(run claude, then /login).")
        if result.get("is_error") or code != 0:
            text = str(result.get("result") or "")[:200]
            if "login" in text.lower():
                raise ProviderError("Claude Code is not logged in. Run claude, then /login.")
            raise ProviderError("Claude Code request failed: " + (text or f"exit code {code}"))
        return result

    async def chat(self, model, prompt, image, schema, system="Return only the requested JSON.",
                   before_image=None):
        images = (before_image, image) if before_image is not None else (image,)
        content = []
        for index, png in enumerate(images):
            if len(images) == 2:
                content.append({"type": "text",
                                "text": "Image 1: BEFORE input." if index == 0 else "Image 2: AFTER input."})
            data, media_type = shrink_image(png)
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": media_type,
                "data": base64.b64encode(data).decode("ascii")}})
        # Fast path: plain JSON in one model turn, validated by the provider's own parsers.
        plain = content + [{"type": "text", "text": prompt + JSON_REPLY.format(
            schema=json.dumps(schema, separators=(",", ":")))}]
        result = await self._ask(self._argv(system), plain)
        value = parse_json_text(result.get("result"))
        if value is not None:
            value = fit_to_schema(value, schema)
        if value is None:
            # Slow path, once: let the CLI's schema tool enforce the shape.
            self.diagnostic("provider_json_rejected", {"retry": "json_schema"})
            wire_schema, wrapped = wrap_schema(schema)
            strict = content + [{"type": "text", "text": prompt + (
                "\nReturn your JSON inside the 'answer' field." if wrapped else "")}]
            result = await self._ask(self._argv(system, wire_schema), strict)
            value = result.get("structured_output")
            if wrapped:
                value = value.get("answer") if type(value) is dict else None
        if type(value) is not dict:
            self.diagnostic("provider_json_rejected", {})
            raise ProviderError("Model did not return valid structured JSON.")
        self.diagnostic("provider_structured_response", {}, structured_diagnostic(value))
        return value

    async def _check(self, method, *args, **kwargs):
        """Yes/no screen checks run on the faster check model."""
        self.model_override = self.config.claude_check_model
        try:
            return await method(*args, **kwargs)
        finally:
            self.model_override = None

    # Every prompt must state the size of the image Claude actually receives: a model that
    # is told 1920x1080 while seeing 1280x720 converts its coordinates with the wrong size.
    async def decide(self, model, instruction, image, width, height, history=(), **kwargs):
        s = image_scale(width, height)
        if s >= 1.0:
            return await super().decide(model, instruction, image, width, height, history=history, **kwargs)
        sw, sh = round(width * s), round(height * s)
        decision = await super().decide(model, instruction, image, sw, sh, history=history, **kwargs)
        if decision.action is None:
            return decision
        up = scale_action(decision.action, 1 / s) if decision.action else decision.action
        for key, limit in (("x", width), ("end_x", width), ("y", height), ("end_y", height)):
            if type(up.get(key)) is int:
                up[key] = min(limit - 1, max(0, up[key]))
        if type(up.get("path")) is list:
            up["path"] = [[min(width - 1, max(0, x)), min(height - 1, max(0, y))] for x, y in up["path"]]
        from dataclasses import replace

        return replace(decision, action=up)

    async def plan(self, model, goal, image, width, height):
        s = image_scale(width, height)
        return await super().plan(model, goal, image, round(width * s), round(height * s))

    async def assess(self, model, goal, plan, results, image, width, height):
        s = image_scale(width, height)
        return await super().assess(model, goal, plan, results, image, round(width * s), round(height * s))

    # Checks that quote pixel coordinates describe them in the downscaled image Claude sees.
    async def verify_action(self, model, instruction, action, expected, image, width, height,
                            before_image=None):
        s = image_scale(width, height)
        return await self._check(super().verify_action, model, instruction, scale_action(action, s),
                                 expected, image, round(width * s), round(height * s),
                                 before_image=before_image)

    async def verify_completion(self, *args, **kwargs):
        return await self._check(super().verify_completion, *args, **kwargs)

    async def verify_drawing_target(self, model, instruction, action, image, width, height):
        s = image_scale(width, height)
        result = await self._check(super().verify_drawing_target, model, instruction,
                                   scale_action(action, s), image, round(width * s), round(height * s))
        region = getattr(result, "region", None)
        if region and s < 1.0:
            x0, y0, x1, y1 = (round(v / s) for v in region)
            region = (max(0, x0), max(0, y0), min(width, x1), min(height, y1))
            result = type(result)(result.status, result.evidence, region)
        return result

    async def verify_draft_target(self, model, instruction, action, image, width, height):
        s = image_scale(width, height)
        return await self._check(super().verify_draft_target, model, instruction,
                                 scale_action(action, s), image, round(width * s), round(height * s))

    async def probe(self, model):
        started = time.monotonic()
        cli = self._cli()
        try:
            code, out, _ = await self.runner([cli, "--version"], b"", 30)
        except (TimeoutError, asyncio.TimeoutError, OSError):
            raise ProviderError("Claude Code CLI did not start.") from None
        version = out.decode("utf-8", "replace").strip().splitlines()[0][:100] if out else ""
        if code != 0 or not version:
            raise ProviderError("Claude Code CLI did not report a version.")
        self.diagnostic("probe_server", {"version": version, "provider": "claude_code"})
        colors = secrets.SystemRandom().sample(["red", "green", "blue"], 3)
        result = await self.chat(self.config.claude_model, PROBE_PROMPT, probe_image(colors),
                                 PROBE_SCHEMA)
        received = result.get("colors")
        if received != colors:
            raise ProviderError(
                f"Vision probe failed: expected {colors}, received {received}.")
        self.diagnostic("probe_passed", {"elapsed_s": time.monotonic() - started})
        return ProbeResult(self.config.claude_model, "claude-code", version, True, True)


def provider_for(config, *args, **kwargs):
    """The configured intelligence provider."""
    if getattr(config, "provider", "ollama") == "claude_code":
        return ClaudeCodeProvider(config, *args, **kwargs)
    return OllamaProvider(config, *args, **kwargs)
