import asyncio
from types import SimpleNamespace

from PySide6.QtCore import QTimer

from monster.computer.contracts import Bounds
from monster.preview import PreviewTaskController
from monster.providers.base import Decision, Model, ProbeResult
from monster.providers.config import OllamaConfig
from monster.ui.provider_settings import ProviderSettings


class FakeProvider:
    def __init__(self, config):
        self.config = config

    async def health(self):
        return "test-1"

    async def models(self):
        return [Model("vision:small", "abc")]

    async def probe(self, model):
        await asyncio.sleep(0.01)
        return ProbeResult(model, "abc", "test-1", True, True)

    async def decide(self, *args):
        await asyncio.sleep(0.02)
        return Decision("action", "Click the editor", {"op": "click", "x": 2, "y": 3})


def config():
    return OllamaConfig(model="vision:small", local_server_confirmed=True, mode="preview")


def observation():
    return SimpleNamespace(png=b"PNG", bounds=Bounds(-1920, 0, 1920, 1080))


def test_preview_observe_decide_no_execution(qtbot):
    controller = PreviewTaskController(config(), provider_factory=FakeProvider)
    requests, previews, finished = [], [], []
    controller.capture_requested.connect(lambda: requests.append(controller.generation))
    controller.preview_ready.connect(previews.append)
    controller.finished.connect(finished.append)
    try:
        assert controller.submit("Write a paragraph")
        qtbot.waitUntil(lambda: bool(requests))
        controller.observation_ready(observation(), requests[-1])
        qtbot.waitUntil(lambda: bool(previews))
        assert previews[0].action["x"] == 2
        assert "no computer actions" in finished[0]
        assert not controller.active
    finally:
        controller.close()


def test_cancelled_response_and_stale_capture_are_discarded(qtbot):
    controller = PreviewTaskController(config(), provider_factory=FakeProvider)
    captures, previews = [], []
    controller.capture_requested.connect(lambda: captures.append(controller.generation))
    controller.preview_ready.connect(previews.append)
    try:
        controller.submit("test")
        qtbot.waitUntil(lambda: bool(captures))
        stale = captures[-1]
        controller.cancel()
        controller.observation_ready(observation(), stale)
        qtbot.wait(60)
        assert not previews
        controller.submit("new task")
        qtbot.waitUntil(lambda: len(captures) == 2)
        controller.observation_ready(observation(), stale)
        assert controller.job is None
        controller.observation_ready(observation(), captures[-1])
        controller.cancel()
        qtbot.wait(60)
        assert not previews
    finally:
        controller.close()


def test_pause_resume_reprobes_and_captures_again(qtbot):
    controller = PreviewTaskController(config(), provider_factory=FakeProvider)
    captures = []
    controller.capture_requested.connect(lambda: captures.append(controller.generation))
    try:
        controller.submit("test")
        qtbot.waitUntil(lambda: bool(captures))
        controller.toggle_pause()
        controller.toggle_pause()
        qtbot.waitUntil(lambda: len(captures) == 2)
        assert captures[0] != captures[1]
    finally:
        controller.close()


def test_provider_settings_discover_probe_and_exact_config_gate(qtbot):
    dialog = ProviderSettings(OllamaConfig(), provider_factory=FakeProvider)
    qtbot.addWidget(dialog)
    saved = []
    dialog.saved.connect(saved.append)
    try:
        dialog.discover()
        qtbot.waitUntil(lambda: dialog.pending is None)
        assert dialog.model.currentText() == "vision:small"
        dialog.local.setChecked(True)
        dialog.mode.setCurrentIndex(1)
        dialog.apply()
        assert not saved
        dialog.probe()
        qtbot.waitUntil(lambda: dialog.pending is None)
        assert dialog.probed_config is not None
        dialog.apply()
        assert saved[-1].mode == "preview"
        dialog.timeout.setValue(90)
        dialog.apply()
        assert len(saved) == 1
    finally:
        dialog.jobs.close()


def test_gui_remains_responsive_during_pending_model(qtbot):
    class Slow(FakeProvider):
        async def probe(self, model):
            await asyncio.sleep(10)

    controller = PreviewTaskController(config(), provider_factory=Slow)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(5)
    try:
        controller.submit("test")
        qtbot.waitUntil(lambda: len(ticks) >= 3)
        controller.cancel()
        assert not controller.active
    finally:
        timer.stop()
        controller.close()


def test_companion_preview_wires_isolated_capture_and_details(qtbot, qapp, tmp_path, monkeypatch):
    from monster.app import CompanionApp
    from monster.computer import screenshot
    from monster.lifecycle import MockTaskController

    monkeypatch.setattr(screenshot, "capture_display", observation)
    ui = CompanionApp(qapp, MockTaskController(), tmp_path / "companion.json", enable_tray=False)
    try:
        ui.model_config = config()
        controller = PreviewTaskController(config(), provider_factory=FakeProvider)
        ui.switch_controller(controller)
        ui.activate()
        ui.submit("Write a note")
        qtbot.waitUntil(lambda: ui.last_preview is not None)
        assert not ui.active
        assert ui.pill.message.text() == "Preview ready · no computer actions performed"
        ui.show_preview_details()
        assert ui.preview_window.isVisible()
        # A second settings surface cannot overlap this one or enter a screenshot.
        ui.show_provider_settings()
        assert ui.provider_window is None
        import pytest

        with pytest.raises(RuntimeError, match="preview"):
            ui.capture_desktop(observation)
        ui.preview_window.reject()
        ui.switch_controller(MockTaskController())
        assert ui.controller.is_mock and ui.last_preview is None
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)
