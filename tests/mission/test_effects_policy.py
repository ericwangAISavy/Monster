import sqlite3
import time

import pytest


def context(**changes):
    return dict(
        mission_id="m",
        subtask_id="s",
        attempt_id="attempt",
        workspace_id="desktop",
        generation=1,
        target_ref="scratch",
        observation_ref="frame1",
        scene_ref="scene1",
        active=True,
        lease_valid=True,
        cleanup_healthy=True,
        **changes,
    )


def service(tmp_path, clock=None):
    from monster.mission.effects import EffectCoordinator
    from monster.mission.repository import Repository

    store = Repository(tmp_path).__enter__()
    return store, EffectCoordinator(store.connection, key=b"x" * 32, clock=clock or time.time)


@pytest.mark.parametrize(
    "risk,expected",
    [
        ("observe_only", "allow"),
        ("local_reversible", "review"),
        ("local_persistent", "review"),
        ("external_commit", "review"),
        ("destructive", "deny"),
        ("credential_or_identity", "handoff"),
        ("financial_or_legal", "deny"),
        ("unknown", "deny"),
    ],
)
def test_policy_matrix(risk, expected):
    from monster.mission.policy import evaluate

    assert evaluate(risk, active=True, live=False).decision == expected
    assert evaluate(risk, active=False, live=False).decision == "deny"


def test_model_and_page_cannot_reduce_risk_or_enable_commits():
    from monster.mission.policy import classify, evaluate

    assert (
        classify({"op": "press", "key": "ENTER"}, expected_text="safe", model_risk="benign")
        == "unknown"
    )
    assert (
        classify(
            {"op": "text", "text": "password=secret"}, expected_text="safe", model_risk="benign"
        )
        == "unknown"
    )
    assert (
        classify({"op": "click", "x": 5, "y": 5}, expected_text="safe", model_risk="benign")
        == "unknown"
    )
    assert (
        classify({"op": "text", "text": "safe"}, expected_text="safe", model_risk="benign")
        == "local_reversible"
    )
    assert evaluate("external_commit", active=True, live=True).decision == "deny"


