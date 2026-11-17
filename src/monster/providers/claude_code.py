"""Claude Code CLI as Monster's reasoning provider.

Every prompt, schema and validation rule is inherited from the Ollama provider; only
the transport differs. Each call runs the local `claude` CLI headless with no tools,
no MCP servers and no saved session, sends the screenshot(s) as image blocks over
stdin, and reads one schema-validated JSON answer. The CLI uses this laptop's Claude
Code login, so screenshots go to Anthropic; that needs explicit consent in settings.
"""

import asyncio
import base64
import glob
import json
import os
import secrets
import shutil
import tempfile
import time

from monster.providers.base import ProbeResult, ProviderError
from monster.providers.ollama import (
    PROBE_PROMPT,
    PROBE_SCHEMA,
    OllamaProvider,
    probe_image,
    structured_diagnostic,
)

MAX_OUTPUT_BYTES = 4 * 1024 * 1024
CLI_ENV = "MONSTER_CLAUDE_CLI"


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


class ClaudeCodeProvider(OllamaProvider):
    def __init__(self, config, transport=None, diagnostic_callback=None, cli=None,
                 runner=None):
        super().__init__(config, transport, diagnostic_callback)
        self.cli = cli
        self.model_override = None  # Set while a yes/no check runs on the faster model.
        # runner(argv, stdin_bytes, timeout) -> (returncode, stdout_bytes, stderr_bytes)
        self.runner = runner or self._run

    def may_transmit_screenshots(self):
        return self.config.claude_code_confirmed

    def _cli(self):
        path = self.cli or find_cli()
        if not path:
            raise ProviderError(
                "Claude Code CLI not found. Install Claude Code, or set "
                f"{CLI_ENV} to the path of claude.exe.")
        return path

    @staticmethod
    async def _run(argv, stdin, timeout):
        # An empty working directory keeps project CLAUDE.md files out of the prompt.
        with tempfile.TemporaryDirectory(prefix="monster-claude-") as cwd:
            process = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(process.communicate(stdin), timeout)
            except (TimeoutError, asyncio.CancelledError):
                process.kill()
                await process.wait()
                raise
            return process.returncode, out, err

    async def chat(self, model, prompt, image, schema, system="Return only the requested JSON.",
                   before_image=None):
        cli = self._cli()
        wire_schema, wrapped = wrap_schema(schema)
        images = (before_image, image) if before_image is not None else (image,)
        content = []
        for index, png in enumerate(images):
            if len(images) == 2:
                content.append({"type": "text",
                                "text": "Image 1: BEFORE input." if index == 0 else "Image 2: AFTER input."})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(png).decode("ascii")}})
        content.append({"type": "text", "text": prompt + (
            "\nReturn your JSON inside the 'answer' field." if wrapped else "")})
        message = {"type": "user", "message": {"role": "user", "content": content}}
        argv = [
            cli, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose", "--json-schema", json.dumps(wire_schema), "--tools", "",
            "--strict-mcp-config", "--no-session-persistence",
            "--model", self.model_override or self.config.claude_model,
            "--effort", "low", "--system-prompt", system,
        ]
        started = time.monotonic()
        self.diagnostic("provider_request_started", {
            "method": "CLI", "route": "claude -p", "request_timeout_s": self.config.timeout,
            "model": self.model_override or self.config.claude_model, "images": len(images)})
        try:
            code, out, err = await self.runner(
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
        })
        if result is None:
            raise ProviderError("Claude Code returned no result. Check that you are logged in "
                                "(run claude, then /login).")
        if result.get("is_error") or code != 0:
            text = str(result.get("result") or "")[:200]
            if "login" in text.lower():
                raise ProviderError("Claude Code is not logged in. Run claude, then /login.")
            raise ProviderError("Claude Code request failed: " + (text or f"exit code {code}"))
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

    async def verify_action(self, *args, **kwargs):
        return await self._check(super().verify_action, *args, **kwargs)

    async def verify_completion(self, *args, **kwargs):
        return await self._check(super().verify_completion, *args, **kwargs)

    async def verify_drawing_target(self, *args, **kwargs):
        return await self._check(super().verify_drawing_target, *args, **kwargs)

    async def verify_draft_target(self, *args, **kwargs):
        return await self._check(super().verify_draft_target, *args, **kwargs)

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
