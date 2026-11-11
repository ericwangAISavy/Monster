import asyncio
from types import SimpleNamespace

import pytest
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

    monkeypatch.setattr(screenshot, "capture_display", lambda *args: observation())
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


def test_advanced_inference_fields_persist_and_invalidate_probe(qtbot):
    from dataclasses import replace

    cfg = replace(config(), max_output_tokens=8192, context_size=32768, keep_alive="15m")
    dialog = ProviderSettings(cfg, provider_factory=FakeProvider)
    qtbot.addWidget(dialog)
    saved = []
    dialog.saved.connect(saved.append)
    try:
        assert dialog.max_output.value() == 8192
        assert dialog.context_size.value() == 32768
        assert dialog.keep_alive.currentText() == "15m"
        assert not dialog.thinking.isChecked()
        dialog.probe()
        qtbot.waitUntil(lambda: dialog.pending is None)
        dialog.apply()
        assert saved[-1] == cfg
        dialog.max_output.setValue(4096)
        assert dialog.probed_config is None
        dialog.apply()
        assert len(saved) == 1
        assert "probe" in dialog.status.text()
    finally:
        dialog.jobs.close()


def test_step_approval_setting_saves_without_repeating_vision_probe(qtbot):
    from dataclasses import replace

    dialog = ProviderSettings(replace(config(), mode="supervised"), provider_factory=FakeProvider)
    qtbot.addWidget(dialog)
    saved = []
    dialog.saved.connect(saved.append)
    try:
        assert dialog.approve_each_step.isChecked() and dialog.approve_each_step.isEnabled()
        dialog.probe()
        qtbot.waitUntil(lambda: dialog.pending is None)
        assert dialog.probed_config is not None
        dialog.approve_each_step.setChecked(False)
        assert dialog.probed_config is not None
        dialog.apply()
        assert saved[-1].approve_each_step is False
        dialog.mode.setCurrentIndex(dialog.mode.findData("preview"))
        assert not dialog.approve_each_step.isEnabled()
    finally:
        dialog.jobs.close()


def test_reopened_active_settings_save_approval_only_change_without_new_probe(qtbot):
    from dataclasses import replace

    cfg = replace(config(), mode="supervised", approve_each_step=True)
    dialog = ProviderSettings(cfg, provider_factory=FakeProvider)
    qtbot.addWidget(dialog)
    saved = []
    dialog.saved.connect(saved.append)
    try:
        assert dialog.probed_config is not None and dialog.pending is None
        dialog.approve_each_step.setChecked(False)
        dialog.apply()
        assert len(saved) == 1 and saved[0].approve_each_step is False
        dialog.max_output.setValue(cfg.max_output_tokens + 256)
        dialog.apply()
        assert len(saved) == 1  # Actual inference changes still require the probe.
    finally:
        dialog.jobs.close()


def test_settings_probe_has_one_total_deadline_and_ignores_late_success(qtbot):
    from dataclasses import replace

    class Stalled(FakeProvider):
        async def probe(self, model):
            await asyncio.sleep(60)

    dialog = ProviderSettings(replace(config(), timeout=5), provider_factory=Stalled)
    qtbot.addWidget(dialog)
    try:
        dialog.probe()
        job = dialog.pending
        qtbot.waitUntil(lambda: "remaining" in dialog.status.text(), timeout=2000)
        qtbot.waitUntil(lambda: dialog.pending is None, timeout=6000)
        assert "timed out" in dialog.status.text().lower()
        assert "5" in dialog.status.text()
        assert dialog.probed_config is None
        assert dialog.probe_button.isEnabled()
        assert not dialog.cancel_button.isEnabled()
        status = dialog.status.text()
        dialog._result(job, ProbeResult("vision:small", "abc", "test-1", True, True), "")
        assert dialog.status.text() == status
        assert dialog.probed_config is None
    finally:
        dialog.jobs.close()


