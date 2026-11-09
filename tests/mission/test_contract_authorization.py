"""Mandatory live contract binding, using the existing ledger and input seam."""

import json
import time
from contextlib import contextmanager

import pytest
from test_provider_policy_boundary import captured_frame

from monster.agent.contract_adapters import grounded_from_checked, proposal_from_decision
from monster.agent.contracts import ActionPayload
from monster.agent.validation import prepare_action
from monster.mission.contracts import Effect, Mission, Subtask
from monster.mission.effects import EffectCoordinator
from monster.mission.repository import Repository
from monster.providers.base import Decision

MARKER = 'PRIVATE_MARKER_NOT_FOR_LEDGER'


@contextmanager
def live_scope(directory):
    with Repository(directory) as store:
        store.create(Mission(mission_id='m', objective_ref='editor-demo', workspace_id='desktop',
                             authorized=True, deadline_utc=time.time() + 300,
                             subtasks=[Subtask(subtask_id='s', objective_ref='editor-a',
                                               success_refs=['visible-a'], category='local_reversible')]))
        journal = EffectCoordinator(store.connection, live=True)
        frame = captured_frame()
        action = {'op': 'text', 'text': MARKER}
        proposal = proposal_from_decision(
            Decision('action', 'Type ' + MARKER, action, risk='routine', expected_result=MARKER + ' visible'),
            frame, 7, task_ref='task-1', model='local-model')
        ctx = {'mission_id': 'm', 'subtask_id': 's', 'attempt_id': 'attempt', 'workspace_id': 'desktop',
               'generation': 1, 'controller_generation': 7, 'target_ref': 'scratch', 'objective_ref': 'editor-a',
               'observation_ref': frame.id, 'scene_ref': journal._contract_scene(proposal.frame),
               'active': True, 'lease_valid': True, 'cleanup_healthy': True}
        yield store, journal, frame, proposal, ctx


def prepared(journal, proposal, ctx):
    return journal.prepare(ctx, proposal.action.to_action(), expected_text=MARKER,
                           review_id='review-1', model_risk='low', proposal=proposal)


def mapped(proposal, frame, eid):
    return grounded_from_checked(proposal, frame, prepare_action(proposal.action.to_action(), frame),
                                 generation=7, effect_ref=eid)


def test_live_preparation_refuses_missing_contract_and_does_not_create_intent(tmp_path):
    with live_scope(tmp_path) as (_, j, _, p, ctx):
        with pytest.raises(ValueError, match='operational proposal'):
            j.prepare(ctx, p.action.to_action(), expected_text=MARKER, review_id='review-1')
        assert not j.records()


def test_version3_records_bind_operational_identity_without_persisting_content(tmp_path):
    with live_scope(tmp_path) as (store, j, frame, p, ctx):
        e = prepared(j, p, ctx)
        assert e.schema_version == 3 and e.action_ref == p.action_id and e.task_ref == p.task_ref
        assert e.controller_generation == 7 and e.generation == 1
        assert e.action_contract_digest and e.status == 'approval_pending'
        j.approve(e.effect_id, ctx, review_id='review-1')
        j.dispatch(e.effect_id, ctx, p.action.to_action(), grounded=mapped(p, frame, e.effect_id))
        assert j.get(e.effect_id).status == 'dispatch_intent_recorded'
        content = ''.join(r[0] for r in store.connection.execute('SELECT record FROM action_effects'))
        assert MARKER not in content and 'payload_json' not in content
        assert 'expected_result' not in content and 'target_description' not in content
        with pytest.raises(ValueError):
            j.dispatch(e.effect_id, ctx, p.action.to_action(), grounded=mapped(p, frame, e.effect_id))
        assert sum(x['code'] == 'dispatch_intent_recorded' for x in j.history(e.effect_id)) == 1


@pytest.mark.parametrize('change', ['action_id', 'task_ref', 'expected_result', 'model_ref', 'criterion_ref', 'target_description'])
def test_changed_proposal_identity_or_criterion_invalidates_exact_approval(tmp_path, change):
    with live_scope(tmp_path) as (_, j, frame, p, ctx):
        e = prepared(j, p, ctx)
        j.approve(e.effect_id, ctx, review_id='review-1')
        altered = p.model_copy(update={change: 'changed'})
        with pytest.raises(ValueError):
            j.dispatch(e.effect_id, ctx, p.action.to_action(), grounded=mapped(altered, frame, e.effect_id))
        assert j.approval(e.effect_id).consumed_at is None
        assert j.get(e.effect_id).status == 'not_dispatched'


