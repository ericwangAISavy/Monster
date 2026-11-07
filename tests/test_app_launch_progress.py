"""Start search is intermediate navigation, not the user's final text deliverable."""

from dataclasses import replace

import pytest
from test_supervised import controller, observation

from monster.agent.progress import application_search_query
from monster.providers.base import Decision, Verification


def win():
    return Decision(
        "action",
        "Open Start",
        {"op": "press", "key": "WIN"},
        risk="routine",
        expected_result="Windows Start is open.",
    )


def query(text="Paint"):
    return Decision(
        "action",
        f"Type '{text}' into Windows Start search",
        {"op": "text", "text": text},
        risk="routine",
        expected_result=f"{text} appears as the best app match.",
    )


def enter():
    return Decision(
        "action",
        "Launch the selected Paint app",
        {"op": "press", "key": "ENTER"},
        risk="routine",
        expected_result="Paint opens.",
    )


@pytest.mark.parametrize(
    "task,text",
    [
        ("open painter and draw simple links", "Paint"),
        ("Open Notepad", "notepad"),
        ("Please launch the Paint app and draw a line", "Paint"),
        ("start Calculator then calculate", "Calculator"),
        ("run Notepad", "Notepad"),
    ],
)
def test_explicit_launch_query_detection(task, text):
    assert application_search_query(task, {"op": "text", "text": text}) == text


@pytest.mark.parametrize(
    "task,action",
    [
        ("Type Paint into the open Notepad document", {"op": "text", "text": "Paint"}),
        ("Type hello in current Slack chat, do not send", {"op": "text", "text": "Slack"}),
        ("open painter and draw a line", {"op": "text", "text": "unrelated content"}),
        ("Open Paint", {"op": "press", "key": "ENTER"}),
        ("Prepare a note about Paint", {"op": "text", "text": "Paint"}),
    ],
)
def test_query_classification_never_reclassifies_final_draft_or_arbitrary_typing(task, action):
    assert application_search_query(task, action) is None


def test_report_sequence_query_review_continues_to_enter_instead_of_completing(qtbot):
    engine, bridge, model = controller(
        [
            win(),
            Decision(
                "action",
                "Type 'Paint' in search",
                {"op": "click", "x": 40, "y": 20},
                risk="routine",
                expected_result="Search field focused",
            ),
            query(),
            Decision("complete", "Paint search result is present"),  # must be rejected
            enter(),
            Decision("complete", "App open; inspect original task"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    checks, questions, finished = [], [], []

    async def verify(*args):
        checks.append(args)
        return Verification(
            "succeeded" if args[2] == {"op": "press", "key": "ENTER"} else "uncertain",
            "Actual Paint window visible"
            if args[2].get("key") == "ENTER"
            else "State is visible but the model is unsure about causality",
        )

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.approval_requested.connect(lambda rid, text: questions.append((rid, text)))
    engine.finished.connect(finished.append)
    try:
        engine.submit("open painter and draw simple links")
        qtbot.waitUntil(lambda: engine.phase == "launch_query_review", timeout=7000)
        assert len(bridge.executed) == 2
        assert "intermediate" in questions[-1][1] and "not task completion" in questions[-1][1]
        assert not finished and not engine.human_result_confirmed
        old = engine.question_id
        engine.respond("stale-review", "approve")
        assert engine.phase == "launch_query_review"
        engine.respond(old, "approve")
        qtbot.waitUntil(lambda: engine.phase == "confirming", timeout=7000)
        assert [e[0] for e in bridge.executed] == [
            {"op": "press", "key": "WIN"},
            {"op": "text", "text": "Paint"},
            {"op": "press", "key": "ENTER"},
        ]
        assert engine.launch_query_pending is None
        assert not engine.human_result_confirmed and not finished
        assert len(questions) == 2  # query step review, then distinct whole-task review
        assert "ENTIRE task is complete" not in questions[0][1]
        assert "draw simple links" in questions[-1][1]
        launch_check = next(args for args in checks if args[2].get("key") == "ENTER")
        assert "own usable foreground window" in launch_check[3]
        assert any("App search is not completion" in item for item in engine.history)
        assert any("NOT app launch or task completion" in item for item in engine.history)
    finally:
        engine.close()


def test_verified_query_cannot_complete_before_grounded_open_click(qtbot):
    engine, bridge, model = controller(
        [
            query("Notepad"),
            Decision("complete", "Search is done"),
            Decision(
                "action",
                "Click the visible Notepad Open control",
                {"op": "click", "x": 50, "y": 40},
                risk="routine",
                expected_result="App opens",
            ),
            Decision("complete", "Notepad window visible"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    checks = []

    async def verify(*args):
        checks.append(args)
        return Verification("succeeded", "Synthetic visible state verified")

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Open Notepad")
        qtbot.waitUntil(lambda: engine.phase == "confirming", timeout=6000)
        assert [a[0]["op"] for a in bridge.executed] == ["text", "click"]
        assert "own usable foreground window" in checks[-1][3]
        assert engine.launch_query_pending is None
        assert engine.rejected_proposals == 0  # reset only after actual activation verifies
        assert any("App search is not completion" in item for item in engine.history)
    finally:
        engine.close()


def test_declined_search_review_never_dispatches_enter(qtbot):
    engine, bridge, model = controller([query(), enter()])
    engine.config = replace(engine.config, approve_each_step=False)

    async def verify(*args):
        return Verification("uncertain", "Result unclear")

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Open painter and draw a line")
        qtbot.waitUntil(lambda: engine.phase == "launch_query_review", timeout=5000)
        engine.respond(engine.question_id, "reject")
        qtbot.waitUntil(lambda: engine.phase == "idle")
        assert [a[0] for a in bridge.executed] == [{"op": "text", "text": "Paint"}]
        assert not engine.human_result_confirmed
    finally:
        engine.close()


def test_failed_activation_remains_incomplete_and_query_cannot_be_retyped(qtbot):
    engine, bridge, model = controller(
        [
            query(),
            enter(),
            Decision("complete", "Only the search panel is present"),
            query(),
            Decision("unable", "Cannot establish an app window"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    failures = []

    async def verify(*args):
        return Verification(
            "failed" if args[2].get("key") == "ENTER" else "succeeded",
            "App window absent" if args[2].get("key") == "ENTER" else "Query visible",
        )

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.failed.connect(failures.append)
    try:
        engine.submit("Open Paint")
        qtbot.waitUntil(lambda: bool(failures), timeout=6000)
        assert [a[0] for a in bridge.executed] == [
            {"op": "text", "text": "Paint"},
            {"op": "press", "key": "ENTER"},
        ]
        assert engine.launch_query_pending == "Paint"
        assert not engine.human_result_confirmed
    finally:
        engine.close()


def test_search_review_button_is_distinct_from_final_task_confirmation(qtbot, qapp, tmp_path):
    from monster.app import CompanionApp
    from monster.lifecycle import MockTaskController

    engine = MockTaskController()
    engine.phase = "launch_query_review"
    ui = CompanionApp(qapp, engine, tmp_path / "preferences.json", enable_tray=False)
    try:
        ui.question("query-step", "Confirm only the app search query", True)
        assert ui.pill.approve.text() == "Confirm query"
        engine.phase = "confirming"
        ui.question("whole-task", "Inspect the complete task", True)
        assert ui.pill.approve.text() == "Approve"
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)
