"""Bounded read-only operations; input authority remains with the controller."""

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest
from test_supervised import observation

from monster.agent.contract_adapters import proposal_from_decision
from monster.providers.base import Decision, Verification


def test_worker_proposes_once_from_private_snapshot():
    from monster.agent.worker import BoundedWorker, ProposalRequest, VisualContext

    frame = observation()
    context = VisualContext("original-model", "PRIVATE TASK", frame)
    request = ProposalRequest(context, ("PRIVATE HISTORY",))

    class Provider:
        def __init__(self):
            self.calls = []

        async def decide(self, model, instruction, image, width, height, history=()):
            self.calls.append((model, instruction, image, width, height, history))
            return Decision("action", "Press Enter", {"op": "press", "key": "ENTER"})

    provider = Provider()
    result = asyncio.run(BoundedWorker().propose(provider, request))
    assert result.action == {"op": "press", "key": "ENTER"}
    assert provider.calls == [
        ("original-model", "PRIVATE TASK", frame.png, 100, 100, ("PRIVATE HISTORY",))
    ]
    assert "PRIVATE" not in repr(request)
    with pytest.raises(FrozenInstanceError):
        request.context = None


def test_verification_snapshot_does_not_retain_mutable_action():
    from monster.agent.worker import BoundedWorker, VerificationRequest, VisualContext

    action = {"op": "chord", "keys": ["CTRL", "A"]}
    request = VerificationRequest.from_action(
        VisualContext("model", "PRIVATE", observation()),
        action,
        "PRIVATE RESULT",
        before_image=b"before",
    )
    action["keys"].append("F4")

    class Provider:
        async def verify_action(
            self, model, instruction, actual, expected, image, width, height, before
        ):
            assert actual == {"op": "chord", "keys": ["CTRL", "A"]}
            assert (expected, before) == ("PRIVATE RESULT", b"before")
            actual["keys"].append("F4")
            return Verification("succeeded", "model opinion only")

    result = asyncio.run(BoundedWorker().verify(Provider(), request))
    assert result.status == "succeeded"
    assert request.action.to_action() == {"op": "chord", "keys": ["CTRL", "A"]}
    assert "PRIVATE" not in repr(request)


