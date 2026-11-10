"""RAM-only read-only checks and metadata-only advisory outcomes, never grants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from monster.agent.contract_adapters import observation_frame
from monster.agent.contracts import (
    ActionPayload,
    ObservationFrame,
    Record,
    Reference,
    model_reference,
)

if TYPE_CHECKING:
    from monster.agent.worker import VisualContext

CheckOperation = Literal["draft_target", "drawing_target", "app_launch", "completion"]
CHECK_PHASES = {
    "draft_target": "checking_target",
    "drawing_target": "checking_drawing_target",
    "app_launch": "checking_launch",
    "completion": "checking_completion",
}


class VisualCheckBinding(Record):
    request_ref: Reference
    task_ref: Reference
    operation: CheckOperation
    model_ref: Reference
    frame: ObservationFrame


@dataclass(frozen=True)
class VisualCheckRequest:
    binding: VisualCheckBinding
    context: VisualContext = field(repr=False)
    action: ActionPayload | None = field(default=None, repr=False)
    expected: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if not isinstance(self.binding, VisualCheckBinding):
            raise TypeError("Visual check requires an immutable typed binding")
        binding = VisualCheckBinding.model_validate(self.binding)
        if binding.frame != observation_frame(
            self.context.observation, binding.frame.controller_generation
        ) or binding.model_ref != model_reference(self.context.model):
            raise ValueError("Changed visual-check context")
        target = binding.operation in ("draft_target", "drawing_target")
        if target:
            if not isinstance(self.action, ActionPayload):
                raise ValueError("Target check requires a bound action")
            ActionPayload.model_validate(self.action)
        elif self.action is not None:
            raise ValueError("Observation-only checks cannot supply input actions")
        if binding.operation == "app_launch":
            if type(self.expected) is not str or not 1 <= len(self.expected.strip()) <= 1000:
                raise ValueError("App check requires a bounded expectation")
        elif self.expected is not None:
            raise ValueError("Unexpected visual-check criterion override")

    @classmethod
    def create(cls, operation, context, *, generation, task_ref, action=None, expected=None):
        return cls(
            VisualCheckBinding(
                request_ref=uuid4().hex,
                task_ref=task_ref,
                operation=operation,
                model_ref=model_reference(context.model),
                frame=observation_frame(context.observation, generation),
            ),
            context,
            ActionPayload.from_action(action) if action is not None else None,
            expected,
        )


class VisualCheckResult(Record):
    binding: VisualCheckBinding
    status: Literal["ready", "needs_focus", "wrong_target", "succeeded", "failed", "uncertain"]
    evidence: str = Field(min_length=1, max_length=300, exclude=True, repr=False)
    evidence_kind: Literal["model_assessment"] = "model_assessment"

    @field_validator("evidence")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Empty visual assessment")
        return value

    @model_validator(mode="after")
    def assessment_kind(self):
        allowed = {
            "draft_target": {"ready", "needs_focus", "wrong_target", "uncertain"},
            "drawing_target": {"ready", "wrong_target", "uncertain"},
            "app_launch": {"succeeded", "failed", "uncertain"},
            "completion": {"succeeded", "failed", "uncertain"},
        }
        if self.status not in allowed[self.binding.operation]:
            raise ValueError("Assessment status does not match request")
        return self
