"""Regressions from ca9a291: late Paint launch, selection loops, and missing drag."""

import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_app_launch_progress import enter, query
from test_ollama import config, transport
from test_supervised import controller, observation

from monster.agent.progress import drawing_requested
from monster.agent.validation import approval_reason, prepare_action, scene_matches
from monster.computer.contracts import Bounds
from monster.providers.base import Decision, DraftTargetCheck, ProviderError, Verification
from monster.providers.decisions import decision_schema_for_draft, parse_decision
from monster.providers.ollama import OllamaProvider


def stroke():
    return Decision('action', 'Draw one line on the visible canvas', {
        'op': 'drag', 'x': 30, 'y': 45, 'end_x': 65, 'end_y': 70,
        'button': 'left', 'duration': .5,
    }, risk='routine', expected_result='One new line is visibly drawn on the canvas.')


def model_stroke():
    return {'kind': 'action', 'summary': 'Draw one line on the canvas', 'risk': 'routine',
            'expected_result': 'One new line on the canvas', 'action': {
                'op': 'drag', 'x_1000': 300, 'y_1000': 450,
                'end_x_1000': 650, 'end_y_1000': 700, 'button': 'left', 'duration': .5,
            }}


def test_provider_can_generate_a_stroke_and_explains_already_selected_tool():
    requests = []
    provider = OllamaProvider(config(), transport(requests, decision=model_stroke()))
    result = asyncio.run(provider.decide('vision:small', 'Open Paint and draw simple lines', b'PNG', 1920, 1080))
    assert result.action == {'op': 'drag', 'x': 576, 'y': 486, 'end_x': 1247, 'end_y': 755,
                             'button': 'left', 'duration': .5}
    assert result.coordinate_source['model_action'] == model_stroke()['action']
    payload = json.loads(requests[-1].content)
    variants = payload['format']['oneOf'][0]['properties']['action']['oneOf']
    drag = next(v for v in variants if v['properties']['op']['const'] == 'drag')
    assert 'end_x_1000' in drag['required'] and 'end_y_1000' in drag['required']
    assert 'already selected' in payload['messages'][1]['content']
    draft = decision_schema_for_draft()['oneOf'][0]['properties']['action']['oneOf']
    assert all(v['properties']['op']['const'] != 'drag' for v in draft)


@pytest.mark.parametrize('change', [
    {'end_x_1000': 1001}, {'end_y_1000': True}, {'end_y_1000': None},
    {'end_x': 700}, {'x_1000': -1}, {'duration': 5},
])
def test_drag_rejects_malformed_or_ambiguous_endpoint(change):
    value = model_stroke()
    value['action'].update(change)
    with pytest.raises(ProviderError):
        parse_decision(value, 1920, 1080, coordinate_space='normalized_1000')


def test_drag_translates_both_endpoints_on_negative_monitor_origin():
    bounds = Bounds(-1920, -100, 1920, 1080)
    actual = prepare_action(stroke().action, SimpleNamespace(bounds=bounds, displays=(bounds,), foreground=77))
    assert (actual['x'], actual['y'], actual['end_x'], actual['end_y']) == (-1890, -55, -1855, -30)
    assert stroke().action['x'] == 30


def test_drag_requires_explicit_drawing_for_automatic_execution():
    assert drawing_requested('open painter app and draw simple lines')
    assert approval_reason(stroke(), 'open painter app and draw simple lines') is None
    assert not drawing_requested('Open Paint')
    assert approval_reason(stroke(), 'Open Paint') is not None
    assert approval_reason(replace(stroke(), risk='uncertain'), 'Draw a line') is not None
    assert not drawing_requested('Write a message about drawing in Slack')


def test_drag_scene_recheck_detects_changed_destination_path():
    before, after = observation(), observation(blink=1)
    action = dict(stroke().action, end_x=99, end_y=99)
    details = {}
    assert not scene_matches(before, after, action, diagnostics=details)
    assert details['reason'] == 'drag_path_changed'


