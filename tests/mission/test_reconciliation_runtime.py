import asyncio
import sqlite3
from types import SimpleNamespace

from test_effects_policy import context, service
from test_gui_adapter import setup


def test_reconciliation_storage_error_is_not_empty_success(qtbot, tmp_path):
    from monster.mission.runtime import MissionRuntime

    _c, _g, b, r = setup(qtbot, tmp_path)
    runtime = MissionRuntime(tmp_path / "unused", r, b, reconciliation_only=True)

    class Broken:
        def attest(self, *args, **kwargs):
            raise sqlite3.OperationalError("private details")

    runtime.coordinator = SimpleNamespace(effects=Broken())
    errors, records = [], []
    runtime.reconciliation_error.connect(errors.append)
    runtime.reconciliation_ready.connect(records.append)
    asyncio.run(runtime._attest_effect("e1", "success"))
    assert errors and records == []
    assert "private details" not in errors[0]
    runtime.close()


def test_global_reconciliation_keeps_all_missions_visible(qtbot, tmp_path):
    from monster.mission.runtime import MissionRuntime

    store, journal = service(tmp_path / "store")
    _c, _g, b, r = setup(qtbot, tmp_path)
    runtime = MissionRuntime(tmp_path / "unused", r, b, reconciliation_only=True)
    runtime.coordinator = SimpleNamespace(effects=journal)
    runtime.mission_id = "m2"
    ids = []
    try:
        for i, mid in enumerate(("m1", "m1", "m2")):
            c = context() | {
                "mission_id": mid,
                "attempt_id": f"attempt{i}",
                "workspace_id": f"ws{i}",
            }
            e = journal.prepare(
                c, {"op": "text", "text": "safe"}, expected_text="safe", review_id=f"ui{i}"
            )
            journal.approve(e.effect_id, c, review_id=f"ui{i}")
            journal.dispatch(e.effect_id, c, {"op": "text", "text": "safe"})
            ids.append(e.effect_id)
        journal.recover()
        records = []
        runtime.reconciliation_ready.connect(records.append)
        asyncio.run(runtime._attest_effect(ids[0], "success"))
        assert {e.effect_id for e in records[-1]} == set(ids[1:])
    finally:
        runtime.close()
        store.__exit__()
