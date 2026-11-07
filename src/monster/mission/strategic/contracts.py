"""Phase 5 boundaries: immutable, reference-only durable planning data.

No natural-language goal, document, screenshots or model reasoning belongs here.
Tuples prevent nested mutation after review (Pydantic frozen alone is shallow).
"""

from typing import Literal

from pydantic import Field, model_validator

from ..contracts import Contract, Ref
from ..scheduling import MissionBudget, ScheduledPlan, canonical_digest


class GoalCriterion(Contract):
    criterion_id: Ref
    requirement_ref: Ref
    importance: Literal["required", "optional"] = "required"
    evidence_kind: Literal["fake", "visual", "human_attested", "none"] = "fake"


class GoalSpecification(Contract):
    goal_ref: Ref
    summary_ref: Literal["design-proposal"] = "design-proposal"
    criteria: tuple[GoalCriterion, ...] = Field(min_length=1, max_length=12)
    workspace_ref: Literal["simulation-desktop"] = "simulation-desktop"
    application_refs: tuple[Literal["simulation-editor"], ...] = Field(
        default=("simulation-editor",), min_length=1, max_length=1
    )
    permitted_destinations: tuple[Ref, ...] = Field(default=(), max_length=4)
    constraints: tuple[Literal["simulation-only", "no-network", "human-review"], ...] = Field(
        default=(), max_length=3
    )
    exclusions: tuple[Literal["native-input", "external-effects", "credentials"], ...] = Field(
        default=("native-input", "external-effects", "credentials"), min_length=3, max_length=3
    )
    private_context_required: bool = False
    allow_optional_gaps: bool = False

    @model_validator(mode="after")
    def unique(self):
        if len({c.criterion_id for c in self.criteria}) != len(self.criteria):
            raise ValueError("duplicate_criteria")
        if set(self.exclusions) != {"native-input", "external-effects", "credentials"}:
            raise ValueError("scope_expansion")
        return self


class ModelBinding(Contract):
    provider: Literal["fake", "ollama"]
    model_ref: Ref
    destination_ref: Ref
    configuration_digest: Ref
    capabilities: tuple[Literal["text_schema", "vision", "action_schema"], ...] = Field(
        min_length=1, max_length=3
    )


class ModelRoleConfig(Contract):
    planner: ModelBinding
    computer_use: ModelBinding
    planner_data_consent: bool = False
    computer_use_data_consent: bool = False
    prompt_version: Literal["phase5-v1"] = "phase5-v1"

    @model_validator(mode="after")
    def capability_routing(self):
        if "text_schema" not in self.planner.capabilities:
            raise ValueError("planner_incompatible")
        if not {"vision", "action_schema"} <= set(self.computer_use.capabilities):
            raise ValueError("executor_incompatible")
        if self.planner.provider != "fake" and not self.planner_data_consent:
            raise ValueError("planner_routing_consent_required")
        if self.computer_use.provider != "fake" and not self.computer_use_data_consent:
            raise ValueError("executor_routing_consent_required")
        return self


class StrategicLimits(Contract):
    max_calls: int = Field(default=6, ge=1, le=8)
    max_replans: int = Field(default=2, ge=0, le=3)
    max_response_bytes: int = Field(default=32768, ge=1024, le=65536)
    max_context_bytes: int = Field(default=16384, ge=1024, le=32768)
    max_depth: int = Field(default=8, ge=1, le=12)
    max_width: int = Field(default=6, ge=1, le=12)
    max_edges: int = Field(default=24, ge=0, le=32)
    timeout_seconds: int = Field(default=30, ge=1, le=60)
    max_output_tokens: int = Field(default=2048, ge=256, le=4096)
    context_tokens: int = Field(default=16384, ge=1024, le=16384)


class StrategicObjective(Contract):
    objective_id: Ref
    objective_kind: Ref
    criterion_ids: tuple[Ref, ...] = Field(min_length=1, max_length=12)
    depends_on: tuple[Ref, ...] = Field(default=(), max_length=12)
    workspace_ref: Ref = "simulation-desktop"
    application_ref: Ref = "simulation-editor"
    risk_class: Literal["simulated"] = "simulated"
    cost_class: Literal["small"] = "small"
    preconditions: tuple[Literal["dependencies-verified"], ...] = Field(
        default=("dependencies-verified",), min_length=1, max_length=1
    )


