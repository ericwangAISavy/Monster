"""Standalone input must follow shared-ledger intent, without fabricated reviews."""

import json
import sqlite3
from dataclasses import replace

import pytest
from PySide6.QtCore import QTimer
from test_supervised import controller, observation

from monster.agent.standalone_journal import StandaloneJournal
from monster.mission.effects import EffectCoordinator
from monster.mission.repository import Repository
from monster.providers.base import Decision, Verification


def attach(engine, directory):
    journal = StandaloneJournal(directory, engine)
    engine.standalone_journal = journal
    engine.standalone_lease_valid = lambda: True
    journal.replied.connect(engine.standalone_reply)
    return journal


def records(directory):
    with sqlite3.connect(directory / 'ledger.sqlite3') as c:
        return [json.loads(row[0]) for row in c.execute('SELECT record FROM action_effects')]


@pytest.mark.parametrize('each,hands', [(False, False), (True, True), (True, False)])
def test_real_controller_commits_before_fake_input_and_preserves_review(qtbot, tmp_path, each, hands):
    engine, bridge, _ = controller([
        Decision('action', 'Press Enter', {'op': 'press', 'key': 'ENTER'}, risk='routine',
                 expected_result='Dialog closes'), Decision('complete', 'Done')])
    engine.config = replace(engine.config, approve_each_step=each, hands_free=hands)
    attach(engine, tmp_path)
    engine.capture_requested.connect(lambda engine=engine: engine.observation_ready(observation(), engine.generation))
    questions, handed_off, terminal = [], [], []
    engine.terminal_ready.connect(terminal.append)

    def review(rid, text):
        questions.append(text)
        QTimer.singleShot(1, lambda: engine.respond(rid, 'approve'))
    engine.approval_requested.connect(review)
    original = bridge.submit

    def submit(operation, *args):
        if operation == 'execute':
            row, = records(tmp_path)
            assert row['status'] == 'dispatch_intent_recorded'
            assert row['authorization_source'] == ('standalone_human' if each and not hands else 'standalone_automatic')
            assert row['schema_version'] == 4
            handed_off.append(row)
        return original(operation, *args)
    bridge.submit = submit
    try:
        engine.submit('Press Enter')
        qtbot.waitUntil(lambda terminal=terminal: bool(terminal), timeout=10000)
        engine.standalone_journal.executor.submit(lambda: None).result(timeout=3)
        assert len(handed_off) == len(bridge.executed) == 1
        assert len(questions) == (2 if each and not hands else 0 if hands else 1)
        assert records(tmp_path)[0]['status'] == 'observed_success'
        with sqlite3.connect(tmp_path / 'ledger.sqlite3') as c:
            assert c.execute('SELECT COUNT(*) FROM action_approvals').fetchone()[0] == 0
    finally:
        engine.close()


@pytest.mark.parametrize('failure', ['locked', 'intent_write', 'missing_contract', 'lost_lease', 'changed_after_intent'])
def test_journal_and_binding_failures_produce_zero_input(qtbot, tmp_path, failure):
    engine, bridge, _ = controller([
        Decision('action', 'Press Enter', {'op': 'press', 'key': 'ENTER'}, risk='routine')])
    engine.config = replace(engine.config, approve_each_step=False)
    journal = attach(engine, tmp_path)
    engine.capture_requested.connect(lambda engine=engine: engine.observation_ready(observation(), engine.generation))
    terminal = []
    engine.terminal_ready.connect(terminal.append)
    owner = None
    if failure == 'locked':
        owner = Repository(tmp_path).__enter__()
    if failure == 'intent_write':
        with Repository(tmp_path) as repo:
            repo.connection.execute("CREATE TRIGGER reject_intent BEFORE INSERT ON action_effects "
                                    "BEGIN SELECT RAISE(ABORT, 'write failed'); END")
    original = journal.intent

    def intent(grounded, nonce, decision):
        if failure == 'changed_after_intent':
            # This runs on Qt after the immutable packet was constructed.
            engine.pending_contract = engine.pending_contract.model_copy(update={'task_ref': 'changed'})
        return original(grounded, nonce, decision)
    journal.intent = intent
    if failure == 'lost_lease':
        engine.standalone_lease_valid = lambda: False
    if failure == 'missing_contract':
        engine._mapped_contract = lambda action: None
    try:
        engine.submit('Press Enter')
        qtbot.waitUntil(lambda terminal=terminal: bool(terminal), timeout=10000)
        journal.executor.submit(lambda: None).result(timeout=3)
        assert not bridge.executed
        if failure == 'changed_after_intent':
            assert records(tmp_path)[0]['status'] == 'uncertain'
    finally:
        engine.close()
        if owner:
            owner.__exit__()