def test_settings_retest_invalidates_previous_qualification_until_success(qtbot):
    class Failed(FakeProvider):
        async def probe(self, model):
            from monster.providers.base import ProviderError
            raise ProviderError("Ollama request timed out")

    dialog = ProviderSettings(config(), provider_factory=Failed)
    qtbot.addWidget(dialog)
    try:
        assert dialog.probed_config is not None
        dialog.probe()
        qtbot.waitUntil(lambda: dialog.pending is None)
        assert dialog.probed_config is None
        assert "timed out" in dialog.status.text()
    finally:
        dialog.jobs.close()


def test_settings_cancel_stops_deadline_and_allows_fresh_check(qtbot):
    class Stalled(FakeProvider):
        async def probe(self, model):
            await asyncio.sleep(60)

    dialog = ProviderSettings(config(), provider_factory=Stalled)
    qtbot.addWidget(dialog)
    try:
        dialog.probe()
        stale_job = dialog.pending
        dialog.cancel_clicked()
        assert dialog.pending is None
        assert not dialog.request_deadline.isActive()
        assert not dialog.request_progress.isActive()
        dialog.provider_factory = FakeProvider
        dialog.probe()
        dialog._result(stale_job, ProbeResult("wrong", "abc", "test-1", True, True), "")
        assert dialog.pending is not None
        qtbot.waitUntil(lambda: dialog.pending is None)
        assert "passed" in dialog.status.text()
        assert dialog.probed_config is not None
        assert not dialog.request_deadline.isActive()
        assert not dialog.request_progress.isActive()
    finally:
        dialog.jobs.close()


def test_model_settings_activity_is_visible_without_capturing_desktop(qtbot, qapp, tmp_path, monkeypatch):
    from monster.app import CompanionApp
    from monster.lifecycle import MockTaskController, State

    class Slow(FakeProvider):
        async def probe(self, model):
            await asyncio.sleep(0.1)
            return ProbeResult(model, "abc", "test-1", True, True)

    ui = CompanionApp(qapp, MockTaskController(), tmp_path / "prefs.json", enable_tray=False)
    captures = []
    monkeypatch.setattr(ui.isolation, "capture", lambda *args: captures.append(True))
    ui.model_config = config()
    try:
        ui.show_provider_settings()
        dialog = ui.provider_window
        dialog.provider_factory = Slow
        dialog.probe()
        assert ui.companion.accessibleName() == "Monster · Vision test"
        assert ui.companion.visual_state == State.THINKING
        assert ui.companion.isVisible()
        qtbot.waitUntil(lambda: dialog.pending is None)
        assert ui.companion.visual_state == State.IDLE
        dialog.probe()
        dialog.cancel_clicked()
        assert ui.companion.visual_state == State.IDLE
        dialog.probe()
        dialog.reject()
        assert ui.companion.visual_state == State.IDLE
        assert not captures
    finally:
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)


def test_model_settings_advanced_form_scrolls_without_hiding_request_controls(qtbot, qapp):
    dialog = ProviderSettings(config(), provider_factory=FakeProvider)
    qtbot.addWidget(dialog)
    dialog.resize(620, 460)
    dialog.show()
    dialog.advanced_toggle.setChecked(True)
    qapp.processEvents()
    assert dialog.height() == 460
    assert dialog.form_scroll.verticalScrollBar().maximum() > 0
    assert dialog.form_scroll.geometry().bottom() < dialog.status.geometry().top()
    for button in (dialog.probe_button, dialog.cancel_button):
        assert button.isVisible()
        assert dialog.rect().contains(button.geometry())
    dialog.reject()