def test_fingerprint_scope_single_use_and_no_plaintext(tmp_path):
    store, s = service(tmp_path)
    try:
        prepared = s.prepare(
            context(), {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1"
        )
        eid = prepared.effect_id
        s.approve(eid, context(), review_id="ui1")
        s.dispatch(eid, context(), {"op": "text", "text": "safe"})
        with pytest.raises(ValueError):
            s.dispatch(eid, context(), {"op": "text", "text": "safe"})
        assert s.get(eid).status == "dispatch_intent_recorded"
        data = b"".join(p.read_bytes() for p in tmp_path.glob("*.sqlite3*"))
        assert b'"text":"safe"' not in data
        assert b"x" * 32 not in data
    finally:
        store.__exit__()


@pytest.mark.parametrize(
    "field,value",
    [
        ("generation", 2),
        ("workspace_id", "other"),
        ("target_ref", "other"),
        ("scene_ref", "other"),
        ("lease_valid", False),
        ("active", False),
    ],
)
def test_approval_changed_scope_denies_dispatch(tmp_path, field, value):
    store, s = service(tmp_path)
    try:
        c = context()
        e = s.prepare(c, {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1")
        s.approve(e.effect_id, c, review_id="ui1")
        with pytest.raises(ValueError):
            s.dispatch(e.effect_id, c | {field: value}, {"op": "text", "text": "safe"})
        assert s.get(e.effect_id).status != "dispatch_intent_recorded"
    finally:
        store.__exit__()


def test_expiry_revocation_and_atomic_audit(tmp_path):
    now = [time.time()]
    store, s = service(tmp_path, lambda: now[0])
    try:
        e = s.prepare(
            context(), {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1"
        )
        s.approve(e.effect_id, context(), review_id="ui1")
        now[0] += 61
        with pytest.raises(ValueError):
            s.dispatch(e.effect_id, context(), {"op": "text", "text": "safe"})
        s.revoke(e.effect_id)
        with pytest.raises(ValueError):
            s.dispatch(e.effect_id, context(), {"op": "text", "text": "safe"})
        with pytest.raises(sqlite3.DatabaseError):
            store.connection.execute("DELETE FROM effect_audit")
        with pytest.raises(sqlite3.DatabaseError):
            store.connection.execute("UPDATE effect_audit SET code='forged'")
    finally:
        store.__exit__()


def test_inserted_is_not_success_and_restart_never_replays(tmp_path):
    store, s = service(tmp_path)
    try:
        e = s.prepare(
            context(), {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1"
        )
        s.approve(e.effect_id, context(), review_id="ui1")
        s.dispatch(e.effect_id, context(), {"op": "text", "text": "safe"})
        s.receipt(e.effect_id, inserted=True)
        assert s.get(e.effect_id).status == "awaiting_observation"
        s.recover()
        assert s.get(e.effect_id).status == "uncertain"
        with pytest.raises(ValueError):
            s.dispatch(e.effect_id, context(), {"op": "text", "text": "safe"})
        s.attest(e.effect_id, "success", observation_ref="manual1")
        assert s.get(e.effect_id).status == "observed_success"
        assert s.history(e.effect_id)[-1]["source"] == "human_attested"
    finally:
        store.__exit__()


def test_human_reconciler_is_observe_only(tmp_path):
    import asyncio

    from monster.mission.reconciliation import HumanEffectReconciler

    store, s = service(tmp_path)
    try:
        e = s.prepare(
            context(), {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1"
        )
        s.approve(e.effect_id, context(), review_id="ui1")
        s.dispatch(e.effect_id, context(), {"op": "text", "text": "safe"})
        s.recover()
        result = asyncio.run(
            HumanEffectReconciler(s).reconcile(s.get(e.effect_id), workspace_id="desktop")
        )
        assert result.status == "human_review_required"
        assert s.get(e.effect_id).status == "uncertain"
    finally:
        store.__exit__()


def test_uncertain_prior_action_blocks_in_attempt_continuation(tmp_path):
    store, s = service(tmp_path)
    try:
        c = context()
        e = s.prepare(c, {"op": "press", "key": "HOME"}, expected_text="safe", review_id="ui1")
        s.approve(e.effect_id, c, review_id="ui1")
        s.dispatch(e.effect_id, c, {"op": "press", "key": "HOME"})
        s.receipt(e.effect_id, inserted=True)
        s.observe(e.effect_id, "uncertain", observation_ref="frame2")
        with pytest.raises(ValueError, match="Unresolved"):
            s.prepare(c, {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui2")
    finally:
        store.__exit__()


def test_version2_effect_and_approval_require_security_bindings():
    from pydantic import ValidationError

    from monster.mission.contracts import Approval, Effect

    with pytest.raises(ValidationError):
        Effect(
            schema_version=2,
            effect_id="e",
            mission_id="m",
            subtask_id="s",
            generation=1,
            status="prepared",
            target_ref="t",
            category="unknown",
        )
    with pytest.raises(ValidationError):
        Approval(
            schema_version=2,
            approval_id="a",
            effect_id="e",
            mission_id="m",
            subtask_id="s",
            generation=1,
            expires_at=time.time() + 60,
        )


def test_success_without_observation_is_rejected(tmp_path):
    store, j = service(tmp_path)
    try:
        e = j.prepare(
            context(), {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1"
        )
        j.approve(e.effect_id, context(), review_id="ui1")
        j.dispatch(e.effect_id, context(), {"op": "text", "text": "safe"})
        j.receipt(e.effect_id, inserted=True)
        with pytest.raises(ValueError):
            j.observe(e.effect_id, "succeeded", observation_ref=None)
        assert j.get(e.effect_id).status == "awaiting_observation"
    finally:
        store.__exit__()


def test_rejection_has_human_provenance(tmp_path):
    store, j = service(tmp_path)
    try:
        e = j.prepare(
            context(), {"op": "text", "text": "safe"}, expected_text="safe", review_id="ui1"
        )
        j.revoke(e.effect_id, decision="denied")
        assert j.approval(e.effect_id).decision == "denied"
        assert any(
            x["source"] == "human" and x["code"] == "approval_denied"
            for x in j.history(e.effect_id)
        )
        with pytest.raises(ValueError):
            j.dispatch(e.effect_id, context(), {"op": "text", "text": "safe"})
    finally:
        store.__exit__()


def test_unknown_permission_fields_are_not_silently_ignored(tmp_path):
    store, j = service(tmp_path)
    try:
        with pytest.raises(ValueError):
            j.prepare(
                context() | {"recipient": "not-supported"},
                {"op": "text", "text": "safe"},
                expected_text="safe",
                review_id="ui1",
            )
    finally:
        store.__exit__()


@pytest.mark.parametrize("active", [1, "yes", None])
def test_malformed_consent_fails_closed(active):
    from monster.mission.policy import evaluate

    assert evaluate("local_reversible", active=active).decision == "deny"