def test_uncertain_standalone_blocks_next_task_and_restart(qtbot, tmp_path):
    for attempt in range(2):
        engine, bridge, model = controller([
            Decision('action', 'Press Enter', {'op': 'press', 'key': 'ENTER'}, risk='routine')])
        engine.config = replace(engine.config, hands_free=True)
        journal = attach(engine, tmp_path)
        async def uncertain(*args):
            return Verification('uncertain', 'No clear result')
        model.verify_action = uncertain
        terminal = []
        engine.terminal_ready.connect(terminal.append)
        engine.capture_requested.connect(lambda engine=engine: engine.observation_ready(observation(), engine.generation))
        try:
            engine.submit('Press Enter')
            qtbot.waitUntil(lambda terminal=terminal: bool(terminal), timeout=10000)
            journal.executor.submit(lambda: None).result(timeout=3)
            assert len(bridge.executed) == (1 if attempt == 0 else 0)
            assert records(tmp_path)[0]['status'] == 'uncertain'
        finally:
            engine.close()
    with Repository(tmp_path) as repo:
        effects = EffectCoordinator(repo.connection, live=True)
        effects.recover()
        assert len(effects.unresolved()) == 1


def test_late_observation_write_failure_cannot_report_completion(qtbot, tmp_path):
    engine, bridge, _ = controller([
        Decision('action', 'Press Enter', {'op': 'press', 'key': 'ENTER'}, risk='routine'),
        Decision('complete', 'Done')])
    engine.config = replace(engine.config, hands_free=True)
    journal = attach(engine, tmp_path)
    with Repository(tmp_path) as repo:
        repo.connection.execute("CREATE TRIGGER reject_observation BEFORE UPDATE ON action_effects "
                                "WHEN json_extract(NEW.record, '$.status') = 'observed_success' "
                                "BEGIN SELECT RAISE(ABORT, 'write failed'); END")
    terminal, completed = [], []
    engine.finished.connect(completed.append)
    engine.terminal_ready.connect(terminal.append)
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    try:
        engine.submit('Press Enter')
        qtbot.waitUntil(lambda: bool(terminal), timeout=10000)
        assert len(bridge.executed) == 1
        assert not completed and terminal[0].terminal_kind == 'error'
        assert records(tmp_path)[0]['status'] == 'uncertain'
        # Terminal release is after the ledger owner has retired.
        with Repository(tmp_path):
            pass
        assert journal.repository is None
    finally:
        engine.close()


def standalone_scope(repo, *, action=None, risk='routine', summary='Type PRIVATE_CONTENT'):
    from monster.agent.contract_adapters import grounded_from_checked, proposal_from_decision
    from monster.agent.validation import prepare_action
    j = EffectCoordinator(repo.connection, live=True)
    frame = observation()
    decision = Decision('action', summary, action or {'op': 'text', 'text': 'PRIVATE_CONTENT'},
                        risk=risk, expected_result='PRIVATE_CONTENT visible')
    p = proposal_from_decision(decision, frame, 7, task_ref='task', model='local-model')
    g = grounded_from_checked(p, frame, prepare_action(p.action.to_action(), frame),
                              generation=7, effect_ref='effect')
    ctx = {'mission_id': 'task', 'subtask_id': 'standalone', 'attempt_id': 'task',
           'workspace_id': 'interactive-desktop', 'generation': 1, 'target_ref': 'standalone-target',
           'controller_generation': 7, 'observation_ref': frame.id,
           'scene_ref': j._contract_scene(p.frame), 'active': True, 'lease_valid': True, 'cleanup_healthy': True}
    return j, p, g, ctx, decision