def test_settings_timeout_report_identifies_pending_image_inference(qtbot):
    from dataclasses import replace

    import httpx

    from monster.providers.ollama import OllamaProvider

    async def handle(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "test-1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "vision:small", "digest": "abc"}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["vision"]})
        await asyncio.sleep(60)

    dialog = ProviderSettings(replace(config(), timeout=5),
                              provider_factory=lambda cfg: OllamaProvider(cfg, httpx.MockTransport(handle)))
    qtbot.addWidget(dialog)
    try:
        dialog.probe()
        qtbot.waitUntil(lambda: dialog.pending is None, timeout=6500)
        assert "Image inference + JSON" in dialog.status.text()
        report = dialog.check_details.toPlainText()
        for fact in ("server_version", "model_list", "model_metadata", "vision_inference",
                     "sha256", "16384", "4096", "No desktop screenshot", "/api/chat"):
            assert fact in report
        assert "thinking" not in report.lower() or "thinking=False" in report
        assert dialog.probed_config is None
    finally:
        dialog.jobs.close()


def test_settings_check_report_rejects_stale_progress_and_model_reasoning(qtbot):
    class Stalled(FakeProvider):
        async def probe(self, model):
            await asyncio.sleep(60)

    dialog = ProviderSettings(config(), provider_factory=Stalled)
    qtbot.addWidget(dialog)
    try:
        dialog.probe()
        old_serial = dialog.request_serial
        dialog.cancel_clicked()
        dialog.probe()
        current = dialog.check_details.toPlainText()
        dialog._provider_event(old_serial, "probe_stage", {"stage": "vision_inference"})
        dialog._provider_event(dialog.request_serial, "provider_structured_response", {"reasoning": "secret"})
        assert dialog.check_details.toPlainText() == current
        assert dialog.check_stage == ""
        dialog.cancel_clicked()
    finally:
        dialog.jobs.close()


def test_settings_check_report_identifies_internal_runtime_exception(qtbot):
    class Broken(FakeProvider):
        async def probe(self, model):
            raise RuntimeError("private payload must not appear")

    dialog = ProviderSettings(config(), provider_factory=Broken)
    qtbot.addWidget(dialog)
    try:
        dialog.probe()
        qtbot.waitUntil(lambda: dialog.pending is None)
        report = dialog.check_details.toPlainText()
        assert "internal_exception" in report
        assert "RuntimeError" in report
        assert "private payload" not in report
        assert dialog.probed_config is None
    finally:
        dialog.jobs.close()


@pytest.mark.parametrize("fresh_probe", [True, False])
@pytest.mark.parametrize("initial_hands_free", [True, False])
def test_hands_free_policy_saves_without_invalidating_model_check(qtbot, fresh_probe, initial_hands_free):
    from dataclasses import replace
    cfg = replace(config(), mode="supervised", hands_free=initial_hands_free)
    dialog = ProviderSettings(cfg, provider_factory=FakeProvider)
    qtbot.addWidget(dialog)
    saved = []
    dialog.saved.connect(saved.append)
    try:
        if fresh_probe:
            dialog.probe()
            qtbot.waitUntil(lambda: dialog.pending is None)
        dialog.hands_free.setChecked(not initial_hands_free)
        dialog.apply()
        assert len(saved) == 1
        assert saved[0].hands_free is not initial_hands_free
        if saved[0].hands_free:
            assert saved[0].approve_each_step is False
        dialog.context_size.setValue(cfg.context_size + 512)
        dialog.apply()
        assert len(saved) == 1  # Actual model settings still require a fresh check.
    finally:
        dialog.jobs.close()


@pytest.mark.parametrize("negative", ["vision", "structured", "model", "none", "boolean"])
def test_settings_rejects_negative_or_wrong_model_probe_results(qtbot, negative):
    from dataclasses import replace
    class InvalidProvider(FakeProvider):
        async def probe(self, model):
            if negative == "none":
                return None
            if negative == "boolean":
                return True
            return ProbeResult("another-model" if negative == "model" else model,
                               "abc", "test", negative != "vision", negative != "structured")
    dialog = ProviderSettings(replace(config(), mode="supervised"), provider_factory=InvalidProvider)
    qtbot.addWidget(dialog)
    saved = []
    dialog.saved.connect(saved.append)
    try:
        dialog.probe()
        qtbot.waitUntil(lambda: dialog.pending is None)
        dialog.apply()
        assert not saved
        assert dialog.probed_config is None
    finally:
        dialog.jobs.close()
