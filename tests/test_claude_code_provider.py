import asyncio
import json
from dataclasses import replace

import pytest

from monster.providers.base import ProviderError
from monster.providers.claude_code import ClaudeCodeProvider, provider_for, wrap_schema
from monster.providers.config import OllamaConfig
from monster.providers.decisions import DECISION_SCHEMA
from monster.providers.ollama import OllamaProvider


def config(**changes):
    base = OllamaConfig(mode="supervised", provider="claude_code", claude_code_confirmed=True)
    return replace(base, **changes)


class FakeCli:
    def __init__(self, answer, *, code=0, is_error=False, text=None):
        self.answer, self.code, self.is_error, self.text = answer, code, is_error, text
        self.calls = []

    async def __call__(self, argv, stdin, timeout):
        self.calls.append((argv, stdin, timeout))
        if argv[1:] == ["--version"]:
            return 0, b"2.1.325 (Claude Code)\n", b""
        result = {"type": "result", "subtype": "success", "is_error": self.is_error,
                  "result": self.text or json.dumps(self.answer), "structured_output": self.answer}
        lines = [json.dumps({"type": "system"}), json.dumps(result)]
        return self.code, "\n".join(lines).encode(), b""


DECISION = {"kind": "action", "summary": "Open Start", "action": {"op": "press", "key": "WIN"},
            "risk": "routine", "expected_result": "Start is open"}


def test_decide_asks_for_plain_json_in_one_call():
    cli = FakeCli(None, text="```json\n" + json.dumps(DECISION) + "\n```")
    provider = ClaudeCodeProvider(config(), cli="claude.exe", runner=cli)
    decision = asyncio.run(provider.decide("ignored", "Open Notepad", b"PNG", 100, 100))
    assert decision.kind == "action" and decision.action == {"op": "press", "key": "WIN"}
    assert len(cli.calls) == 1
    argv, stdin, _ = cli.calls[0]
    assert argv[:2] == ["claude.exe", "-p"] and "--strict-mcp-config" in argv
    assert "--json-schema" not in argv
    assert argv[argv.index("--tools") + 1] == "" and argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    content = json.loads(stdin)["message"]["content"]
    assert [block["type"] for block in content] == ["image", "text"]
    assert json.dumps(DECISION_SCHEMA, separators=(",", ":")) in content[-1]["text"]


def test_unparseable_answer_retries_once_with_the_schema_tool():
    class TwoStep(FakeCli):
        async def __call__(self, argv, stdin, timeout):
            self.text = "I think you should press WIN." if not self.calls else None
            self.answer = {"answer": DECISION} if self.calls else None
            return await super().__call__(argv, stdin, timeout)

    cli = TwoStep(None)
    provider = ClaudeCodeProvider(config(), cli="claude.exe", runner=cli)
    decision = asyncio.run(provider.decide("ignored", "Open Notepad", b"PNG", 100, 100))
    assert decision.action == {"op": "press", "key": "WIN"} and len(cli.calls) == 2
    retry = cli.calls[1][0]
    schema = json.loads(retry[retry.index("--json-schema") + 1])
    assert schema["properties"]["answer"] == DECISION_SCHEMA
    assert retry[retry.index("--tools") + 1] == ""


def test_verify_sends_before_and_after_images_with_labels():
    cli = FakeCli({"status": "succeeded", "evidence": "Start menu visible"})
    provider = ClaudeCodeProvider(config(), cli="claude.exe", runner=cli)
    result = asyncio.run(provider.verify_action(
        "m", "Open Notepad", {"op": "press", "key": "WIN"}, "Start opens", b"AFTER", 100, 100,
        before_image=b"BEFORE"))
    assert result.status == "succeeded"
    content = json.loads(cli.calls[0][1])["message"]["content"]
    assert [b["type"] for b in content] == ["text", "image", "text", "image", "text"]
    assert content[0]["text"].startswith("Image 1: BEFORE")
    assert cli.calls[0][0][cli.calls[0][0].index("--model") + 1] == "claude-haiku-5-5"


def test_parse_json_text():
    from monster.providers.claude_code import parse_json_text

    assert parse_json_text('{"a": 1}') == {"a": 1}
    assert parse_json_text('Sure:\n```json\n{"a": 2}\n```') == {"a": 2}
    assert parse_json_text("no json here") is None
    assert parse_json_text("[1, 2]") is None


def test_warm_pool_hands_out_a_started_process_and_prewarms_the_next():
    from monster.providers.claude_code import WarmPool

    class Proc:
        def __init__(self, n):
            self.n, self.killed = n, False

        def poll(self):
            return 0 if self.killed else None

        def kill(self):
            self.killed = True

        def communicate(self, *a, **k):
            return b"", b""

    spawned, now = [], [0.0]
    pool = WarmPool(spawn=lambda argv: spawned.append(Proc(len(spawned))) or spawned[-1],
                    clock=lambda: now[0])
    argv = ["claude", "-p", "x"]
    first = pool.take(argv)  # Cold: spawns one to use and one to keep ready.
    assert first.n == 0 and len(spawned) == 2
    second = pool.take(argv)  # Warm: gets the ready one and starts another.
    assert second.n == 1 and len(spawned) == 3
    now[0] = WarmPool.IDLE_S + 1
    third = pool.take(argv)  # The ready one went stale: killed, a fresh one used.
    assert spawned[2].killed and third.n == 3
    pool.close()
    assert all(p.killed for p in spawned[4:])