def commit(j, g, ctx, decision, *, each=False, hands=False, reviewed=None):
    return j.standalone_intent(ctx, g, instruction='Type PRIVATE_INSTRUCTION', approve_each_step=each,
                               hands_free=hands, decision=decision, reviewed_digest=reviewed,
                               reviewed_ref='review-1' if reviewed else None)


def test_standalone_journal_is_private_and_never_replays_action_id(tmp_path):
    with Repository(tmp_path) as repo:
        j, p, g, ctx, decision = standalone_scope(repo)
        e = commit(j, g, ctx, decision)
        assert e.category == 'unknown'  # Routine review policy does not certify semantic safety.
        assert j.history(e.effect_id)[0]['source'] == 'standalone_automatic'
        j.receipt(e.effect_id, inserted=True)
        j.observe(e.effect_id, 'succeeded', observation_ref='after')
        with pytest.raises(ValueError, match='already registered'):
            commit(j, g.model_copy(update={'effect_ref': 'another-effect'}), ctx, decision)
        data = ''.join(row[0] for row in repo.connection.execute('SELECT record FROM action_effects'))
        assert 'PRIVATE_CONTENT' not in data and 'PRIVATE_INSTRUCTION' not in data
        assert 'payload_json' not in data and 'target_description' not in data
        assert j.get(e.effect_id).action_ref == p.action_id


@pytest.mark.parametrize('each,hands,risk', [(True, False, 'routine'), (False, False, 'sensitive'),
                                          (False, False, 'uncertain'), (False, True, 'sensitive')])
def test_standalone_exact_review_cannot_be_skipped(tmp_path, each, hands, risk):
    with Repository(tmp_path) as repo:
        j, p, g, ctx, decision = standalone_scope(repo, risk=risk)
        with pytest.raises(ValueError, match='review is unavailable'):
            commit(j, g, ctx, decision, each=each, hands=hands)
        assert not j.records()
        if not hands:
            e = commit(j, g, ctx, decision, each=each, hands=hands, reviewed=j._proposal_digest(p))
            assert e.authorization_source == 'standalone_human'


@pytest.mark.parametrize('change', ['stale', 'future', 'inactive', 'lease', 'cleanup', 'task', 'decision', 'risk'])
def test_standalone_context_and_proposal_changes_are_refused(tmp_path, change):
    with Repository(tmp_path) as repo:
        j, _, g, ctx, decision = standalone_scope(repo)
        if change in ('stale', 'future'):
            j.monotonic = lambda: g.checked_frame.captured_at + (3 if change == 'stale' else -1)
        elif change in ('inactive', 'lease', 'cleanup'):
            ctx[{'inactive': 'active', 'lease': 'lease_valid', 'cleanup': 'cleanup_healthy'}[change]] = False
        elif change == 'task':
            ctx['mission_id'] = 'other-task'
        elif change == 'decision':
            decision = replace(decision, summary='Different reviewed target')
        elif change == 'risk':
            decision = replace(decision, risk='sensitive')
        with pytest.raises(ValueError):
            commit(j, g, ctx, decision)
        assert not j.records()


def test_restart_after_intent_requires_reconciliation_without_approval_row(tmp_path):
    with Repository(tmp_path) as repo:
        j, _, g, ctx, decision = standalone_scope(repo)
        commit(j, g, ctx, decision)
    with Repository(tmp_path) as repo:
        j = EffectCoordinator(repo.connection, live=True)
        j.recover()
        assert j.get('effect').status == 'uncertain'
        assert len(j.unresolved()) == 1
        j.attest('effect', 'failure', observation_ref='user-inspection')
        assert not j.unresolved()


def test_legacy_neutral_click_risk_still_needs_actual_review(tmp_path):
    with Repository(tmp_path) as repo:
        j, p, g, ctx, decision = standalone_scope(repo, action={'op': 'click', 'x': 50, 'y': 50}, risk='benign')
        assert p.model_risk_hint == 'routine'
        with pytest.raises(ValueError, match='review is unavailable'):
            commit(j, g, ctx, decision)
        e = commit(j, g, ctx, decision, reviewed=j._proposal_digest(p))
        assert e.authorization_source == 'standalone_human'


