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


def launch_batch(**extra):
    return Decision("action", "Open Paint", {"op": "press", "key": "WIN"}, risk="routine",
                    expected_result="Paint window is open",
                    then=({"op": "text", "text": "Paint"}, {"op": "press", "key": "ENTER"}), **extra)


def test_batch_runs_follow_up_keys_with_one_check_at_the_end(qtbot):
    engine, bridge, model = controller([launch_batch(), Decision("complete", "Paint open")])
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True)
    checks, frames = [], []

    async def verify(*args):
        checks.append(args[2])
        return Verification("succeeded", "Paint window open")

    async def completion(*args):
        return Verification("succeeded", "Paint open")

    model.verify_action, model.verify_completion = verify, completion
    engine.capture_requested.connect(
        lambda: frames.append(engine.phase) or engine.observation_ready(observation(), engine.generation))
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("Open Paint")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        assert [a[0] for a in bridge.executed] == [
            {"op": "press", "key": "WIN"}, {"op": "text", "text": "Paint"}, {"op": "press", "key": "ENTER"}]
        assert frames.count("batch_capture") == 2  # A fresh local look before each follow-up.
        assert checks == [{"op": "press", "key": "ENTER"}]  # One model check, for the launch.
        assert engine.launch_query_pending is None  # The batch went through the launch logic.
    finally:
        engine.close()


def test_batch_follow_up_into_a_protected_app_is_refused(qtbot):
    engine, bridge, model = controller([launch_batch(), Decision("unable", "stop")])
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True)
    engine.window_guard = lambda action, fg, task: "Visual Studio Code" if action["op"] == "text" else None
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    failed = []
    engine.failed.connect(failed.append)
    try:
        engine.submit("Open Paint")
        qtbot.waitUntil(lambda: bool(failed), timeout=6000)
        assert [a[0] for a in bridge.executed] == [{"op": "press", "key": "WIN"}]
        assert engine.batch == []
    finally:
        engine.close()


def test_batches_are_not_used_with_per_step_approval(qtbot):
    engine, bridge, model = controller([launch_batch()])
    engine.config = replace(engine.config, approve_each_step=True, hands_free=False)
    questions = []
    engine.approval_requested.connect(lambda rid, text: questions.append(rid))
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    try:
        engine.submit("Open Paint")
        qtbot.waitUntil(lambda: bool(questions), timeout=5000)
        assert engine.batch == []
    finally:
        engine.close()


def test_follow_ups_are_parsed_and_bad_ones_dropped():
    from monster.providers.decisions import decision_schema_for_draft

    base = {"kind": "action", "summary": "Open Start", "risk": "routine", "expected_result": "Paint",
            "action": {"op": "press", "key": "WIN"}}
    good = parse_decision({**base, "then": [{"op": "text", "text": "Paint"},
                                            {"op": "press", "key": "ENTER"}]}, 100, 100)
    assert good.then == ({"op": "text", "text": "Paint"}, {"op": "press", "key": "ENTER"})
    clicky = parse_decision({**base, "then": [{"op": "click", "x": 1, "y": 1}]}, 100, 100)
    assert clicky.then == () and clicky.action == {"op": "press", "key": "WIN"}
    assert "then" not in decision_schema_for_draft()["oneOf"][0]["properties"]


