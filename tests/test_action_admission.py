"""Review-policy compatibility before standalone durable admission migration."""

from dataclasses import FrozenInstanceError
from itertools import product

import pytest

from monster.agent.admission import admit_action
from monster.providers.base import Decision


def admission(action, *, summary='Navigate', risk='routine', instruction='Open Paint',
              each=False, hands=False, gate=False):
    return admit_action(Decision('action', summary, action, risk=risk), instruction,
                        approve_each_step=each, hands_free=hands, action_gate=gate)


@pytest.mark.parametrize('action', [
    {'op': 'press', 'key': 'ENTER'},
    {'op': 'press', 'key': 'WIN'},
    {'op': 'click', 'x': 10, 'y': 20},
    {'op': 'text', 'text': 'Paint'},
    {'op': 'scroll', 'delta': 1},
    {'op': 'chord', 'keys': ['CTRL', 'HOME']},
])
@pytest.mark.parametrize('each,hands,gate', list(product([False, True], repeat=3)))
def test_routine_policy_preserves_host_modes(action, each, hands, gate):
    result = admission(action, each=each, hands=hands, gate=gate)
    expected = 'review' if each and (not hands or gate) else 'automatic'
    assert result.disposition == expected
    assert result.reason == ('Per-step approval is enabled.' if expected == 'review' else None)


@pytest.mark.parametrize('kwargs', [
    {'risk': 'sensitive'}, {'risk': 'uncertain'}, {'summary': 'Save the document'},
    {'summary': 'Submit this message'},
])
@pytest.mark.parametrize('each,hands,gate', list(product([False, True], repeat=3)))
def test_escalations_never_become_automatic(kwargs, each, hands, gate):
    result = admission({'op': 'press', 'key': 'ENTER'}, each=each, hands=hands, gate=gate, **kwargs)
    assert result.disposition == ('stop' if hands and not gate else 'review')
    assert result.reason


@pytest.mark.parametrize('hands', [False, True])
def test_close_intent_escalates_even_when_proposed_action_is_routine(hands):
    result = admission({'op': 'click', 'x': 10, 'y': 20},
                       instruction='Close the current window', hands=hands)
    assert result.disposition == ('stop' if hands else 'review')
    assert result.reason.startswith('Closing a window')


def test_drawing_requires_explicit_user_intent():
    drag = {'op': 'drag', 'x': 1, 'y': 2, 'end_x': 3, 'end_y': 4}
    assert admission(drag, instruction='Draw a line in Paint').disposition == 'automatic'
    assert admission(drag, instruction='Open Paint').disposition == 'review'
    assert admission(drag, instruction='Open Paint', hands=True).disposition == 'stop'


def test_new_document_still_requires_review():
    assert admission({'op': 'chord', 'keys': ['CTRL', 'N']}).disposition == 'review'


@pytest.mark.parametrize('field', ['approve_each_step', 'hands_free', 'action_gate'])
@pytest.mark.parametrize('value', [0, 1, None, 'false'])
def test_settings_are_strict_host_booleans(field, value):
    flags = {'approve_each_step': False, 'hands_free': False, 'action_gate': False}
    flags[field] = value
    with pytest.raises(ValueError, match='host booleans'):
        admit_action(Decision('action', 'Navigate', {'op': 'press', 'key': 'ENTER'}), '', **flags)


def test_admission_is_immutable_and_does_not_change_the_action():
    action = {'op': 'press', 'key': 'ENTER'}
    result = admission(action)
    assert action == {'op': 'press', 'key': 'ENTER'}
    with pytest.raises(FrozenInstanceError):
        result.disposition = 'review'
    assert result.policy_version == 'controller-review-v1'


@pytest.mark.parametrize('decision', [Decision('complete', 'Done'), Decision('action', 'Missing')])
def test_non_action_cannot_be_admitted(decision):
    with pytest.raises(ValueError, match='action proposal'):
        admit_action(decision, '', approve_each_step=False, hands_free=False, action_gate=False)
