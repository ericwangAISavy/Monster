"""V4 slice 1: immutable compatibility records without new dispatch authority."""

import json
from dataclasses import FrozenInstanceError, replace

import pytest
from pydantic import ValidationError
from test_supervised import controller, observation

from monster.agent.contract_adapters import (
    grounded_from_checked,
    observation_frame,
    proposal_from_decision,
    proposal_from_preview,
    receipt_from_legacy,
    verification_from_legacy,
)
from monster.agent.contracts import (
    ActionPayload,
    ApprovalGrant,
    ObservationFrame,
)
from monster.agent.validation import prepare_action
from monster.computer.contracts import Bounds
from monster.mission.contracts import Approval
from monster.providers.base import Decision, Verification
from monster.providers.decisions import parse_decision


def proposal(frame=None, action=None):
    frame = frame or observation()
    decision = Decision('action', 'Type private marker', action or {'op': 'text', 'text': 'PRIVATE_MARKER'},
                        risk='routine', expected_result='PRIVATE_MARKER visible in editor')
    return proposal_from_decision(decision, frame, 4, task_ref='task-1', model='local-model', frame_ref='before')


def grounded():
    frame = observation()
    p = proposal(frame)
    return grounded_from_checked(p, frame, p.action.to_action(), generation=4, frame_ref='checked')


def test_default_exports_are_content_free_and_do_not_rehydrate_an_action():
    g = grounded()
    data = g.model_dump_json()
    assert 'PRIVATE_MARKER' not in data and 'PRIVATE_MARKER' not in repr(g)
    assert 'payload_json' not in data and 'foreground_guard' not in data
    assert 'target_description' not in data and 'expected_result' not in data
    assert 'png' not in data
    with pytest.raises(ValidationError):
        type(g).model_validate_json(data)  # Volatile input must be supplied again explicitly.
    assert g.execution_authority == 'none'
    assert g.proposal.action.to_action()['text'] == 'PRIVATE_MARKER'


def test_action_payload_and_nested_proposal_are_deeply_immutable():
    payload = ActionPayload.from_action({'op': 'chord', 'keys': ['CTRL', 'N']})
    exposed = payload.to_action()
    exposed['keys'].append('ENTER')
    assert payload.to_action()['keys'] == ['CTRL', 'N']
    with pytest.raises(ValidationError):
        payload.payload_json = '{}'
    p = proposal(action={'op': 'chord', 'keys': ['CTRL', 'N']})
    with pytest.raises(ValidationError):
        p.action.operation = 'text'
    with pytest.raises(FrozenInstanceError):
        p.frame.bounds.x = 0


@pytest.mark.parametrize('version', [0, 2, True, '1'])
def test_unknown_and_malformed_versions_are_rejected(version):
    value = observation_frame(observation(), 4).model_dump()
    value['schema_version'] = version
    with pytest.raises(ValidationError):
        ObservationFrame.model_validate(value)


def test_frame_metadata_reports_unknown_legacy_fields_and_serializes_without_pixels():
    frame = observation_frame(observation(), 4, frame_ref='before')
    assert frame.selected_display_index == 0
    assert frame.coordinate_space == 'native_image_pixels'
    assert frame.dpi_scale is None and frame.capture_epoch is None and frame.session_ref is None
    restored = ObservationFrame.model_validate_json(frame.model_dump_json())
    assert restored.image_sha256 == frame.image_sha256
    assert restored.foreground_guard == 0  # Export is not a reusable foreground grant.
    assert ApprovalGrant is Approval


@pytest.mark.parametrize('change', [
    {'png': b'Not a screenshot'},
    {'sha256': '0' * 64},
    {'bounds': Bounds(-100, 0, 99, 100)},
])
def test_capture_content_and_geometry_must_match(change):
    with pytest.raises(ValueError):
        observation_frame(replace(observation(), **change), 4)


@pytest.mark.parametrize('action', [
    {'op': 'screenshot'}, {'op': 'down', 'x': 1, 'y': 1},
    {'op': 'key_down', 'key': 'CTRL'}, {'op': 'run', 'command': 'anything'},
    {'op': 'click', 'x': True, 'y': 0}, {'op': 'press', 'key': 'ENTER', 'unexpected': 1},
])
def test_contract_does_not_expand_provider_operations(action):
    with pytest.raises(ValueError):
        ActionPayload.from_action(action)


