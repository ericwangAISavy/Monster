from PySide6.QtWidgets import QMessageBox, QPushButton

from monster.app import CompanionApp
from monster.lifecycle import MockTaskController
from monster.ui.workspace_settings import WorkspaceSettings
from monster.workspaces.contracts import WorkspaceDescriptor, new_id
from monster.workspaces.runtime import WorkspaceRuntime


def test_workspace_settings_are_opt_in_and_secondary(qtbot):
    window = WorkspaceSettings()
    qtbot.addWidget(window)
    requests = []
    window.mode_requested.connect(requests.append)
    assert window.mode.currentData() == "personal_desktop"
    window.mode.setCurrentIndex(1)
    assert requests == ["guest_simulation"]
    assert any("Stop guest" in b.text() for b in window.findChildren(QPushButton))


def test_qt_runtime_pair_review_simulate_preview_and_stop_remain_responsive(qtbot, tmp_path):
    runtime = WorkspaceRuntime(
        tmp_path, WorkspaceDescriptor(workspace_id=new_id(), display_name="Qt guest")
    )
    changes, offers, reviews, frames, retired = [], [], [], [], []
    runtime.changed.connect(changes.append)
    runtime.pairing_offer.connect(offers.append)
    runtime.review_requested.connect(reviews.append)
    runtime.preview_ready.connect(frames.append)
    runtime.retired.connect(lambda: retired.append(True))
    runtime.start()
    try:
        qtbot.waitUntil(
            lambda: runtime.loop is not None and runtime.guest is not None, timeout=8000
        )
        runtime.begin_pairing()
        qtbot.waitUntil(lambda: bool(offers), timeout=8000)
        assert runtime.client is None
        runtime.confirm_pairing(offers[0])
        qtbot.waitUntil(lambda: any(not p["stale"] for p in changes), timeout=8000)
        runtime.start_mission()
        qtbot.waitUntil(lambda: bool(reviews), timeout=8000)
        assert not runtime.guest.runner.calls
        runtime.answer(reviews[-1].challenge_id, "approve")
        qtbot.waitUntil(
            lambda: any((p.get("status") or {}).get("state") == "completed" for p in changes),
            timeout=8000,
        )
        assert len(runtime.guest.runner.calls) == 6
        runtime.preview()
        qtbot.waitUntil(lambda: bool(frames), timeout=8000)
        assert frames[0].provenance == "synthetic"
        runtime.stop()
        qtbot.waitUntil(lambda: any(p["stop"] == "confirmed" for p in changes), timeout=8000)
    finally:
        runtime.close()
        qtbot.waitUntil(lambda: bool(retired), timeout=8000)
        runtime.thread.join(timeout=2)


def test_host_application_keeps_personal_default_and_routes_guest_review(
    qtbot, qapp, tmp_path, monkeypatch
):
    engine = MockTaskController(interval_ms=10)
    personal_submissions = []
    engine.submit = lambda text: personal_submissions.append(text)
    ui = CompanionApp(qapp, engine, tmp_path / "prefs.json", enable_tray=False)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    runtime = None
    try:
        assert ui.workspace_mode == "personal_desktop" and ui.workspace_runtime is None
        ui.show_workspaces()
        ui.register_workspace()
        assert ui.select_workspace_mode("guest_simulation")
        runtime = ui.workspace_runtime
        qtbot.waitUntil(
            lambda: runtime.loop is not None and runtime.guest is not None, timeout=8000
        )
        ui._workspace_command("begin_pairing")
        qtbot.waitUntil(
            lambda: runtime.client is not None and runtime.client.connected, timeout=8000
        )
        ui._workspace_command("start_mission")
        qtbot.waitUntil(
            lambda: bool(ui.pending_question) and ui.pending_question[0].startswith("workspace-"),
            timeout=8000,
        )
        rid = ui.pending_question[0]
        ui.answer("workspace-" + new_id(), "approve")
        assert not runtime.guest.runner.calls
        ui.answer(rid, "approve")
        qtbot.waitUntil(lambda: len(runtime.guest.runner.calls) == 6, timeout=8000)
        ui.submit("type arbitrary text on my host")
        assert not personal_submissions
        ui.stop()
        qtbot.waitUntil(lambda: runtime.last_projection["stop"] == "confirmed", timeout=8000)
        assert ui.select_workspace_mode("personal_desktop")
        ui.submit("ordinary personal task")
        assert personal_submissions == ["ordinary personal task"]
    finally:
        retired = []
        if runtime:
            runtime.retired.connect(lambda: retired.append(True))
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)
        if runtime:
            qtbot.waitUntil(lambda: bool(retired), timeout=8000)
            runtime.thread.join(timeout=2)