@pytest.mark.parametrize('change', ['missing', 'wrong_effect', 'physical_payload', 'context_generation', 'stale', 'future'])
def test_invalid_mapped_handoff_never_consumes_grant(tmp_path, change):
    with live_scope(tmp_path) as (_, j, frame, p, ctx):
        e = prepared(j, p, ctx)
        j.approve(e.effect_id, ctx, review_id='review-1')
        g = mapped(p, frame, e.effect_id)
        if change == 'missing':
            g = None
        elif change == 'wrong_effect':
            g = g.model_copy(update={'effect_ref': 'other-effect'})
        elif change == 'physical_payload':
            g = g.model_copy(update={'physical_action': ActionPayload.from_action({'op': 'text', 'text': 'other'})})
        elif change == 'context_generation':
            ctx = ctx | {'controller_generation': True}
        elif change == 'stale':
            j.monotonic = lambda: frame.captured_at + 3
        else:
            j.monotonic = lambda: frame.captured_at - 1
        with pytest.raises(ValueError):
            j.dispatch(e.effect_id, ctx, p.action.to_action(), grounded=g)
        assert j.approval(e.effect_id).consumed_at is None
        assert j.get(e.effect_id).status == 'not_dispatched'


def test_same_operational_action_cannot_mint_a_second_effect_after_success(tmp_path):
    with live_scope(tmp_path) as (_, j, frame, p, ctx):
        e = prepared(j, p, ctx)
        j.approve(e.effect_id, ctx, review_id='review-1')
        j.dispatch(e.effect_id, ctx, p.action.to_action(), grounded=mapped(p, frame, e.effect_id))
        j.receipt(e.effect_id, inserted=True)
        j.observe(e.effect_id, 'succeeded', observation_ref='after')
        with pytest.raises(ValueError, match='already registered'):
            prepared(j, p, ctx)
        assert len(j.records()) == 1


def test_restart_preserves_version3_uncertainty_and_does_not_replay(tmp_path):
    with live_scope(tmp_path) as (_, j, frame, p, ctx):
        e = prepared(j, p, ctx)
        j.approve(e.effect_id, ctx, review_id='review-1')
        g = mapped(p, frame, e.effect_id)
        j.dispatch(e.effect_id, ctx, p.action.to_action(), grounded=g)
        eid = e.effect_id
    with Repository(tmp_path) as reopened:
        recovered = EffectCoordinator(reopened.connection, live=True)
        recovered.recover()
        assert recovered.get(eid).schema_version == 3
        assert recovered.get(eid).status == 'uncertain'
        with pytest.raises(ValueError):
            recovered.dispatch(eid, ctx, p.action.to_action(), grounded=g)
        assert sum(x['code'] == 'dispatch_intent_recorded' for x in recovered.history(eid)) == 1


def test_legacy_effects_still_parse_but_live_dispatch_requires_fresh_contract_review(tmp_path):
    with live_scope(tmp_path) as (store, _, _, p, ctx):
        legacy = EffectCoordinator(store.connection, live=False)
        e = legacy.prepare(ctx, p.action.to_action(), expected_text=MARKER, review_id='review-1')
        assert e.schema_version == 2 and 'action_ref' not in json.loads(e.model_dump_json())
        legacy.approve(e.effect_id, ctx, review_id='review-1')
        strict = EffectCoordinator(store.connection, live=True, key=legacy.key)
        with pytest.raises(ValueError, match='fresh operational review'):
            strict.dispatch(e.effect_id, ctx, p.action.to_action())
        assert strict.approval(e.effect_id).consumed_at is None


def test_v3_missing_or_future_binding_records_fail_closed(tmp_path):
    with live_scope(tmp_path) as (_, j, _, p, ctx):
        e = prepared(j, p, ctx)
        record = e.model_dump()
        for changed in (record | {'schema_version': 4}, record | {'action_contract_digest': None}):
            with pytest.raises(ValueError):
                Effect.model_validate(changed)


