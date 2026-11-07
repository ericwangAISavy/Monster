"""Readiness evidence only. Never authorizes, arms or dispatches computer input."""

from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from monster.agent.diagnostics import RUNTIME
from monster.benchmarks.grounding import percentiles, require
from monster.providers.ollama import strict_json
from monster.providers.ui_venus_preview import _read

CHECKS = (
    'unicode_once', 'physical_dpi', 'monitor_selection', 'foreground_restore_mouse',
    'foreground_restore_keyboard', 'overlay_exclusion', 'emergency_stop', 'held_cleanup',
    'partial_no_replay', 'timeout_cancel', 'takeover_disarm', 'geometry_disarm',
    'draft_focused_approval', 'draft_focused_automatic',
    'draft_unfocused_approval', 'draft_unfocused_automatic',
)
COUNTS = ('attempts', 'supported', 'wrong_target', 'duplicate', 'false_completion',
          'unintended_consequence', 'interventions', 'recovery_attempts', 'recovered')
IDENTITY_FIELDS = ('checkpoint_sha256', 'projector_sha256', 'quantization',
                   'runtime', 'runtime_version', 'gpu', 'context_tokens')


def validate_model_identity(value):
    require(type(value) is dict and set(value) == set(IDENTITY_FIELDS), 'Incomplete model identity.')
    for name in IDENTITY_FIELDS:
        item = value[name]
        if name.endswith('sha256'):
            require(type(item) is str and re.fullmatch('[a-f0-9]{64}', item), 'Invalid checkpoint/projector hash.')
        elif name == 'context_tokens':
            require(type(item) is int and 512 <= item <= 1048576, 'Invalid served context size.')
        else:
            require(type(item) is str and 1 <= len(item) <= 128 and item.strip()
                    and not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in item),
                    'Invalid model runtime identity.')
    return deepcopy(value)


def preflight():
    result = {'schema_version': 1, 'runtime': deepcopy(RUNTIME),
              'native_windows_available': sys.platform == 'win32', 'real_input': False,
              'screenshots_captured': False, 'execution_authorized': False,
              'unlocked_standard_desktop': None, 'displays': [], 'status': 'unavailable'}
    if sys.platform == 'win32':
        try:
            from monster.computer.windows import WindowsInput
            windows = WindowsInput()
            result['unlocked_standard_desktop'] = windows.desktop_ready()
            result['displays'] = [asdict(display) for display in windows.displays()]
            result['status'] = 'observed' if result['unlocked_standard_desktop'] else 'desktop_not_ready'
        except (RuntimeError, ValueError, OSError):
            result['status'] = 'native_preflight_failed'
    return result


def native_template():
    return {'schema_version': 1, 'evidence_kind': 'native_operator_observed',
            'created_utc': datetime.now(UTC).isoformat(), 'runtime': deepcopy(RUNTIME),
            'environment': {'unlocked_standard_desktop': None, 'dpi_percent_tested': [],
                            'display_layouts_tested': []},
            'stop_threshold_ms': None, 'stop_samples_ms': [],
            'checks': {name: {'status': 'unavailable', **dict.fromkeys(COUNTS, 0)} for name in CHECKS}}


def _count(value):
    require(type(value) is int and 0 <= value <= 1000000, 'Counts must be nonnegative bounded integers.')
    return value


def _samples(values):
    require(type(values) is list and len(values) <= 10000, 'Invalid latency samples.')
    require(all(type(v) in (int, float) and 0 <= v <= 600000 for v in values),
            'Latency samples must be finite nonnegative milliseconds.')