def test_screenshots_are_shrunk_and_pixel_coordinates_scaled(qapp):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    from monster.providers.claude_code import image_scale, scale_action, shrink_image

    image = QImage(1920, 1080, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    small, media = shrink_image(bytes(data))
    assert media == "image/jpeg" and QImage.fromData(small).width() == 1280
    assert shrink_image(b"not a png") == (b"not a png", "image/png")
    s = image_scale(1920, 1080)
    assert scale_action({"op": "drag", "x": 960, "y": 540, "end_x": 1920, "end_y": 0,
                         "path": [[300, 300]]}, s) == {
        "op": "drag", "x": 640, "y": 360, "end_x": 1280, "end_y": 0, "path": [[200, 200]]}


def test_canvas_box_from_a_scaled_check_is_mapped_back():
    from monster.providers.base import DraftTargetCheck

    seen = []

    async def runner(argv, stdin, timeout):
        seen.append(stdin)
        answer = {"status": "ready", "evidence": "ok", "canvas_box": {"x0": 100, "y0": 100, "x1": 600, "y1": 400}}
        result = {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(answer)}
        return 0, json.dumps(result).encode(), b""

    config = OllamaConfig(mode="supervised", provider="claude_code", claude_code_confirmed=True)
    provider = ClaudeCodeProvider(config, cli="claude.exe", runner=runner)
    action = {"op": "drag", "x": 960, "y": 540, "end_x": 1200, "end_y": 600, "button": "left", "duration": 0.5}
    result = asyncio.run(provider.verify_drawing_target("m", "draw", action, b"PNG", 1920, 1080))
    assert isinstance(result, DraftTargetCheck) and result.region == (150, 150, 900, 600)
    prompt = json.loads(seen[0])["message"]["content"][-1]["text"]
    assert '"x": 640' in prompt and "1280x720" in prompt


def test_decide_states_the_sent_image_size_and_maps_coordinates_back():
    seen = []

    async def runner(argv, stdin, timeout):
        seen.append(json.loads(stdin)["message"]["content"][-1]["text"])
        answer = {"kind": "action", "summary": "Click the 1 key", "risk": "routine",
                  "expected_result": "1 shown", "action": {"op": "click", "x_1000": 500, "y_1000": 729}}
        result = {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(answer)}
        return 0, json.dumps(result).encode(), b""

    config = OllamaConfig(mode="supervised", provider="claude_code", claude_code_confirmed=True)
    provider = ClaudeCodeProvider(config, cli="claude.exe", runner=runner)
    decision = asyncio.run(provider.decide("m", "calculate", b"PNG", 1920, 1080))
    assert "Image size: 1280x720" in seen[0]
    # 0-1000 coordinates are converted with the sent size, then mapped to full-resolution pixels.
    assert decision.action["x"] in (959, 960) and decision.action["y"] in (785, 786)


def test_follow_ups_are_not_held_to_the_first_actions_description(qtbot):
    win = Decision("action", "Press the Windows key to open Start, then type Slack and press Enter",
                   {"op": "press", "key": "WIN"}, risk="routine",
                   expected_result="The Slack window is in front",
                   then=({"op": "text", "text": "Slack"}, {"op": "press", "key": "ENTER"}))
    engine, bridge, model = controller([win, Decision("complete", "Slack in front")])
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True)

    async def completion(*args):
        return Verification("succeeded", "Slack in front")

    model.verify_completion = completion
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    finished, failed = [], []
    engine.finished.connect(finished.append)
    engine.failed.connect(failed.append)
    try:
        engine.submit("Switch to Slack by pressing WIN, typing Slack and pressing ENTER")
        qtbot.waitUntil(lambda: bool(finished or failed), timeout=6000)
        assert not failed and len(bridge.executed) == 3
    finally:
        engine.close()


def test_previous_step_and_history_use_the_sent_image_coordinates():
    seen = []

    async def runner(argv, stdin, timeout):
        seen.append(json.loads(stdin)["message"]["content"][-1]["text"])
        answer = {"kind": "complete", "summary": "done",
                  "previous_step": {"status": "succeeded", "evidence": "ok"}}
        result = {"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(answer)}
        return 0, json.dumps(result).encode(), b""

    config = OllamaConfig(mode="supervised", provider="claude_code", claude_code_confirmed=True)
    provider = ClaudeCodeProvider(config, cli="claude.exe", runner=runner)
    history = ['Step 1: input inserted. Action: {"op": "click", "x": 1500, "y": 900}.']
    asyncio.run(provider.decide("m", "task", b"PNG", 1920, 1080, history=history,
                                previous_step={"action": {"op": "click", "x": 1500, "y": 900},
                                               "expected_result": "menu"}))
    prompt = seen[0]
    assert '"x": 1000' in prompt and '"y": 600' in prompt and '"x": 1500' not in prompt
