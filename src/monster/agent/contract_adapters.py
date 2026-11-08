"""Read-only adapters for existing operational data; no approval or input port."""

import hashlib
import struct
from uuid import uuid4

from monster.agent.contracts import (
    ActionPayload,
    ActionProposal,
    ExecutionReceipt,
    GroundedAction,
    ObservationFrame,
    VerificationResult,
    model_reference,
)
from monster.providers.base import ProviderError
from monster.providers.decisions import parse_decision


def observation_frame(observation, generation, *, frame_ref=None):
    png, bounds = observation.png, observation.bounds
    if (type(png) is not bytes or not 24 <= len(png) <= 32 * 1024 * 1024
            or png[:8] != b'\x89PNG\r\n\x1a\n'
            or png[12:16] != b'IHDR'
            or struct.unpack('>II', png[16:24]) != (bounds.width, bounds.height)
            or hashlib.sha256(png).hexdigest() != observation.sha256):
        raise ValueError('Capture content/geometry mismatch')
    selected = next((i for i, display in enumerate(observation.displays)
                     if display.contains(bounds.x, bounds.y)
                     and display.contains(bounds.x + bounds.width - 1, bounds.y + bounds.height - 1)), None)
    return ObservationFrame(
        observation_id=frame_ref or observation.id, capture_ref=observation.id,
        captured_at=observation.captured_at, controller_generation=generation,
        bounds=bounds, displays=observation.displays, image_sha256=observation.sha256,
        selected_display_index=selected, foreground_guard=observation.foreground,
    )


def proposal_from_decision(decision, observation, generation, *, task_ref, model, frame_ref=None):
    if decision.kind != 'action' or decision.action is None:
        raise ValueError('Only input decisions have action proposals')
    source = decision.coordinate_source
    space = 'native_image_pixels'
    if source is not None:
        if type(source) is not dict or source.get('space') != 'normalized_1000':
            raise ValueError('Unsupported legacy coordinate source')
        space = source['space']
        if source.get('image_size') != {'width': observation.bounds.width, 'height': observation.bounds.height}:
            raise ValueError('Coordinate source image changed')
        try:
            mapped = parse_decision({'kind': 'action', 'summary': 'Coordinate provenance check',
                                     'action': source.get('model_action')},
                                    observation.bounds.width, observation.bounds.height,
                                    coordinate_space=space)
        except ProviderError as exc:
            raise ValueError('Invalid coordinate provenance') from exc
        if mapped.action != decision.action:
            raise ValueError('Mapped model coordinates changed')
    aid = uuid4().hex
    return ActionProposal(
        action_id=aid, task_ref=task_ref, frame=observation_frame(observation, generation, frame_ref=frame_ref),
        action=ActionPayload.from_action(decision.action), target_description=decision.summary,
        expected_result=decision.expected_result or decision.summary, criterion_ref='criterion:' + aid,
        model_ref=model_reference(model), model_risk_hint=decision.risk, source_coordinate_space=space,
    )


def proposal_from_preview(preview, *, task_ref, model, expected_result):
    if preview.execution_allowed or preview.image_action is None:
        raise ValueError('Expected an inert input preview')
    frame = preview.frame
    aid = uuid4().hex
    return ActionProposal(
        action_id=aid, task_ref=task_ref,
        frame=ObservationFrame(observation_id=frame.observation_id, capture_ref=frame.observation_id,
                               captured_at=frame.captured_at, controller_generation=preview.generation,
                               bounds=frame.bounds, displays=frame.displays, image_sha256=frame.image_sha256,
                               foreground_guard=frame.foreground_guard),
        action=ActionPayload.from_action(dict(preview.image_action)),
        target_description=preview.summary or 'Legacy preview target', expected_result=expected_result,
        criterion_ref='criterion:' + aid, model_ref=model_reference(model),
        source_coordinate_space='ui_venus_normalized_999', preview_only=True,
    )


def grounded_from_checked(proposal, observation, physical_action, *, generation, frame_ref=None, effect_ref=None):
    return GroundedAction(
        proposal=proposal, effect_ref=effect_ref,
        checked_frame=observation_frame(observation, generation, frame_ref=frame_ref),
        physical_action=ActionPayload.from_action(physical_action),
        locator_method='legacy_model_coordinates' if 'x' in physical_action else 'keyboard',
    )


def receipt_from_legacy(grounded, result, job_id, *, error=False, started_at=None, received_at=None):
    data = result if isinstance(result, dict) else {}
    status = data.get('status', 'partial_or_unknown') if not error else 'partial_or_unknown'
    if type(status) is not str or status not in ('inserted', 'rejected', 'partial_or_unknown'):
        raise ValueError('Invalid legacy input receipt status')
    return ExecutionReceipt(
        action_id=grounded.proposal.action_id, effect_ref=grounded.effect_ref,
        controller_generation=grounded.checked_frame.controller_generation, input_job_id=job_id,
        status=status, events=data.get('events'), cleanup_confirmed=data.get('cleanup_ok'),
        handoff_started_at=started_at, receipt_received_at=received_at,
        reason_code={'inserted': 'input_inserted', 'rejected': 'input_rejected',
                     'partial_or_unknown': 'input_unknown'}.get(status, 'input_unknown'),
    )


def verification_from_legacy(grounded, result, observation, *, generation, model, expected_result, evidence_ref, frame_ref=None):
    frame = observation_frame(observation, generation, frame_ref=frame_ref)
    if frame.controller_generation != grounded.checked_frame.controller_generation:
        raise ValueError('Verification generation changed')
    if frame.captured_at < grounded.checked_frame.captured_at:
        raise ValueError('Verification observation predates input observation')
    if type(result.status) is not str or result.status not in ('succeeded', 'failed', 'uncertain'):
        raise ValueError('Invalid legacy model assessment status')
    if type(result.evidence) is not str or not 1 <= len(result.evidence.strip()) <= 300:
        raise ValueError('Invalid legacy verification evidence')
    return VerificationResult(
        action_id=grounded.proposal.action_id, effect_ref=grounded.effect_ref,
        criterion_ref=(grounded.proposal.criterion_ref if expected_result == grounded.proposal.expected_result
                       else 'legacy-criterion:' + grounded.proposal.action_id),
        post_frame=frame, model_status=result.status, verifier_ref=model_reference(model), evidence_ref=evidence_ref,
        criterion_source='proposal' if expected_result == grounded.proposal.expected_result else 'legacy_controller_override',
        reason_code={'succeeded': 'model_reported_success', 'failed': 'model_reported_failure',
                     'uncertain': 'model_uncertain'}.get(result.status, 'model_uncertain'),
    )