def test_login_and_cli_errors_are_reported_plainly():
    cli = FakeCli(None, code=1, is_error=True, text="Not logged in · Please run /login")
    provider = ClaudeCodeProvider(config(), cli="claude.exe", runner=cli)
    with pytest.raises(ProviderError, match="not logged in"):
        asyncio.run(provider.verify_completion("m", "task", b"PNG", 10, 10))
    missing = ClaudeCodeProvider(config(), cli=None, runner=cli)
    missing.cli = None
    import monster.providers.claude_code as module
    original = module.find_cli
    module.find_cli = lambda: None
    try:
        with pytest.raises(ProviderError, match="CLI not found"):
            asyncio.run(missing.verify_completion("m", "task", b"PNG", 10, 10))
    finally:
        module.find_cli = original


def test_screenshots_need_claude_consent():
    provider = ClaudeCodeProvider(config(claude_code_confirmed=False, mode="demo"),
                                  cli="claude.exe", runner=FakeCli({}))
    with pytest.raises(ProviderError, match="Confirm"):
        asyncio.run(provider.verify_completion("m", "task", b"PNG", 10, 10))


def test_probe_checks_version_and_vision():
    class ColorCli(FakeCli):
        async def __call__(self, argv, stdin, timeout):
            if argv[1:] != ["--version"]:
                # Answer with the colors the probe image was drawn with (left to right).
                self.answer = {"colors": probe_colors[0]}
            return await super().__call__(argv, stdin, timeout)

    import monster.providers.claude_code as module
    probe_colors = []
    original = module.probe_image
    module.probe_image = lambda colors: probe_colors.append(colors) or b"PNG"
    try:
        provider = ClaudeCodeProvider(config(), cli="claude.exe", runner=ColorCli(None))
        result = asyncio.run(provider.probe("ignored"))
    finally:
        module.probe_image = original
    assert result.model == "claude-sonnet-5-5" and result.server_version.startswith("2.1.325")


def test_provider_selection_and_config_validation():
    assert type(provider_for(config(), cli="x")) is ClaudeCodeProvider
    assert type(provider_for(OllamaConfig())) is OllamaProvider
    with pytest.raises(ValueError, match="Confirm that screenshots"):
        config(claude_code_confirmed=False).validate()
    with pytest.raises(ValueError, match="Claude model"):
        config(claude_model="gpt-4").validate()
    with pytest.raises(ValueError, match="provider"):
        config(provider="other").validate()
    config().validate()  # No Ollama model or local confirmation needed for Claude Code.


def test_settings_dialog_saves_claude_code_after_probe(qtbot):
    from monster.ui.provider_settings import ProviderSettings

    saved = []
    dialog = ProviderSettings(OllamaConfig(), provider_factory=lambda c: None)
    qtbot.addWidget(dialog)
    dialog.saved.connect(saved.append)
    dialog.provider.setCurrentIndex(dialog.provider.findData("claude_code"))
    assert not dialog.form.isRowVisible(dialog.url) and dialog.form.isRowVisible(dialog.claude_model)
    dialog.claude_consent.setChecked(True)
    dialog.mode.setCurrentIndex(dialog.mode.findData("supervised"))
    dialog.apply()
    assert not saved and "probe" in dialog.status.text()
    dialog.probed_config = dialog.check_key(dialog.value())  # As after a passed probe.
    dialog.apply()
    assert saved and saved[0].provider == "claude_code" and saved[0].claude_code_confirmed


def test_over_long_text_is_fitted_to_the_schema_instead_of_failing():
    long_evidence = "The page did not scroll. " * 40
    cli = FakeCli({"status": "failed", "evidence": long_evidence})
    provider = ClaudeCodeProvider(config(), cli="claude.exe", runner=cli)
    result = asyncio.run(provider.verify_action(
        "m", "Scroll", {"op": "scroll", "x": 5, "y": 5, "delta": -120}, "Page moves", b"A", 10, 10))
    assert result.status == "failed" and len(result.evidence) == 300 and len(cli.calls) == 1


def test_fit_to_schema_picks_the_matching_decision_variant():
    from monster.providers.claude_code import fit_to_schema

    report = {"kind": "complete", "summary": "y" * 1500}
    assert len(fit_to_schema(report, DECISION_SCHEMA)["summary"]) == 1200
    step = {"kind": "action", "summary": "x" * 500, "action": {"op": "press", "key": "WIN"}}
    assert len(fit_to_schema(step, DECISION_SCHEMA)["summary"]) == 300


def test_context_images_are_labelled_and_sent_before_the_screen():
    cli = FakeCli({"status": "succeeded", "evidence": "ok"})
    provider = ClaudeCodeProvider(config(), cli="claude.exe", runner=cli)
    jpeg = bytes([255, 216, 255, 224]) + b"rest"
    asyncio.run(provider.chat("m", "Check", b"SCREEN", {"type": "object"},
                              extra_images=[("architecture.jpg", jpeg)]))
    content = json.loads(cli.calls[0][1])["message"]["content"]
    assert content[0] == {"type": "text", "text": "Context image: architecture.jpg"}
    assert content[1]["source"]["media_type"] == "image/jpeg"
    assert content[2] == {"type": "text", "text": "Current screen:"}