@pytest.mark.parametrize('target_status', ['ready', 'uncertain', 'wrong_target'])
def test_draw_checks_canvas_then_dispatches_once_without_approval(qtbot, target_status):
    engine, bridge, model = controller([stroke(), Decision('complete', 'Line is drawn')])
    engine.config = replace(engine.config, hands_free=True)
    questions, done, checks, completion_checks = [], [], [], []

    async def target(*args):
        checks.append(args)
        return DraftTargetCheck(target_status, 'Canvas target assessment')

    async def complete(*args):
        completion_checks.append(args)
        return Verification('succeeded', 'The whole requested drawing is visible')

    model.verify_drawing_target = target
    model.verify_completion = complete
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    engine.approval_requested.connect(lambda *args: questions.append(args))
    engine.finished.connect(done.append)
    engine.failed.connect(done.append)
    try:
        engine.submit('Draw a simple line in the open Paint canvas')
        qtbot.waitUntil(lambda: bool(done), timeout=5000)
        assert not questions and not engine.human_result_confirmed
        assert len(checks) == 1
        assert len(bridge.executed) == (1 if target_status == 'ready' else 0)
        assert len(completion_checks) == (1 if target_status == 'ready' else 0)
        if target_status == 'ready':
            actual = bridge.executed[0][0]
            assert (actual['x'], actual['y'], actual['end_x'], actual['end_y']) == (-70, 45, -35, 70)
            assert 'Completed' in done[0]
        else:
            assert 'Drawing target could not be established' in done[0]
    finally:
        engine.close()


def test_failed_early_launch_observes_later_window_and_draws_without_repeating_enter(qtbot):
    engine, bridge, model = controller([query(), enter(), stroke(), Decision('complete', 'Line drawn')])
    engine.config = replace(engine.config, hands_free=True)
    done, questions, enter_checks, stroke_checks = [], [], [], []
    initial, loading, ready = observation(), observation('#aaaaaa'), observation('#eeeeee', foreground=88)

    async def verify(*args):
        if args[2].get('key') == 'ENTER':
            enter_checks.append(args)
            return Verification('failed' if len(enter_checks) == 1 else 'succeeded',
                                'App loading' if len(enter_checks) == 1 else 'Usable Paint canvas visible')
        if args[2].get('op') == 'drag':
            stroke_checks.append(args)
        return Verification('succeeded', 'Requested step is visible')

    async def target(*args):
        return DraftTargetCheck('ready', 'Both endpoints and full path are on the Paint canvas')

    async def complete(*args):
        return Verification('succeeded', 'Requested line visible in Paint')

    def capture():
        frame = initial
        if engine.actions >= 2:
            frame = loading if engine.verification_launch_activation and engine.verification_retakes == 0 else ready
        engine.observation_ready(replace(frame, captured_at=time.monotonic()), engine.generation)

    model.verify_action = verify
    model.verify_drawing_target = target
    model.verify_completion = complete
    engine.capture_requested.connect(capture)
    engine.approval_requested.connect(lambda *args: questions.append(args))
    engine.finished.connect(done.append)
    engine.failed.connect(done.append)
    try:
        engine.submit('open painter app and draw simple lines')
        qtbot.waitUntil(lambda: bool(done), timeout=7000)
        assert 'Completed' in done[0], done
        assert [a[0]['op'] for a in bridge.executed] == ['text', 'press', 'drag']
        assert len(enter_checks) == 2 and len(stroke_checks) == 1
        assert enter_checks[0][-1] == enter_checks[1][-1]  # original before retained
        assert 'One new line' in stroke_checks[0][3]  # never overridden by app launch readiness
        assert not questions and engine.launch_query_pending is None
    finally:
        engine.close()


def test_late_app_reconciliation_does_not_dispatch_or_falsely_verify_tool_click(qtbot):
    tool = Decision('action', 'Select the brush tool', {'op': 'click', 'x': 35, 'y': 22},
                    risk='routine', expected_result='The brush tool is selected')
    engine, bridge, model = controller([query(), enter(), tool, stroke(), Decision('complete', 'Line drawn')])
    engine.config = replace(engine.config, approve_each_step=False)
    questions, checks = [], []

    async def verify(*args):
        checks.append(args)
        return Verification('failed' if args[2].get('key') == 'ENTER' else 'succeeded',
                            'Window not ready' if args[2].get('key') == 'ENTER' else 'Current requested state visible')

    async def target(*args):
        return DraftTargetCheck('ready', 'Canvas and tool ready')

    model.verify_action = verify
    model.verify_drawing_target = target
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    engine.approval_requested.connect(lambda *args: questions.append(args))
    try:
        engine.submit('Open Paint and draw a line')
        qtbot.waitUntil(lambda: engine.phase == 'confirming', timeout=9000)
        assert [a[0]['op'] for a in bridge.executed] == ['text', 'press', 'drag']
        assert any(args[2] == {'op': 'screenshot'} for args in checks)
        assert all(args[2] != tool.action for args in checks)
        assert len(questions) == 1  # only the existing supervised final review
        assert any('earlier input failure remains recorded' in item for item in engine.history)
    finally:
        engine.close()


