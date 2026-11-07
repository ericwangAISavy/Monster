import sqlite3
import time

import pytest


def test_contracts_reject_content_and_unsafe_limits():
    from pydantic import ValidationError

    from monster.mission.contracts import Mission, Subtask

    task = Subtask(subtask_id="s1", objective_ref="goal-1", success_refs=["visible-1"])
    assert task.max_actions == 10
    for change in (
        {"screenshot": b"private"},
        {"objective_ref": "my password is secret"},
        {"max_actions": 11},
        {"max_model_calls": 21},
        {"deadline_seconds": 301},
    ):
        with pytest.raises(ValidationError):
            Subtask.model_validate(task.model_dump() | change)
    with pytest.raises(ValidationError):
        Mission(
            mission_id="m1",
            deadline_utc=time.time() + 60,
            objective_ref="goal",
            subtasks=[task, task],
        )
    with pytest.raises(ValidationError):
        Mission(
            mission_id="m1",
            deadline_utc=time.time() + 60,
            objective_ref="goal",
            subtasks=[task],
            password="secret",
        )


def test_dependency_cycles_and_unknown_dependencies_rejected():
    from pydantic import ValidationError

    from monster.mission.contracts import Mission, Subtask

    for deps in (["s1"], ["missing"]):
        with pytest.raises(ValidationError):
            Mission(
                mission_id="m",
                deadline_utc=time.time() + 60,
                objective_ref="goal",
                subtasks=[
                    Subtask(subtask_id="s1", objective_ref="g", success_refs=["v"], depends_on=deps)
                ],
            )


def test_state_migration_and_future_version():
    from monster.mission.contracts import migrate_state, valid_transition

    assert migrate_state({"schema_version": 0, "status": "waiting"}) == {
        "schema_version": 1,
        "status": "waiting_approval",
    }
    with pytest.raises(ValueError):
        migrate_state({"schema_version": 99})
    assert valid_transition("running", "waiting_approval")
    assert not valid_transition("completed", "running")


def test_store_migrates_v1_and_rejects_future_schema(tmp_path):
    from monster.mission.repository import Repository

    db = tmp_path / "ledger.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE missions (mission_id TEXT PRIMARY KEY, spec TEXT NOT NULL, "
        "created_at REAL NOT NULL, terminal_at REAL)"
    )
    conn.execute("PRAGMA user_version=1")
    conn.close()
    with Repository(tmp_path) as store:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert "stop_requested" in {
            r[1] for r in store.connection.execute("PRAGMA table_info(missions)")
        }
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA user_version=99")
    conn.close()
    with pytest.raises(ValueError, match="schema"), Repository(tmp_path):
        pass


def test_exclusive_runtime_lock_and_release(tmp_path):
    from monster.mission.repository import Repository

    with Repository(tmp_path), pytest.raises(RuntimeError, match="owned"), Repository(tmp_path):
        pass
    with Repository(tmp_path):
        pass


def test_interrupted_migration_rolls_back_all_ddl(tmp_path, monkeypatch):
    from monster.mission import repository

    original = sqlite3.connect

    class FaultConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA user_version=3":
                raise RuntimeError("simulated migration crash")
            return super().execute(sql, *args, **kwargs)

    monkeypatch.setattr(
        repository.sqlite3, "connect", lambda *a, **kw: original(*a, **kw, factory=FaultConnection)
    )
    with pytest.raises(RuntimeError, match="migration crash"), repository.Repository(tmp_path):
        pass
    monkeypatch.setattr(repository.sqlite3, "connect", original)
    with repository.Repository(tmp_path) as store:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_cancellation_flag_is_monotonic(tmp_path):
    from test_graph import mission

    from monster.mission.repository import Repository

    with Repository(tmp_path) as store:
        store.create(mission())
        store.stop("mission1", cancel=True)
        store.stop("mission1", cancel=False)
        assert store.control("mission1")["cancelled"] == 1


def test_ledger_requires_evidence_for_observed_success(tmp_path):
    from test_graph import mission

    from monster.mission.contracts import Effect
    from monster.mission.repository import Repository

    with Repository(tmp_path) as store:
        store.create(mission())
        effect = Effect(
            effect_id="e1",
            mission_id="mission1",
            subtask_id="s0",
            generation=1,
            status="prepared",
            target_ref="fake-app",
            category="read_only",
        )
        store.prepare(effect)
        for status in ("approved", "dispatch_started"):
            store.transition(effect, status)
            effect = store.effect("mission1", "s0")
        with pytest.raises(ValueError, match="evidence"):
            store.transition(effect, "observed_success")


def test_unknown_effect_status_rejected():
    from pydantic import ValidationError

    from monster.mission.contracts import Effect

    with pytest.raises(ValidationError):
        Effect(
            effect_id="e",
            mission_id="m",
            subtask_id="s",
            generation=1,
            status="done",
            target_ref="t",
            category="read_only",
        )
