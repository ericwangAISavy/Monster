"""Deterministic admission/evaluation over the existing journal and Phase4 scheduler.

No runner, input API or independent scheduling loop. SQL commits are authoritative;
LangGraph may lag and reconstruct at its next guarded checkpoint.
"""

import asyncio
import json
import time
from uuid import uuid4

from ..contracts import TaskResult
from ..scheduling import BudgetUsage, MissionBudget, canonical_digest
from .catalog import CATALOG, compile_plan, validate_proposal
from .contracts import (
    AdmittedPlan,
    CriterionEvaluation,
    GoalSpecification,
    ModelRoleConfig,
    PlannerRunRecord,
    StrategicConsent,
    StrategicEvaluation,
    StrategicLimits,
    default_budget,
    fake_roles,
)
from .planning import PlannerFailure, check_private_intake


def migrate(c):
    c.execute(
        "CREATE TABLE IF NOT EXISTS strategic_sessions ("
        "mission_id TEXT PRIMARY KEY, metadata TEXT NOT NULL, active_digest TEXT, "
        "pending TEXT, pending_digest TEXT, epoch INTEGER NOT NULL DEFAULT 1, "
        "calls INTEGER NOT NULL DEFAULT 0, replans INTEGER NOT NULL DEFAULT 0, "
        "stopped INTEGER NOT NULL DEFAULT 0)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS strategic_revisions ("
        "digest TEXT PRIMARY KEY, mission_id TEXT NOT NULL, payload TEXT NOT NULL, "
        "consent TEXT NOT NULL, created_at REAL NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS strategic_calls ("
        "mission_id TEXT NOT NULL, call_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS strategic_blockers ("
        "mission_id TEXT NOT NULL, subtask_id TEXT NOT NULL, evidence_ref TEXT NOT NULL, "
        "proof TEXT NOT NULL, attempted INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY(mission_id,subtask_id))"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS strategic_evaluations ("
        "id INTEGER PRIMARY KEY, mission_id TEXT NOT NULL, payload TEXT NOT NULL)"
    )


class StrategicService:
    def __init__(self, coordinator):
        self.owner = coordinator
        self.store, self.scheduler = coordinator.store, coordinator.scheduler
        self.c, self.clock = self.store.connection, coordinator.clock
        self.inflight = set()
        self.private_context = set()  # RAM only; never survives restart.

    def exists(self, mid):
        return (
            self.c.execute("SELECT 1 FROM strategic_sessions WHERE mission_id=?", (mid,)).fetchone()
            is not None
        )

    def row(self, mid):
        row = self.c.execute(
            "SELECT * FROM strategic_sessions WHERE mission_id=?", (mid,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown_strategic_mission")
        return row

    def metadata(self, mid):
        return json.loads(self.row(mid)["metadata"])

    def active(self, mid):
        digest = self.row(mid)["active_digest"]
        row = self.c.execute(
            "SELECT payload FROM strategic_revisions WHERE digest=? AND mission_id=?", (digest, mid)
        ).fetchone()
        if row is None:
            raise ValueError("strategic_admission_required")
        plan = AdmittedPlan.model_validate_json(row[0])
        if plan.digest() != digest:
            raise ValueError("strategic_digest_changed")
        return plan

    def pending(self, mid):
        row = self.row(mid)
        if not row["pending"] or row["stopped"]:
            raise ValueError("no_pending_plan")
        plan = AdmittedPlan.model_validate_json(row["pending"])
        if plan.digest() != row["pending_digest"]:
            raise ValueError("strategic_digest_changed")
        return plan

    def guard(self, mid):
        try:
            plan = self.active(mid)
            row = self.row(mid)
            if row["stopped"]:
                return "strategic_stopped"
            if self.scheduler.plan(mid).digest() != plan.schedule.digest():
                return "strategic_digest_changed"
            if row["pending"]:
                return "strategic_review_required"
            if plan.goal.private_context_required and mid not in self.private_context:
                return "private_context_required"
        except (ValueError, KeyError):
            return "strategic_admission_required"
        return None

    def reconciliation_required(self, mid):
        return (
            self.owner.effects.unresolved(mid)
            or any(
                e.status in ("uncertain", "dispatch_started")
                or (e.result is not None and not e.result.cleanup_confirmed)
                for e in self.store.effects(mid)
            )
            or any(key[0] == mid for key in self.scheduler.running)
        )

    async def prepare(self, goal, planner, *, roles=None, limits=None, budget=None, mid=None):
        goal = GoalSpecification.model_validate_json(goal.model_dump_json())
        roles = ModelRoleConfig.model_validate_json((roles or fake_roles()).model_dump_json())
        limits = StrategicLimits.model_validate_json(
            (limits or StrategicLimits()).model_dump_json()
        )
        budget = MissionBudget.model_validate_json((budget or default_budget()).model_dump_json())
        mid = mid or "strategic-" + uuid4().hex
        from ..runner import FakeGuiSubtaskRunner

        if self.owner.experimental_gui or type(self.owner.runner) is not FakeGuiSubtaskRunner:
            raise ValueError("strategic_simulation_only")
        if self.owner.effects.unresolved():
            raise ValueError("reconciliation_required")
        if self.c.execute("SELECT COUNT(*) FROM strategic_sessions").fetchone()[0] >= 128:
            raise ValueError("strategic_session_limit")
        if "no-network" in goal.constraints and roles.planner.provider != "fake":
            raise ValueError("goal_forbids_network")
        if (
            roles.planner.provider != "fake"
            and roles.planner.destination_ref not in goal.permitted_destinations
        ):
            raise ValueError("destination_outside_goal")
        now = self.clock()
        metadata = {
            "goal": goal.model_dump(mode="json"),
            "roles": roles.model_dump(mode="json"),
            "limits": limits.model_dump(mode="json"),
            "budget": budget.model_dump(mode="json"),
            "created_at": now,
        }
        with self.c:
            self.c.execute(
                "INSERT INTO strategic_sessions(mission_id,metadata) VALUES (?,?)",
                (mid, json.dumps(metadata)),
            )
        # Caller has supplied the approved reference-only goal, not private content.
        if goal.private_context_required:
            self.private_context.add(mid)
        proposal = await self._call(
            mid,
            planner,
            "plan",
            lambda: planner.propose_plan(
                goal, capabilities=CATALOG, limits=limits, context={"goal_ref": goal.goal_ref}
            ),
            lambda result: validate_proposal(result, goal, limits),
        )
        candidate = compile_plan(
            proposal,
            goal,
            roles,
            limits,
            budget,
            mid=mid,
            revision_id="revision-" + uuid4().hex,
            now=now,
        )
        self._candidate(mid, candidate, expected_parent=None)
        return mid, self.consent_request(mid)

    def _candidate(self, mid, candidate, expected_parent):
        row = self.row(mid)
        with self.c:
            cur = self.c.execute(
                "UPDATE strategic_sessions SET pending=?,pending_digest=? "
                "WHERE mission_id=? AND active_digest IS ? AND epoch=? AND stopped=0",
                (
                    candidate.model_dump_json(),
                    candidate.digest(),
                    mid,
                    expected_parent,
                    row["epoch"],
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("stale_revision_parent")

    async def _call(self, mid, planner, operation, invoke, validate):
        row, metadata = self.row(mid), self.metadata(mid)
        limits = StrategicLimits.model_validate_json(json.dumps(metadata["limits"]))
        roles = ModelRoleConfig.model_validate_json(json.dumps(metadata["roles"]))
        if planner.binding != roles.planner:
            raise PlannerFailure("routing_changed")
        if mid in self.inflight:
            raise PlannerFailure("planner_busy")
        if row["stopped"] or row["calls"] >= limits.max_calls:
            raise PlannerFailure("planner_budget_or_stopped")
        if (
            self.clock() < metadata["created_at"]
            or self.clock() >= metadata["created_at"] + metadata["budget"]["max_wall_seconds"]
        ):
            raise PlannerFailure("wall_budget_exhausted")
        if row["active_digest"] and self.reconciliation_required(mid):
            raise PlannerFailure("reconciliation_required")
        if row["active_digest"]:
            reason = self.scheduler.guard(mid)
            if reason and (
                reason == "no_progress" or "budget" in reason or reason == "timing_uncertain"
            ):
                raise PlannerFailure("mission_budget_exhausted")
        # Reserve durably before awaiting the provider; failed/cancelled calls count.
        record = PlannerRunRecord(call_id="call-" + uuid4().hex, operation=operation)
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            if self.scheduler.exists(mid):
                usage = self.scheduler.usage(mid)
                if usage.models_charged >= self.scheduler.plan(mid).budget.max_total_model_calls:
                    raise PlannerFailure("global_budget_exhausted")
                self.scheduler._usage(
                    mid, usage.model_copy(update={"models_charged": usage.models_charged + 1})
                )
            self.c.execute("UPDATE strategic_sessions SET calls=calls+1 WHERE mission_id=?", (mid,))
            self.c.execute(
                "INSERT INTO strategic_calls VALUES (?,?,?)",
                (mid, record.call_id, record.model_dump_json()),
            )
        self.inflight.add(mid)
        started = time.monotonic()
        status, code = "invalid", "invalid_response"
        try:
            async with asyncio.timeout(limits.timeout_seconds):
                value = await invoke()
            current = self.row(mid)
            if (
                current["epoch"] != row["epoch"]
                or current["active_digest"] != row["active_digest"]
                or current["stopped"]
                or self.clock() >= metadata["created_at"] + metadata["budget"]["max_wall_seconds"]
            ):
                code = "stale_response"
                raise PlannerFailure("stale_response")
            value = validate(value)
            if len(value.model_dump_json().encode()) > limits.max_response_bytes:
                raise PlannerFailure("response_budget")
            status, code = "valid", "none"
            return value
        except asyncio.CancelledError:
            status, code = "cancelled", "stale_response"
            raise
        except Exception as exc:  # noqa: BLE001 -- persist categories, never model/provider text.
            if (
                isinstance(exc, (TimeoutError, ConnectionError))
                or str(exc) == "provider_unavailable"
            ):
                status, code = "unavailable", "provider_unavailable"
            raise PlannerFailure(code) from None
        finally:
            self.inflight.remove(mid)
            usage = getattr(planner, "usage", {})
            finished = record.model_copy(
                update={
                    "status": status,
                    "validation_code": code,
                    "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
                    "input_tokens": usage.get("prompt_eval_count"),
                    "output_tokens": usage.get("eval_count"),
                }
            )
            finished = PlannerRunRecord.model_validate_json(finished.model_dump_json())
            with self.c:
                self.c.execute(
                    "UPDATE strategic_calls SET payload=? WHERE call_id=?",
                    (finished.model_dump_json(), record.call_id),
                )

    def consent_request(self, mid):
        plan, row = self.pending(mid), self.row(mid)
        return StrategicConsent(
            mission_id=mid,
            revision_id=plan.revision_id,
            plan_digest=plan.digest(),
            epoch=row["epoch"],
            expires_at=plan.schedule.mission.deadline_utc,
        )

    def admit(self, mid, consent):
        consent = StrategicConsent.model_validate_json(consent.model_dump_json())
        expected = self.consent_request(mid)
        if consent != expected or consent.expires_at <= self.clock():
            raise ValueError("stale_plan_consent")
        if mid in self.inflight or mid in self.owner._busy:
            raise ValueError("strategic_busy")
        candidate, row = self.pending(mid), self.row(mid)
        if candidate.parent_digest != row["active_digest"]:
            raise ValueError("stale_revision_parent")
        if (
            candidate.proposal.unresolved_questions
            or candidate.proposal.uncertainty == "needs_user"
        ):
            raise ValueError("clarification_required")
        if row["active_digest"]:
            self._validate_revision(mid, candidate)
        now = self.clock()
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            self.c.execute(
                "INSERT INTO strategic_revisions VALUES (?,?,?,?,?)",
                (
                    candidate.digest(),
                    mid,
                    candidate.model_dump_json(),
                    consent.model_dump_json(),
                    now,
                ),
            )
            if row["active_digest"] is None:
                self.c.execute(
                    "INSERT INTO missions(mission_id,spec,created_at) VALUES (?,?,?)",
                    (mid, candidate.schedule.mission.model_dump_json(), now),
                )
                self.c.execute(
                    "INSERT INTO mission_schedules(mission_id,spec,digest,usage,last_utc) VALUES (?,?,?,?,?)",
                    (
                        mid,
                        candidate.schedule.model_dump_json(),
                        candidate.schedule.digest(),
                        BudgetUsage(models_charged=row["calls"]).model_dump_json(),
                        now,
                    ),
                )
            else:
                self.c.execute(
                    "UPDATE missions SET spec=?,generation=generation+1 WHERE mission_id=?",
                    (candidate.schedule.mission.model_dump_json(), mid),
                )
                self.c.execute(
                    "UPDATE mission_schedules SET spec=?,digest=?,consent=NULL WHERE mission_id=?",
                    (candidate.schedule.model_dump_json(), candidate.schedule.digest(), mid),
                )
            cur = self.c.execute(
                "UPDATE strategic_sessions SET active_digest=?,pending=NULL,pending_digest=NULL "
                "WHERE mission_id=? AND active_digest IS ? AND epoch=? AND stopped=0",
                (candidate.digest(), mid, row["active_digest"], consent.epoch),
            )
            if cur.rowcount != 1:
                raise ValueError("stale_revision_parent")
        return candidate

    def pre_dispatch_blocked(self, mid, sid, result):
        if not self.exists(mid):
            return
        result = TaskResult.model_validate_json(result.model_dump_json())
        if not (
            result.proven_not_started
            and result.cleanup_confirmed
            and result.confirmed_input_transactions == 0
        ):
            raise ValueError("not_a_safe_boundary")
        effect = self.store.effect(mid, sid)
        if effect is None or effect.status not in ("prepared", "approved"):
            raise ValueError("not_a_safe_boundary")
        ref = "preflight-" + canonical_digest(result.model_dump(mode="json"))[:48]
        with self.c:
            self.c.execute(
                "INSERT OR IGNORE INTO strategic_blockers VALUES (?,?,?,?,0)",
                (mid, sid, ref, result.model_dump_json()),
            )

    def _validate_revision(self, mid, candidate):
        parent = self.active(mid)
        if (
            candidate.parent_digest != parent.digest()
            or candidate.parent_revision_id != parent.revision_id
            or candidate.goal != parent.goal
            or candidate.roles != parent.roles
            or candidate.limits != parent.limits
            or candidate.schedule.budget != parent.schedule.budget
            or candidate.schedule.mission.deadline_utc != parent.schedule.mission.deadline_utc
        ):
            raise ValueError("revision_scope_changed")
        if self.reconciliation_required(mid) or mid in self.owner._active:
            raise ValueError("reconciliation_required")
        if (
            self.store.control(mid)["cancelled"]
            or self.store.control(mid)["terminal_at"] is not None
        ):
            raise ValueError("terminal_mission")
        before = {o.objective_id: o for o in parent.proposal.objectives}
        after = {o.objective_id: o for o in candidate.proposal.objectives}
        # Stable identities defeat rename-to-retry. Scope enlargement is not supported.
        if before.keys() != after.keys():
            raise ValueError("objective_identity_changed")
        for effect in self.store.effects(mid):
            if (
                effect.status not in ("prepared", "approved")
                and before[effect.subtask_id] != after[effect.subtask_id]
            ):
                raise ValueError("historical_objective_changed")
        if parent.proposal.gaps != candidate.proposal.gaps:
            raise ValueError("criterion_gap_changed")
        changed = [sid for sid in before if before[sid] != after[sid]]
        if not changed:
            raise ValueError("circular_revision")
        # Only one pre-dispatch strategy substitution per revision, same acceptance contract.
        if len(changed) != 1:
            raise ValueError("revision_delta_excessive")
        sid = changed[0]
        proof = self.c.execute(
            "SELECT * FROM strategic_blockers WHERE mission_id=? AND subtask_id=?", (mid, sid)
        ).fetchone()
        if proof is None:
            raise ValueError("pre_dispatch_proof_required")
        if candidate.blocker_ref != sid or candidate.revision_evidence_refs != (
            proof["evidence_ref"],
        ):
            raise ValueError("revision_evidence_mismatch")
        old, new = before[sid], after[sid]
        if old.model_copy(update={"objective_kind": new.objective_kind}) != new:
            raise ValueError("revision_scope_changed")
        if CATALOG[old.objective_kind][0] != CATALOG[new.objective_kind][0]:
            raise ValueError("criterion_changed")
        old_kinds = [
            AdmittedPlan.model_validate_json(r[0]).proposal
            for r in self.c.execute(
                "SELECT payload FROM strategic_revisions WHERE mission_id=?", (mid,)
            )
        ]
        if any(
            next(o.objective_kind for o in p.objectives if o.objective_id == sid)
            == new.objective_kind
            for p in old_kinds
        ):
            raise ValueError("circular_revision")

    async def propose_revision(self, mid, planner, blocker):
        parent, row = self.active(mid), self.row(mid)
        if row["pending"] or row["replans"] >= parent.limits.max_replans:
            raise ValueError("replan_budget_or_review_required")
        if mid in self.inflight or mid in self.owner._busy or self.reconciliation_required(mid):
            raise ValueError("reconciliation_required")
        proof = self.c.execute(
            "SELECT * FROM strategic_blockers WHERE mission_id=? AND subtask_id=?", (mid, blocker)
        ).fetchone()
        if proof is None or proof["attempted"]:
            raise ValueError("blocker_attempt_exhausted")
        with self.c:
            self.c.execute(
                "UPDATE strategic_blockers SET attempted=1 WHERE mission_id=? AND subtask_id=?",
                (mid, blocker),
            )
            self.c.execute(
                "UPDATE strategic_sessions SET replans=replans+1 WHERE mission_id=?", (mid,)
            )

        def validate(result):
            from .contracts import PlanRevision

            result = PlanRevision.model_validate_json(result.model_dump_json())
            if (
                result.parent_digest != parent.digest()
                or result.parent_revision_id != parent.revision_id
                or result.blocker_ref != blocker
                or result.evidence_refs != (proof["evidence_ref"],)
            ):
                raise ValueError("revision_binding_mismatch")
            candidate = compile_plan(
                result.proposal,
                parent.goal,
                parent.roles,
                parent.limits,
                parent.schedule.budget,
                mid=mid,
                revision_id="revision-" + uuid4().hex,
                now=self.clock(),
                parent=parent,
                revision_evidence_refs=result.evidence_refs,
                blocker_ref=result.blocker_ref,
            )
            self._validate_revision(mid, candidate)
            return candidate

        candidate = await self._call(
            mid,
            planner,
            "revision",
            lambda: planner.propose_revision(
                parent, evidence=(proof["evidence_ref"],), blocker=blocker, limits=parent.limits
            ),
            validate,
        )
        self._candidate(mid, candidate, parent.digest())
        return self.consent_request(mid)

    def evaluate(self, mid, *, persist=True):
        plan = self.active(mid)
        effects = {e.subtask_id: e for e in self.store.effects(mid)}
        gaps = {g.criterion_id: g.reason for g in plan.proposal.gaps}
        results = []
        unresolved = bool(self.reconciliation_required(mid))
        for criterion in plan.goal.criteria:
            refs, provenance, statuses = [], [], []
            objectives = [
                o for o in plan.proposal.objectives if criterion.criterion_id in o.criterion_ids
            ]
            for objective in objectives:
                effect = effects.get(objective.objective_id)
                result = effect.result if effect else None
                if (
                    result is None
                    or not result.cleanup_confirmed
                    or not result.evidence_ref
                    or result.evidence_kind != criterion.evidence_kind
                ):
                    statuses.append("uncertain")
                    continue
                # Native evidence is never accepted by this simulation-only compiler.
                if result.evidence_kind != "fake" and not (
                    result.evidence_kind == "human_attested"
                    and result.human_confirmed
                    and result.observation_ref
                ):
                    statuses.append("uncertain")
                    continue
                refs.append(result.evidence_ref)
                provenance.append(result.evidence_kind)
                statuses.append(
                    {"observed_success": "satisfied", "observed_failure": "contradicted"}.get(
                        effect.status, "uncertain"
                    )
                )
            status = gaps.get(criterion.criterion_id, "uncertain")
            if statuses:
                status = (
                    "contradicted"
                    if "contradicted" in statuses
                    else "satisfied"
                    if all(s == "satisfied" for s in statuses)
                    else "uncertain"
                )
            if criterion.evidence_kind in ("visual", "none") and status != "needs_user":
                status = "unsupported"
            results.append(
                CriterionEvaluation(
                    criterion_id=criterion.criterion_id,
                    status=status,
                    evidence_refs=tuple(refs),
                    provenance=tuple(provenance),
                )
            )
        required = [
            r
            for r, c in zip(results, plan.goal.criteria, strict=True)
            if c.importance == "required"
        ]
        all_tasks = len(effects) == len(plan.proposal.objectives) and all(
            e.status == "observed_success" and e.result and e.result.cleanup_confirmed
            for e in effects.values()
        )
        ready = all(r.status == "satisfied" for r in required) and all_tasks and not unresolved
        gaps_left = any(r.status != "satisfied" for r in results)
        completed = ready and (not gaps_left or plan.goal.allow_optional_gaps)
        value = StrategicEvaluation(
            plan_digest=plan.digest(),
            criteria=tuple(results),
            status=("completed_with_optional_gaps" if gaps_left else "completed")
            if completed
            else "partial",
            blockers=("reconciliation-required",) if unresolved else (),
            next_step="complete" if completed else "reconcile" if unresolved else "review",
        )
        if persist:
            with self.c:
                self.c.execute(
                    "INSERT INTO strategic_evaluations(mission_id,payload) VALUES (?,?)",
                    (mid, value.model_dump_json()),
                )
                self.c.execute(
                    "DELETE FROM strategic_evaluations WHERE mission_id=? AND id NOT IN "
                    "(SELECT id FROM strategic_evaluations WHERE mission_id=? ORDER BY id DESC LIMIT 16)",
                    (mid, mid),
                )
        return value

    async def assess_advisory(self, mid, planner):
        """Semantic opinion is checked against authoritative evidence, never applied to it."""
        plan = self.active(mid)
        truth = self.evaluate(mid, persist=False)
        facts = {c.criterion_id: c for c in truth.criteria}

        def validate(value):
            value = StrategicEvaluation.model_validate_json(value.model_dump_json())
            if value.plan_digest != plan.digest() or len(
                {c.criterion_id for c in value.criteria}
            ) != len(value.criteria):
                raise ValueError("advisory_binding_mismatch")
            for criterion in value.criteria:
                fact = facts.get(criterion.criterion_id)
                if fact is None or not set(criterion.evidence_refs) <= set(fact.evidence_refs):
                    raise ValueError("hallucinated_evidence")
                if criterion.status == "satisfied" and fact.status != "satisfied":
                    raise ValueError("model_assertion_is_not_evidence")
            if (
                value.status in ("completed", "completed_with_optional_gaps")
                and value.status != truth.status
            ):
                raise ValueError("model_assertion_is_not_evidence")
            return value

        return await self._call(
            mid,
            planner,
            "assessment",
            lambda: planner.assess_goal(plan.goal, evidence=self.context(mid), admitted_plan=plan),
            validate,
        )

    def context(self, mid):
        plan = self.active(mid)
        evaluation = self.evaluate(mid, persist=False)
        value = {
            "mission_id": mid,
            "goal_ref": plan.goal.goal_ref,
            "plan_digest": plan.digest(),
            "revision_id": plan.revision_id,
            "criteria": evaluation.model_dump(mode="json")["criteria"],
            "usage": self.scheduler.usage(mid).model_dump(mode="json"),
            "planner_calls": self.row(mid)["calls"],
            "replans": self.row(mid)["replans"],
            "private_context_missing": plan.goal.private_context_required
            and mid not in self.private_context,
            "prompt_version": plan.roles.prompt_version,
        }
        if len(json.dumps(value).encode()) > plan.limits.max_context_bytes:
            raise ValueError("context_budget")
        return value

    def stop(self, mid):
        with self.c:
            self.c.execute(
                "UPDATE strategic_sessions SET epoch=epoch+1,stopped=1,pending=NULL,pending_digest=NULL "
                "WHERE mission_id=?",
                (mid,),
            )

    def pause(self, mid):
        # Invalidates late planning and plan-review callbacks without cancelling admitted history.
        with self.c:
            self.c.execute(
                "UPDATE strategic_sessions SET epoch=epoch+1,pending=NULL,pending_digest=NULL "
                "WHERE mission_id=?",
                (mid,),
            )

    def recover(self):
        with self.c:
            self.c.execute(
                "UPDATE strategic_sessions SET epoch=epoch+1,pending=NULL,pending_digest=NULL"
            )
            self.c.execute(
                "UPDATE strategic_calls SET payload=json_set(payload,'$.status','cancelled',"
                "'$.validation_code','stale_response') WHERE json_extract(payload,'$.status')='reserved'"
            )

    def report(self, mid):
        plan = self.active(mid)
        calls = [
            PlannerRunRecord.model_validate_json(r[0]).model_dump(mode="json")
            for r in self.c.execute(
                "SELECT payload FROM strategic_calls WHERE mission_id=? ORDER BY rowid", (mid,)
            )
        ]
        evaluation = self.evaluate(mid)
        proposals = [r for r in calls if r["operation"] in ("plan", "revision")]
        valid = sum(r["status"] == "valid" for r in proposals)
        revisions = self.c.execute(
            "SELECT COUNT(*) FROM strategic_revisions WHERE mission_id=?", (mid,)
        ).fetchone()[0]
        duplicate_dispatches = self.c.execute(
            "SELECT COALESCE(SUM(n-1),0) FROM (SELECT COUNT(*) n FROM events "
            "WHERE mission_id=? AND code='dispatch_started' GROUP BY effect_id HAVING COUNT(*)>1)",
            (mid,),
        ).fetchone()[0]
        return {
            "evaluation": evaluation.model_dump(mode="json"),
            "context": self.context(mid),
            "planner_calls": calls,
            "native_qualification": "PENDING",
            "native_input_transactions": 0,
            "provenance": "simulated",
            "policy_bypass_count": 0,
            "unintended_native_dispatch_count": 0,
            "criterion_coverage": 1.0,  # admission requires explicit coverage OR declared gap
            "plan_schema_valid_rate": valid / len(proposals) if proposals else None,
            "plan_admission_rate": revisions / len(proposals) if proposals else None,
            "duplicate_effect_dispatch_count": duplicate_dispatches,
            "replan_success_rate": (
                (revisions - 1) / self.row(mid)["replans"]
                if evaluation.status == "completed"
                else 0.0
            )
            if self.row(mid)["replans"]
            else None,
            "time_to_first_valid_plan_ms": next(
                (
                    r["latency_ms"]
                    for r in proposals
                    if r["operation"] == "plan" and r["status"] == "valid"
                ),
                None,
            ),
            "fixture_ref": "design-proposal-six-v1",
            "catalog_digest": plan.schedule.catalog_digest,
            "prompt_version": plan.roles.prompt_version,
            "unsupported_objective_detection_rate": None,
            "semantic_evaluation_accuracy": None,
            "live_model_benchmarks": "PENDING",
            "plan_digest": plan.digest(),
            "roles": plan.roles.model_dump(mode="json"),
        }


def interpret_design_goal(text):
    """Conservative local intake of the one supported fixture family; raw text stays in RAM."""
    import re

    from .catalog import fixture_goal

    check_private_intake(text)
    # Unknown outcomes/constraints require clarification rather than dropping them.
    accepted = re.fullmatch(
        r"\s*(?:please\s+)?(?:prepare|draft|create)\s+(?:a\s+)?"
        r"(?:(?:short|technical|small)\s+)*design\s+proposal"
        r"(?:\s+covering\s+(?:the\s+)?problem,\s*architecture,\s*tradeoffs,\s*"
        r"test\s+plan,?\s*and\s*next\s+steps)?[.!]?\s*",
        text,
        re.IGNORECASE,
    )
    if not accepted:
        raise PlannerFailure("clarify_goal")
    return fixture_goal("goal-" + uuid4().hex)