def test_qt_stop_latch_invalidates_queued_remote_approval(qtbot, tmp_path):
    runtime = WorkspaceRuntime(
        tmp_path, WorkspaceDescriptor(workspace_id=new_id(), display_name="Stop guest")
    )
    offers, reviews, retired = [], [], []
    runtime.pairing_offer.connect(offers.append)
    runtime.review_requested.connect(reviews.append)
    runtime.retired.connect(lambda: retired.append(True))
    runtime.start()
    try:
        qtbot.waitUntil(lambda: runtime.guest is not None, timeout=8000)
        runtime.begin_pairing()
        qtbot.waitUntil(lambda: bool(offers), timeout=8000)
        runtime.confirm_pairing(offers[0])
        qtbot.waitUntil(
            lambda: runtime.client is not None and runtime.client.connected, timeout=8000
        )
        runtime.start_mission()
        qtbot.waitUntil(lambda: bool(reviews), timeout=8000)
        runtime.stop()
        runtime.answer(reviews[0].challenge_id, "approve")
        qtbot.waitUntil(lambda: runtime.last_projection["stop"] == "confirmed", timeout=8000)
        assert runtime.guest.runner.calls == []
    finally:
        runtime.close()
        qtbot.waitUntil(lambda: bool(retired), timeout=8000)
        runtime.thread.join(timeout=2)


def test_host_qt_remains_responsive_during_indefinite_guest_stall(qtbot, tmp_path):
    from PySide6.QtCore import QTimer

    runtime = WorkspaceRuntime(
        tmp_path, WorkspaceDescriptor(workspace_id=new_id(), display_name="Stalled guest")
    )
    offers, reviews, retired, pulses = [], [], [], []
    runtime.pairing_offer.connect(offers.append)
    runtime.review_requested.connect(reviews.append)
    runtime.retired.connect(lambda: retired.append(True))
    runtime.start()
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: pulses.append(True))
    try:
        qtbot.waitUntil(lambda: runtime.guest is not None, timeout=8000)
        runtime.begin_pairing()
        qtbot.waitUntil(lambda: bool(offers), timeout=8000)
        runtime.confirm_pairing(offers[0])
        qtbot.waitUntil(
            lambda: runtime.client is not None and runtime.client.connected, timeout=8000
        )

        async def stall():
            runtime.guest.runner.outcomes["pilot-a"] = "hang"

        runtime._schedule(stall)
        runtime.start_mission()
        qtbot.waitUntil(lambda: bool(reviews), timeout=8000)
        runtime.answer(reviews[0].challenge_id, "approve")
        qtbot.waitUntil(lambda: bool(runtime.guest.runner.calls), timeout=8000)
        timer.start()
        qtbot.waitUntil(lambda: len(pulses) >= 5, timeout=1000)
        runtime.stop()
        qtbot.waitUntil(lambda: runtime.last_projection["stop"] == "confirmed", timeout=8000)
        assert len(runtime.guest.runner.calls) == 1
    finally:
        timer.stop()
        runtime.close()
        qtbot.waitUntil(lambda: bool(retired), timeout=8000)
        runtime.thread.join(timeout=2)


def test_workspace_pairing_surface_does_not_open_during_personal_input(
    qtbot, qapp, tmp_path, monkeypatch
):
    ui = CompanionApp(qapp, MockTaskController(), tmp_path / "prefs.json", enable_tray=False)
    stops = []
    try:
        ui.active = True
        monkeypatch.setattr(ui, "stop", lambda: stops.append(True))
        ui.show_workspaces()
        assert stops == [True]
        assert ui.workspace_window is None
    finally:
        ui.active = False
        ui.shutdown()
        qapp.aboutToQuit.disconnect(ui.shutdown)