def test_duplicate_json_fields_are_not_silently_overwritten():
    with pytest.raises(ValueError):
        ActionPayload(operation='press', payload_json='{"op":"press","key":"ENTER","key":"WIN"}')


def test_1000_and_999_dialects_keep_their_original_mapping_and_preview_is_inert():
    from test_ui_venus import observation as large_observation

    from monster.providers.venus_actions import FrameBinding, parse_preview

    frame = large_observation(width=2560, height=1440)
    decision = parse_decision({
        'kind': 'action', 'summary': 'Focus target', 'risk': 'routine', 'expected_result': 'Target focused',
        'action': {'op': 'click', 'x_1000': 500, 'y_1000': 500},
    }, 2560, 1440, coordinate_space='normalized_1000')
    p = proposal_from_decision(decision, frame, 1, task_ref='task', model='local-model')
    assert p.action.to_action()['x'] == 1280
    assert p.source_coordinate_space == 'normalized_1000'
    physical = prepare_action(decision.action, frame)
    g = grounded_from_checked(p, frame, physical, generation=1)
    assert g.physical_action.to_action()['x'] == -640
    assert g.proposal.action.to_action()['x'] == 1280

    preview = parse_preview('Click(box=(500,500))', FrameBinding.from_observation(frame), 'task', 1)
    pp = proposal_from_preview(preview, task_ref='task', model='venus', expected_result='Target focused')
    assert pp.action.to_action()['x'] == 1281
    assert pp.source_coordinate_space == 'ui_venus_normalized_999'
    assert pp.preview_only and not preview.execution_allowed
    with pytest.raises(ValueError, match='Preview'):
        grounded_from_checked(pp, frame, dict(preview.desktop_action), generation=1)


@pytest.mark.parametrize('kind', ['payload', 'generation', 'foreground', 'geometry', 'time'])
def test_mapped_action_cannot_rebind_to_changed_context(kind):
    frame = observation()
    p = proposal(frame)
    physical, generation = p.action.to_action(), 4
    if kind == 'payload':
        physical['text'] = 'Changed content'
    elif kind == 'generation':
        generation = 5
    elif kind == 'foreground':
        frame = replace(frame, foreground=88)
    elif kind == 'geometry':
        frame = replace(frame, displays=(Bounds(-100, 0, 200, 100),))
    else:
        frame = replace(frame, captured_at=frame.captured_at - 1)
    with pytest.raises(ValueError):
        grounded_from_checked(p, frame, physical, generation=generation)


def test_drag_maps_both_endpoints_exactly_once():
    frame = observation()
    action = {'op': 'drag', 'x': 20, 'y': 30, 'end_x': 70, 'end_y': 80, 'duration': .5}
    p = proposal(frame, action)
    g = grounded_from_checked(p, frame, prepare_action(action, frame), generation=4)
    actual = g.physical_action.to_action()
    assert (actual['x'], actual['y'], actual['end_x'], actual['end_y']) == (-80, 30, -30, 80)


@pytest.mark.parametrize('status,reason', [
    ('inserted', 'input_inserted'), ('rejected', 'input_rejected'), ('partial_or_unknown', 'input_unknown'),
])
def test_receipt_does_not_claim_visual_success_or_unknown_cleanup(status, reason):
    receipt = receipt_from_legacy(grounded(), {'status': status, 'events': 4}, 5)
    assert receipt.status == status and receipt.reason_code == reason
    assert receipt.cleanup_confirmed is None and receipt.worker_generation is None
    assert not receipt.outcome_verified and receipt.execution_authority == 'none'
    assert 'PRIVATE_MARKER' not in receipt.model_dump_json()


