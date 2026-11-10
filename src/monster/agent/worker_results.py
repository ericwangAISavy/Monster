"""Versioned Worker correlation and metadata-only advisory result envelopes."""

import json
from dataclasses import asdict
from typing import Literal

from pydantic import Field, field_validator, model_validator

from monster.agent.contracts import Hash, ObservationFrame, Record, Reference
from monster.providers.base import Decision, Verification

WorkerOperation = Literal[
    "proposal",
    "action_verification",
    "scene_comparison",
    "draft_target",
    "drawing_target",
    "app_launch",
    "completion",
]
WORKER_PHASES = {
    "proposal": "deciding",
    "action_verification": "verifying",
    "scene_comparison": "comparing",
    "draft_target": "checking_target",
    "drawing_target": "checking_drawing_target",
    "app_launch": "checking_launch",
    "completion": "checking_completion",
}


class WorkerBinding(Record):
    request_ref: Reference
    task_ref: Reference
    operation: WorkerOperation
    model_ref: Reference
    frame: ObservationFrame
    before_frame: ObservationFrame | None = None
    reference_image_sha256: Hash | None = None

    @model_validator(mode="after")
    def scope(self):
        if (self.operation == "scene_comparison") != (self.before_frame is not None):
            raise ValueError("Scene comparison requires both frames")
        if self.before_frame is not None and (
            self.before_frame.controller_generation != self.frame.controller_generation
            or self.before_frame.captured_at > self.frame.captured_at
        ):
            raise ValueError("Changed scene generation/order")
        if self.operation != "action_verification" and self.reference_image_sha256 is not None:
            raise ValueError("Unexpected reference image")
        return self


class ProposalResult(Record):
    binding: WorkerBinding
    decision_json: str = Field(min_length=1, max_length=64000, exclude=True, repr=False)

    @classmethod
    def from_decision(cls, binding, decision):
        if not isinstance(decision, Decision):
            raise TypeError("Invalid decision type")
        return cls(
            binding=binding,
            decision_json=json.dumps(
                asdict(decision),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
        )

    def to_decision(self):
        # Fresh payload/provenance on every read; default export cannot recreate it.
        return Decision(**json.loads(self.decision_json))

    @model_validator(mode="after")
    def snapshot(self):
        if self.binding.operation != "proposal":
            raise ValueError("Wrong proposal binding")
        try:
            value = json.loads(self.decision_json)
            if (
                type(value) is not dict
                or set(value) != set(Decision.__dataclass_fields__)
                or value["kind"] not in ("action", "complete", "done", "clarify", "unable")
                or type(value["summary"]) is not str
                or not 1 <= len(value["summary"].strip()) <= 300
                or (value["kind"] == "action" and type(value["action"]) is not dict)
                or (value["kind"] != "action" and value["action"] is not None)
                or (
                    value["risk"] is not None
                    and (type(value["risk"]) is not str or len(value["risk"]) > 300)
                )
                or (
                    value["expected_result"] is not None
                    and (
                        type(value["expected_result"]) is not str
                        or not 1 <= len(value["expected_result"].strip()) <= 300
                    )
                )
                or (
                    value["coordinate_source"] is not None
                    and type(value["coordinate_source"]) is not dict
                )
                or json.dumps(
                    value,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                != self.decision_json
            ):
                raise ValueError("Invalid proposal snapshot")
        except (ValueError, TypeError, RecursionError, KeyError):
            raise ValueError("Invalid proposal snapshot") from None
        return self


class ActionVerificationResult(Record):
    binding: WorkerBinding
    status: Literal["succeeded", "failed", "uncertain"]
    evidence: str = Field(min_length=1, max_length=300, exclude=True, repr=False)
    evidence_kind: Literal["model_assessment"] = "model_assessment"

    @field_validator("evidence")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Empty assessment")
        return value

    @model_validator(mode="after")
    def operation(self):
        if self.binding.operation != "action_verification":
            raise ValueError("Wrong assessment binding")
        return self

    def to_verification(self):
        return Verification(self.status, self.evidence)


class SceneResult(Record):
    binding: WorkerBinding
    matched: bool
    reason: Literal[
        "capture_bounds_changed",
        "display_geometry_changed",
        "foreground_changed_or_missing",
        "invalid_image_or_dimensions_changed",
        "target_patch_changed",
        "drag_path_changed",
        "whole_scene_changed",
        "scene_matches",
    ]
    patch: tuple[int, int, int, int] | None = None
    changed_pixels: int | None = Field(default=None, ge=0, le=40000000)
    sampled_pixels: int | None = Field(default=None, ge=0, le=40000000)
    allowed_changed_pixels: float | None = Field(default=None, ge=0, le=40000000)
    count_is_lower_bound: bool | None = None
    evidence_kind: Literal["scene_comparison"] = "scene_comparison"

    @model_validator(mode="after")
    def operation(self):
        if self.binding.operation != "scene_comparison" or self.matched != (
            self.reason == "scene_matches"
        ):
            raise ValueError("Wrong scene result binding")
        return self

    def details(self):
        return {
            key: getattr(self, key)
            for key in (
                "matched",
                "reason",
                "patch",
                "changed_pixels",
                "sampled_pixels",
                "allowed_changed_pixels",
                "count_is_lower_bound",
            )
            if getattr(self, key) is not None
        }