@pytest.mark.parametrize('status', ['ready', 'wrong_target', 'uncertain'])
def test_provider_drawing_target_uses_both_endpoints_and_retains_result(status):
    requests = []
    provider = OllamaProvider(config(), transport(requests, decision={'status': status, 'evidence': 'Canvas evidence'}))
    result = asyncio.run(provider.verify_drawing_target('vision:small', 'Draw a line', stroke().action, b'PNG', 100, 100))
    assert result.status == status
    prompt = json.loads(requests[-1].content)['messages'][1]['content']
    assert 'BOTH endpoints' in prompt and 'straight path' in prompt


def test_canvas_assessment_is_bound_to_fresh_scene_before_dispatch(qtbot):
    engine, bridge, model = controller([stroke(), Decision('unable', 'Canvas changed')])
    engine.config = replace(engine.config, hands_free=True)
    done, target_checks = [], []

    async def target(*args):
        target_checks.append(args)
        return DraftTargetCheck('ready', 'Canvas was ready in the checked frame')

    def capture():
        frame = observation('#aaaaaa') if engine.drawing_target_proposal else observation()
        engine.observation_ready(frame, engine.generation)

    model.verify_drawing_target = target
    engine.capture_requested.connect(capture)
    engine.failed.connect(done.append)
    try:
        engine.submit('Draw a line in Paint')
        qtbot.waitUntil(lambda: bool(done), timeout=5000)
        assert len(target_checks) == 1 and not bridge.executed
        assert engine.stale == 1
    finally:
        engine.close()


def test_cancel_during_canvas_assessment_never_dispatches_drag(qtbot):
    engine, bridge, model = controller([stroke()])
    engine.config = replace(engine.config, hands_free=True)

    async def target(*args):
        await asyncio.sleep(.2)
        return DraftTargetCheck('ready', 'Canvas ready')

    model.verify_drawing_target = target
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    try:
        engine.submit('Draw a line in Paint')
        qtbot.waitUntil(lambda: engine.phase == 'checking_drawing_target', timeout=3000)
        engine.cancel()
        qtbot.waitUntil(lambda: not engine.active)
        qtbot.wait(250)
        assert not bridge.executed
    finally:
        engine.close()


def test_permanently_missing_app_has_bounded_checks_and_no_repeated_enter(qtbot):
    engine, bridge, model = controller([query(), enter()])
    engine.config = replace(engine.config, hands_free=True)
    checks, done = [], []

    async def verify(*args):
        if args[2].get('key') == 'ENTER':
            checks.append(args)
            return Verification('failed', 'Only search is visible')
        return Verification('succeeded', 'The query is present')

    model.verify_action = verify
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    engine.failed.connect(done.append)
    try:
        engine.submit('Open Paint and draw a line')
        qtbot.waitUntil(lambda: bool(done), timeout=6000)
        assert len(checks) == 3
        assert [a[0]['op'] for a in bridge.executed] == ['text', 'press']
        assert engine.launch_query_pending == 'Paint'
        assert not engine.human_result_confirmed
    finally:
        engine.close()


def test_reports_include_effective_hands_free_setting():
    from monster.agent.diagnostics import RunDiagnostics
    from monster.providers.config import OllamaConfig

    report = RunDiagnostics('Draw a line', replace(OllamaConfig(), hands_free=True))
    assert report.snapshot().data['configuration']['hands_free'] is True


@pytest.mark.parametrize('status', ['failed', 'uncertain'])
def test_unverified_stroke_is_observed_twice_and_never_redrawn(qtbot, status):
    engine, bridge, model = controller([stroke(), stroke()])
    engine.config = replace(engine.config, approve_each_step=False)
    checks, done = [], []

    async def target(*args):
        return DraftTargetCheck('ready', 'Canvas ready')

    async def verify(*args):
        checks.append(args)
        return Verification(status, 'Cannot establish the requested new line')

    model.verify_drawing_target = target
    model.verify_action = verify
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    engine.failed.connect(done.append)
    try:
        engine.submit('Draw a line in Paint')
        qtbot.waitUntil(lambda: bool(done), timeout=4000)
        assert len(bridge.executed) == 1 and len(checks) == 2
        assert 'No automatic redraw' in done[0]
        assert engine.calls == 1
    finally:
        engine.close()


def test_tool_readiness_prompt_keeps_failed_model_verdict_honest():
    requests = []
    provider = OllamaProvider(config(), transport(requests, decision={
        'status': 'failed', 'evidence': 'Brush is selected; requested pencil is not selected',
    }))
    result = asyncio.run(provider.verify_action('vision:small', 'Draw lines in Paint',
                                               {'op': 'click', 'x': 720, 'y': 254},
                                               'Pencil selected', b'PNG', 1920, 1080))
    assert result.status == 'failed'
    assert 'already-selected requested tool is ready' in json.loads(requests[-1].content)['messages'][1]['content']