@pytest.mark.parametrize('operation', ['cancel', 'close'])
def test_cancellation_during_pending_intent_never_dispatches(qtbot, tmp_path, operation):
    import threading
    engine, bridge, _ = controller([
        Decision('action', 'Press Enter', {'op': 'press', 'key': 'ENTER'}, risk='routine')])
    engine.config = replace(engine.config, approve_each_step=False)
    journal = attach(engine, tmp_path)
    terminal = []
    engine.terminal_ready.connect(terminal.append)
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    original = journal.intent
    release = threading.Event()

    def intent(grounded, nonce, decision):
        journal.executor.submit(lambda: release.wait(3))
        original(grounded, nonce, decision)
        QTimer.singleShot(1, engine.close if operation == 'close' else engine.cancel)
        QTimer.singleShot(10, release.set)
    journal.intent = intent
    try:
        engine.submit('Press Enter')
        qtbot.waitUntil(lambda: bool(terminal), timeout=10000)
        assert not bridge.executed
        assert records(tmp_path)[0]['status'] == 'uncertain'
        with Repository(tmp_path):
            pass
    finally:
        release.set()
        engine.close()


@pytest.mark.parametrize('action', [
    {'op': 'text', 'text': 'https://example.com'},
    {'op': 'text', 'text': 'powershell'},
    {'op': 'press', 'key': 'DELETE'},
    {'op': 'chord', 'keys': ['CTRL', 'V']},
])
def test_exact_review_cannot_override_blocked_input_validation(tmp_path, action):
    from monster.agent.contract_adapters import grounded_from_checked, proposal_from_decision
    from monster.agent.contracts import ActionPayload
    with Repository(tmp_path) as repo:
        j, _, original, ctx, _ = standalone_scope(repo)
        decision = Decision('action', 'Reviewed operation', action, risk='routine')
        p = proposal_from_decision(decision, observation(), 7, task_ref='task', model='local-model')
        # Valid native mapping does not waive the supervised operation allowlist.
        g = grounded_from_checked(p, observation(), ActionPayload.from_action(action).to_action(),
                                  generation=7, effect_ref=original.effect_ref)
        with pytest.raises(ValueError):
            commit(j, g, ctx, decision, reviewed=j._proposal_digest(p))
        assert not j.records()


def test_v4_records_require_source_reference_and_unknown_versions_fail(tmp_path):
    from monster.mission.contracts import Effect
    with Repository(tmp_path) as repo:
        j, _, g, ctx, decision = standalone_scope(repo)
        record = commit(j, g, ctx, decision).model_dump()
        for changed in (record | {'authorization_source': None}, record | {'authorization_ref': None},
                        record | {'schema_version': 3}, record | {'schema_version': 5}):
            with pytest.raises(ValueError):
                Effect.model_validate(changed)


def test_human_task_confirmation_does_not_implicitly_reconcile_uncertain_input(qtbot, tmp_path):
    engine, bridge, model = controller([
        Decision('action', 'Type hello', {'op': 'text', 'text': 'hello'}, risk='routine')])
    engine.config = replace(engine.config, approve_each_step=False)
    attach(engine, tmp_path)
    async def uncertain(*args):
        return Verification('uncertain', 'Location unclear')
    model.verify_action = uncertain
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    terminal, completed = [], []
    engine.terminal_ready.connect(terminal.append)
    engine.finished.connect(completed.append)
    engine.approval_requested.connect(lambda rid, text: QTimer.singleShot(1, lambda: engine.respond(rid, 'approve')))
    try:
        engine.submit('Type \"hello\" into the current Slack composer. Do not send.')
        qtbot.waitUntil(lambda: bool(terminal), timeout=10000)
        assert len(bridge.executed) == 1
        assert engine.human_result_confirmed
        assert not completed and terminal[0].terminal_kind == 'error'
        assert records(tmp_path)[0]['status'] == 'uncertain'
    finally:
        engine.close()
