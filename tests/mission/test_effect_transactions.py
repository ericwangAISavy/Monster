import sqlite3
import subprocess
import sys
import threading

import pytest
from test_effects_policy import context, service


def test_atomic_consumption_under_two_writers(tmp_path):
    from monster.mission.effects import EffectCoordinator

    store, journal = service(tmp_path)
    e = journal.prepare(
        context(), {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1"
    )
    journal.approve(e.effect_id, context(), review_id="ui1")
    barrier = threading.Barrier(2)
    outcomes = []

    def work():
        conn = sqlite3.connect(tmp_path / "ledger.sqlite3")
        try:
            other = EffectCoordinator(conn, key=b"x" * 32)
            barrier.wait(timeout=2)
            try:
                other.dispatch(e.effect_id, context(), {"op": "text", "text": "safe"})
                outcomes.append(True)
            except ValueError:
                outcomes.append(False)
        finally:
            conn.close()

    workers = [threading.Thread(target=work) for _ in range(2)]
    try:
        for t in workers:
            t.start()
        for t in workers:
            t.join(timeout=5)
        assert sorted(outcomes) == [False, True]
        assert [x["code"] for x in journal.history(e.effect_id)].count(
            "dispatch_intent_recorded"
        ) == 1
    finally:
        store.__exit__()


def test_failed_audit_write_rolls_back_approval_consumption(tmp_path):
    store, journal = service(tmp_path)
    try:
        e = journal.prepare(
            context(), {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1"
        )
        journal.approve(e.effect_id, context(), review_id="ui1")
        store.connection.execute(
            "CREATE TRIGGER abort_intent BEFORE INSERT ON effect_audit "
            "WHEN NEW.code='dispatch_intent_recorded' BEGIN SELECT RAISE(ABORT,'fail'); END"
        )
        with pytest.raises(sqlite3.DatabaseError):
            journal.dispatch(e.effect_id, context(), {"op": "text", "text": "safe"})
        assert journal.get(e.effect_id).status == "approved"
        assert journal.approval(e.effect_id).consumed_at is None
        assert "dispatch_intent_recorded" not in [x["code"] for x in journal.history(e.effect_id)]
    finally:
        store.__exit__()


def test_busy_journal_fails_without_consuming(tmp_path):
    from monster.mission.effects import EffectCoordinator

    store, journal = service(tmp_path)
    other = sqlite3.connect(tmp_path / "ledger.sqlite3", timeout=0.01)
    try:
        e = journal.prepare(
            context(), {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1"
        )
        journal.approve(e.effect_id, context(), review_id="ui1")
        store.connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError):
            EffectCoordinator(other, key=b"x" * 32).dispatch(
                e.effect_id, context(), {"op": "text", "text": "safe"}
            )
        store.connection.rollback()
        assert journal.approval(e.effect_id).consumed_at is None
    finally:
        other.close()
        store.__exit__()


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("approval_pending", "not_dispatched"),
        ("approved", "not_dispatched"),
        ("dispatch_intent_recorded", "uncertain"),
        ("dispatch_started", "uncertain"),
        ("awaiting_observation", "uncertain"),
        ("observed_success", "observed_success"),
        ("observed_failure", "observed_failure"),
    ],
)
def test_real_process_exit_at_every_journal_boundary(tmp_path, stage, expected):
    code = """
import os,sys
from pathlib import Path
from monster.mission.repository import Repository
from monster.mission.effects import EffectCoordinator
c=dict(mission_id='m',subtask_id='s',attempt_id='a',workspace_id='w',generation=1,
       target_ref='scratch',observation_ref='frame',scene_ref='scene',active=True,
       lease_valid=True,cleanup_healthy=True)
with Repository(Path(sys.argv[1])) as store:
    j=EffectCoordinator(store.connection,key=b'x'*32)
    e=j.prepare(c,{'op':'text','text':'safe'},expected_text='safe',review_id='review')
    stage=sys.argv[2]
    if stage!='approval_pending':j.approve(e.effect_id,c,review_id='review')
    if stage not in ('approval_pending','approved'):j.dispatch(e.effect_id,c,{'op':'text','text':'safe'})
    if stage in ('dispatch_started','awaiting_observation','observed_success','observed_failure'):j.started(e.effect_id)
    if stage in ('awaiting_observation','observed_success','observed_failure'):j.receipt(e.effect_id,inserted=True)
    if stage.startswith('observed_'):j.observe(e.effect_id,'succeeded' if stage=='observed_success' else 'failed',observation_ref='after')
    os._exit(37)
"""
    child = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), stage], check=False, timeout=10
    )
    assert child.returncode == 37
    store, journal = service(tmp_path)
    try:
        journal.recover()
        record = journal.records()[0]
        assert record.status == expected
        with pytest.raises(ValueError):
            journal.dispatch(record.effect_id, context(), {"op": "text", "text": "safe"})
    finally:
        store.__exit__()


def test_canonical_hmac_and_changed_review_bindings(tmp_path):
    store, j = service(tmp_path)
    try:
        assert j.digest({"a": 1, "b": 2}) == j.digest({"b": 2, "a": 1})
        e = j.prepare(
            context(), {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1"
        )
        with pytest.raises(ValueError):
            j.approve(e.effect_id, context(), review_id="wrong")
        j.approve(e.effect_id, context(), review_id="ui1")
        with pytest.raises(ValueError):
            j.dispatch(e.effect_id, context(), {"op": "text", "text": "changed"})
        assert j.get(e.effect_id).status == "not_dispatched"
    finally:
        store.__exit__()
