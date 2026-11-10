"""Read-only visual checks cannot become authority or cross request boundaries."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest
from test_supervised import observation

from monster.agent.worker import BoundedWorker, VisualContext
from monster.providers.base import DraftTargetCheck, Verification


@pytest.mark.parametrize("kind", ["draft_target", "drawing_target", "app_launch", "completion"])
def test_worker_visual_checks_are_one_call_bound_and_private(kind):
    from monster.agent.worker import VisualCheckRequest

    action = {"op": "drag", "x": 10, "y": 20, "end_x": 30, "end_y": 40}
    request = VisualCheckRequest.create(
        kind,
        VisualContext("private-model", "PRIVATE TASK", observation()),
        generation=7,
        task_ref="task",
        action=action if kind.endswith("target") else None,
        expected="PRIVATE EXPECTATION" if kind == "app_launch" else None,
    )
    action["end_x"] = 70

    class Provider:
        def __init__(self):
            self.calls = []

        async def verify_draft_target(self, *args):
            self.calls.append(("draft", args))
            assert args[2]["end_x"] == 30
            args[2]["end_x"] = 80
            return DraftTargetCheck("ready", "PRIVATE evidence")

        async def verify_drawing_target(self, *args):
            self.calls.append(("drawing", args))
            assert args[2]["end_x"] == 30
            return DraftTargetCheck("ready", "PRIVATE evidence")

        async def verify_action(self, *args):
            self.calls.append(("launch", args))
            assert args[2] == {"op": "screenshot"}
            assert args[3] == "PRIVATE EXPECTATION"
            return Verification("succeeded", "PRIVATE evidence")

        async def verify_completion(self, *args):
            self.calls.append(("completion", args))
            return Verification("succeeded", "PRIVATE evidence")

    provider = Provider()
    result = asyncio.run(BoundedWorker().check(provider, request))
    assert len(provider.calls) == 1
    assert result.binding == request.binding
    assert result.binding.frame.controller_generation == 7
    assert result.binding.operation == kind
    assert result.evidence_kind == "model_assessment"
    assert result.execution_authority == "none"
    assert "PRIVATE" not in result.model_dump_json()
    assert "private-model" not in result.model_dump_json()
    assert "PRIVATE" not in repr(request)
    with pytest.raises(FrozenInstanceError):
        request.context = None


@pytest.mark.parametrize(
    "kind,status",
    [
        ("draft_target", "succeeded"),
        ("drawing_target", "needs_focus"),
        ("app_launch", "ready"),
        ("completion", "ready"),
        ("completion", True),
        ("completion", "unknown"),
    ],
)
def test_worker_rejects_incompatible_check_status(kind, status):
    from monster.agent.worker import VisualCheckRequest

    request = VisualCheckRequest.create(
        kind,
        VisualContext("model", "test", observation()),
        generation=1,
        task_ref="task",
        action={"op": "text", "text": "hello"} if kind.endswith("target") else None,
        expected="App open" if kind == "app_launch" else None,
    )

    class Provider:
        async def verify_draft_target(self, *args):
            return DraftTargetCheck(status, "visible")

        async def verify_drawing_target(self, *args):
            return DraftTargetCheck(status, "visible")

        async def verify_action(self, *args):
            return Verification(status, "visible")

        async def verify_completion(self, *args):
            return Verification(status, "visible")

    from monster.providers.base import ProviderError

    with pytest.raises(ProviderError, match="invalid read-only"):
        asyncio.run(BoundedWorker().check(Provider(), request))


@pytest.mark.parametrize(
    "change",
    [
        "nonce",
        "task",
        "generation",
        "model",
        "observation",
        "foreground",
        "raw_result",
        "lost_binding",
        "host_frame",
        "phase",
        "authority",
    ],
)
def test_controller_rejects_mismatched_target_result_before_intent(qtbot, tmp_path, change):
    from dataclasses import replace

    from test_standalone_journal import attach
    from test_supervised import controller

    from monster.providers.base import Decision

    engine, bridge, _ = controller(
        [
            Decision(
                "action",
                "Type Hello",
                {"op": "text", "text": "Hello"},
                risk="routine",
                expected_result="Hello is visible, unsent",
            ),
            Decision("complete", "Draft visible"),
        ]
    )
    engine.config = replace(engine.config, approve_each_step=False)
    attach(engine, tmp_path)

    class MismatchedWorker(BoundedWorker):
        async def check(self, provider, request):
            result = await super().check(provider, request)
            binding = result.binding
            if change == "raw_result":
                return DraftTargetCheck("ready", "Ready without binding")
            if change == "lost_binding":
                engine.worker_binding = None
            if change == "host_frame":
                engine.observation = replace(engine.observation, foreground=88)
            if change == "phase":
                engine.phase = "checking_completion"
            if change == "authority":
                return result.model_copy(update={"execution_authority": "allowed"})
            if change == "nonce":
                binding = binding.model_copy(update={"request_ref": "other-request"})
            if change == "task":
                binding = binding.model_copy(update={"task_ref": "other-task"})
            if change == "model":
                binding = binding.model_copy(update={"model_ref": "other-model"})
            if change in ("generation", "observation", "foreground"):
                key, value = {
                    "generation": ("controller_generation", engine.generation + 1),
                    "observation": ("capture_ref", "other-capture"),
                    "foreground": ("foreground_guard", 88),
                }[change]
                binding = binding.model_copy(
                    update={"frame": binding.frame.model_copy(update={key: value})}
                )
            return result.model_copy(update={"binding": binding})

    engine.worker = MismatchedWorker()
    terminal = []
    engine.terminal_ready.connect(terminal.append)
    from PySide6.QtCore import QTimer

    engine.approval_requested.connect(
        lambda rid, text: QTimer.singleShot(1, lambda: engine.respond(rid, "approve"))
    )
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit('Type "Hello" into the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: bool(terminal), timeout=7000)
        assert bridge.executed == []
        assert terminal[0].terminal_kind == "error"
        assert not (tmp_path / "ledger.sqlite3").exists()
    finally:
        engine.close()


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"execution_authority": "allowed"},
        {"approved": True},
        {"human_confirmed": True},
    ],
)
def test_check_outcome_is_strict_metadata_only_and_cannot_grant(change):
    from monster.agent.worker import VisualCheckRequest
    from monster.agent.worker_checks import VisualCheckResult

    request = VisualCheckRequest.create(
        "completion",
        VisualContext("model", "PRIVATE TASK", observation()),
        generation=3,
        task_ref="task",
    )
    data = {
        "binding": request.binding,
        "status": "succeeded",
        "evidence": "PRIVATE EVIDENCE",
        **change,
    }
    with pytest.raises(ValueError):
        VisualCheckResult.model_validate(data)


@pytest.mark.parametrize(
    "kind,action,expected",
    [
        ("completion", {"op": "press", "key": "ENTER"}, None),
        ("app_launch", {"op": "press", "key": "ENTER"}, "App visible"),
        ("draft_target", None, None),
        ("completion", None, "Override criterion"),
        ("unknown", None, None),
    ],
)
def test_check_request_refuses_input_or_unknown_operation(kind, action, expected):
    from monster.agent.worker import VisualCheckRequest

    with pytest.raises(ValueError):
        VisualCheckRequest.create(
            kind,
            VisualContext("model", "test", observation()),
            generation=1,
            task_ref="task",
            action=action,
            expected=expected,
        )


def test_check_cancellation_propagates_without_retry():
    from monster.agent.worker import VisualCheckRequest

    async def scenario():
        ready = asyncio.Event()

        class Provider:
            calls = 0

            async def verify_completion(self, *args):
                self.calls += 1
                ready.set()
                await asyncio.Event().wait()

        provider = Provider()
        request = VisualCheckRequest.create(
            "completion",
            VisualContext("model", "test", observation()),
            generation=1,
            task_ref="task",
        )
        task = asyncio.create_task(BoundedWorker().check(provider, request))
        await ready.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.calls == 1

    asyncio.run(scenario())


def test_controller_does_not_complete_from_forged_completion(qtbot):
    from dataclasses import replace

    from test_supervised import controller

    from monster.providers.base import Decision

    engine, bridge, model = controller([Decision("complete", "Task appears done")])
    engine.config = replace(engine.config, hands_free=True)

    async def completion(*args):
        return Verification("succeeded", "PRIVATE visible result")

    model.verify_completion = completion

    class OldResultWorker(BoundedWorker):
        async def check(self, provider, request):
            result = await super().check(provider, request)
            return result.model_copy(
                update={"binding": result.binding.model_copy(update={"request_ref": "old-request"})}
            )

    engine.worker = OldResultWorker()
    terminal, finished = [], []
    engine.terminal_ready.connect(terminal.append)
    engine.finished.connect(finished.append)
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Inspect the current visible result")
        qtbot.waitUntil(lambda: bool(terminal), timeout=5000)
        assert terminal[0].terminal_kind == "error"
        assert terminal[0].cleanup_confirmed
        assert not finished and not bridge.executed
    finally:
        engine.close()


def test_controller_cancelled_completion_ignores_late_success(qtbot):
    import threading
    from dataclasses import replace

    from test_supervised import controller

    from monster.providers.base import Decision

    engine, bridge, model = controller([Decision("complete", "Task appears done")])
    engine.config = replace(engine.config, hands_free=True)
    entered = threading.Event()

    async def completion(*args):
        entered.set()
        await asyncio.Event().wait()

    model.verify_completion = completion
    terminal, finished = [], []
    engine.terminal_ready.connect(terminal.append)
    engine.finished.connect(finished.append)
    engine.capture_requested.connect(
        lambda: engine.observation_ready(observation(), engine.generation)
    )
    try:
        engine.submit("Inspect the current visible result")
        qtbot.waitUntil(entered.is_set)
        job = engine.job
        request = engine.worker_binding[2]
        engine.cancel()
        qtbot.waitUntil(lambda: bool(terminal))
        from monster.agent.worker_checks import VisualCheckResult

        engine._model_result(
            job,
            VisualCheckResult(
                binding=request.binding, status="succeeded", evidence="Late model claim"
            ),
            "",
        )
        assert terminal[0].terminal_kind == "cancel"
        assert not finished and not bridge.executed
    finally:
        engine.close()


def test_visual_check_request_refuses_mutable_binding_dictionary():
    from monster.agent.worker_checks import VisualCheckRequest

    original = VisualCheckRequest.create(
        "completion", VisualContext("model", "task", observation()), generation=1, task_ref="task"
    )
    # Keep the actual immutable frame, so this is otherwise a fully valid binding.
    mutable_binding = dict(original.binding)
    with pytest.raises(TypeError, match="immutable typed binding"):
        VisualCheckRequest(mutable_binding, original.context)
