from copy import deepcopy

import pytest


def test_template_cannot_qualify_linux_or_unmeasured_tools():
    from monster.qualification import assess_native, native_template
    template = native_template()
    result = assess_native(template)
    assert result['complete'] is False and result['execution_authorized'] is False
    assert result['blockers']
    assert all(check['status'] == 'unavailable' for check in template['checks'].values())


def measured_record():
    from monster.qualification import native_template
    record = native_template()
    record['runtime']['os'] = 'Windows'
    record['environment']['unlocked_standard_desktop'] = True
    record['environment']['dpi_percent_tested'] = [100, 125, 150, 200]
    record['environment']['display_layouts_tested'] = ['single', 'negative_origin', 'mixed_dpi']
    record['stop_threshold_ms'] = 1000
    record['stop_samples_ms'] = [20, 30, 40]
    for check in record['checks'].values():
        check.update(status='pass', attempts=10, supported=10)
    return record


def test_native_record_is_self_reported_evidence_not_execution_permission():
    from monster.qualification import assess_native
    result = assess_native(measured_record())
    assert result['complete'] is True
    assert result['evidence_trust'] == 'operator_reported_not_attested'
    assert result['execution_authorized'] is False
    assert result['stop_latency_ms']['p95'] == 40


@pytest.mark.parametrize('mutation', ['missing', 'failed', 'zero', 'wrong_target', 'duplicate',
                                     'false_completion', 'stop', 'dpi', 'simulated'])
def test_incomplete_or_unsafe_native_evidence_blocks_readiness(mutation):
    from monster.qualification import assess_native
    record = measured_record()
    item = record['checks']['unicode_once']
    if mutation == 'missing':
        del record['checks']['unicode_once']
    elif mutation == 'failed':
        item['status'] = 'fail'
    elif mutation == 'zero':
        item.update(attempts=0, supported=0)
    elif mutation in ('wrong_target', 'duplicate', 'false_completion'):
        item[mutation] = 1
    elif mutation == 'stop':
        record['stop_samples_ms'] = [2000]
    elif mutation == 'dpi':
        record['environment']['dpi_percent_tested'] = [100]
    else:
        record['evidence_kind'] = 'simulated'
    assert assess_native(record)['complete'] is False


def test_replay_or_incomplete_grounding_can_never_pass_the_gate():
    from monster.qualification import assess_readiness
    ground = {'schema_version': 1, 'mode': 'recorded_replay', 'dataset_split': 'held_out',
              'status': 'completed', 'planned': 100, 'attempted': 100, 'matched': 100,
              'model': 'venus', 'model_identity': {}}
    assert assess_readiness(measured_record(), ground)['review_eligible'] is False
    ground['mode'] = 'live_preview'
    assert assess_readiness(measured_record(), ground)['review_eligible'] is False


def test_native_bad_counts_are_rejected_instead_of_rounded_or_coerced():
    from monster.qualification import assess_native
    for value in (True, -1, 1.5, '10'):
        record = deepcopy(measured_record())
        record['checks']['unicode_once']['attempts'] = value
        with pytest.raises(ValueError):
            assess_native(record)


def clean_grounding():
    from monster.agent.diagnostics import RUNTIME
    return {'schema_version': 1, 'mode': 'live_preview', 'dataset_split': 'held_out',
            'dataset_sha256': 'c' * 64, 'status': 'completed', 'planned': 1,
            'attempted': 1, 'matched': 1, 'case_ids': ['case-1'], 'repeats': 1,
            'model': 'venus', 'model_identity': {
                'checkpoint_sha256': 'a' * 64, 'projector_sha256': 'b' * 64,
                'quantization': 'test', 'runtime': 'test', 'runtime_version': 'test',
                'gpu': 'test', 'context_tokens': 16384}, 'runtime': deepcopy(RUNTIME),
            'outcomes': {'matched': 1, 'wrong_target': 0, 'action_mismatch': 0,
                         'unverified_completion': 0, 'proposal_validation_error': 0,
                         'inference_error': 0, 'cancelled': 0},
            'trials': [{'case_id': 'case-1', 'repeat': 1, 'outcome': 'matched',
                        'image_sha256': 'd' * 64, 'dpi_percent': 100,
                        'request_and_validation_s': .1}],
            'real_input': False, 'execution_authorized': False,
            'model_accuracy': 1.0, 'latency_s': {'p50': .1, 'p95': .1, 'p99': .1}}


def test_consistent_evidence_is_only_eligible_for_review_not_execution():
    from monster.qualification import assess_readiness
    result = assess_readiness(measured_record(), clean_grounding())
    assert result['review_eligible'] is True and result['execution_authorized'] is False


@pytest.mark.parametrize('mutation', ['empty', 'contradiction', 'duplicate', 'duration', 'schema', 'model'])
def test_readiness_rejects_internally_inconsistent_trial_evidence(mutation):
    from monster.qualification import assess_readiness
    report = clean_grounding()
    if mutation == 'empty':
        report['trials'] = []
    elif mutation == 'contradiction':
        report['trials'][0]['outcome'] = 'wrong_target'
    elif mutation == 'duplicate':
        report['trials'] *= 2
    elif mutation == 'duration':
        report['trials'][0]['request_and_validation_s'] = float('nan')
    elif mutation == 'schema':
        report['schema_version'] = True
    else:
        report['model'] = ['venus']
    assert assess_readiness(measured_record(), report)['review_eligible'] is False


@pytest.mark.parametrize('field', ['stop_samples_ms', 'stop_threshold_ms'])
def test_extreme_latency_numbers_fail_safely_without_numeric_overflow(field):
    from monster.qualification import assess_native
    record = measured_record()
    record[field] = [10 ** 400] if field == 'stop_samples_ms' else 10 ** 400
    with pytest.raises(ValueError):
        assess_native(record)


def test_extreme_trial_duration_blocks_review_without_numeric_overflow():
    from monster.qualification import assess_readiness
    report = clean_grounding()
    report['trials'][0]['request_and_validation_s'] = 10 ** 400
    assert assess_readiness(measured_record(), report)['review_eligible'] is False


def test_automatic_draft_trials_requiring_manual_recovery_block_completion():
    from monster.qualification import assess_native
    record = measured_record()
    record['checks']['draft_focused_automatic']['interventions'] = 1
    assert assess_native(record)['complete'] is False
