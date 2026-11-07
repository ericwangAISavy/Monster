from dataclasses import replace

import pytest
from test_app_launch_progress import enter, query, win
from test_supervised import controller, observation

from monster.providers.base import Decision, Verification
from monster.providers.config import load, save


@pytest.mark.parametrize('completion_status', ['succeeded', 'uncertain', 'failed'])
@pytest.mark.parametrize('activation', ['enter', 'click'])
def test_no_prompts_through_launch_and_independent_completion(qtbot, completion_status, activation):
    launch = enter() if activation == 'enter' else Decision(
        'action', 'Click the visible Paint Open control',
        {'op': 'click', 'x': 50, 'y': 40}, risk='routine', expected_result='Paint opens',
    )
    engine, bridge, model = controller([win(), query(), launch, Decision('complete', 'Paint open')])
    engine.config = replace(engine.config, hands_free=True)
    questions, finished, checks = [], [], []

    async def completion(*args):
        checks.append(args)
        return Verification(completion_status, 'Independent task evidence')

    model.verify_completion = completion
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    engine.approval_requested.connect(lambda *args: questions.append(args))
    engine.clarification_requested.connect(lambda *args: questions.append(args))
    engine.finished.connect(finished.append)
    engine.failed.connect(finished.append)
    try:
        engine.submit('Open Paint')
        qtbot.waitUntil(lambda: bool(finished), timeout=10000)
        assert len(bridge.executed) == 3
        assert not questions and not engine.human_result_confirmed
        assert len(checks) == 1 and checks[0][1] == 'Open Paint'
        assert ('Completed' in finished[0]) == (completion_status == 'succeeded')
    finally:
        engine.close()


@pytest.mark.parametrize('scenario', ['uncertain', 'sensitive', 'clarify'])
def test_review_required_stops_without_prompt(qtbot, scenario):
    decision = win()
    if scenario == 'sensitive':
        decision = replace(decision, risk='sensitive')
    elif scenario == 'clarify':
        decision = Decision('clarify', 'Which application?')
    engine, bridge, model = controller([decision])
    engine.config = replace(engine.config, hands_free=True)
    questions, finished = [], []

    async def uncertain(*args):
        return Verification('uncertain', 'Target unclear')

    if scenario == 'uncertain':
        model.verify_action = uncertain
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    engine.approval_requested.connect(lambda *args: questions.append(args))
    engine.clarification_requested.connect(lambda *args: questions.append(args))
    engine.finished.connect(finished.append)
    engine.failed.connect(finished.append)
    try:
        engine.submit('Open Paint')
        qtbot.waitUntil(lambda: bool(finished), timeout=7000)
        assert not questions and not engine.human_result_confirmed
        assert len(bridge.executed) == (1 if scenario == 'uncertain' else 0)
        assert 'Completed' not in finished[0]
    finally:
        engine.close()


def test_hands_free_config_roundtrip(tmp_path):
    from monster.providers.config import OllamaConfig
    config = replace(OllamaConfig(), hands_free=True)
    path = tmp_path / 'provider.json'
    save(path, config)
    assert load(path).hands_free


@pytest.mark.parametrize('status', ['succeeded', 'failed', 'uncertain'])
def test_completion_provider_checks_entire_task(status):
    import asyncio
    import json

    from test_ollama import config, transport

    from monster.providers.ollama import OllamaProvider

    requests = []
    provider = OllamaProvider(config(), transport(requests, decision={
        'status': status, 'evidence': 'Visible task evidence',
    }))
    result = asyncio.run(provider.verify_completion('vision:small', 'Open Paint and draw a line', b'PNG', 100, 100))
    assert result.status == status
    payload = json.loads(requests[-1].content)
    assert 'ENTIRE requested task' in payload['messages'][1]['content']
    assert 'drawing exists' in payload['messages'][1]['content']


def test_hands_free_settings_override_per_step_review(qtbot):
    from monster.providers.config import OllamaConfig
    from monster.ui.provider_settings import ProviderSettings

    dialog = ProviderSettings(replace(OllamaConfig(), hands_free=True))
    qtbot.addWidget(dialog)
    assert dialog.hands_free.isChecked()
    assert not dialog.approve_each_step.isEnabled()
    assert dialog.value().hands_free
    assert not dialog.value().approve_each_step


def test_mission_gate_keeps_human_reviews():
    engine, _, _ = controller([])
    try:
        engine.config = replace(engine.config, hands_free=True)
        assert engine.hands_free
        engine.action_gate = True
        assert not engine.hands_free
    finally:
        engine.close()
