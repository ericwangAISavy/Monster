"""Bounded scheduling envelope around the existing Mission/graph, not another engine."""

import hashlib
import json
import math
import time
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Mission, Ref
from .policy import POLICY_VERSION


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class MissionBudget(Contract):
    max_wall_seconds: int = Field(default=3600, ge=1, le=7200)
    max_active_seconds: int = Field(default=1200, ge=1, le=3600)
    max_subtask_starts: int = Field(default=12, ge=1, le=12)
    max_total_input_transactions: int = Field(default=40, ge=1, le=120)
    max_total_model_calls: int = Field(default=80, ge=1, le=240)
    max_total_reviews: int = Field(default=256, ge=1, le=1024)
    max_progress_checks: int = Field(default=512, ge=1, le=2048)
    max_no_progress: Literal[2] = 2
    max_pure_attempts: int = Field(default=2, ge=1, le=3)


class ScheduledPlan(Contract):
    mission: Mission
    template_ref: Literal["editor-six-v1"]
    mode: Literal["simulation", "observe_only", "supervised_pilot"] = "simulation"
    budget: MissionBudget = Field(default_factory=MissionBudget)
    created_at: float = Field(gt=0, allow_inf_nan=False)
    provider_ref: Ref
    model_ref: Ref
    configuration_digest: Ref
    catalog_digest: Ref

    @model_validator(mode="after")
    def admitted_catalog(self):
        from .plan_templates import PILOT_TEXT

        if self.catalog_digest != canonical_digest(PILOT_TEXT):
            raise ValueError("Reviewed catalog content changed")
        tasks = self.mission.subtasks
        if (
            len(tasks) != 6
            or {s.objective_ref for s in tasks} != set(PILOT_TEXT)
            or any(
                s.category != "local_reversible"
                or s.application_ref != "disposable-editor"
                or not s.requires_approval
                or s.success_refs != [f"visible-{s.objective_ref}"]
                for s in tasks
            )
        ):
            raise ValueError("Unsupported mission catalog/scope")
        if self.mission.deadline_utc > self.created_at + self.budget.max_wall_seconds:
            raise ValueError("Mission exceeds wall envelope")
        return self

    def digest(self):
        return canonical_digest(self.model_dump(mode="json"))


class MissionConsent(Contract):
    mission_id: Ref
    plan_digest: Ref
    generation: int = Field(ge=1)
    workspace_id: Ref
    mode: Literal["simulation", "observe_only", "supervised_pilot"]
    policy_version: Ref = POLICY_VERSION
    provider_ref: Ref
    model_ref: Ref
    configuration_digest: Ref
    egress_ref: Literal["no_egress", "saved_provider_screenshot_consent"]
    human_present: Literal[True] = True
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    approved: bool = False


class NativePilotQualification(Contract):
    phase2: Literal["PASS"]
    phase3: Literal["PASS"]
    operator_review_ref: Ref
    phase2_evidence_ref: Ref
    phase3_evidence_ref: Ref
    workspace_id: Ref
    configuration_digest: Ref
    source_ref: Ref
    expires_at: float = Field(gt=0, allow_inf_nan=False)


class BudgetUsage(Contract):
    input_charged: int = Field(default=0, ge=0)
    input_confirmed: int = Field(default=0, ge=0)
    models_confirmed: int = Field(default=0, ge=0)
    reviews_confirmed: int = Field(default=0, ge=0)
    active_measured_ms: int = Field(default=0, ge=0)
    models_charged: int = Field(default=0, ge=0)
    reviews_charged: int = Field(default=0, ge=0)
    active_ms: int = Field(default=0, ge=0)
    starts: int = Field(default=0, ge=0)
    progress_checks: int = Field(default=0, ge=0)
    envelope_violation: bool = False
    no_progress: int = Field(default=0, ge=0)
    interventions: int = Field(default=0, ge=0)


class ProgressSnapshot(Contract):
    mission_id: Ref
    generation: int
    provenance: Literal["simulated", "observe_only", "native_experimental"]
    verified_subtask_ids: list[Ref]
    remaining_subtask_ids: list[Ref]
    active_subtask_id: Ref | None = None
    reason_code: Ref | None = None
    usage: BudgetUsage


