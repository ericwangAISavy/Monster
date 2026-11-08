"""V1 compatibility records, not dispatch authority or durable action storage.

Default serialization is content-free. Payloads/criteria stay in RAM and cannot
be reconstructed from a metadata export. Existing policy, grants and the native
input boundary remain authoritative. Unknown legacy session/DPI fields are None.
"""

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from monster.computer.contracts import Bounds, validate_action
from monster.mission.contracts import Approval as ApprovalGrant  # noqa: F401 -- shared authority

Reference = Annotated[str, StringConstraints(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9_.:-]+$')]
Hash = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
Operation = Literal['move', 'click', 'scroll', 'drag', 'text', 'press', 'chord']


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True, allow_inf_nan=False, revalidate_instances='always')
    schema_version: Literal[1] = 1
    execution_authority: Literal['none'] = 'none'

    @model_validator(mode='before')
    @classmethod
    def version(cls, value):
        if (isinstance(value, dict) and 'schema_version' in value
                and (type(value['schema_version']) is not int or value['schema_version'] != 1)):
            raise ValueError('Unsupported operational contract version')
        return value


def _geometry(bounds):
    if (any(type(v) is not int for v in (bounds.x, bounds.y, bounds.width, bounds.height))
            or bounds.width < 1 or bounds.height < 1
            or bounds.width * bounds.height > 40_000_000
            or not -100000 <= bounds.x <= bounds.x + bounds.width - 1 <= 100000
            or not -100000 <= bounds.y <= bounds.y + bounds.height - 1 <= 100000):
        raise ValueError('Invalid physical geometry')


class ObservationFrame(Record):
    observation_id: Reference
    capture_ref: Reference
    captured_at: float = Field(ge=0)
    controller_generation: int = Field(ge=0)
    bounds: Bounds
    displays: tuple[Bounds, ...] = Field(min_length=1, max_length=64)
    image_sha256: Hash
    coordinate_space: Literal['native_image_pixels'] = 'native_image_pixels'
    transform_version: Literal['native-image-origin-v1'] = 'native-image-origin-v1'
    selected_display_index: int | None = Field(default=None, ge=0, le=63)
    dpi_scale: tuple[float, float] | None = None
    capture_epoch: int | None = Field(default=None, ge=0)
    session_ref: Reference | None = None
    foreground_guard: int = Field(default=0, ge=0, exclude=True, repr=False)

    @field_validator('displays', 'dpi_scale', mode='before')
    @classmethod
    def json_arrays(cls, value, info: ValidationInfo):
        # Wire JSON has arrays; Python construction still requires immutable tuples.
        return tuple(value) if info.mode == 'json' and type(value) is list else value

    @model_validator(mode='after')
    def geometry(self):
        _geometry(self.bounds)
        for display in self.displays:
            _geometry(display)
        if self.selected_display_index is not None:
            if self.selected_display_index >= len(self.displays):
                raise ValueError('Unknown selected display')
            display = self.displays[self.selected_display_index]
            if not (display.contains(self.bounds.x, self.bounds.y)
                    and display.contains(self.bounds.x + self.bounds.width - 1,
                                         self.bounds.y + self.bounds.height - 1)):
                raise ValueError('Capture is outside the selected display')
        if self.dpi_scale is not None and any(not math.isfinite(v) or not .25 <= v <= 8 for v in self.dpi_scale):
            raise ValueError('Invalid DPI metadata')
        return self


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate action field')
        result[key] = value
    return result


class ActionPayload(Record):
    operation: Operation
    payload_json: str = Field(min_length=1, max_length=16000, exclude=True, repr=False)

    @classmethod
    def from_action(cls, action):
        return cls(operation=action.get('op'), payload_json=json.dumps(
            action, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False,
        ))

    def to_action(self):
        # A fresh dictionary every time; callers cannot mutate the stored record.
        return json.loads(self.payload_json, object_pairs_hook=_unique_pairs)

    @model_validator(mode='after')
    def action(self):
        value = self.to_action()
        if type(value) is not dict or value.get('op') != self.operation:
            raise ValueError('Action operation mismatch')
        validate_action(value, [Bounds(-100000, -100000, 200001, 200001)])
        canonical = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        if self.payload_json != canonical:
            raise ValueError('Action payload must use canonical JSON')
        return self