@pytest.mark.parametrize('fault', [None, 'missing_contract', 'changed_after_intent', 'gate_changed_after_intent'])
def test_real_controller_bridge_commits_bound_intent_before_fake_input(qtbot, tmp_path, monkeypatch, fault):
    import sqlite3

    from PySide6.QtCore import QObject, QTimer, Signal
    from test_gui_adapter import background

    from monster.agent.controller import SupervisedController
    from monster.mission.contracts import GuiSubtaskRequest
    from monster.mission.gui_bridge import MissionGuiBridge
    from monster.mission.gui_subtask_adapter import AsyncGuiSubtaskRunner
    from monster.mission.runtime import EDITOR_OBJECTIVES, EXPECTED_TEXT
    from monster.mission.workspace import WorkspaceGate
    from monster.providers.base import DraftTargetCheck, ProbeResult, Verification
    from monster.providers.config import OllamaConfig

    directory = tmp_path / 'ledger'
    input_handoffs = []

    class Input(QObject):
        completed = Signal(int, object, str)
        interrupted = Signal()

        def __init__(self):
            super().__init__()
            self.next_id = 0

        def submit(self, operation, action=None, focus=None, geometry=None):
            self.next_id += 1
            job = self.next_id
            if operation == 'execute':
                with sqlite3.connect(directory / 'ledger.sqlite3') as conn:
                    records = [json.loads(row[0]) for row in conn.execute('SELECT record FROM action_effects')]
                assert len(records) == 1
                assert records[0]['schema_version'] == 3
                assert records[0]['status'] == 'dispatch_intent_recorded'
                input_handoffs.append((action, records[0]['action_ref']))
            value = {'status': 'inserted', 'events': 4} if operation == 'execute' else True
            QTimer.singleShot(1, lambda: self.completed.emit(job, value, ''))
            return job

        def stop(self):
            pass

        def close(self):
            return self.submit('close')

        def shutdown(self):
            pass

    class Model:
        def __init__(self):
            self.decisions = [Decision('action', 'Type reviewed marker',
                                       {'op': 'text', 'text': EXPECTED_TEXT['editor-a']}, risk='routine',
                                       expected_result='The reviewed marker is visible'),
                              Decision('complete', 'Reviewed marker visible')]

        async def probe(self, model):
            return ProbeResult(model, 'fixture', 'fixture', True, True)

        async def decide(self, *args, **kwargs):
            return self.decisions.pop(0)

        async def verify_draft_target(self, *args):
            return DraftTargetCheck('ready', 'Synthetic editor ready')

        async def verify_action(self, *args):
            return Verification('succeeded', 'Synthetic marker visible')

    engine = SupervisedController(OllamaConfig(model='fixture', local_server_confirmed=True, mode='supervised'),
                                  provider_factory=lambda _: Model(), input_bridge=Input())
    # The factory must retain one bounded decision sequence across calls.
    model = Model()
    engine.provider_factory = lambda _: model
    gate = WorkspaceGate(tmp_path / 'session.lock')
    bridge = MissionGuiBridge(engine, gate, preflight=lambda: 77,
                              objective_catalog=EDITOR_OBJECTIVES, workspace_id='desktop')
    runner = AsyncGuiSubtaskRunner(bridge)
    engine.capture_requested.connect(lambda: engine.observation_ready(captured_frame(), engine.generation))
    if fault == 'missing_contract':
        def refuse(*args, **kwargs):
            raise ValueError('Fixture contract refusal')
        monkeypatch.setattr('monster.agent.controller.proposal_from_decision', refuse)
    elif fault in ('changed_after_intent', 'gate_changed_after_intent'):
        original_reply = engine.effect_reply

        def alter(reply):
            if reply.get('operation') == 'dispatch' and reply.get('allowed'):
                if fault == 'changed_after_intent':
                    engine.pending_contract = engine.pending_contract.model_copy(update={'expected_result': 'Different criterion'})
                else:
                    engine.action_gate = False
            original_reply(reply)

        monkeypatch.setattr(engine, 'effect_reply', alter)

    async def run():
        with Repository(directory) as store:
            subtask = Subtask(subtask_id='s', objective_ref='editor-a', success_refs=['visible-a'], category='local_reversible')
            store.create(Mission(mission_id='m', objective_ref='editor-demo', workspace_id='desktop',
                                 authorized=True, deadline_utc=time.time() + 300, subtasks=[subtask]))
            journal = EffectCoordinator(store.connection, live=True)
            runner.configure_effects(journal)
            result = await runner.run(GuiSubtaskRequest(mission_id='m', workspace_id='desktop', effect_id='attempt',
                                                        generation=1, subtask=subtask, authorization_expires_at=time.time() + 60))
            return result, journal.records()

    thread, out = background(run())
    try:
        if fault != 'missing_contract':
            qtbot.waitUntil(lambda: engine.phase == 'effect_approval' or bool(out), timeout=5000)
            assert engine.phase == 'effect_approval', out
            assert not input_handoffs
            action_id = engine.pending_contract.action_id
            engine.respond(engine.question_id, 'approve')
            if fault is None:
                qtbot.waitUntil(lambda: engine.phase == 'confirming' or bool(out), timeout=5000)
                assert engine.phase == 'confirming', out
                engine.respond(engine.question_id, 'approve')
        qtbot.waitUntil(lambda: not thread.is_alive(), timeout=7000)
        assert out and not isinstance(out[0], BaseException), out
        result, records = out[0]
        assert not gate.busy and result.cleanup_confirmed
        if fault is None:
            assert result.status == 'verified_success'
            assert input_handoffs == [({'op': 'text', 'text': EXPECTED_TEXT['editor-a']}, action_id)]
            assert records[0].status == 'observed_success'
        else:
            assert not input_handoffs and result.status != 'verified_success'
            if fault in ('changed_after_intent', 'gate_changed_after_intent'):
                assert records[0].status == 'uncertain'
    finally:
        engine.cancel()
        thread.join(timeout=2)
        runner.detach()
        bridge.shutdown()
        engine.close()


