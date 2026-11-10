"""All operational Worker results are bounded advisory packets, not grants."""

import asyncio
from dataclasses import replace

import pytest
from test_supervised import observation

from monster.agent.worker import BoundedWorker, ProposalRequest, VerificationRequest, VisualContext
from monster.providers.base import Decision, Verification


def test_proposal_envelope_freezes_private_payload_and_coordinate_provenance():
    from monster.agent.worker_results import ProposalResult

    frame = observation()
    original = Decision(
        "action",
        "PRIVATE move",
        {"op": "chord", "keys": ["CTRL", "A"]},
        risk="routine",
        expected_result="PRIVATE selected",
        coordinate_source={"space": "native-test", "model_action": {"keys": ["CTRL", "A"]}},
    )
    request = ProposalRequest.create(
        VisualContext("PRIVATE MODEL", "PRIVATE TASK", frame),
        generation=4,
        task_ref="task",
        history=("PRIVATE HISTORY",),
    )

    class Provider:
        async def decide(self, *args, **kwargs):
            return original

    result = asyncio.run(BoundedWorker().propose(Provider(), request))
    assert isinstance(result, ProposalResult)
    assert result.binding == request.binding
    original.action["keys"].append("F4")
    original.coordinate_source["model_action"]["keys"].append("F4")
    copy = result.to_decision()
    assert copy.action == {"op": "chord", "keys": ["CTRL", "A"]}
    assert copy.coordinate_source["model_action"]["keys"] == ["CTRL", "A"]
    copy.action["keys"].append("X")
    assert result.to_decision().action == {"op": "chord", "keys": ["CTRL", "A"]}
    assert "PRIVATE" not in result.model_dump_json()
    assert result.execution_authority == "none"


def test_action_verification_result_is_bound_and_advisory():
    request = VerificationRequest.from_action(
        VisualContext("model", "PRIVATE TASK", observation()),
        {"op": "press", "key": "ENTER"},
        "PRIVATE expected",
        generation=2,
        task_ref="task",
        before_image=b"before",
    )

    class Provider:
        async def verify_action(self, *args):
            return Verification("succeeded", "PRIVATE evidence")

    result = asyncio.run(BoundedWorker().verify(Provider(), request))
    assert result.binding == request.binding
    assert result.evidence_kind == "model_assessment"
    assert "PRIVATE" not in result.model_dump_json()
    assert result.to_verification() == Verification("succeeded", "PRIVATE evidence")


def test_scene_result_binds_both_frames_and_image_action():
    from monster.agent.worker import SceneRequest

    before = observation()
    after = replace(observation(), id="after", foreground=88)
    action = {"op": "drag", "x": 10, "y": 20, "end_x": 50, "end_y": 60}
    request = SceneRequest.create(
        VisualContext("model", "PRIVATE TASK", after), before, action, generation=3, task_ref="task"
    )
    action["end_x"] = 90
    result = asyncio.run(BoundedWorker().compare(request))
    assert result.binding == request.binding
    assert result.binding.before_frame.capture_ref == before.id
    assert result.binding.frame.capture_ref == "after"
    assert request.action.to_action()["end_x"] == 50
    assert result.matched is False
    assert result.reason == "foreground_changed_or_missing"
    assert result.execution_authority == "none"


