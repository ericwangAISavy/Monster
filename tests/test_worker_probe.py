"""Synthetic probe contracts and controller regressions, not native qualification."""

import asyncio
from dataclasses import replace

import pytest

from monster.providers.base import ProbeResult, ProviderError
from monster.providers.config import OllamaConfig


def config():
    return OllamaConfig(model="PRIVATE-model", mode="supervised", local_server_confirmed=True)


class Provider:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def probe(self, model):
        self.calls.append(model)
        return self.result


def test_probe_is_correlated_and_has_no_frame_or_execution_authority():
    from monster.agent.worker import BoundedWorker
    from monster.agent.worker_probe import ProbeRequest

    request = ProbeRequest.create(config(), generation=3, task_ref="task")
    provider = Provider(ProbeResult(config().model, "abc", "test", True, True))
    result = asyncio.run(BoundedWorker().probe(provider, request))
    assert result.binding == request.binding
    assert provider.calls == [config().model]
    assert result.execution_authority == "none"
    assert "frame" not in result.model_dump_json()
    assert "PRIVATE" not in result.model_dump_json() and "PRIVATE" not in repr(request)
    assert result.to_probe_result(config().model) == provider.result


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        {},
        "yes",
        ProbeResult("other", "abc", "test", True, True),
        ProbeResult("PRIVATE-model", "abc", "test", False, True),
        ProbeResult("PRIVATE-model", "abc", "test", True, False),
        ProbeResult("PRIVATE-model", "abc", "test", 1, True),
        ProbeResult("PRIVATE-model", "", "test", True, True),
    ],
)
def test_invalid_or_negative_probe_cannot_pass(value):
    from monster.agent.worker import BoundedWorker
    from monster.agent.worker_probe import ProbeRequest

    request = ProbeRequest.create(config(), generation=1, task_ref="task")
    provider = Provider(value)
    with pytest.raises(ProviderError):
        asyncio.run(BoundedWorker().probe(provider, request))
    assert len(provider.calls) == 1


def test_changed_request_binding_or_configuration_is_refused():
    from monster.agent.worker_probe import ProbeRequest

    request = ProbeRequest.create(config(), generation=1, task_ref="task")
    with pytest.raises(ValueError):
        replace(request, config=replace(config(), timeout=60))
    with pytest.raises(ValueError):
        replace(request, binding=request.binding.model_copy(update={"model_ref": "other"}))


def test_probe_cancellation_does_not_retry():
    from monster.agent.worker import BoundedWorker
    from monster.agent.worker_probe import ProbeRequest

    async def scenario():
        started = asyncio.Event()
        calls = []

        class Waiting:
            async def probe(self, model):
                calls.append(model)
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(
            BoundedWorker().probe(
                Waiting(), ProbeRequest.create(config(), generation=1, task_ref="task")
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == [config().model]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        ProbeResult("wrong", "abc", "test", True, True),
        ProbeResult("vision", "abc", "test", False, True),
    ],
)
def test_controller_rejects_error_free_invalid_probe_before_capture(qtbot, value):
    from test_supervised import controller

    engine, bridge, model = controller([])
    captures, failures = [], []
    engine.capture_requested.connect(lambda: captures.append(True))
    engine.failed.connect(lambda *args: failures.append(args))

    async def probe(model_name):
        return value

    model.probe = probe
    try:
        engine.submit("Type hello in the current composer. Do not send.")
        qtbot.waitUntil(lambda: not engine.active, timeout=5000)
        qtbot.waitUntil(lambda: engine.phase == "idle", timeout=5000)
        assert not captures and not bridge.executed
        assert failures
    finally:
        engine.close()


@pytest.mark.parametrize(
    "mutation", ["nonce", "raw", "lost", "config", "generation", "task", "phase"]
)
def test_controller_refuses_stale_probe_results(qtbot, mutation):
    from test_supervised import controller

    engine, bridge, _model = controller([])
    captures = []
    engine.capture_requested.connect(lambda: captures.append(True))
    original = engine.worker.probe if hasattr(engine.worker, "probe") else None

    async def altered(provider, request):
        result = await original(provider, request)
        if mutation == "nonce":
            return result.model_copy(
                update={"binding": result.binding.model_copy(update={"request_ref": "wrong"})}
            )
        if mutation == "raw":
            return ProbeResult(
                model="vision",
                digest="abc",
                server_version="test",
                vision_verified=True,
                structured_verified=True,
            )
        if mutation == "lost":
            engine.worker_binding = None
        elif mutation == "config":
            engine.config = replace(engine.config, timeout=60)
        elif mutation == "generation":
            engine.generation += 1
        elif mutation == "task":
            engine.run_diagnostics.run_id = "other-task"
        elif mutation == "phase":
            engine.phase = "observing"
        return result

    engine.worker.probe = altered
    try:
        engine.submit("Type hello in the current composer. Do not send.")
        qtbot.waitUntil(lambda: not engine.active, timeout=5000)
        qtbot.waitUntil(lambda: engine.phase == "idle", timeout=5000)
        assert not captures and not bridge.executed
    finally:
        engine.close()


def test_queued_probe_uses_submitted_config_even_when_host_changes(qtbot):
    from test_supervised import controller

    engine, bridge, model = controller([])
    initial = engine.config
    constructed, captures = [], []
    engine.capture_requested.connect(lambda: captures.append(True))

    def factory(selected):
        constructed.append(selected)
        return model

    engine.provider_factory = factory
    start = engine.jobs.start

    def queued(operation):
        async def invoke():
            engine.config = replace(initial, model="different-model", timeout=60)
            return await operation()

        return start(invoke)

    engine.jobs.start = queued
    try:
        engine.submit("Type hello in the current composer. Do not send.")
        qtbot.waitUntil(lambda: not engine.active, timeout=5000)
        qtbot.waitUntil(lambda: engine.phase == "idle", timeout=5000)
        assert constructed == [initial]
        assert not captures and not bridge.executed
    finally:
        engine.close()