def test_verification_binds_model_claim_to_action_and_fresh_post_frame():
    g = grounded()
    frame = observation()
    result = verification_from_legacy(g, Verification('succeeded', 'PRIVATE_MARKER visible'), frame,
                                      generation=4, model='local-model', expected_result=g.proposal.expected_result,
                                      evidence_ref='evidence-1', frame_ref='after')
    assert result.action_id == g.proposal.action_id
    assert result.criterion_ref == g.proposal.criterion_ref
    assert result.post_frame.observation_id == 'after'
    assert result.evidence_kind == 'model_assessment' and result.execution_authority == 'none'
    assert 'PRIVATE_MARKER' not in result.model_dump_json()
    override = verification_from_legacy(g, Verification('uncertain', 'Target unclear'), frame,
                                        generation=4, model='local-model', expected_result='A different legacy criterion',
                                        evidence_ref='evidence-2')
    assert override.criterion_source == 'legacy_controller_override'
    assert override.criterion_ref != g.proposal.criterion_ref
    with pytest.raises(ValueError):
        verification_from_legacy(g, Verification('succeeded', 'Visible'), frame, generation=5,
                                 model='local-model', expected_result='Visible', evidence_ref='evidence-3')


def test_controller_records_the_contract_chain_without_changing_input_or_reviews(qtbot):
    engine, bridge, _ = controller([
        Decision('action', 'Type marker', {'op': 'text', 'text': 'PRIVATE_MARKER'}, risk='routine', expected_result='Marker visible'),
        Decision('complete', 'Marker visible'),
    ])
    engine.config = replace(engine.config, approve_each_step=False)
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    try:
        engine.submit('Type PRIVATE_MARKER in Notepad')
        qtbot.waitUntil(lambda: engine.phase == 'confirming', timeout=5000)
        assert len(bridge.executed) == 1 and not engine.human_result_confirmed
        data = engine.run_diagnostics.snapshot(include_content=False).data
        kinds = ['action_proposal_contract', 'grounded_action_contract', 'execution_receipt_contract', 'verification_contract']
        records = {e['event']: e['details']['contract'] for e in data['events'] if e['event'] in kinds}
        assert set(records) == set(kinds)
        aid = records[kinds[0]]['action_id']
        assert records[kinds[1]]['proposal']['action_id'] == aid
        assert records[kinds[2]]['action_id'] == records[kinds[3]]['action_id'] == aid
        assert records[kinds[2]]['cleanup_confirmed'] is None
        assert not any(e['event'] == 'operational_contract_unavailable' for e in data['events'])
        assert 'PRIVATE_MARKER' not in json.dumps(records)
        engine.respond(engine.question_id, 'reject')
        qtbot.waitUntil(lambda: engine.phase == 'idle')
        assert engine.pending_contract is None and engine.grounded_contract is None
    finally:
        engine.close()


def test_normalized_provenance_must_match_the_existing_mapped_payload():
    frame = observation()
    decision = parse_decision({'kind': 'action', 'summary': 'Focus target',
                               'action': {'op': 'click', 'x_1000': 500, 'y_1000': 250}},
                              100, 100, coordinate_space='normalized_1000')
    decision.action['x'] += 1
    with pytest.raises(ValueError, match='coordinates changed'):
        proposal_from_decision(decision, frame, 4, task_ref='task', model='local-model')


def test_wire_arrays_do_not_make_python_frame_geometry_mutable():
    frame = observation_frame(observation(), 4)
    with pytest.raises(ValidationError):
        ObservationFrame.model_validate(dict(frame.model_dump(), displays=list(frame.displays)))
    value = json.loads(frame.model_dump_json())
    value['human_confirmed'] = True
    with pytest.raises(ValidationError):
        ObservationFrame.model_validate_json(json.dumps(value))


def test_receipt_unknown_input_is_not_reclassified_as_rejected_or_cleaned():
    g = grounded()
    receipt = receipt_from_legacy(g, {'status': 'inserted', 'events': 2}, 5, error=True,
                                  started_at=10.0, received_at=11.0)
    assert receipt.status == 'partial_or_unknown'
    assert receipt.cleanup_confirmed is None and not receipt.outcome_verified
    assert receipt.handoff_started_at == 10.0 and receipt.receipt_received_at == 11.0
    with pytest.raises(ValueError):
        receipt_from_legacy(g, {'status': 'inserted'}, 5, started_at=11.0, received_at=10.0)


