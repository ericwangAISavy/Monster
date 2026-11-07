import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QPlainTextEdit

from monster.mission.runner import FakeGuiSubtaskRunner
from monster.mission.runtime import MissionRuntime
from monster.mission.strategic.catalog import CATALOG, compile_plan, fixture_goal
from monster.mission.strategic.contracts import StrategicLimits, default_budget, fake_roles
from monster.mission.strategic.planning import FakeStrategicPlanner
from monster.ui.strategic_review import StrategicReviewDialog


def test_phase5_plan_review_is_explicit_and_readable(qtbot):
    goal = fixture_goal()
    proposal = asyncio.run(
        FakeStrategicPlanner().propose_plan(
            goal, capabilities=CATALOG, limits=StrategicLimits(), context={}
        )
    )
    plan = compile_plan(
        proposal,
        goal,
        fake_roles(),
        StrategicLimits(),
        default_budget(),
        mid="ui",
        revision_id="r1",
        now=100.0,
    )
    dialog = StrategicReviewDialog(plan)
    qtbot.addWidget(dialog)
    received = []
    dialog.simulate.connect(lambda: received.append(True))
    dialog.show()
    content = dialog.findChild(QPlainTextEdit).toPlainText()
    assert plan.digest() in content and "no desktop input" in content
    assert "no real design document" in content and "no-egress" in content
    qtbot.keyClick(dialog, Qt.Key.Key_Return)
    # Keyboard activation uses the dialog's explicit action button, not the GUI worker.
    if not received:
        button = next(
            b for b in dialog.findChild(QDialogButtonBox).buttons() if "Simulate" in b.text()
        )
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert received == [True]


def test_phase5_runtime_prepares_and_simulates_on_worker_thread(qtbot, tmp_path):
    runtime = MissionRuntime(
        tmp_path,
        FakeGuiSubtaskRunner(),
        None,
        strategic_text="Prepare a short technical design proposal.",
    )
    updates, retired = [], []
    runtime.changed.connect(updates.append)
    runtime.retired.connect(lambda: retired.append(True))
    runtime.start()
    try:
        qtbot.waitUntil(lambda: any(u.get("strategic_consent") for u in updates), timeout=8000)
        assert runtime.runner.calls == []
        runtime.answer("approve")
        qtbot.waitUntil(lambda: any(u.get("consent") for u in updates), timeout=8000)
        assert runtime.runner.calls == []
        runtime.answer("approve")
        qtbot.waitUntil(
            lambda: any(u["state"]["status"] == "completed" for u in updates), timeout=8000
        )
        assert len(runtime.runner.calls) == 6
        assert runtime.last_report["strategic"]["evaluation"]["status"] == "completed"
    finally:
        runtime.close()
        qtbot.waitUntil(lambda: bool(retired), timeout=8000)
        runtime.thread.join(timeout=2)


def test_phase5_runtime_cancelled_plan_review_never_runs(qtbot, tmp_path):
    runtime = MissionRuntime(
        tmp_path,
        FakeGuiSubtaskRunner(),
        None,
        strategic_text="Prepare a short technical design proposal.",
    )
    updates, retired = [], []
    runtime.changed.connect(updates.append)
    runtime.retired.connect(lambda: retired.append(True))
    runtime.start()
    try:
        qtbot.waitUntil(lambda: any(u.get("strategic_consent") for u in updates), timeout=8000)
        runtime.stop()
        qtbot.waitUntil(
            lambda: any(u["state"]["status"] == "cancelled" for u in updates), timeout=8000
        )
        runtime.answer("approve")
        qtbot.wait(30)
        assert runtime.runner.calls == []
    finally:
        runtime.close()
        qtbot.waitUntil(lambda: bool(retired), timeout=8000)
        runtime.thread.join(timeout=2)
