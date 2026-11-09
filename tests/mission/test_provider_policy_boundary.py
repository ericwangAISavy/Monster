"""Provider-shaped decisions through the live mission adapter and real effect ledger."""

import asyncio
import hashlib
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Signal
from PySide6.QtGui import QColor, QImage
from test_effects_policy import context

from monster.agent.contract_adapters import grounded_from_checked, proposal_from_decision
from monster.agent.validation import prepare_action
from monster.computer.contracts import Bounds
from monster.computer.screenshot import Observation
from monster.mission.contracts import Mission, Subtask
from monster.mission.effects import EffectCoordinator
from monster.mission.gui_subtask_adapter import AsyncGuiSubtaskRunner
from monster.mission.repository import Repository
from monster.mission.runtime import EXPECTED_TEXT
from monster.providers.base import Decision, ProviderError
from monster.providers.decisions import parse_decision


def captured_frame():
    image = QImage(100, 100, QImage.Format.Format_RGB32)
    image.fill(QColor('white'))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    png = bytes(data)
    bounds = Bounds(-100, 0, 100, 100)
    return Observation('frame1', time.monotonic(), bounds, (bounds,), png,
                       hashlib.sha256(png).hexdigest(), 77)


class PolicyBridge(QObject):
    resolved = Signal(object)
    policy_requested = Signal(object)
    policy_completed = Signal(object)

    def __init__(self):
        super().__init__()
        self.controller = SimpleNamespace(effect_requested=True)


@pytest.fixture
def boundary(qtbot, tmp_path):
    with Repository(tmp_path) as store:
        store.create(Mission(
            mission_id='m', objective_ref='editor-demo', workspace_id='desktop',
            authorized=True, deadline_utc=time.time() + 300,
            subtasks=[Subtask(subtask_id='s', objective_ref='editor-a',
                              success_refs=['visible-a'], category='local_reversible')],
        ))
        journal = EffectCoordinator(store.connection, live=True)
        bridge = PolicyBridge()
        runner = AsyncGuiSubtaskRunner(bridge)
        runner.configure_effects(journal)
        replies = []
        bridge.policy_completed.connect(replies.append)

        proposals = {}
        frame = captured_frame()
        ctx = context(objective_ref='editor-a', controller_generation=3)
        ctx['scene_ref'] = journal._contract_scene(proposal_from_decision(
            Decision('action', 'Reviewed marker', {'op': 'text', 'text': EXPECTED_TEXT['editor-a']}),
            frame, 3, task_ref='provider-task', model='fixture',
        ).frame)

        def call(operation, **fields):
            if operation == 'prepare' and 'proposal' not in fields:
                hint = fields.get('model_risk')
                risk = hint if type(hint) is str and hint in ('routine', 'sensitive', 'uncertain') else None
                fields['proposal'] = proposal_from_decision(
                    Decision('action', 'Reviewed marker', fields['action'], risk=risk),
                    frame, 3, task_ref='provider-task', model='fixture',
                )
            if operation == 'dispatch' and 'grounded' not in fields:
                prior = proposals[fields['effect_id']]
                fields['grounded'] = grounded_from_checked(
                    prior, frame, prepare_action(prior.action.to_action(), frame),
                    generation=3, effect_ref=fields['effect_id'],
                )
            packet = dict(operation=operation, nonce='nonce', request_key=('attempt', 1),
                          context=ctx, **fields)
            asyncio.run(runner._policy_operation(packet))
            reply = replies[-1]
            if operation == 'prepare' and 'effect_id' in reply:
                proposals[reply['effect_id']] = fields['proposal']
            return reply

        yield journal, call
        runner.detach()


def proposal(action=None, risk='routine'):
    return parse_decision({
        'kind': 'action', 'summary': 'Perform the reviewed editor step',
        'action': action or {'op': 'text', 'text': EXPECTED_TEXT['editor-a']},
        'risk': risk, 'expected_result': 'The reviewed editor result is visible',
    }, 100, 100, coordinate_space='normalized_1000')


def prepare(call, decision, review='review-1'):
    return call('prepare', action=decision.action, model_risk=decision.risk,
                objective_ref='editor-a', review_id=review)


def test_provider_routine_requires_exact_review_and_single_use_before_dispatch(boundary):
    journal, call = boundary
    decision = proposal()
    reply = prepare(call, decision)
    assert reply['allowed'] is True
    eid = reply['effect_id']
    assert journal.get(eid).category == 'local_reversible'
    assert journal.get(eid).status == 'approval_pending'
    assert journal.approval(eid).decision == 'pending'
    assert journal.approval(eid).consumed_at is None

    assert not call('dispatch', effect_id=eid, action=decision.action)['allowed']
    assert journal.get(eid).status == 'not_dispatched'

    reply = prepare(call, decision, review='review-2')
    eid = reply['effect_id']
    assert call('approve', effect_id=eid, review_id='review-2')['allowed']
    assert call('dispatch', effect_id=eid, action=decision.action)['allowed']
    assert journal.approval(eid).consumed_at is not None
    assert not call('dispatch', effect_id=eid, action=decision.action)['allowed']
    assert sum(e['code'] == 'dispatch_intent_recorded' for e in journal.history(eid)) == 1


@pytest.mark.parametrize('risk', ['sensitive', 'uncertain'])
def test_provider_caution_still_denies_even_the_exact_reviewed_text(boundary, risk):
    journal, call = boundary
    reply = prepare(call, proposal(risk=risk))
    assert not reply['allowed']
    assert journal.get(reply['effect_id']).category == 'unknown'
    assert journal.get(reply['effect_id']).status == 'denied'


@pytest.mark.parametrize('action', [
    {'op': 'press', 'key': 'ENTER'},
    {'op': 'click', 'x_1000': 500, 'y_1000': 500, 'button': 'left', 'count': 1},
    {'op': 'chord', 'keys': ['CTRL', 'N']},
    {'op': 'text', 'text': 'Unreviewed replacement text'},
    {'op': 'drag', 'x_1000': 200, 'y_1000': 300,
     'end_x_1000': 600, 'end_y_1000': 700, 'button': 'left', 'duration': .5},
])
def test_routine_never_authorizes_operations_outside_mission_policy(boundary, action):
    journal, call = boundary
    reply = prepare(call, proposal(action))
    assert not reply['allowed']
    assert journal.get(reply['effect_id']).status == 'denied'


@pytest.mark.parametrize('hint', ['unknown', 'Routine', 'routine ', True, 1, ['routine'], {'risk': 'routine'}])
def test_unknown_or_malformed_hint_remains_denied_at_adapter_boundary(boundary, hint):
    journal, call = boundary
    decision = proposal()
    reply = call('prepare', action=decision.action, model_risk=hint,
                 objective_ref='editor-a', review_id='review-1')
    assert not reply['allowed']
    assert journal.get(reply['effect_id']).status == 'denied'


def test_provider_unknown_label_is_rejected_before_mission_handoff():
    with pytest.raises(ProviderError, match='risk classification'):
        proposal(risk='low')


def test_routine_normalization_does_not_bypass_exact_payload_binding(boundary):
    journal, call = boundary
    decision = proposal()
    reply = prepare(call, decision)
    assert reply['allowed']
    eid = reply['effect_id']
    assert call('approve', effect_id=eid, review_id='review-1')['allowed']
    assert not call('dispatch', effect_id=eid, action={'op': 'text', 'text': 'Changed'})['allowed']
    assert journal.get(eid).status == 'not_dispatched'
    assert journal.approval(eid).consumed_at is None