class ActionProposal(Record):
    action_id: Reference
    task_ref: Reference
    frame: ObservationFrame
    action: ActionPayload
    target_description: str = Field(min_length=1, max_length=300, exclude=True, repr=False)
    expected_result: str = Field(min_length=1, max_length=300, exclude=True, repr=False)
    criterion_ref: Reference
    model_ref: Reference
    mission_ref: Reference | None = None
    subtask_ref: Reference | None = None
    model_risk_hint: Literal['routine', 'sensitive', 'uncertain'] | None = None
    source_coordinate_space: Literal['native_image_pixels', 'normalized_1000', 'ui_venus_normalized_999']
    preview_only: bool = False
    prompt_version: Reference | None = None

    @model_validator(mode='after')
    def image_scope(self):
        if self.source_coordinate_space == 'ui_venus_normalized_999' and not self.preview_only:
            raise ValueError('UI-Venus provenance must remain preview-only')
        validate_action(self.action.to_action(), [Bounds(0, 0, self.frame.bounds.width, self.frame.bounds.height)])
        return self


class GroundedAction(Record):
    effect_ref: Reference | None = None
    proposal: ActionProposal
    checked_frame: ObservationFrame
    physical_action: ActionPayload
    locator_method: Literal['legacy_model_coordinates', 'keyboard']
    grounding_confidence: None = None  # No calibrated confidence exists in the legacy adapter.

    @model_validator(mode='after')
    def binding(self):
        original, checked = self.proposal.frame, self.checked_frame
        if self.proposal.preview_only:
            raise ValueError('Preview records cannot enter the live action adapter')
        for field in ('controller_generation', 'bounds', 'displays', 'dpi_scale', 'capture_epoch', 'session_ref'):
            if getattr(original, field) != getattr(checked, field):
                raise ValueError('Observation context changed')
        if original.foreground_guard != checked.foreground_guard:
            raise ValueError('Foreground context changed')
        if checked.captured_at < original.captured_at:
            raise ValueError('Recheck predates proposal')
        expected = self.proposal.action.to_action()
        for xkey, ykey in (('x', 'y'), ('end_x', 'end_y')):
            if xkey in expected:
                expected[xkey] += checked.bounds.x
                expected[ykey] += checked.bounds.y
        method = 'legacy_model_coordinates' if 'x' in expected else 'keyboard'
        if self.locator_method != method:
            raise ValueError('Locator method does not match payload')
        if self.physical_action.to_action() != expected:
            raise ValueError('Mapped payload changed')
        validate_action(expected, list(checked.displays))
        return self


class ExecutionReceipt(Record):
    effect_ref: Reference | None = None
    action_id: Reference
    controller_generation: int = Field(ge=0)
    input_job_id: int = Field(ge=0)
    worker_generation: int | None = Field(default=None, ge=0)
    status: Literal['inserted', 'rejected', 'partial_or_unknown']
    events: int | None = Field(default=None, ge=0, le=100000)
    cleanup_confirmed: bool | None = None
    handoff_started_at: float | None = Field(default=None, ge=0)
    receipt_received_at: float | None = Field(default=None, ge=0)
    outcome_verified: Literal[False] = False
    reason_code: Literal['input_inserted', 'input_rejected', 'input_unknown']

    @model_validator(mode='after')
    def reason(self):
        expected = {'inserted': 'input_inserted', 'rejected': 'input_rejected',
                    'partial_or_unknown': 'input_unknown'}[self.status]
        if self.reason_code != expected:
            raise ValueError('Receipt reason does not match status')
        if (self.handoff_started_at is not None and self.receipt_received_at is not None
                and self.receipt_received_at < self.handoff_started_at):
            raise ValueError('Receipt predates handoff')
        return self


class VerificationResult(Record):
    effect_ref: Reference | None = None
    action_id: Reference
    criterion_ref: Reference
    post_frame: ObservationFrame
    model_status: Literal['succeeded', 'failed', 'uncertain']
    verifier_ref: Reference
    evidence_ref: Reference
    evidence_kind: Literal['model_assessment'] = 'model_assessment'
    criterion_source: Literal['proposal', 'legacy_controller_override']
    reason_code: Literal['model_reported_success', 'model_reported_failure', 'model_uncertain']

    @model_validator(mode='after')
    def reason(self):
        expected = {'succeeded': 'model_reported_success', 'failed': 'model_reported_failure',
                    'uncertain': 'model_uncertain'}[self.model_status]
        if self.reason_code != expected:
            raise ValueError('Verification reason does not match model assessment')
        return self


def model_reference(model):
    return 'model:' + hashlib.sha256(model.encode('utf-8')).hexdigest()
