"""Content-free reports; unavailable native measurements remain null."""

import platform
import sys


def build_report(coordinator, mid, state):
    s = coordinator.scheduler
    active = state.get("current_subtask_id") if mid in coordinator.nodes.running else None
    progress = s.progress(mid, active, state.get("reason"))
    plan = s.plan(mid)
    u = progress.usage
    effects = coordinator.store.effects(mid)
    provenance = progress.provenance
    from monster.agent.diagnostics import RUNTIME

    from .scheduling import ProgressSnapshot

    history = [
        ProgressSnapshot.model_validate_json(r[0])
        for r in coordinator.store.connection.execute(
            "SELECT payload FROM mission_progress WHERE mission_id=? ORDER BY id", (mid,)
        )
    ]
    timings = []
    last_verified = last_active = 0
    for item in history:
        if len(item.verified_subtask_ids) > last_verified and item.active_subtask_id is None:
            timings.append(max(0, (item.usage.active_ms - last_active) / 1000))
            last_verified = len(item.verified_subtask_ids)
            last_active = item.usage.active_ms
    return {
        **progress.model_dump(mode="json"),
        "status": state["status"],
        "template_id": plan.template_ref,
        "plan_digest": plan.digest(),
        "mode": plan.mode,
        "input_transactions_confirmed": u.input_confirmed,
        "native_input_transactions": u.input_confirmed
        if provenance == "native_experimental"
        else 0,
        "budget": plan.budget.model_dump(),
        "budget_used": u.model_dump(),
        "subtasks": [
            {
                "subtask_id": task.subtask_id,
                "status": next(
                    (e.status for e in effects if e.subtask_id == task.subtask_id), "not_started"
                ),
                "evidence_source": next(
                    (
                        e.result.evidence_kind
                        for e in effects
                        if (e.subtask_id == task.subtask_id and e.result)
                    ),
                    None,
                ),
            }
            for task in plan.mission.subtasks
        ],
        "uncertain_effect_count": len(coordinator.effects.unresolved(mid)),
        "uncertain_delegation_count": sum(
            e.status in ("uncertain", "dispatch_started") for e in effects
        ),
        "time_to_verified_progress": timings,
        "time_basis": "conservative_active_budget_charge",
        "human_attested_subtask_ids": [
            e.subtask_id for e in effects if e.result and e.result.evidence_kind == "human_attested"
        ],
        "mission_verified_completion_rate": 1.0
        if len(progress.verified_subtask_ids) == 6 and state["status"] == "completed"
        else 0.0,
        "subtask_verified_completion_rate": len(progress.verified_subtask_ids) / max(1, u.starts),
        "median_model_decision_latency": None,
        "median_capture_latency": None,
        "median_input_transaction_latency": None,
        "duplicate_native_dispatch_count": None,
        "human_interventions": u.interventions + u.reviews_confirmed,
        "intervention_count_basis": "known_minimum",
        "planning_calls_confirmed": u.models_confirmed,
        "no_progress_stops": int(u.no_progress >= 2),
        "native_phase4_qualification": "PENDING",
        "manifest": {
            "schema": 4,
            "source_revision": RUNTIME["git_revision_at_startup"],
            "source_sha256": RUNTIME["source_sha256"],
            "gpu": None,
            "dpi": None,
            "model_digest": None,
            "fixture": "editor-six-v1",
            "os": platform.system(),
            "python": sys.version.split()[0],
            "machine": platform.machine(),
            "provider_ref": plan.provider_ref,
            "model_ref": plan.model_ref,
            "configuration_digest": plan.configuration_digest,
        },
        "created_at_utc": plan.created_at,
        "deadline_utc": plan.mission.deadline_utc,
        "wall_elapsed_seconds": max(0, s.clock() - plan.created_at),
        "budget_exhaustions": int("budget" in (state.get("reason") or "")),
        "worker_cleanup_failure_count": sum(
            bool(e.result and not e.result.cleanup_confirmed) for e in effects
        ),
        "replay_prevented_count": None,
        "remaining_work": progress.remaining_subtask_ids,
    }