def migrate_scheduling(c):
    c.execute(
        "CREATE TABLE IF NOT EXISTS mission_schedules (mission_id TEXT PRIMARY KEY, "
        "spec TEXT NOT NULL, digest TEXT NOT NULL, consent TEXT, usage TEXT NOT NULL, "
        "pause_requested INTEGER NOT NULL DEFAULT 0, last_utc REAL NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS mission_progress (id INTEGER PRIMARY KEY, mission_id TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS mission_reservations (attempt_id TEXT PRIMARY KEY, "
        "mission_id TEXT NOT NULL, subtask_id TEXT NOT NULL, actions INTEGER NOT NULL, "
        "models INTEGER NOT NULL, reviews INTEGER NOT NULL, active_ms INTEGER NOT NULL, "
        "settled INTEGER NOT NULL DEFAULT 0)"
    )


class MissionScheduler:
    def __init__(self, store, *, clock=time.time, monotonic=time.monotonic):
        self.store, self.c, self.clock, self.monotonic = store, store.connection, clock, monotonic
        self.running = {}
        self.observer = None
        self.mode_guard = None

    def exists(self, mid):
        return (
            self.c.execute("SELECT 1 FROM mission_schedules WHERE mission_id=?", (mid,)).fetchone()
            is not None
        )

    def row(self, mid):
        row = self.c.execute(
            "SELECT * FROM mission_schedules WHERE mission_id=?", (mid,)
        ).fetchone()
        if row is None:
            raise ValueError("No scheduling envelope")
        return row

    def plan(self, mid):
        row = self.row(mid)
        plan = ScheduledPlan.model_validate_json(row["spec"])
        if (
            plan.digest() != row["digest"]
            or self.store.mission(mid).model_dump() != plan.mission.model_dump()
        ):
            raise ValueError("Admitted plan digest changed")
        return plan

    def usage(self, mid):
        return BudgetUsage.model_validate_json(self.row(mid)["usage"])

    def _usage(self, mid, usage):
        usage = BudgetUsage.model_validate(usage.model_dump())
        self.c.execute(
            "UPDATE mission_schedules SET usage=? WHERE mission_id=?",
            (usage.model_dump_json(), mid),
        )

    def event(self, mid, code):
        self.c.execute(
            "INSERT INTO effect_audit (effect_id,mission_id,code,at,source) "
            "VALUES (?,?,?,?,'schedule')",
            (mid, mid, code, self.clock()),
        )

    def register(self, plan):
        plan = ScheduledPlan.model_validate(plan.model_dump())
        with self.c:
            self.c.execute(
                "INSERT INTO mission_schedules (mission_id,spec,digest,usage,last_utc) VALUES (?,?,?,?,?)",
                (
                    plan.mission.mission_id,
                    plan.model_dump_json(),
                    plan.digest(),
                    BudgetUsage().model_dump_json(),
                    self.clock(),
                ),
            )
            self.event(plan.mission.mission_id, "plan_admitted")

    def consent_request(self, mid):
        plan = self.plan(mid)
        return MissionConsent(
            mission_id=mid,
            plan_digest=plan.digest(),
            generation=self.store.control(mid)["generation"],
            workspace_id=plan.mission.workspace_id,
            mode=plan.mode,
            provider_ref=plan.provider_ref,
            model_ref=plan.model_ref,
            configuration_digest=plan.configuration_digest,
            egress_ref="saved_provider_screenshot_consent"
            if plan.mode == "supervised_pilot"
            else "no_egress",
            expires_at=plan.mission.deadline_utc,
        )

    def grant(self, mid, consent):
        consent = MissionConsent.model_validate(consent.model_dump())
        expected = self.consent_request(mid)
        if (
            consent.model_dump(exclude={"approved"}) != expected.model_dump(exclude={"approved"})
            or consent.expires_at <= self.clock()
            or self.store.control(mid)["cancelled"]
        ):
            raise ValueError("Mission consent scope/expiry mismatch")
        u = self.usage(mid)
        if u.reviews_charged >= self.plan(mid).budget.max_total_reviews:
            raise ValueError("Mission review budget exhausted")
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            self.c.execute(
                "UPDATE mission_schedules SET consent=?,pause_requested=0 WHERE mission_id=?",
                (consent.model_copy(update={"approved": True}).model_dump_json(), mid),
            )
            self.c.execute("UPDATE missions SET stop_requested=0 WHERE mission_id=?", (mid,))
            self.c.execute(
                "UPDATE effects SET status='prepared',generation=? WHERE mission_id=? "
                "AND status IN ('prepared','approved')",
                (expected.generation, mid),
            )
            self._usage(
                mid,
                u.model_copy(
                    update={
                        "reviews_charged": u.reviews_charged + 1,
                        "interventions": u.interventions + 1,
                    }
                ),
            )
            self.event(mid, "mission_consent_approved")

    def guard(self, mid, *, tick=False):
        plan = self.plan(mid)
        row = self.row(mid)
        u = self.usage(mid)
        now = self.clock()
        if self.mode_guard is not None:
            try:
                self.mode_guard(plan)
            except ValueError:
                return "native_qualification_required"
        if now < row["last_utc"]:
            return "timing_uncertain"
        if u.envelope_violation:
            return "envelope_budget_exhausted"
        if any(e.status in ("uncertain",) for e in self.store.effects(mid)):
            return "reconciliation_required"
        with self.c:
            self.c.execute("UPDATE mission_schedules SET last_utc=? WHERE mission_id=?", (now, mid))
            if tick:
                u = u.model_copy(update={"progress_checks": u.progress_checks + 1})
                self._usage(mid, u)
        if now >= plan.mission.deadline_utc:
            return "wall_budget_exhausted"
        if u.progress_checks > plan.budget.max_progress_checks:
            return "progress_budget_exhausted"
        if u.no_progress >= 2:
            return "no_progress"
        if row["pause_requested"] and not any(k[0] == mid for k in self.running):
            return "safe_boundary_pause"
        if row["consent"] is None:
            return "mission_consent_required"
        consent = MissionConsent.model_validate_json(row["consent"])
        expected = self.consent_request(mid)
        if (
            not consent.approved
            or consent.model_dump(exclude={"approved"}) != expected.model_dump(exclude={"approved"})
            or consent.expires_at <= now
        ):
            return "mission_consent_required"
        if plan.mode == "observe_only":
            return "observe_only_handoff"
        return None

    def reserve(self, mid, task, effect):
        reason = self.guard(mid)
        if reason:
            raise ValueError(reason)
        if self.row(mid)["pause_requested"]:
            raise ValueError("safe_boundary_pause")
        p, u = self.plan(mid), self.usage(mid)
        key = f"{effect.effect_id}:{effect.generation}"
        caps = (task.max_actions, task.max_model_calls, 64, (task.deadline_seconds + 10) * 1000)
        if (
            u.starts + 1 > p.budget.max_subtask_starts
            or u.input_charged + caps[0] > p.budget.max_total_input_transactions
            or u.models_charged + caps[1] > p.budget.max_total_model_calls
            or u.reviews_charged + caps[2] > p.budget.max_total_reviews
            or u.active_ms + caps[3] > p.budget.max_active_seconds * 1000
        ):
            raise ValueError("global_budget_exhausted")
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            self.c.execute(
                "INSERT INTO mission_reservations VALUES (?,?,?,?,?,?,?,0)",
                (key, mid, task.subtask_id, *caps),
            )
            self._usage(
                mid,
                u.model_copy(
                    update={
                        "input_charged": u.input_charged + caps[0],
                        "models_charged": u.models_charged + caps[1],
                        "reviews_charged": u.reviews_charged + caps[2],
                        "active_ms": u.active_ms + caps[3],
                        "starts": u.starts + 1,
                    }
                ),
            )
            self.event(mid, "subtask_envelope_reserved")
        self.running[(mid, key)] = self.monotonic()
        self.notify(mid, task.subtask_id)
        return key

    def settle(self, mid, key, result):
        row = self.c.execute(
            "SELECT * FROM mission_reservations WHERE attempt_id=?", (key,)
        ).fetchone()
        if row is None or row["settled"]:
            return
        start = self.running.pop((mid, key), None)
        safe = (
            result.cleanup_confirmed
            and result.confirmed_input_transactions is not None
            and result.model_planning_calls is not None
            and result.review_count is not None
            and (
                result.status in ("verified_success", "verified_failure")
                or (result.status == "blocked" and result.proven_not_started)
            )
        )
        u = self.usage(mid)
        updates = {
            "input_confirmed": u.input_confirmed + (result.confirmed_input_transactions or 0),
            "models_confirmed": u.models_confirmed + (result.model_planning_calls or 0),
            "reviews_confirmed": u.reviews_confirmed + (result.review_count or 0),
            "no_progress": 0
            if result.status == "verified_success"
            else (u.no_progress if result.reason == "cancelled" else min(2, u.no_progress + 1)),
        }
        elapsed = (
            max(0, math.ceil((self.monotonic() - start) * 1000))
            if start is not None
            else row["active_ms"]
        )
        if start is not None:
            updates["active_measured_ms"] = u.active_measured_ms + elapsed
        measured = (
            result.confirmed_input_transactions,
            result.model_planning_calls,
            result.review_count,
            elapsed,
        )
        caps = (row["actions"], row["models"], row["reviews"], row["active_ms"])
        overrun = any(
            value is not None and value > cap for value, cap in zip(measured, caps, strict=True)
        )
        if start is not None and self.monotonic() < start:
            overrun = True
        if overrun:
            updates["envelope_violation"] = True
            safe = False
        for name, cap, value in zip(
            ("input_charged", "models_charged", "reviews_charged", "active_ms"),
            caps,
            measured,
            strict=True,
        ):
            charged = value if safe and start is not None else max(cap, value or 0)
            updates[name] = getattr(u, name) - cap + charged
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            self.c.execute("UPDATE mission_reservations SET settled=1 WHERE attempt_id=?", (key,))
            self._usage(mid, u.model_copy(update=updates))
            self.event(mid, "subtask_accounted")

    def note_failure(self, mid):
        u = self.usage(mid)
        with self.c:
            self._usage(mid, u.model_copy(update={"no_progress": min(2, u.no_progress + 1)}))
            self.event(mid, "pure_pre_dispatch_failed")

    def request_pause(self, mid):
        with self.c:
            self.c.execute(
                "UPDATE mission_schedules SET pause_requested=CASE WHEN pause_requested=0 THEN 1 ELSE pause_requested END WHERE mission_id=?",
                (mid,),
            )
            self.event(mid, "safe_pause_requested")

    def finalize_pause(self, mid):
        if self.row(mid)["pause_requested"] == 2:
            return
        with self.c:
            self.c.execute(
                "UPDATE mission_schedules SET consent=NULL,pause_requested=2 WHERE mission_id=?",
                (mid,),
            )
            self.c.execute("UPDATE missions SET generation=generation+1 WHERE mission_id=?", (mid,))
            self.event(mid, "mission_paused")

    def recover(self):
        with self.c:
            rows = self.c.execute("SELECT mission_id FROM mission_schedules").fetchall()
            for row in rows:
                mid = row[0]
                self.plan(mid)
                self.c.execute(
                    "UPDATE mission_schedules SET consent=NULL WHERE mission_id=?", (mid,)
                )
                self.c.execute(
                    "UPDATE missions SET generation=generation+1 WHERE mission_id=?", (mid,)
                )
                self.c.execute(
                    "UPDATE mission_reservations SET settled=1 WHERE mission_id=? AND settled=0",
                    (mid,),
                )
                self.event(mid, "restart_requires_consent")

    def progress(self, mid, active=None, reason=None):
        plan = self.plan(mid)
        verified = [
            e.subtask_id
            for e in self.store.effects(mid)
            if e.status == "observed_success" and e.result and e.result.evidence_ref
        ]
        return ProgressSnapshot(
            mission_id=mid,
            generation=self.store.control(mid)["generation"],
            provenance={
                "simulation": "simulated",
                "observe_only": "observe_only",
                "supervised_pilot": "native_experimental",
            }[plan.mode],
            verified_subtask_ids=verified,
            remaining_subtask_ids=[
                s.subtask_id for s in plan.mission.subtasks if s.subtask_id not in verified
            ],
            active_subtask_id=active,
            reason_code=reason,
            usage=self.usage(mid),
        )

    def notify(self, mid, active=None, reason=None):
        progress = self.progress(mid, active, reason)
        with self.c:
            self.c.execute(
                "INSERT INTO mission_progress (mission_id,payload) VALUES (?,?)",
                (mid, progress.model_dump_json()),
            )
            self.c.execute(
                "DELETE FROM mission_progress WHERE mission_id=? AND id NOT IN "
                "(SELECT id FROM mission_progress WHERE mission_id=? ORDER BY id DESC LIMIT 64)",
                (mid, mid),
            )
            self.event(mid, "progress_checkpointed")
        if self.observer:
            self.observer(progress)


def provider_binding(config):
    from dataclasses import asdict

    return canonical_digest(
        {k: v for k, v in asdict(config).items() if k not in ("mode", "approve_each_step")}
    )
