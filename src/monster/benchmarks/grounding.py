"""Read-only screenshot grounding trials; no desktop capture or input dispatch."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import time
from copy import deepcopy
from pathlib import Path

from monster.providers.base import ProviderError
from monster.providers.ollama import strict_json
from monster.providers.ui_venus import DecisionRequest, UIVenusComputerAdapter, UIVenusConfig
from monster.providers.ui_venus_preview import _read, load_observation
from monster.providers.venus_actions import UIVenusActionError, parse_preview


def require(value, message='Invalid grounding manifest.'):
    if not value:
        raise ValueError(message)


def percentiles(values):
    if not values:
        return None
    ordered = sorted(values)
    return {f'p{n}': ordered[max(0, math.ceil(len(ordered) * n / 100) - 1)]
            for n in (50, 95, 99)}


def _region(value, width, height):
    require(type(value) is list and len(value) == 4 and all(type(n) is int for n in value))
    x, y, w, h = value
    require(x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= width and y + h <= height)


def validate_oracle(value, width, height):
    require(type(value) is dict and value.get('kind') in ('action', 'observe', 'needs_user'))
    if value['kind'] != 'action':
        require(set(value) == {'kind'})
        return
    op = value.get('op')
    fields = {'click': {'region_px', 'button', 'count'}, 'move': {'region_px'},
              'drag': {'region_px', 'end_region_px'}, 'text': {'text_sha256'},
              'press': {'key'}, 'chord': {'keys'}}
    require(type(op) is str and op in fields)
    require(set(value) == {'kind', 'op'} | fields[op])
    for name in ('region_px', 'end_region_px'):
        if name in value:
            _region(value[name], width, height)
    if op == 'click':
        require(value['button'] in ('left', 'right', 'middle')
                and type(value['count']) is int and value['count'] in (1, 2))
    if op == 'text':
        require(type(value['text_sha256']) is str
                and re.fullmatch('[a-f0-9]{64}', value['text_sha256']))
    if op in ('press', 'chord'):
        from monster.computer.contracts import Bounds, validate_action
        payload = {'op': op, **{key: value[key] for key in fields[op]}}
        validate_action(payload, [Bounds(0, 0, width, height)])


def _inside(action, key_x, key_y, region):
    x, y, width, height = region
    return x <= action[key_x] < x + width and y <= action[key_y] < y + height


def score(candidate, oracle):
    """Independent oracle uses screenshot pixels, never model-supplied risk/evidence."""
    if candidate.kind == 'completion_claim':
        return 'unverified_completion'
    if candidate.kind != oracle['kind']:
        return 'action_mismatch'
    if candidate.kind != 'action':
        return 'matched'
    action = candidate.image_action
    if action['op'] != oracle['op']:
        return 'action_mismatch'
    for field in ('button', 'count', 'key', 'keys'):
        if field in oracle and list_or_value(action[field]) != list_or_value(oracle[field]):
            return 'action_mismatch'
    if ('text_sha256' in oracle
            and hashlib.sha256(action['text'].encode('utf-8')).hexdigest() != oracle['text_sha256']):
        return 'action_mismatch'
    for region, x, y in (('region_px', 'x', 'y'), ('end_region_px', 'end_x', 'end_y')):
        if region in oracle and not _inside(action, x, y, oracle[region]):
            return 'wrong_target'
    return 'matched'


def list_or_value(value):
    return list(value) if isinstance(value, (tuple, list)) else value


async def _manifest(path):
    raw = _read(path, 1024 * 1024)
    try:
        value = strict_json(raw)
    except (ValueError, RecursionError):
        raise ValueError('Malformed grounding manifest JSON.') from None
    require(type(value) is dict and set(value) == {'version', 'split', 'cases'}
            and type(value['version']) is int and value['version'] == 1
            and value['split'] in ('synthetic', 'held_out')
            and type(value['cases']) is list and 1 <= len(value['cases']) <= 256)
    seen = set()
    for case in value['cases']:
        await asyncio.sleep(0)
        require(type(case) is dict and set(case) <= {
            'id', 'screenshot', 'image_sha256', 'origin_px', 'displays', 'instruction',
            'dpi_percent', 'oracle', 'recorded_response'}
            and {'id', 'screenshot', 'image_sha256', 'origin_px', 'instruction', 'dpi_percent', 'oracle'} <= set(case))
        require(type(case['id']) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}', case['id'])
                and case['id'] not in seen)
        seen.add(case['id'])
        require(type(case['screenshot']) is str and len(case['screenshot']) <= 256)
        relative = Path(case['screenshot'])
        require(not relative.is_absolute() and '..' not in relative.parts)
        screenshot = (path.parent / relative).resolve()
        require(screenshot.is_relative_to(path.parent.resolve()))
        require(type(case['image_sha256']) is str and re.fullmatch('[a-f0-9]{64}', case['image_sha256']))
        require(type(case['dpi_percent']) is int and case['dpi_percent'] in (100, 125, 150, 200))
        if 'recorded_response' in case:
            require(type(case['recorded_response']) is str and len(case['recorded_response']) <= 65536)
        observation = load_observation(screenshot, case['origin_px'], case['id'], case.get('displays'))
        require(observation.sha256 == case['image_sha256'], 'Grounding image hash mismatch.')
        DecisionRequest.from_observation('validate', 0, case['instruction'], observation)
        validate_oracle(case['oracle'], observation.bounds.width, observation.bounds.height)
    await asyncio.sleep(0)
    return value, hashlib.sha256(raw).hexdigest()


async def run_benchmark(path, *, adapter=None, repeats=1, deadline=600, model_identity=None):
    """Explicit repeated predictions. History resets per trial; no executor is imported."""
    require(type(repeats) is int and 1 <= repeats <= 20, 'Repeats must be 1–20.')
    require(type(deadline) is int and 5 <= deadline <= 3600, 'Deadline must be 5–3600 seconds.')
    from monster.agent.diagnostics import RUNTIME
    if model_identity is not None:
        from monster.qualification import validate_model_identity
        model_identity = validate_model_identity(model_identity)
    path = Path(path)
    live = adapter is not None
    report = {'schema_version': 1, 'mode': 'live_preview' if live else 'recorded_replay',
              'dataset_sha256': None, 'dataset_split': None, 'case_ids': [], 'repeats': repeats,
              'model': adapter.config.model if live else None,
              'model_identity': model_identity, 'runtime': deepcopy(RUNTIME),
              'real_input': False, 'execution_authorized': False, 'model_qualified': False,
              'planned': 0, 'status': 'completed', 'trials': []}
    started = time.monotonic()
    failures = 0
    try:
        async with asyncio.timeout(deadline):
            manifest, digest = await _manifest(path)
            planned = len(manifest['cases']) * repeats
            require(planned <= 1000, 'At most 1000 benchmark predictions.')
            require(live or all('recorded_response' in case for case in manifest['cases']),
                    'Recorded replay requires a response for every case.')
            report.update(dataset_sha256=digest, dataset_split=manifest['split'], planned=planned,
                          case_ids=[case['id'] for case in manifest['cases']])
            for repeat in range(repeats):
                for case in manifest['cases']:
                    await asyncio.sleep(0)
                    # Re-read and validate bytes: a changed file cannot inherit an old oracle.
                    obs = load_observation(path.parent / case['screenshot'], case['origin_px'],
                                           case['id'], case.get('displays'))
                    require(obs.sha256 == case['image_sha256'], 'Grounding image changed during trial.')
                    req = DecisionRequest.from_observation(f"trial-{repeat}-{case['id']}", repeat,
                                                           case['instruction'], obs)
                    await asyncio.sleep(0)
                    row = {'case_id': case['id'], 'repeat': repeat + 1,
                           'image_sha256': obs.sha256, 'dpi_percent': case['dpi_percent']}
                    report['trials'].append(row)
                    request_start = time.monotonic()
                    try:
                        if live:
                            adapter.invalidate()
                            prediction = await adapter.next_decision(req)
                        else:
                            prediction = parse_preview(case['recorded_response'], req.frame,
                                                       req.task_id, req.generation)
                        row['outcome'] = score(prediction, case['oracle'])
                        row['candidate_kind'] = prediction.kind
                        if prediction.image_action:
                            action = prediction.image_action
                            row['op'] = action['op']
                            row['image_points'] = [[action[x], action[y]]
                                                   for x, y in (('x', 'y'), ('end_x', 'end_y')) if x in action]
                        failures = 0
                    except asyncio.CancelledError:
                        row['outcome'] = 'cancelled'
                        raise
                    except UIVenusActionError as exc:
                        row['outcome'] = 'proposal_validation_error'
                        row['error_code'] = exc.code
                        failures += 1
                    except ProviderError as exc:
                        row['outcome'] = 'inference_error'
                        row['error'] = str(exc)
                        failures += 1
                    finally:
                        row['request_and_validation_s'] = round(time.monotonic() - request_start, 6) if live else None
                    await asyncio.sleep(0)
                    if failures >= 3:
                        report['status'] = 'stopped_after_three_errors'
                        break
                if failures >= 3:
                    break
    except TimeoutError:
        report['status'] = 'deadline'
    finally:
        if live:
            adapter.invalidate()
    report['elapsed_s'] = round(time.monotonic() - started, 6)
    rows = report['trials']
    report['attempted'] = len(rows)
    report['matched'] = sum(row['outcome'] == 'matched' for row in rows)
    report['outcomes'] = {name: sum(row['outcome'] == name for row in rows) for name in (
        'matched', 'wrong_target', 'action_mismatch', 'unverified_completion',
        'proposal_validation_error', 'inference_error', 'cancelled')}
    report['model_accuracy'] = report['matched'] / len(rows) if live and rows else None
    report['latency_s'] = percentiles([row['request_and_validation_s'] for row in rows]) if live else None
    report['per_dpi'] = {str(dpi): {'attempted': sum(row['dpi_percent'] == dpi for row in rows),
                                   'matched': sum(row['dpi_percent'] == dpi and row['outcome'] == 'matched' for row in rows)}
                         for dpi in sorted({row['dpi_percent'] for row in rows})}
    return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--manifest', required=True, type=Path)
    cli.add_argument('--repeats', type=int, default=1)
    cli.add_argument('--deadline', type=int, default=600)
    cli.add_argument('--server')
    cli.add_argument('--model')
    cli.add_argument('--local-server-confirmed', action='store_true')
    cli.add_argument('--allow-lan-http', action='store_true')
    cli.add_argument('--timeout', type=int, default=60)
    cli.add_argument('--identity', type=Path, help='Declared checkpoint/projector/runtime identity JSON')
    args = cli.parse_args()
    if bool(args.server) != bool(args.model) or (args.server and not args.local_server_confirmed):
        cli.error('Live trials require --server, --model and --local-server-confirmed.')
    try:
        adapter = UIVenusComputerAdapter(UIVenusConfig(
            args.server, args.model, args.local_server_confirmed,
            args.allow_lan_http, args.timeout)) if args.server else None
        report = asyncio.run(run_benchmark(args.manifest, adapter=adapter,
                                          repeats=args.repeats, deadline=args.deadline,
                                          model_identity=strict_json(_read(args.identity, 8192)) if args.identity else None))
        print(json.dumps(report, indent=2))
        return 0 if report['status'] == 'completed' and report['matched'] == report['planned'] else 1
    except (ValueError, TypeError, OSError, RecursionError, ProviderError):
        print('Grounding benchmark stopped: invalid input or unavailable provider; no input dispatched.')
        return 1
    except KeyboardInterrupt:
        print('Grounding benchmark cancelled; no input dispatched.')
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