def assess_native(record):
    require(type(record) is dict and type(record.get('schema_version')) is int
            and record['schema_version'] == 1, 'Invalid native record.')
    checks, environment, runtime = record.get('checks'), record.get('environment'), record.get('runtime')
    require(type(checks) is dict and type(environment) is dict and type(runtime) is dict,
            'Native checks/environment/runtime required.')
    blockers = []
    if record.get('evidence_kind') != 'native_operator_observed':
        blockers.append('native_evidence_required')
    if runtime.get('os') != 'Windows':
        blockers.append('windows_runtime_required')
    if (runtime.get('source_fingerprint_complete') is not True
            or runtime.get('source_sha256') != RUNTIME['source_sha256']):
        blockers.append('source_fingerprint_missing_or_changed')
    if environment.get('unlocked_standard_desktop') is not True:
        blockers.append('unlocked_standard_desktop_required')
    dpi = environment.get('dpi_percent_tested')
    require(type(dpi) is list and all(type(v) is int and v in (100, 125, 150, 200) for v in dpi))
    if not {100, 125, 150, 200} <= set(dpi):
        blockers.append('dpi_matrix_incomplete')
    layouts = environment.get('display_layouts_tested')
    require(type(layouts) is list and all(type(v) is str and v in ('single', 'negative_origin', 'mixed_dpi') for v in layouts))
    if not {'single', 'negative_origin', 'mixed_dpi'} <= set(layouts):
        blockers.append('display_matrix_incomplete')
    total_drafts = supported_drafts = 0
    totals = dict.fromkeys(COUNTS, 0)
    require(set(checks) <= set(CHECKS), 'Unknown native acceptance check.')
    for name in CHECKS:
        item = checks.get(name)
        if item is None:
            blockers.append(name + ':missing')
            continue
        require(type(item) is dict and set(item) == {'status', *COUNTS}
                and item['status'] in ('pass', 'fail', 'unavailable'), 'Invalid native check.')
        for field in COUNTS:
            totals[field] += _count(item[field])
        require(item['supported'] <= item['attempts'] and item['recovered'] <= item['recovery_attempts'],
                'Inconsistent native counts.')
        if (item['status'] != 'pass' or item['attempts'] == 0
                or item['supported'] != item['attempts'] or any(item[field] for field in (
                    'wrong_target', 'duplicate', 'false_completion', 'unintended_consequence'))):
            blockers.append(name + ':not_passed')
        if name.startswith('draft_') and name.endswith('_automatic') and item['interventions']:
            blockers.append(name + ':manual_recovery_required')
        if name.startswith('draft_'):
            total_drafts += item['attempts']
            supported_drafts += item['supported']
    samples = record.get('stop_samples_ms')
    _samples(samples)
    threshold = record.get('stop_threshold_ms')
    if threshold is not None:
        require(type(threshold) in (int, float) and 0 < threshold <= 600000, 'Invalid declared stop threshold.')
    if not samples or threshold is None:
        blockers.append('measured_stop_latency_and_declared_threshold_required')
    elif max(samples) > threshold:
        blockers.append('stop_threshold_exceeded')
    return {'schema_version': 1, 'complete': not blockers, 'blockers': blockers,
            'evidence_trust': 'operator_reported_not_attested', 'execution_authorized': False,
            'counts': totals, 'draft_trials': total_drafts, 'supported_drafts': supported_drafts,
            'draft_success_rate': supported_drafts / total_drafts if total_drafts else None,
            'stop_latency_ms': percentiles(samples), 'stop_samples': len(samples)}


def _grounding_rows_consistent(report):
    """Reject missing/contradictory evidence even though authenticity is not attested."""
    rows, ids, repeats = report.get('trials'), report.get('case_ids'), report.get('repeats')
    if (type(rows) is not list or not 1 <= len(rows) <= 1000
            or type(ids) is not list or not 1 <= len(ids) <= 256
            or not all(type(i) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}', i) for i in ids)
            or len(set(ids)) != len(ids) or type(repeats) is not int or not 1 <= repeats <= 20
            or report.get('planned') != len(ids) * repeats or report.get('attempted') != len(rows)):
        return False
    outcomes = dict.fromkeys(('matched', 'wrong_target', 'action_mismatch', 'unverified_completion',
                              'proposal_validation_error', 'inference_error', 'cancelled'), 0)
    seen, durations = set(), []
    for row in rows:
        if type(row) is not dict:
            return False
        identifier, repeat, outcome = row.get('case_id'), row.get('repeat'), row.get('outcome')
        if (type(identifier) is not str or identifier not in ids or type(repeat) is not int
                or not 1 <= repeat <= repeats or (identifier, repeat) in seen
                or type(outcome) is not str or outcome not in outcomes
                or type(row.get('dpi_percent')) is not int or row['dpi_percent'] not in (100, 125, 150, 200)
                or type(row.get('image_sha256')) is not str
                or not re.fullmatch('[a-f0-9]{64}', row['image_sha256'])):
            return False
        duration = row.get('request_and_validation_s')
        if type(duration) not in (int, float) or not 0 <= duration <= 3600:
            return False
        seen.add((identifier, repeat))
        outcomes[outcome] += 1
        durations.append(duration)
    digest = report.get('dataset_sha256')
    return (outcomes == report.get('outcomes') and outcomes['matched'] == report.get('matched')
            and report.get('latency_s') == percentiles(durations)
            and report.get('model_accuracy') == outcomes['matched'] / len(rows)
            and report.get('real_input') is False and report.get('execution_authorized') is False
            and type(digest) is str and bool(re.fullmatch('[a-f0-9]{64}', digest)))