@pytest.mark.parametrize(
    "stage,mutation",
    [
        ("proposal", "nonce"),
        ("proposal", "raw"),
        ("proposal", "lost"),
        ("scene", "nonce"),
        ("scene", "raw"),
        ("scene", "lost"),
        ("scene", "before"),
        ("scene", "action"),
        ("verification", "nonce"),
        ("verification", "raw"),
        ("verification", "lost"),
        ("verification", "action"),
    ],
)
def test_controller_refuses_uncorrelated_worker_results(qtbot, tmp_path, stage, mutation):
    from test_standalone_journal import attach, records
    from test_supervised import controller

    engine, bridge, _ = controller(
        [
            Decision("action", "Press Enter", {"op": "press", "key": "ENTER"}, risk="routine"),
            Decision("complete", "Done"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    attach(engine, tmp_path)

    def corrupt(result):
        if mutation == "lost":
            engine.worker_binding = None
        if mutation == "action":
            engine.pending.action["key"] = "SPACE"
        if mutation == "raw":
            if stage == "proposal":
                return result.to_decision()
            if stage == "scene":
                return True
            return Verification("succeeded", "Unbound claim")
        binding = result.binding
        if mutation == "nonce":
            binding = binding.model_copy(update={"request_ref": "wrong-request"})
        if mutation == "before":
            binding = binding.model_copy(
                update={
                    "before_frame": binding.before_frame.model_copy(
                        update={"capture_ref": "other-before"}
                    )
                }
            )
        return result.model_copy(update={"binding": binding})

    class WrongWorker(BoundedWorker):
        async def propose(self, provider, request):
            result = await super().propose(provider, request)
            return corrupt(result) if stage == "proposal" else result

        async def compare(self, request):
            result = await super().compare(request)
            return corrupt(result) if stage == "scene" else result

        async def verify(self, provider, request):
            result = await super().verify(provider, request)
            return corrupt(result) if stage == "verification" else result

    engine.worker = WrongWorker()
    terminal = []
    engine.terminal_ready.connect(terminal.append)
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Press Enter")
        qtbot.waitUntil(lambda: bool(terminal), timeout=7000)
        assert terminal[0].terminal_kind == "error"
        assert len(bridge.executed) == (1 if stage == "verification" else 0)
        if stage == "verification":
            assert records(tmp_path)[0]["status"] == "uncertain"
        else:
            assert not (tmp_path / "ledger.sqlite3").exists()
    finally:
        engine.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("schema_version", True),
        ("execution_authority", "allowed"),
        ("approved", True),
        ("human_confirmed", True),
    ],
)
def test_proposal_result_cannot_gain_authority_or_unknown_fields(field, value):
    from monster.agent.worker_results import ProposalResult

    request = ProposalRequest.create(
        VisualContext("model", "task", observation()), generation=1, task_ref="task"
    )
    result = ProposalResult.from_decision(request.binding, Decision("complete", "Visible"))
    raw = {"binding": request.binding, "decision_json": result.decision_json, field: value}
    with pytest.raises(ValueError):
        ProposalResult.model_validate(raw)


@pytest.mark.parametrize(
    "operation,expected", [("proposal", "same"), ("action_verification", "different")]
)
def test_action_assessment_refuses_wrong_operation_or_changed_criterion(
    qtbot, tmp_path, operation, expected
):
    from test_standalone_journal import attach, records
    from test_supervised import controller

    engine, bridge, _ = controller(
        [Decision("action", "Press Enter", {"op": "press", "key": "ENTER"}, risk="routine")]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    attach(engine, tmp_path)

    class WrongCriterionWorker(BoundedWorker):
        async def verify(self, provider, request):
            result = await super().verify(provider, request)
            if expected == "different":
                engine.verification_expected = "Different criterion"
            if operation == "proposal":
                return result.model_copy(
                    update={"binding": result.binding.model_copy(update={"operation": operation})}
                )
            return result

    engine.worker = WrongCriterionWorker()
    terminal = []
    engine.terminal_ready.connect(terminal.append)
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Press Enter")
        qtbot.waitUntil(lambda: bool(terminal), timeout=7000)
        assert terminal[0].terminal_kind == "error"
        assert len(bridge.executed) == 1
        assert records(tmp_path)[0]["status"] == "uncertain"
    finally:
        engine.close()


def test_capture_only_proposal_stays_read_only_and_does_not_consume_input_budget(qtbot, tmp_path):
    from PySide6.QtCore import QTimer
    from test_standalone_journal import attach, records
    from test_supervised import controller

    engine, bridge, _ = controller(
        [
            Decision("action", "Observe again", {"op": "screenshot"}),
            Decision("action", "Press Enter", {"op": "press", "key": "ENTER"}, risk="routine"),
            Decision("complete", "Done"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    attach(engine, tmp_path)
    terminal = []
    engine.terminal_ready.connect(terminal.append)
    engine.approval_requested.connect(
        lambda rid, text: QTimer.singleShot(1, lambda: engine.respond(rid, "approve"))
    )
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Press Enter")
        qtbot.waitUntil(lambda: bool(terminal), timeout=7000)
        assert terminal[0].terminal_kind == "complete", engine.terminal
        assert engine.calls == 3 and engine.actions == 1 and len(bridge.executed) == 1
        assert len(records(tmp_path)) == 1
    finally:
        engine.close()


@pytest.mark.parametrize("review,hands", [(False, False), (True, False), (True, True)])
def test_resolved_journal_history_does_not_block_actual_completion(qtbot, tmp_path, review, hands):
    from PySide6.QtCore import QTimer
    from test_standalone_journal import attach, records
    from test_supervised import controller

    engine, bridge, model = controller(
        [
            Decision(
                "action",
                "Press Enter",
                {"op": "press", "key": "ENTER"},
                risk="routine",
                expected_result="Dialog closes",
            ),
            Decision("complete", "Done"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=review, hands_free=hands)

    async def completion(*args):
        return Verification("succeeded", "Synthetic visible task result")

    model.verify_completion = completion
    attach(engine, tmp_path)
    terminal = []
    engine.terminal_ready.connect(terminal.append)
    engine.approval_requested.connect(
        lambda rid, text: QTimer.singleShot(1, lambda: engine.respond(rid, "approve"))
    )
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Press Enter")
        qtbot.waitUntil(lambda: bool(terminal), timeout=7000)
        assert records(tmp_path)[0]["status"] == "observed_success"
        assert terminal[0].terminal_kind == "complete", engine.terminal
        assert terminal[0].cleanup_confirmed and len(bridge.executed) == 1
    finally:
        engine.close()


def test_legacy_done_projection_is_preserved_without_changing_authority():
    from monster.agent.worker_results import ProposalResult

    request = ProposalRequest.create(
        VisualContext("model", "task", observation()), generation=1, task_ref="task"
    )
    outcome = ProposalResult.from_decision(request.binding, Decision("done", "Visible result"))
    assert outcome.to_decision() == Decision("done", "Visible result")
    assert outcome.execution_authority == "none"