def test_worker_cancellation_propagates_without_retry():
    from monster.agent.worker import BoundedWorker, ProposalRequest, VisualContext

    async def scenario():
        entered = asyncio.Event()

        class Provider:
            calls = 0

            async def decide(self, *args, **kwargs):
                self.calls += 1
                entered.set()
                await asyncio.Event().wait()

        provider = Provider()
        task = asyncio.create_task(
            BoundedWorker().propose(
                provider, ProposalRequest(VisualContext("model", "test", observation()))
            )
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.calls == 1

    asyncio.run(scenario())


def test_worker_grounding_binds_both_drag_endpoints_and_generation():
    from monster.agent.worker import BoundedWorker

    frame = observation()
    decision = Decision(
        "action",
        "Draw a line",
        {"op": "drag", "x": 20, "y": 30, "end_x": 50, "end_y": 60},
        expected_result="A visible stroke",
    )
    worker = BoundedWorker()
    physical = worker.prepare(
        decision,
        decision.action,
        frame,
        "draw a line",
        single_click_policy=False,
        single_click_dispatched=False,
        close_dispatched=False,
    )
    proposal = proposal_from_decision(decision, frame, 4, task_ref="task", model="model")
    mapped = worker.ground(proposal, frame, physical, generation=4, effect_ref="effect")
    assert mapped.physical_action.to_action() == {
        "op": "drag",
        "x": -80,
        "y": 30,
        "end_x": -50,
        "end_y": 60,
    }
    assert mapped.execution_authority == "none"
    with pytest.raises(ValueError):
        worker.ground(proposal, frame, {**physical, "end_x": -40}, generation=4)
    with pytest.raises(ValueError):
        worker.ground(proposal, frame, physical, generation=5)
    with pytest.raises(ValueError):
        worker.ground(proposal, replace(frame, foreground=88), physical, generation=4)


@pytest.mark.parametrize(
    "single,done,action",
    [
        (True, True, {"op": "click", "x": 20, "y": 30}),
        (True, False, {"op": "press", "key": "ENTER"}),
        (True, False, {"op": "click", "x": 20, "y": 30, "count": 2}),
    ],
)
def test_worker_preserves_one_click_budget(single, done, action):
    from monster.agent.worker import BoundedWorker

    with pytest.raises(ValueError):
        BoundedWorker().prepare(
            Decision("action", "Click", action),
            action,
            observation(),
            "click once",
            single_click_policy=single,
            single_click_dispatched=done,
            close_dispatched=False,
        )


def test_controller_freezes_model_configuration_before_queued_worker(qtbot):
    from test_supervised import controller

    engine, bridge, model = controller(
        [Decision("action", "Press Enter", {"op": "press", "key": "ENTER"})]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    calls, configs, failures = [], [], []
    original_decide = model.decide

    async def decide(model_id, *args, **kwargs):
        calls.append(model_id)
        return await original_decide(model_id, *args, **kwargs)

    model.decide = decide

    def factory(config):
        configs.append(config.model)
        return model

    engine.provider_factory = factory
    start = engine.jobs.start

    def queue(operation):
        if engine.phase == "deciding":
            engine.config = replace(engine.config, model="changed-while-queued")
        return start(operation)

    engine.jobs.start = queue
    engine.failed.connect(failures.append)
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Press Enter")
        qtbot.waitUntil(lambda: bool(failures))
        assert bridge.executed == []
        assert "configuration changed" in failures[0]
        assert calls == ["vision"]
        assert configs == ["vision", "vision"]
    finally:
        engine.close()


def test_controller_worker_proposal_is_not_authority(qtbot, tmp_path):
    from test_standalone_journal import attach
    from test_supervised import controller

    from monster.agent.worker import BoundedWorker

    engine, bridge, _ = controller(
        [Decision("action", "Press Enter", {"op": "press", "key": "ENTER"}, risk="routine")]
    )
    attach(engine, tmp_path)

    class ReadOnlyWorker(BoundedWorker):
        async def propose(self, provider, request):
            # A valid model result cannot skip the host's current per-step review.
            return Decision(
                "action", "Press Enter", {"op": "press", "key": "ENTER"}, risk="routine"
            )

    engine.worker = ReadOnlyWorker()
    questions = []
    engine.approval_requested.connect(lambda rid, text: questions.append(rid))
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Press Enter")
        qtbot.waitUntil(lambda: bool(questions))
        assert bridge.executed == []
        assert engine.phase == "approval"
        # No intent exists until host review + scene checks, even with a ready proposal.
        assert not (tmp_path / "ledger.sqlite3").exists()
    finally:
        engine.close()


def test_worker_scene_comparison_and_model_assessment_do_not_assert_success():
    from monster.agent.worker import BoundedWorker

    worker = BoundedWorker()
    frame = observation()
    diagnostics = {}
    assert not asyncio.run(
        worker.compare(
            frame,
            replace(frame, foreground=88),
            {"op": "press", "key": "ENTER"},
            diagnostics=diagnostics,
        )
    )
    assert diagnostics
    decision = Decision(
        "action", "Press Enter", {"op": "press", "key": "ENTER"}, expected_result="Dialog closed"
    )
    proposal = proposal_from_decision(decision, frame, 4, task_ref="task", model="model")
    grounded = worker.ground(proposal, frame, decision.action, generation=4)
    result = worker.assess(
        grounded,
        Verification("succeeded", "PRIVATE model claim"),
        frame,
        generation=4,
        model="model",
        expected_result="Dialog closed",
        evidence_ref="evidence",
    )
    assert result.evidence_kind == "model_assessment"
    assert result.execution_authority == "none"
    assert "PRIVATE" not in result.model_dump_json()
    with pytest.raises(ValueError):
        worker.assess(
            grounded,
            Verification("succeeded", "Visible"),
            frame,
            generation=5,
            model="model",
            expected_result="Dialog closed",
            evidence_ref="evidence",
        )


def test_worker_provider_failure_propagates_once():
    from monster.agent.worker import BoundedWorker, ProposalRequest, VisualContext

    class Provider:
        calls = 0

        async def decide(self, *args, **kwargs):
            self.calls += 1
            raise ConnectionError("private server exception")

    provider = Provider()
    with pytest.raises(ConnectionError):
        asyncio.run(
            BoundedWorker().propose(
                provider, ProposalRequest(VisualContext("model", "test", observation()))
            )
        )
    assert provider.calls == 1


def test_verification_packet_refusal_finishes_cleanup_instead_of_raising(qtbot, tmp_path):
    from test_standalone_journal import attach, records
    from test_supervised import controller

    engine, bridge, _ = controller(
        [Decision("action", "Press Enter", {"op": "press", "key": "ENTER"}, risk="routine")]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    attach(engine, tmp_path)
    terminal = []
    engine.terminal_ready.connect(terminal.append)

    def capture():
        if engine.phase == "verifying_capture":
            engine.pending.action["op"] = "unsupported-mutated-payload"
        engine.observation_ready(observation(), engine.generation)

    engine.capture_requested.connect(capture)
    try:
        engine.submit("Press Enter")
        qtbot.waitUntil(lambda: bool(terminal), timeout=10000)
        assert len(bridge.executed) == 1
        assert terminal[0].terminal_kind == "error"
        assert engine.phase == "idle"
        assert records(tmp_path)[0]["status"] == "uncertain"
    finally:
        engine.close()