class CoverageGap(Contract):
    criterion_id: Ref
    reason: Literal["unsupported", "needs_user"]


class PlanProposal(Contract):
    proposal_id: Ref
    goal_ref: Ref
    objectives: tuple[StrategicObjective, ...] = Field(min_length=1, max_length=12)
    gaps: tuple[CoverageGap, ...] = Field(default=(), max_length=12)
    assumptions: tuple[Literal["fixture-only", "user-review-required"], ...] = Field(
        default=("fixture-only",), max_length=2
    )
    unresolved_questions: tuple[Literal["clarify-goal", "human-handoff"], ...] = Field(
        default=(), max_length=2
    )
    risks: tuple[Literal["simulation-only"], ...] = Field(
        default=("simulation-only",), max_length=1
    )
    uncertainty: Literal["bounded", "needs_user"] = "bounded"
    provenance: Literal["fake", "ollama"] = "fake"


class PlanRevision(Contract):
    parent_revision_id: Ref
    parent_digest: Ref
    proposal: PlanProposal
    blocker_ref: Ref
    evidence_refs: tuple[Ref, ...] = Field(min_length=1, max_length=12)
    reason: Literal["safe-pre-dispatch-alternative"] = "safe-pre-dispatch-alternative"
    consent_scope_changed: Literal[True] = True


class AdmittedPlan(Contract):
    revision_id: Ref
    parent_revision_id: Ref | None = None
    parent_digest: Ref | None = None
    revision_reason: Literal["initial", "safe-pre-dispatch-alternative"] = "initial"
    revision_evidence_refs: tuple[Ref, ...] = Field(default=(), max_length=12)
    blocker_ref: Ref | None = None
    goal: GoalSpecification
    proposal: PlanProposal
    roles: ModelRoleConfig
    limits: StrategicLimits
    schedule: ScheduledPlan
    policy_version: Literal["strategic-v1"] = "strategic-v1"

    def digest(self):
        return canonical_digest(self.model_dump(mode="json"))


class StrategicConsent(Contract):
    mission_id: Ref
    revision_id: Ref
    plan_digest: Ref
    epoch: int = Field(ge=1)
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    reviewed_summary: Literal[True] = True


class CriterionEvaluation(Contract):
    criterion_id: Ref
    status: Literal["satisfied", "contradicted", "uncertain", "unsupported", "needs_user"]
    evidence_refs: tuple[Ref, ...] = Field(default=(), max_length=12)
    provenance: tuple[Literal["fake", "visual", "human_attested"], ...] = Field(
        default=(), max_length=12
    )


class StrategicEvaluation(Contract):
    plan_digest: Ref
    criteria: tuple[CriterionEvaluation, ...] = Field(max_length=12)
    status: Literal["completed", "completed_with_optional_gaps", "partial", "needs_user"]
    blockers: tuple[Ref, ...] = Field(default=(), max_length=12)
    next_step: Literal["continue", "complete", "review", "reconcile"]
    confidence: Literal["evidence-bound"] = "evidence-bound"


class PlannerRunRecord(Contract):
    call_id: Ref
    operation: Literal["plan", "revision", "assessment"]
    status: Literal["reserved", "valid", "invalid", "cancelled", "unavailable"] = "reserved"
    latency_ms: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    validation_code: Literal[
        "none", "invalid_response", "stale_response", "provider_unavailable"
    ] = "none"
    prompt_version: Literal["phase5-v1"] = "phase5-v1"


def fake_roles():
    return ModelRoleConfig(
        planner=ModelBinding(
            provider="fake",
            model_ref="fake-planner",
            destination_ref="no-egress",
            configuration_digest="0" * 64,
            capabilities=("text_schema",),
        ),
        computer_use=ModelBinding(
            provider="fake",
            model_ref="fake-model",
            destination_ref="no-egress",
            configuration_digest="0" * 64,
            capabilities=("vision", "action_schema"),
        ),
    )


def default_budget():
    # Each stage reserves two planning calls, one fake input and 30 active seconds.
    return MissionBudget(max_pure_attempts=1)