@pytest.mark.parametrize('hint', ['sensitive', 'uncertain'])
def test_transport_cannot_lower_caution_in_the_bound_proposal(tmp_path, hint):
    with live_scope(tmp_path) as (_, j, _, p, ctx):
        e = prepared(j, p.model_copy(update={'model_risk_hint': hint}), ctx)
        assert e.category == 'unknown' and e.status == 'denied'


def test_concurrent_preparation_cannot_duplicate_an_operational_action(tmp_path):
    import sqlite3
    import threading

    with live_scope(tmp_path) as (_, j, _, p, ctx):
        barrier = threading.Barrier(2)
        results = []

        def prepare_once():
            with sqlite3.connect(tmp_path / 'ledger.sqlite3') as conn:
                service = EffectCoordinator(conn, key=j.key, live=True)
                barrier.wait(timeout=3)
                try:
                    prepared(service, p, ctx)
                    results.append('prepared')
                except ValueError:
                    results.append('refused')

        workers = [threading.Thread(target=prepare_once) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
            assert not worker.is_alive()
        assert sorted(results) == ['prepared', 'refused']
        assert len(j.records()) == 1


def test_failed_v3_intent_write_rolls_back_grant_consumption(tmp_path):
    import sqlite3

    with live_scope(tmp_path) as (store, j, frame, p, ctx):
        e = prepared(j, p, ctx)
        j.approve(e.effect_id, ctx, review_id='review-1')
        store.connection.execute("CREATE TRIGGER refuse_intent BEFORE UPDATE ON action_effects "
                                 "WHEN NEW.record LIKE '%dispatch_intent_recorded%' "
                                 "BEGIN SELECT RAISE(ABORT,'fixture refusal'); END")
        with pytest.raises(sqlite3.DatabaseError):
            j.dispatch(e.effect_id, ctx, p.action.to_action(), grounded=mapped(p, frame, e.effect_id))
        assert j.approval(e.effect_id).consumed_at is None
        assert j.get(e.effect_id).status == 'approved'
        assert all(row['code'] != 'dispatch_intent_recorded' for row in j.history(e.effect_id))
