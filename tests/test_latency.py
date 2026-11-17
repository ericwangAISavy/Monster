"""Latency work: merged step checks, cached canvas checks and a faster check model."""

import asyncio
import json
from dataclasses import replace

from test_supervised import controller, observation

from monster.providers.base import Decision, DraftTargetCheck, Verification
from monster.providers.claude_code import ClaudeCodeProvider
from monster.providers.config import OllamaConfig
from monster.providers.decisions import parse_decision


def click(x=20, previous=None):
    return Decision("action", "Click", {"op": "click", "x": x, "y": 20}, risk="routine",
                    expected_result="Menu opens", previous_step=previous)


def fast(decisions, **changes):
    engine, bridge, model = controller(decisions)
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True,
                            merged_checks=True, **changes)
    checks, prompts = [], []
    original = model.decide

    async def decide(*args, history=(), previous_step=None):
        prompts.append(previous_step)
        return await original(*args, history=history)

    async def verify(*args):
        checks.append(args)
        return Verification("succeeded", "Independent check passed")

    async def completion(*args):
        return Verification("succeeded", "Done")

    model.decide, model.verify_action, model.verify_completion = decide, verify, completion
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    return engine, bridge, model, checks, prompts


def test_planner_check_replaces_the_separate_check_for_routine_steps(qtbot):
    ok = {"status": "succeeded", "evidence": "Menu is open"}
    engine, bridge, _, checks, prompts = fast(
        [click(10), click(30, previous=ok), Decision("complete", "Done", previous_step=ok)])
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("open the view menu and the zoom menu")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        assert len(bridge.executed) == 2 and checks == []  # No separate step checks.
        assert prompts[0] is None and prompts[1]["action"]["op"] == "click"
        assert any("succeeded. Evidence: Menu is open" in line for line in engine.history)
    finally:
        engine.close()


def test_inconclusive_planner_check_falls_back_to_independent_check(qtbot):
    unsure = {"status": "uncertain", "evidence": "Cannot tell"}
    engine, bridge, model, checks, _ = fast(
        [click(10), click(99, previous=unsure), Decision("complete", "Done")])
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("open the view menu")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        assert len(checks) == 1  # The independent check ran for the first click.
        assert [a[0]["x"] for a in bridge.executed] == [-90]  # Dropped proposal never ran.
    finally:
        engine.close()


def test_typing_keeps_the_independent_check(qtbot):
    typing = Decision("action", "Type", {"op": "text", "text": "hi"}, risk="routine",
                      expected_result="hi visible")
    engine, bridge, _, checks, prompts = fast([typing, Decision("complete", "Done")])
    engine.finished.connect(lambda *_: None)
    try:
        engine.submit("search for hi")
        qtbot.waitUntil(lambda: engine.phase in {"idle", "closing"} or len(checks) == 1, timeout=6000)
        assert len(checks) == 1 and all(p is None for p in prompts)
    finally:
        engine.close()


def stroke(x0, x1):
    return Decision("action", "Stroke", {"op": "drag", "x": x0, "y": 50, "end_x": x1, "end_y": 50,
                                         "button": "left", "duration": 0.3},
                    risk="routine", expected_result="Line drawn",
                    previous_step={"status": "succeeded", "evidence": "Line visible"})


def test_canvas_check_is_reused_for_strokes_inside_the_checked_canvas(qtbot):
    engine, bridge, model, _, _ = fast(
        [stroke(30, 60), stroke(35, 65), stroke(5, 95), Decision("complete", "Drawn",
         previous_step={"status": "succeeded", "evidence": "ok"})])
    targets = []

    async def target(*args):
        targets.append(args)
        return DraftTargetCheck("ready", "Canvas ready", (20, 20, 80, 80))

    model.verify_drawing_target = target
    frames = lambda: engine.observation_ready(
        replace(observation(), foreground_rect=observation().bounds), engine.generation)
    engine.capture_requested.disconnect()
    engine.capture_requested.connect(frames)
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("draw three lines in the open Paint canvas")
        qtbot.waitUntil(lambda: bool(finished), timeout=8000)
        assert len(bridge.executed) == 3
        assert len(targets) == 2  # Second stroke reused the check; the third left the box.
    finally:
        engine.close()


def test_claude_checks_use_the_check_model():
    calls = []

    async def runner(argv, stdin, timeout):
        calls.append(argv[argv.index("--model") + 1])
        result = {"type": "result", "subtype": "success", "is_error": False,
                  "result": json.dumps({"status": "succeeded", "evidence": "ok"})}
        return 0, json.dumps(result).encode(), b""

    config = OllamaConfig(mode="supervised", provider="claude_code", claude_code_confirmed=True)
    provider = ClaudeCodeProvider(config, cli="claude.exe", runner=runner)
    asyncio.run(provider.verify_completion("m", "task", b"PNG", 10, 10))
    assert calls == ["claude-haiku-5-5"] and provider.model_override is None


def test_previous_step_is_parsed_and_malformed_ones_are_ignored():
    base = {"kind": "complete", "summary": "Done"}
    good = parse_decision({**base, "previous_step": {"status": "succeeded", "evidence": "ok"}}, 10, 10)
    bad = parse_decision({**base, "previous_step": {"status": "great", "evidence": "ok"}}, 10, 10)
    assert good.previous_step == {"status": "succeeded", "evidence": "ok"}
    assert bad.previous_step is None


def test_uncertain_risk_in_no_approval_is_replanned_by_the_orchestrator(qtbot):
    risky = Decision("action", "Maybe click", {"op": "click", "x": 20, "y": 20}, risk="uncertain",
                     expected_result="?")
    engine, bridge, model = controller([risky, Decision("complete", "Done")])
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True, orchestrate=True)
    reviews = []

    async def make_plan(*args):
        return [{"instruction": "Click the thing", "done_when": "visible"}]

    async def assess(*args):
        reviews.append(args)
        return {"decision": "stop", "summary": "Needs the user", "subtasks": []}

    model.plan, model.assess = make_plan, assess
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    failed = []
    engine.failed.connect(failed.append)
    try:
        engine.submit("click the thing")
        qtbot.waitUntil(lambda: bool(failed), timeout=6000)
        assert len(reviews) == 1 and not bridge.executed
    finally:
        engine.close()


def test_long_final_reports_are_kept_and_long_step_labels_are_trimmed():
    report = "Headlines: " + "story; " * 120
    assert parse_decision({"kind": "complete", "summary": report}, 10, 10).summary == report.strip()
    step = parse_decision({"kind": "action", "summary": "Click " * 100, "risk": "routine",
                           "action": {"op": "press", "key": "WIN"}, "expected_result": "Start"},
                          10, 10)
    assert len(step.summary) == 300 and step.summary.endswith("…")