def assess_readiness(native, grounding):
    native_result = assess_native(native)
    require(type(grounding) is dict, 'Grounding report required.')
    blockers = ['native:' + name for name in native_result['blockers']]
    if (type(grounding.get('schema_version')) is not int or grounding.get('schema_version') != 1
            or grounding.get('mode') != 'live_preview'
            or grounding.get('dataset_split') != 'held_out' or grounding.get('status') != 'completed'):
        blockers.append('completed_live_held_out_grounding_required')
    counts = [grounding.get(name) for name in ('planned', 'attempted', 'matched')]
    if (any(type(n) is not int or n <= 0 for n in counts) or len(set(counts)) != 1):
        blockers.append('grounding_trials_missing_incomplete_or_mismatched')
    outcomes = grounding.get('outcomes')
    if (type(outcomes) is not dict or outcomes.get('matched') != grounding.get('matched')
            or any(type(v) is not int or v < 0 for v in outcomes.values())
            or sum(outcomes.values()) != grounding.get('attempted')
            or any(value for key, value in outcomes.items() if key != 'matched')):
        blockers.append('grounding_outcomes_not_clean')
    if not _grounding_rows_consistent(grounding):
        blockers.append('grounding_trial_evidence_inconsistent')
    identity = grounding.get('model_identity')
    try:
        validate_model_identity(identity)
    except ValueError:
        blockers.append('exact_model_runtime_identity_required')
    if (type(grounding.get('model')) is not str or not 1 <= len(grounding['model']) <= 200
            or not grounding['model'].strip()
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in grounding['model'])):
        blockers.append('explicit_model_required')
    client = grounding.get('runtime')
    if (type(client) is not dict or client.get('source_fingerprint_complete') is not True
            or client.get('source_sha256') != RUNTIME['source_sha256']):
        blockers.append('grounding_source_fingerprint_missing_or_changed')
    return {'schema_version': 1, 'review_eligible': not blockers, 'blockers': blockers,
            'execution_authorized': False, 'model_qualified': False,
            'evidence_trust': 'operator_reported_not_attested',
            'note': 'Advisory evidence inventory only. No control integration, authenticity proof, '
                    'task completion guarantee or hands-off release qualification.'}


def _json_file(path):
    try:
        return strict_json(_read(path, 2 * 1024 * 1024))
    except RecursionError:
        raise ValueError('Excessively nested qualification record.') from None


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest='command', required=True)
    sub.add_parser('preflight')
    sub.add_parser('template')
    native = sub.add_parser('native')
    native.add_argument('--record', required=True, type=Path)
    gate = sub.add_parser('readiness')
    gate.add_argument('--native', required=True, type=Path)
    gate.add_argument('--grounding', required=True, type=Path)
    args = cli.parse_args()
    try:
        if args.command == 'preflight':
            result = preflight()
        elif args.command == 'template':
            result = native_template()
        elif args.command == 'native':
            result = assess_native(_json_file(args.record))
        else:
            result = assess_readiness(_json_file(args.native), _json_file(args.grounding))
        print(json.dumps(result, indent=2))
        return 0 if result.get('complete', result.get('review_eligible', True)) else 1
    except (ValueError, TypeError, OSError, RecursionError):
        print('Invalid qualification evidence; no input dispatched.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
