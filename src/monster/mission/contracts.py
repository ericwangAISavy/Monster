"""Versioned, JSON-only mission contracts. Text/content resolves outside this plane."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Ref = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1


class MissionStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING = "waiting_approval"
    PAUSED = "paused"
    PARTIAL = "partially_complete"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubtaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFIED = "verified_success"
    FAILED = "verified_failure"
    UNCERTAIN = "uncertain"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    NEEDS_HUMAN = "needs_human"


class EffectStatus(StrEnum):
    PREPARED = "prepared"
    APPROVED = "approved"
    DISPATCH = "dispatch_started"
    SUCCESS = "observed_success"
    FAILURE = "observed_failure"
    UNCERTAIN = "uncertain"


class Subtask(Contract):
    subtask_id: Ref
    objective_ref: Ref
    success_refs: list[Ref] = Field(min_length=1, max_length=16)
    depends_on: list[Ref] = Field(default_factory=list, max_length=100)
    application_ref: Ref = "fake-app"
    category: Literal[
        "read_only",
        "local_reversible",
        "external_commit",
        "destructive",
        "credential_or_identity",
        "unknown",
    ] = "unknown"
    requires_approval: bool = True
    max_actions: int = Field(default=10, ge=1, le=10)
    max_model_calls: int = Field(default=20, ge=1, le=20)
    deadline_seconds: int = Field(default=300, ge=1, le=300)
    retry_budget: Literal[0] = 0  # No automatic effect retries in Phase 1.


class Mission(Contract):
    mission_id: Ref
    objective_ref: Ref
    workspace_id: Ref = "fake-workspace"
    authorized: bool = False
    subtasks: list[Subtask] = Field(min_length=1, max_length=100)
    max_delegations: int = Field(default=100, ge=1, le=100)
    max_transitions: int = Field(default=1000, ge=8, le=1000)
    deadline_utc: float = Field(gt=0, allow_inf_nan=False)
    approval_seconds: int = Field(default=60, ge=1, le=300)

    @model_validator(mode="after")
    def dependencies(self):
        ids = [s.subtask_id for s in self.subtasks]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate subtask IDs")
        visited = set()
        remaining = {s.subtask_id: set(s.depends_on) for s in self.subtasks}
        while remaining:
            ready = [k for k, deps in remaining.items() if deps <= visited]
            if not ready:
                raise ValueError("Unknown dependency or dependency cycle")
            for key in ready:
                visited.add(key)
                del remaining[key]
        return self


class TaskResult(Contract):
    mission_id: Ref
    subtask_id: Ref
    effect_id: Ref
    generation: int = Field(ge=1)
    status: Literal[
        "verified_success",
        "verified_failure",
        "uncertain",
        "needs_human",
        "blocked",
        "cancelled",
        "timed_out",
    ]
    proven_not_started: bool = False
    model_planning_calls: int | None = Field(default=None, ge=0, le=20)
    review_count: int | None = Field(default=None, ge=0, le=1024)
    cleanup_confirmed: bool = False
    confirmed_input_transactions: int | None = Field(default=None, ge=0, le=10)
    visual_verified: bool = False
    human_confirmed: bool = False
    observation_ref: Ref | None = None
    evidence_ref: Ref | None = None
    evidence_kind: Literal["fake", "visual", "human", "human_attested", "none"] = "none"
    model_id: Ref = "fake-model"
    elapsed_ms: int = Field(default=0, ge=0, le=300000)
    transaction_ids: list[Ref] = Field(default_factory=list, max_length=10)
    reason: Literal[
        "verified",
        "not_observed",
        "timeout",
        "cancelled",
        "malformed",
        "runner_error",
        "needs_review",
        "budget",
        "denied",
        "expired",
    ]

    @model_validator(mode="after")
    def evidence(self):
        if self.status in ("verified_success", "verified_failure") and (
            self.evidence_ref is None or self.evidence_kind == "none"
        ):
            raise ValueError("Verified outcomes require evidence")
        return self


class Effect(Contract):
    schema_version: Literal[1, 2] = 1
    action_kind: Ref | None = None
    objective_ref: Ref | None = None
    attempt_id: Ref | None = None
    workspace_id: Ref | None = None
    fingerprint: Ref | None = None
    action_digest: Ref | None = None
    observation_ref: Ref | None = None
    scene_ref: Ref | None = None
    policy_version: Ref | None = None
    created_at: float | None = Field(default=None, allow_inf_nan=False)
    expires_at: float | None = Field(default=None, allow_inf_nan=False)
    effect_id: Ref
    mission_id: Ref
    subtask_id: Ref
    generation: int = Field(ge=1)
    status: Literal[
        "prepared",
        "approved",
        "dispatch_started",
        "observed_success",
        "observed_failure",
        "uncertain",
        "approval_pending",
        "denied",
        "dispatch_intent_recorded",
        "awaiting_observation",
        "not_dispatched",
    ]
    target_ref: Ref
    category: Ref
    result: TaskResult | None = None

    @model_validator(mode="after")
    def versioned_binding(self):
        if self.schema_version == 2 and any(
            getattr(self, name) is None
            for name in (
                "attempt_id",
                "workspace_id",
                "fingerprint",
                "action_digest",
                "observation_ref",
                "scene_ref",
                "policy_version",
                "created_at",
                "expires_at",
                "action_kind",
                "objective_ref",
            )
        ):
            raise ValueError("Version2 effect requires immutable security bindings")
        return self


class Approval(Contract):
    schema_version: Literal[1, 2] = 1
    fingerprint: Ref | None = None
    workspace_id: Ref | None = None
    policy_version: Ref | None = None
    review_digest: Ref | None = None
    actor_ref: Ref | None = None
    issued_at: float | None = Field(default=None, allow_inf_nan=False)
    consumed_at: float | None = Field(default=None, allow_inf_nan=False)
    ui_request_id: Ref | None = None
    approval_id: Ref
    mission_id: Ref
    subtask_id: Ref
    effect_id: Ref
    generation: int = Field(ge=1)
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    decision: Literal["pending", "approved", "denied", "expired", "revoked"] = "pending"

    @model_validator(mode="after")
    def versioned_binding(self):
        if self.schema_version == 2 and any(
            getattr(self, name) is None
            for name in (
                "fingerprint",
                "workspace_id",
                "policy_version",
                "review_digest",
                "ui_request_id",
            )
        ):
            raise ValueError("Version2 approval requires exact review bindings")
        return self


class EffectEvidence(Contract):
    source: Literal["worker", "visual", "human_attested"]
    observed_at: float = Field(ge=0, allow_inf_nan=False)
    observation_ref: Ref | None = None
    category: Literal["input_receipt", "visible_outcome", "human_attestation"]

    @model_validator(mode="after")
    def observed_reference(self):
        if self.source != "worker" and self.observation_ref is None:
            raise ValueError("Outcome evidence requires an observation reference")
        return self


class ApprovalDecision(Contract):
    approval_id: Ref
    effect_id: Ref
    generation: int = Field(ge=1)
    decision: Literal["approve", "deny"]


class GuiSubtaskRequest(Contract):
    mission_id: Ref
    workspace_id: Ref
    effect_id: Ref
    generation: int = Field(ge=1)
    subtask: Subtask
    schedule_ref: Ref | None = None
    configuration_digest: Ref | None = None
    authorization_expires_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)


MISSION_TRANSITIONS = {
    "created": {"running", "failed", "cancelled"},
    "running": {
        "waiting_approval",
        "paused",
        "partially_complete",
        "completed",
        "failed",
        "cancelled",
    },
    "waiting_approval": {"running", "paused", "partially_complete", "cancelled"},
    "paused": {"running", "cancelled", "partially_complete"},
    "partially_complete": set(),
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}
EFFECT_TRANSITIONS = {
    "prepared": {"approved"},
    "approved": {"dispatch_started"},
    "dispatch_started": {"observed_success", "observed_failure", "uncertain"},
    "uncertain": {"observed_success", "observed_failure"},
    "observed_success": set(),
    "observed_failure": set(),
}


def valid_transition(old: str, new: str) -> bool:
    return new in MISSION_TRANSITIONS.get(old, set())


def migrate_state(state: dict) -> dict:
    state = dict(state)
    version = state.get("schema_version", 0)
    if type(version) is not int or version not in (0, 1):
        raise ValueError("Unsupported mission state schema")
    if version == 0:
        state["schema_version"] = 1
        if state.get("status") == "waiting":
            state["status"] = "waiting_approval"
    return state