@pytest.mark.parametrize('change', ['epoch', 'dpi', 'session'])
def test_known_session_metadata_cannot_be_removed_or_changed_at_grounding(change):
    from monster.agent.contracts import GroundedAction

    frame = observation()
    p = proposal(frame)
    before = dict(p.frame.__dict__)
    before.update(capture_epoch=1, dpi_scale=(1.25, 1.25), session_ref='session-1')
    bound = ObservationFrame.model_validate(before)
    original = p.model_copy(update={'frame': bound})
    after = dict(bound.__dict__)
    after.update({'epoch': {'capture_epoch': 2}, 'dpi': {'dpi_scale': None},
                  'session': {'session_ref': 'session-2'}}[change])
    with pytest.raises(ValueError, match='context changed'):
        GroundedAction(proposal=original, checked_frame=ObservationFrame.model_validate(after),
                       physical_action=p.action, locator_method='keyboard')


def test_contract_projection_refusal_never_bypasses_legacy_review(qtbot, monkeypatch):
    engine, bridge, _ = controller([Decision('action', 'Type marker', {'op': 'text', 'text': 'marker'}, risk='routine')])
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))

    def refuse(*args, **kwargs):
        raise ValueError('Must not be logged: PRIVATE_MARKER')

    monkeypatch.setattr('monster.agent.controller.proposal_from_decision', refuse)
    try:
        engine.submit('Type marker into Notepad')
        qtbot.waitUntil(lambda: engine.phase == 'approval', timeout=4000)
        assert not bridge.executed
        data = engine.run_diagnostics.snapshot(include_content=False).data
        failures = [e for e in data['events'] if e['event'] == 'operational_contract_unavailable']
        assert len(failures) == 1
        assert failures[0]['details']['reason'] == 'legacy_adapter_validation'
        assert 'PRIVATE_MARKER' not in json.dumps(failures)
        engine.respond(engine.question_id, 'reject')
        qtbot.waitUntil(lambda: engine.phase == 'idle')
        assert not bridge.executed
    finally:
        engine.close()


def test_unchecked_model_copy_does_not_bypass_contract_version_validation():
    frame = observation_frame(observation(), 4)
    invalid = frame.model_copy(update={'schema_version': 2})
    with pytest.raises(ValidationError):
        ObservationFrame.model_validate(invalid)


def test_existing_effect_reference_is_carried_without_creating_a_grant():
    frame = observation()
    p = proposal(frame)
    g = grounded_from_checked(p, frame, p.action.to_action(), generation=4, effect_ref='existing-effect')
    receipt = receipt_from_legacy(g, {'status': 'inserted', 'events': 4}, 5)
    assert receipt.effect_ref == 'existing-effect'
    assert receipt.execution_authority == 'none' and not receipt.outcome_verified


def test_direct_payload_construction_requires_canonical_encoding():
    with pytest.raises(ValueError, match='canonical JSON'):
        ActionPayload(operation='press', payload_json='{"op": "press", "key": "ENTER"}')


def test_preview_provenance_cannot_be_relabelled_as_live_data():
    p = proposal()
    with pytest.raises(ValueError, match='preview-only'):
        type(p).model_validate(p.model_copy(update={'source_coordinate_space': 'ui_venus_normalized_999'}))


def test_keyboard_payload_cannot_claim_model_coordinate_grounding():
    from monster.agent.contracts import GroundedAction

    g = grounded()
    with pytest.raises(ValueError, match='Locator method'):
        GroundedAction(proposal=g.proposal, checked_frame=g.checked_frame,
                       physical_action=g.physical_action, locator_method='legacy_model_coordinates')


@pytest.mark.parametrize('hint', ['benign', 'low'])
def test_legacy_neutral_aliases_project_without_changing_the_provider_parser(hint):
    frame = observation()
    p = proposal_from_decision(Decision('action', 'Type marker', {'op': 'text', 'text': 'marker'},
                                        risk=hint), frame, 4, task_ref='task', model='fixture')
    assert p.model_risk_hint == 'routine'
    from monster.providers.base import ProviderError
    with pytest.raises(ProviderError):
        parse_decision({'kind': 'action', 'summary': 'Type marker', 'action': {'op': 'text', 'text': 'marker'},
                        'risk': hint}, 100, 100)
