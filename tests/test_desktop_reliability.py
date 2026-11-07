"""Current-desktop regressions use fake input and real Qt controller sequencing."""

from dataclasses import replace

import pytest
from test_supervised import controller, observation

from monster.agent.validation import validate_proposal_intent
from monster.providers.base import Decision, DraftTargetCheck, Verification


def typing_controller():
    engine, bridge, model = controller(
        [
            Decision(
                "action",
                "Type 'hello' into Notepad",
                {"op": "text", "text": "hello"},
                risk="routine",
                expected_result="hello is visible in the Notepad document.",
            ),
            Decision("complete", "The requested text is visible"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    return engine, bridge, model


def test_late_visible_text_reobserves_once_without_retyping_and_preserves_before_frame(qtbot):
    engine, bridge, model = typing_controller()
    original, early, late = observation("white"), observation("#dddddd"), observation("#bbbbbb")
    checks, questions = [], []

    async def verify(*args):
        checks.append(args)
        return Verification(
            "uncertain" if len(checks) == 1 else "succeeded",
            "Text is not readable yet" if len(checks) == 1 else "hello is visible",
        )

    def capture():
        frame = (
            (early if engine.verification_retakes == 0 else late)
            if engine.phase == "verifying_capture"
            else original
        )
        engine.observation_ready(frame, engine.generation)

    model.verify_action = verify
    engine.capture_requested.connect(capture)
    engine.approval_requested.connect(lambda rid, text: questions.append((rid, text)))
    try:
        engine.submit("Type hello into the currently open Notepad document")
        qtbot.waitUntil(lambda: engine.phase == "confirming", timeout=5000)
        assert len(bridge.executed) == 1
        assert len(checks) == 2
        assert [args[7] for args in checks] == [original.png, original.png]
        assert [args[4] for args in checks] == [early.png, late.png]
        assert engine.failed_actions == []
        assert engine.last_verification.status == "succeeded"
        assert engine.actions == 1 and engine.verification_calls == 2
        assert len(questions) == 1  # still requires final human completion confirmation
        assert not engine.human_result_confirmed
    finally:
        engine.close()


def test_persistent_uncertainty_has_two_observations_then_human_handoff_without_replay(qtbot):
    engine, bridge, model = typing_controller()
    checks = []

    async def verify(*args):
        checks.append(args)
        return Verification("uncertain", "Cannot establish the location of the text")

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Type hello into the currently open Notepad document")
        qtbot.waitUntil(lambda: engine.phase == "confirming", timeout=5000)
        assert len(checks) == 2 and len(bridge.executed) == 1
        assert engine.last_verification.status == "uncertain"
        assert engine.calls == 1
        assert not engine.human_result_confirmed
        engine.respond(engine.question_id, "reject")
        qtbot.waitUntil(lambda: engine.phase == "idle")
        assert not engine.human_result_confirmed
        assert len(bridge.executed) == 1
    finally:
        engine.close()


def test_explicit_verification_failure_is_not_retried(qtbot):
    engine, bridge, model = typing_controller()
    checks, failures = [], []

    async def verify(*args):
        checks.append(args)
        return Verification("failed", "hello appeared in Find rather than the document")

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.failed.connect(failures.append)
    try:
        engine.submit("Type hello into the currently open Notepad document")
        qtbot.waitUntil(lambda: bool(failures), timeout=5000)
        assert len(checks) == 1 and len(bridge.executed) == 1
        assert engine.verification_retakes == 0
    finally:
        engine.close()


@pytest.mark.parametrize("assessment", ["wrong_target", "uncertain"])
def test_notepad_target_check_blocks_typing_into_wrong_or_unestablished_field(qtbot, assessment):
    engine, bridge, model = typing_controller()
    failures, target_checks = [], []

    async def target(*args):
        target_checks.append(args)
        return DraftTargetCheck(assessment, "The editable Notepad document is not established")

    model.verify_draft_target = target
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    engine.failed.connect(failures.append)
    try:
        engine.submit("Type hello into the currently open blank Notepad document")
        qtbot.waitUntil(lambda: bool(failures), timeout=5000)
        assert len(target_checks) == 1
        assert bridge.executed == []
    finally:
        engine.close()


def test_notepad_unclear_focus_requires_human_confirmation_before_text(qtbot):
    engine, bridge, model = typing_controller()

    async def target(*args):
        return DraftTargetCheck("needs_focus", "Notepad visible; document caret not established")

    model.verify_draft_target = target
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Type hello into the currently open blank Notepad document")
        qtbot.waitUntil(lambda: engine.phase == "target_focus_review", timeout=5000)
        assert not bridge.executed
        engine.respond("stale-question", "approve")
        assert not bridge.executed
        engine.respond(engine.question_id, "approve")
        qtbot.waitUntil(lambda: engine.phase == "confirming", timeout=5000)
        assert len(bridge.executed) == 1
        assert bridge.executed[0][0] == {"op": "text", "text": "hello"}
        assert not engine.human_result_confirmed
    finally:
        engine.close()


def test_cancel_during_read_only_reobserve_stops_scheduled_capture(qtbot):
    engine, bridge, model = typing_controller()
    checks = []

    async def verify(*args):
        checks.append(args)
        return Verification("uncertain", "Not enough evidence")

    model.verify_action = verify
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Type hello into the currently open Notepad document")
        qtbot.waitUntil(
            lambda: engine.phase == "verifying_capture" and engine.verification_retakes == 1,
            timeout=5000,
        )
        old_generation = engine.generation
        engine.cancel()
        engine.observation_ready(observation(), old_generation)
        qtbot.waitUntil(lambda: engine.phase == "idle")
        qtbot.wait(400)
        assert len(checks) == 1 and len(bridge.executed) == 1
        assert not engine.capture_timer.isActive()
    finally:
        engine.close()


@pytest.mark.parametrize("changed", ["foreground", "display"])
def test_read_only_reobserve_refuses_changed_target(qtbot, changed):
    engine, bridge, model = typing_controller()
    checks, failures = [], []

    async def verify(*args):
        checks.append(args)
        return Verification("uncertain", "Text not established")

    def capture():
        frame = observation()
        if engine.phase == "verifying_capture" and engine.verification_retakes:
            frame = (
                replace(frame, foreground=88)
                if changed == "foreground"
                else replace(frame, bounds=replace(frame.bounds, x=0))
            )
        engine.observation_ready(frame, engine.generation)

    model.verify_action = verify
    engine.capture_requested.connect(capture)
    engine.failed.connect(failures.append)
    try:
        engine.submit("Type hello into the currently open Notepad document")
        qtbot.waitUntil(lambda: bool(failures), timeout=5000)
        assert "changed" in failures[0]
        assert len(checks) == 1 and len(bridge.executed) == 1
    finally:
        engine.close()


@pytest.mark.parametrize(
    "action",
    [
        {"op": "press", "key": "HOME"},
        {"op": "press", "key": "END"},
        {"op": "chord", "keys": ["CTRL", "HOME"]},
        {"op": "chord", "keys": ["CTRL", "END"]},
    ],
)
def test_navigation_cannot_claim_new_document_without_explicit_shortcut_text(action):
    bad = Decision(
        "action",
        "Create a new document in Notepad",
        action,
        expected_result="A blank document opens.",
    )
    with pytest.raises(ValueError, match="caret"):
        validate_proposal_intent(bad, "Create a new Notepad document and type hello")
    validate_proposal_intent(
        replace(bad, summary="Move the caret", expected_result="Caret at document boundary"),
        "Navigate the document",
    )


def test_notepad_readiness_check_does_not_reclassify_app_launch_queries_as_drafts():
    from monster.agent.progress import needs_text_target_check

    assert not needs_text_target_check("Open Notepad", {"op": "text", "text": "Notepad"})
    assert needs_text_target_check(
        "Type hello in the currently open Notepad document", {"op": "text", "text": "hello"}
    )
    assert not needs_text_target_check(
        "Create a new Notepad document and type hello", {"op": "chord", "keys": ["CTRL", "N"]}
    )
