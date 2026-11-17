"""Orchestrator: plan -> subtasks on the step loop -> review/re-plan -> one final answer."""

from dataclasses import replace

from test_supervised import controller, observation

from monster.agent.protected import protected_target
from monster.agent.validation import prepare_action
from monster.providers.base import Decision, Verification
from monster.providers.decisions import parse_decision


def click(x=20, summary="Click"):
    return Decision("action", summary, {"op": "click", "x": x, "y": 20}, risk="routine",
                    expected_result="It worked")


def step(text, done="visible"):
    return {"instruction": text, "done_when": done}


def planned(decisions, plan, assessments, *, hands_free=True):
    engine, bridge, model = controller(decisions)
    engine.config = replace(engine.config, orchestrate=True, hands_free=hands_free,
                            approve_each_step=False)
    calls = {"plan": 0, "assess": [], "completion": 0}

    async def make_plan(*args):
        calls["plan"] += 1
        return plan

    async def assess(model_name, goal, plan_now, results, *args):
        calls["assess"].append(results)
        return assessments.pop(0)

    async def completion(*args):
        calls["completion"] += 1
        return Verification("succeeded", "Step visibly done")

    model.plan, model.assess, model.verify_completion = make_plan, assess, completion
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    return engine, bridge, model, calls


def test_plan_runs_each_subtask_then_reports_final_answer(qtbot):
    engine, bridge, _, calls = planned(
        [click(10), Decision("complete", "Chrome open"), click(30), Decision("complete", "News read")],
        [step("Open Chrome"), step("Read the headlines")],
        [{"decision": "done", "summary": "Top story: X", "subtasks": []}])
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("check the news")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        assert [a[0]["x"] for a in bridge.executed] == [-90, -70]
        assert calls["plan"] == 1 and calls["completion"] == 2
        assert len(calls["assess"]) == 1  # Only after the last subtask, not between successes.
        assert engine.completion_summary == "Top story: X" and "Top story: X" in finished[0]
    finally:
        engine.close()


def test_failed_subtask_is_replanned_with_a_different_approach(qtbot):
    engine, bridge, _, calls = planned(
        [Decision("unable", "No browser visible"), click(40), Decision("complete", "Chrome open")],
        [step("Click the Chrome icon")],
        [{"decision": "revise", "summary": "Use Start instead",
          "subtasks": [step("Press WIN, type Chrome, press ENTER")]},
         {"decision": "done", "summary": "Chrome is open", "subtasks": []}])
    finished, failed = [], []
    engine.finished.connect(finished.append)
    engine.failed.connect(failed.append)
    try:
        engine.submit("open chrome")
        qtbot.waitUntil(lambda: bool(finished or failed), timeout=6000)
        assert finished and not failed
        first_review = calls["assess"][0]
        assert first_review[0]["succeeded"] is False and "No browser" in first_review[0]["report"]
        assert engine.plan_revisions == 1 and len(bridge.executed) == 1
    finally:
        engine.close()


def test_revisions_are_bounded(qtbot):
    revise = {"decision": "revise", "summary": "Try again differently", "subtasks": [step("Other")]}
    engine, bridge, _, calls = planned(
        [Decision("unable", "no")] * 4, [step("First")], [dict(revise) for _ in range(4)])
    failed = []
    engine.failed.connect(failed.append)
    try:
        engine.submit("impossible task")
        qtbot.waitUntil(lambda: bool(failed), timeout=6000)
        assert "revised 2 times" in failed[0] and not bridge.executed
        assert len(calls["assess"]) == 3
    finally:
        engine.close()


def test_unsafe_failure_ends_the_whole_task_without_replanning(qtbot):
    typing = Decision("action", "Type", {"op": "text", "text": "hello"}, risk="routine",
                      expected_result="hello visible")
    engine, bridge, model, calls = planned([typing], [step("Type hello in Notepad")], [])

    async def verify(*args):
        return Verification("failed", "Text not visible")

    model.verify_action = verify
    failed = []
    engine.failed.connect(failed.append)
    try:
        engine.submit("type hello in notepad")
        qtbot.waitUntil(lambda: bool(failed), timeout=6000)
        assert calls["assess"] == [] and len(bridge.executed) == 1
    finally:
        engine.close()


def test_laptop_approval_reviews_plan_and_final_result_once(qtbot):
    engine, bridge, _, calls = planned(
        [Decision("complete", "A done"), Decision("complete", "B done")],
        [step("Step A"), step("Step B")],
        [{"decision": "done", "summary": "All done", "subtasks": []}], hands_free=False)
    questions, finished = [], []
    engine.approval_requested.connect(lambda rid, text: questions.append((rid, text)))
    engine.finished.connect(finished.append)
    try:
        engine.submit("do A then B")
        qtbot.waitUntil(lambda: engine.phase == "plan_review", timeout=5000)
        assert "1. Step A" in questions[-1][1] and "2. Step B" in questions[-1][1]
        engine.respond(questions[-1][0], "approve")
        qtbot.waitUntil(lambda: engine.phase == "final_confirming", timeout=5000)
        assert len(questions) == 2 and "All done" in questions[-1][1]  # No per-subtask review.
        engine.respond(questions[-1][0], "approve")
        qtbot.waitUntil(lambda: bool(finished), timeout=5000)
        assert engine.human_result_confirmed
    finally:
        engine.close()


