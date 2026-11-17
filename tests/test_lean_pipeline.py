"""Lean pipeline: the orchestrator gives one subtask at a time and is the independent check;
the executor makes one model call per step that also judges the previous step."""

from dataclasses import replace

from test_supervised import controller, observation

from monster.providers.base import Decision, Verification
from monster.providers.config import load


def ok(evidence="Done"):
    return {"status": "succeeded", "evidence": evidence}


def click(x, previous=None, summary="Click"):
    return Decision("action", summary, {"op": "click", "x": x, "y": 20}, risk="routine",
                    expected_result="It worked", previous_step=previous)


def verdict(status, evidence="seen", answer="", subtask="", criterion=""):
    return {"success_criteria": "Calculator shows 408", "status": status, "evidence": evidence,
            "answer": answer, "next_subtask": subtask, "success_criterion": criterion}


def lean(decisions, verdicts, **changes):
    engine, bridge, model = controller(decisions)
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True,
                            pipeline="lean", **changes)
    calls = {"orchestrate": [], "verify": 0, "completion": 0}

    async def next_subtask(model_name, goal, criteria, history, *args, **kwargs):
        calls["orchestrate"].append((criteria, history))
        return verdicts.pop(0)

    async def verify(*args):
        calls["verify"] += 1
        return Verification("succeeded", "independent")

    async def completion(*args):
        calls["completion"] += 1
        return Verification("succeeded", "independent")

    model.next_subtask, model.verify_action, model.verify_completion = next_subtask, verify, completion
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    return engine, bridge, model, calls


def test_one_model_call_per_step_and_the_orchestrator_checks_each_subtask(qtbot):
    engine, bridge, _, calls = lean(
        [click(10), click(30, previous=ok("Menu open")),
         Decision("complete", "Result 408 visible", previous_step=ok("408 shown"))],
        [verdict("next", subtask="Compute 12*34 in Calculator", criterion="408 visible"),
         verdict("done", evidence="408 visible", answer="12 x 34 = 408")])
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("calculate 12 times 34")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        assert [a[0]["x"] for a in bridge.executed] == [-90, -70]
        assert calls["verify"] == 0 and calls["completion"] == 0  # No separate checks at all.
        assert len(calls["orchestrate"]) == 2
        criteria, history = calls["orchestrate"][1]
        assert criteria == "Calculator shows 408"  # Fixed after the first call.
        assert history[0]["succeeded"] and history[0]["success_criterion"] == "408 visible"
        assert engine.completion_summary == "12 x 34 = 408" and "408" in finished[0]
    finally:
        engine.close()


def test_failed_step_is_noted_without_an_extra_check_and_work_continues(qtbot):
    engine, bridge, model, calls = lean(
        [click(10), click(60, previous={"status": "failed", "evidence": "Nothing changed"}),
         Decision("complete", "Done", previous_step=ok())],
        [verdict("next", subtask="Open the menu", criterion="Menu visible"),
         verdict("done", answer="Menu open")])
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("open the menu")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        assert calls["verify"] == 0 and len(bridge.executed) == 2
        assert any("visual verification: failed" in line for line in model.history[-1])
    finally:
        engine.close()


def test_failed_subtask_returns_to_the_orchestrator_for_a_different_one(qtbot):
    engine, bridge, _, calls = lean(
        [Decision("unable", "No browser visible"), click(40), Decision("complete", "Open",
         previous_step=ok())],
        [verdict("next", subtask="Click the Chrome icon", criterion="Chrome in front"),
         verdict("next", subtask="Press WIN, type Chrome, press ENTER", criterion="Chrome in front"),
         verdict("done", answer="Chrome is open")])
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("open chrome")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        history = calls["orchestrate"][1][1]
        assert history[0]["succeeded"] is False and "No browser" in history[0]["report"]
        assert len(bridge.executed) == 1
    finally:
        engine.close()


def test_repeated_subtask_failures_stop_the_task(qtbot):
    engine, bridge, _, calls = lean(
        [Decision("unable", "no")] * 4,
        [verdict("next", subtask=f"Try {i}", criterion="x") for i in range(4)] + [verdict("next", subtask="again", criterion="x")])
    failed = []
    engine.failed.connect(failed.append)
    try:
        engine.submit("impossible")
        qtbot.waitUntil(lambda: bool(failed), timeout=6000)
        assert "subtasks all failed" in failed[0] and not bridge.executed
    finally:
        engine.close()


def test_screen_is_watched_until_it_settles_before_planning(qtbot):
    engine, bridge, model, _ = lean(
        [click(10), Decision("complete", "Done", previous_step=ok())],
        [verdict("next", subtask="Click", criterion="done"), verdict("done", answer="ok")])
    # Orchestrator, plan and pre-input recheck see a white screen; after the click the screen
    # changes (black), then stays grey for two frames, which counts as settled.
    frames = iter([observation(), observation(), observation(), observation("#000000"),
                   observation("#888888"), observation("#888888")])
    phases = []
    engine.capture_requested.disconnect()
    engine.capture_requested.connect(lambda: phases.append(engine.phase) or engine.observation_ready(
        next(frames, observation("#888888")), engine.generation))
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("click it")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        # After the click: changing frame, then two matching frames -> plan.
        assert phases.count("settling") >= 2
    finally:
        engine.close()


def test_saved_settings_default_to_the_lean_pipeline(tmp_path):
    assert load(tmp_path / "missing.json").pipeline == "lean"
