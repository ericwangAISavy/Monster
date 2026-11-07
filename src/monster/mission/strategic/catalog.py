"""Application-owned simulation handlers, never model-defined execution instructions."""

from ..contracts import Mission, Subtask
from ..scheduling import ScheduledPlan, canonical_digest
from .contracts import AdmittedPlan, GoalCriterion, GoalSpecification, PlanProposal

CATALOG = {
    "requirements": ("requirements", "Identify requirements"),
    "architecture": ("architecture", "Draft architecture outline"),
    "tradeoffs": ("tradeoffs", "Describe tradeoffs"),
    "tradeoffs-alternative": ("tradeoffs", "Compare alternatives with a structured rubric"),
    "validation": ("validation", "Describe test approach"),
    "review": ("review", "Review criterion coverage"),
    "summary": ("summary", "Present evidenced summary"),
}
STAGES = ("requirements", "architecture", "tradeoffs", "validation", "review", "summary")
CATALOG_DIGEST = canonical_digest(CATALOG)


def fixture_goal(goal_ref="design-goal"):
    return GoalSpecification(
        goal_ref=goal_ref,
        criteria=tuple(GoalCriterion(criterion_id=kind, requirement_ref=kind) for kind in STAGES),
    )


def validate_proposal(proposal, goal, limits):
    proposal = PlanProposal.model_validate_json(proposal.model_dump_json())
    if proposal.goal_ref != goal.goal_ref:
        raise ValueError("goal_mismatch")
    ids = [o.objective_id for o in proposal.objectives]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_objective")
    criteria = {c.criterion_id: c for c in goal.criteria}
    gap_ids = [g.criterion_id for g in proposal.gaps]
    if len(set(gap_ids)) != len(gap_ids) or not set(gap_ids) <= criteria.keys():
        raise ValueError("invalid_coverage_gap")
    covered = set()
    for o in proposal.objectives:
        if o.objective_kind not in CATALOG:
            raise ValueError("unsupported_objective")
        if o.workspace_ref != goal.workspace_ref or o.application_ref not in goal.application_refs:
            raise ValueError("scope_expansion")
        if len(set(o.criterion_ids)) != len(o.criterion_ids):
            raise ValueError("duplicate_criterion_binding")
        for ref in o.criterion_ids:
            if ref not in criteria or criteria[ref].requirement_ref != CATALOG[o.objective_kind][0]:
                raise ValueError("unsupported_criterion_binding")
        if len(set(o.depends_on)) != len(o.depends_on):
            raise ValueError("duplicate_dependency")
        covered.update(o.criterion_ids)
    if covered & set(gap_ids) or covered | set(gap_ids) != criteria.keys():
        raise ValueError("uncovered_criterion")
    remaining = {o.objective_id: set(o.depends_on) for o in proposal.objectives}
    if sum(map(len, remaining.values())) > limits.max_edges:
        raise ValueError("dag_edges")
    visited, depth = set(), 0
    while remaining:
        ready = [k for k, deps in remaining.items() if deps <= visited]
        depth += 1
        if not ready:
            raise ValueError("dependency_cycle_or_missing")
        if len(ready) > limits.max_width or depth > limits.max_depth:
            raise ValueError("dag_shape")
        visited.update(ready)
        for key in ready:
            del remaining[key]
    return proposal


def compile_plan(
    proposal,
    goal,
    roles,
    limits,
    budget,
    *,
    mid,
    revision_id,
    now,
    parent=None,
    revision_evidence_refs=(),
    blocker_ref=None,
):
    proposal = validate_proposal(proposal, goal, limits)
    if (
        goal.permitted_destinations
        and roles.planner.destination_ref not in goal.permitted_destinations
    ):
        raise ValueError("destination_outside_goal")
    # Dynamic execution is strictly fake-only, even if native qualification is supplied.
    mission = Mission(
        mission_id=mid,
        objective_ref="strategic-design-v1",
        workspace_id=goal.workspace_ref,
        authorized=True,
        deadline_utc=(
            parent.schedule.mission.deadline_utc if parent else now + budget.max_wall_seconds
        ),
        subtasks=[
            Subtask(
                subtask_id=o.objective_id,
                objective_ref=o.objective_kind,
                success_refs=[f"criterion-{CATALOG[o.objective_kind][0]}"],
                depends_on=list(o.depends_on),
                application_ref="simulation-editor",
                category="local_reversible",
                requires_approval=True,
                max_actions=1,
                max_model_calls=2,
                deadline_seconds=20,
            )
            for o in proposal.objectives
        ],
        max_delegations=12,
    )
    schedule = ScheduledPlan(
        mission=mission,
        template_ref="strategic-design-v1",
        mode="simulation",
        budget=budget,
        created_at=parent.schedule.created_at if parent else now,
        provider_ref=roles.computer_use.provider,
        model_ref=roles.computer_use.model_ref,
        configuration_digest=canonical_digest(roles.model_dump(mode="json")),
        catalog_digest=CATALOG_DIGEST,
    )
    return AdmittedPlan(
        revision_id=revision_id,
        parent_revision_id=parent.revision_id if parent else None,
        parent_digest=parent.digest() if parent else None,
        revision_reason="safe-pre-dispatch-alternative" if parent else "initial",
        revision_evidence_refs=revision_evidence_refs,
        blocker_ref=blocker_ref,
        goal=goal,
        proposal=proposal,
        roles=roles,
        limits=limits,
        schedule=schedule,
    )


def validate_schedule(schedule):
    tasks = schedule.mission.subtasks
    if (
        schedule.mode != "simulation"
        or schedule.catalog_digest != CATALOG_DIGEST
        or schedule.mission.workspace_id != "simulation-desktop"
        or not 1 <= len(tasks) <= 12
    ):
        raise ValueError("strategic_simulation_only")
    for task in tasks:
        if (
            task.objective_ref not in CATALOG
            or task.application_ref != "simulation-editor"
            or task.category != "local_reversible"
            or not task.requires_approval
            or task.success_refs != [f"criterion-{CATALOG[task.objective_ref][0]}"]
            or task.max_actions != 1
            or task.max_model_calls != 2
            or task.deadline_seconds != 20
        ):
            raise ValueError("unsupported_strategic_catalog")