def test_protected_window_input_is_refused_before_dispatch(qtbot):
    engine, bridge, model = controller([click(10), Decision("unable", "stop")])
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True)
    engine.window_guard = lambda action, foreground, task: "Visual Studio Code"
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    failed = []
    engine.failed.connect(failed.append)
    try:
        engine.submit("open linkedin")
        qtbot.waitUntil(lambda: bool(failed), timeout=5000)
        assert not bridge.executed
        assert any("controls Monster" in line for line in model.history[-1])
    finally:
        engine.close()


def test_protected_target_rules():
    names = {1: "code.exe", 2: "chrome.exe"}
    common = dict(window_at=lambda x, y: 1 if x < 100 else 2, process_name=names.get)
    assert protected_target({"op": "click", "x": 5, "y": 5}, 2, "open linkedin", **common) == "Visual Studio Code"
    assert protected_target({"op": "click", "x": 500, "y": 5}, 1, "open linkedin", **common) is None
    assert protected_target({"op": "press", "key": "WIN"}, 1, "open linkedin", **common) is None
    assert protected_target({"op": "text", "text": "x"}, 1, "open linkedin", **common) == "Visual Studio Code"
    assert protected_target({"op": "text", "text": "x"}, 1, "type x in VS Code", **common) is None


def test_planner_drag_path_maps_to_pixels_and_physical_coordinates():
    decision = parse_decision({
        "kind": "action", "summary": "Draw head", "risk": "routine", "expected_result": "circle",
        "action": {"op": "drag", "x_1000": 500, "y_1000": 400, "end_x_1000": 500, "end_y_1000": 400,
                   "button": "left", "duration": 2.0, "path_1000": [[600, 500], [400, 500]]}},
        101, 101, coordinate_space="normalized_1000")
    assert decision.action["path"] == [[60, 50], [40, 50]]
    physical = prepare_action(decision.action, observation())
    assert physical["path"] == [[-40, 50], [-60, 50]] and physical["duration"] == 1.5


def test_no_approval_uncertain_click_recovers_instead_of_stopping(qtbot):
    engine, bridge, model = controller([click(10), click(60), Decision("unable", "done trying")])
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True)

    async def verify(*args):
        return Verification("uncertain", "No visible change")

    model.verify_action = verify
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    failed = []
    engine.failed.connect(failed.append)
    try:
        engine.submit("open the settings page")
        qtbot.waitUntil(lambda: bool(failed), timeout=6000)
        assert len(bridge.executed) == 2  # Kept going after the first uncertain step.
        assert "Visual verification uncertain" not in failed[0]
    finally:
        engine.close()


def test_planned_launch_steps_are_recognised_as_app_search():
    from monster.agent.progress import application_search_query

    step_text = "Press WIN, type Paint, press ENTER to open the Paint app. If needed, create a canvas."
    assert application_search_query(step_text, {"op": "text", "text": "Paint"}) == "Paint"
    assert application_search_query(step_text, {"op": "text", "text": "Notepad"}) is None
    assert application_search_query("Type Paint into the open Notepad document",
                                    {"op": "text", "text": "Paint"}) is None


def test_window_opened_by_a_click_during_relook_is_verified_not_fatal(qtbot):
    engine, bridge, model = controller([click(10), Decision("unable", "stop here")])
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True)
    verdicts = iter(["failed", "succeeded"])

    async def verify(*args):
        return Verification(next(verdicts, "succeeded"), "New window")

    model.verify_action = verify
    frames = iter([observation(), observation(), observation(), observation(foreground=88)])
    engine.capture_requested.connect(
        lambda: engine.observation_ready(next(frames, observation(foreground=88)), engine.generation))
    failed = []
    engine.failed.connect(failed.append)
    try:
        engine.submit("open the settings window")
        qtbot.waitUntil(lambda: bool(failed), timeout=5000)
        assert "Verification target or display changed" not in failed[0]
        assert engine.verification_calls == 2
    finally:
        engine.close()


def test_drag_subtask_of_a_drawing_goal_needs_no_review(qtbot):
    oval = Decision("action", "Drag oval", {"op": "drag", "x": 30, "y": 30, "end_x": 60, "end_y": 60,
                                            "button": "left", "duration": 0.3},
                    risk="routine", expected_result="Oval drawn")
    engine, bridge, model, calls = planned(
        [oval, Decision("complete", "Snout drawn")],
        [step("In Paint, select the Ellipse tool and drag a small oval for the snout")],
        [{"decision": "done", "summary": "Pig drawn", "subtasks": []}])

    async def target(*args):
        from monster.providers.base import DraftTargetCheck
        return DraftTargetCheck("ready", "Canvas ready")

    model.verify_drawing_target = target
    finished, failed = [], []
    engine.finished.connect(finished.append)
    engine.failed.connect(failed.append)
    try:
        engine.submit("open painter app and draw simple pig")
        qtbot.waitUntil(lambda: bool(finished or failed), timeout=6000)
        assert finished and len(bridge.executed) == 1
    finally:
        engine.close()


def test_launching_an_app_by_name_is_not_drafting_a_message():
    from monster.agent.progress import draft_only

    assert not draft_only("Switch to the already open Slack window by clicking its icon in the "
                          "taskbar (if it isn't visible there, press WIN, type Slack, press ENTER).")
    assert not draft_only("Type Slack into Start search and open it")
    assert draft_only("Click the message input box in Slack and type exactly: hello")
    assert draft_only("In Slack, type hello team, press ENTER")
    assert draft_only("Press WIN, type Microsoft Teams, press ENTER, then type hello in the chat")
