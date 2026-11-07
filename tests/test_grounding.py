import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from monster.providers.ui_venus_preview import load_observation
from monster.providers.venus_actions import FrameBinding, parse_preview

FIXTURES = Path(__file__).parent / 'fixtures/gui_synthetic'


def candidate(native):
    obs = load_observation(FIXTURES / 'desktop_1080.png', [-1920, 0], 'frame')
    return parse_preview(native, FrameBinding.from_observation(obs), 'task', 1)


def test_grounding_scores_region_not_a_single_magic_coordinate():
    from monster.benchmarks.grounding import score
    oracle = {'kind': 'action', 'op': 'click', 'region_px': [900, 500, 150, 100],
              'button': 'left', 'count': 1}
    assert score(candidate('Click(box=(500,500))'), oracle) == 'matched'
    assert score(candidate('Click(box=(0,0))'), oracle) == 'wrong_target'
    assert score(candidate('RightClick(box=(500,500))'), oracle) == 'action_mismatch'
    assert score(candidate('Finished()'), oracle) == 'unverified_completion'


def test_grounding_text_exactness_and_drag_both_endpoints():
    from monster.benchmarks.grounding import score
    oracle = {'kind': 'action', 'op': 'text',
              'text_sha256': hashlib.sha256(b'Hello').hexdigest()}
    assert score(candidate("Type(content='Hello')"), oracle) == 'matched'
    assert score(candidate("Type(content='HelloHello')"), oracle) == 'action_mismatch'
    drag = {'kind': 'action', 'op': 'drag', 'region_px': [0, 0, 10, 10],
            'end_region_px': [1900, 1060, 20, 20]}
    assert score(candidate('Drag(start=(0,0),end=(999,999))'), drag) == 'matched'
    assert score(candidate('Drag(start=(0,0),end=(500,500))'), drag) == 'wrong_target'


def test_grounding_recorded_replay_never_counts_as_model_accuracy():
    from monster.benchmarks.grounding import run_benchmark
    report = asyncio.run(run_benchmark(FIXTURES / 'grounding_cases.json', repeats=2))
    assert report['mode'] == 'recorded_replay' and report['real_input'] is False
    assert report['attempted'] == 6 and report['matched'] == 6
    assert report['model_accuracy'] is None and report['latency_s'] is None
    assert report['execution_authorized'] is False
    assert 'Hello from Monster' not in json.dumps(report)


def test_grounding_live_prompts_do_not_include_oracles_and_history_is_isolated():
    import httpx

    from monster.benchmarks.grounding import run_benchmark
    from monster.providers.ui_venus import UIVenusComputerAdapter, UIVenusConfig
    calls = []

    def handle(req):
        body = json.loads(req.content)
        calls.append(body)
        assert len(body['messages']) == 3
        assert 'region_px' not in json.dumps(body)
        return httpx.Response(200, json={'model': 'venus', 'choices': [{
            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'Click(box=(500,500))'}}]})

    adapter = UIVenusComputerAdapter(UIVenusConfig('http://127.0.0.1:8000', 'venus', True),
                                     transport=httpx.MockTransport(handle))
    report = asyncio.run(run_benchmark(FIXTURES / 'grounding_cases.json', adapter=adapter))
    assert report['attempted'] == 3 and len(calls) == 3
    assert report['mode'] == 'live_preview' and report['latency_s']['p95'] >= 0
    assert report['model_accuracy'] == pytest.approx(2 / 3)
    assert report['execution_authorized'] is False


def test_grounding_rejects_image_tampering_and_path_escape(tmp_path):
    from monster.benchmarks.grounding import run_benchmark
    source = json.loads((FIXTURES / 'grounding_cases.json').read_text())
    path = tmp_path / 'cases.json'
    for screenshot in ['../private.png', str(FIXTURES / 'desktop_1080.png')]:
        source['cases'][0]['screenshot'] = screenshot
        path.write_text(json.dumps(source))
        with pytest.raises(ValueError):
            asyncio.run(run_benchmark(path))


def test_global_deadline_counts_the_in_flight_trial_and_preserves_stop_outcome():
    import httpx

    from monster.benchmarks.grounding import run_benchmark
    from monster.providers.ui_venus import UIVenusComputerAdapter, UIVenusConfig

    async def handle(req):
        await asyncio.sleep(10)
        raise AssertionError('Request should have been cancelled')

    adapter = UIVenusComputerAdapter(UIVenusConfig('http://127.0.0.1:8000', 'venus', True),
                                     transport=httpx.MockTransport(handle))
    report = asyncio.run(run_benchmark(FIXTURES / 'grounding_cases.json', adapter=adapter, deadline=5))
    assert report['status'] == 'deadline'
    assert report['attempted'] == 1
    assert report['outcomes']['cancelled'] == 1
    assert report['latency_s']['p50'] >= 4
    assert adapter.history_metadata() == ()


def test_benchmark_stops_after_three_provider_errors_without_retries():
    import httpx

    from monster.benchmarks.grounding import run_benchmark
    from monster.providers.ui_venus import UIVenusComputerAdapter, UIVenusConfig
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(500, text='PRIVATE_BODY')

    adapter = UIVenusComputerAdapter(UIVenusConfig('http://127.0.0.1:8000', 'venus', True),
                                     transport=httpx.MockTransport(handle))
    report = asyncio.run(run_benchmark(FIXTURES / 'grounding_cases.json', adapter=adapter, repeats=5))
    assert report['status'] == 'stopped_after_three_errors'
    assert report['attempted'] == 3 and len(calls) == 3
    assert report['outcomes']['inference_error'] == 3
    assert 'PRIVATE_BODY' not in json.dumps(report)


def test_recorded_replay_cooperatively_honors_cancellation():
    from monster.benchmarks.grounding import run_benchmark

    async def scenario():
        pending = asyncio.create_task(run_benchmark(FIXTURES / 'grounding_cases.json', repeats=20))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(scenario())
