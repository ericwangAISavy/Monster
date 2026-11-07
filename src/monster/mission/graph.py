"""Outer mission loop. No Qt, desktop, model or OS-input imports are permitted here."""

import asyncio
import time
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from .contracts import (
    Approval,
    ApprovalDecision,
    Effect,
    GuiSubtaskRequest,
    TaskResult,
    migrate_state,
    valid_transition,
)

TERMINAL = {"completed", "failed", "partially_complete", "cancelled"}


class MissionState(TypedDict):
    schema_version: int
    mission_id: str
    status: str
    current_subtask_id: str | None
    pending_approval_id: str | None
    transitions: int
    reason: str | None


def stable_id(*parts):
    return uuid5(NAMESPACE_URL, "/".join(parts)).hex


class MissionNodes:
    def __init__(self, store, runner, clock):
        self.store, self.runner, self.clock = store, runner, clock
        self.running: dict[str, asyncio.Task] = {}

    def update(self, state, **changes):
        if (
            "status" in changes
            and changes["status"] != state["status"]
            and not valid_transition(state["status"], changes["status"])
        ):
            raise ValueError("Invalid mission transition")
        return {**changes, "transitions": state["transitions"] + 1}

    def guard(self, state):
        control = self.store.control(state["mission_id"])
        if control["cancelled"]:
            return self.update(state, status="cancelled", reason="cancelled")
        if control["stop_requested"]:
            return self.update(state, status="paused", reason="stopped")
        mission = self.store.mission(state["mission_id"])
        if self.clock() >= mission.deadline_utc:
            return self.update(state, status="partially_complete", reason="deadline")
        if state["transitions"] >= mission.max_transitions:
            return self.update(state, status="partially_complete", reason="transition_budget")
        return None

    async def authorize(self, state):
        migrate_state(state)
        if not self.store.mission(state["mission_id"]).authorized:
            return self.update(state, status="failed", reason="unauthorized")
        return self.update(state, status="running")

    async def plan(self, state):
        # Phase 1 receives a validated static DAG. No model or speculative replan.
        return self.guard(state) or self.update(state)

    async def choose(self, state):
        guarded = self.guard(state)
        if guarded:
            return guarded
        mission = self.store.mission(state["mission_id"])
        effects = {e.subtask_id: e for e in self.store.effects(mission.mission_id)}
        verified = {key for key, e in effects.items() if e.status == "observed_success"}
        # Any unresolved or failed effect blocks advancing the mission.
        for task in mission.subtasks:
            effect = effects.get(task.subtask_id)
            if effect and effect.status in ("dispatch_started", "uncertain"):
                if effect.status == "dispatch_started":
                    self.store.transition(effect, "uncertain")
                return self.update(
                    state,
                    status="paused",
                    current_subtask_id=task.subtask_id,
                    reason="reconciliation_required",
                )
            if effect and effect.status == "observed_failure":
                return self.update(state, status="partially_complete", reason="verified_failure")
            if task.subtask_id not in verified and set(task.depends_on) <= verified:
                return self.update(state, current_subtask_id=task.subtask_id)
        return self.update(state, current_subtask_id=None)

    async def preflight(self, state):
        guarded = self.guard(state)
        if guarded:
            return guarded
        mid, sid = state["mission_id"], state["current_subtask_id"]
        mission = self.store.mission(mid)
        task = next(s for s in mission.subtasks if s.subtask_id == sid)
        effect = self.store.effect(mid, sid)
        if effect and effect.status in (
            "observed_success",
            "observed_failure",
            "uncertain",
            "dispatch_started",
        ):
            return self.update(state)
        if self.store.metrics(mid)["dispatches"] >= mission.max_delegations:
            return self.update(state, status="partially_complete", reason="delegation_budget")
        generation = self.store.control(mid)["generation"]
        if effect is None:
            effect = Effect(
                effect_id=stable_id(mid, sid),
                mission_id=mid,
                subtask_id=sid,
                generation=generation,
                status="prepared",
                target_ref=task.application_ref,
                category=task.category,
            )
            self.store.prepare(effect)
        if effect.generation != generation:
            return self.update(state, status="partially_complete", reason="stale_generation")
        if effect.status == "approved":
            return self.update(state)
        # Risk labels cannot reduce review for consequential or unknown effects.
        review = task.requires_approval or task.category not in ("read_only", "local_reversible")
        if not review:
            self.store.transition(effect, "approved")
            return self.update(state)
        aid = stable_id(effect.effect_id, str(generation), "approval")
        try:
            approval = self.store.get_approval(aid)
        except KeyError:
            approval = Approval(
                approval_id=aid,
                mission_id=mid,
                subtask_id=sid,
                effect_id=effect.effect_id,
                generation=generation,
                expires_at=min(self.clock() + mission.approval_seconds, mission.deadline_utc),
            )
            self.store.approval(approval)
        if approval.decision != "pending" or approval.expires_at <= self.clock():
            return self.update(state, status="partially_complete", reason="approval_expired")
        return self.update(state, status="waiting_approval", pending_approval_id=aid)

    async def approval(self, state):
        # Interrupt nodes restart from their beginning. All writes happen outside
        # the interrupt-before-return boundary or are compare-and-set/idempotent.
        approval = self.store.get_approval(state["pending_approval_id"])
        answer = interrupt(approval.model_dump(mode="json"))
        guarded = self.guard(state)
        if guarded:
            return guarded
        try:
            decision = ApprovalDecision.model_validate(answer)
        except ValidationError:
            return self.update(state, status="partially_complete", reason="invalid_approval")
        effect = self.store.effect(approval.mission_id, approval.subtask_id)
        if (decision.approval_id, decision.effect_id, decision.generation) != (
            approval.approval_id,
            approval.effect_id,
            approval.generation,
        ) or approval.generation != self.store.control(approval.mission_id)["generation"]:
            return self.update(state, status="partially_complete", reason="stale_approval")
        if approval.expires_at <= self.clock():
            self.store.approval(approval.model_copy(update={"decision": "expired"}))
            return self.update(state, status="partially_complete", reason="approval_expired")
        if decision.decision != "approve":
            self.store.approval(approval.model_copy(update={"decision": "denied"}))
            return self.update(state, status="partially_complete", reason="approval_denied")
        # Recovery after approval committed but checkpoint failed is safe only for
        # the SAME authorization; dispatch rechecks its expiry again.
        if effect.status == "prepared" and approval.decision in ("pending", "approved"):
            self.store.approval(approval.model_copy(update={"decision": "approved"}))
            self.store.transition(effect, "approved")
        elif effect.status != "approved" or approval.decision != "approved":
            return self.update(state, status="paused", reason="reconciliation_required")
        return self.update(state, status="running", pending_approval_id=None)

    async def delegate(self, state):
        guarded = self.guard(state)
        if guarded:
            return guarded
        mid, sid = state["mission_id"], state["current_subtask_id"]
        effect = self.store.effect(mid, sid)
        if effect.status != "approved":
            # A replayed graph node must consult the committed ledger first.
            if effect.status == "dispatch_started":
                self.store.transition(effect, "uncertain")
            return self.update(state)
        mission = self.store.mission(mid)
        task = next(s for s in mission.subtasks if s.subtask_id == sid)
        if effect.generation != self.store.control(mid)["generation"]:
            return self.update(state, status="paused", reason="stale_generation")
        if task.requires_approval or task.category not in ("read_only", "local_reversible"):
            approval = self.store.get_approval(
                stable_id(effect.effect_id, str(effect.generation), "approval")
            )
            if approval.decision != "approved" or approval.expires_at <= self.clock():
                return self.update(state, status="partially_complete", reason="approval_expired")
        self.store.transition(effect, "dispatch_started")
        effect = self.store.effect(mid, sid)
        request = GuiSubtaskRequest(
            mission_id=mid,
            workspace_id=mission.workspace_id,
            effect_id=effect.effect_id,
            generation=effect.generation,
            subtask=task,
            authorization_expires_at=approval.expires_at
            if (task.requires_approval or task.category not in ("read_only", "local_reversible"))
            else mission.deadline_utc,
        )
        started = time.monotonic()
        self.running[mid] = asyncio.create_task(self.runner.run(request))
        try:
            raw = await asyncio.wait_for(
                self.running[mid],
                min(task.deadline_seconds, max(0.001, mission.deadline_utc - self.clock())),
            )
            result = TaskResult.model_validate(
                raw.model_dump() if isinstance(raw, TaskResult) else raw
            )
            if (
                result.status == "verified_success"
                and hasattr(self.runner, "bridge")
                and not (
                    result.cleanup_confirmed and result.visual_verified and result.human_confirmed
                )
            ):
                raise ValueError("GUI success lacks required terminal evidence")
            if (result.mission_id, result.subtask_id, result.effect_id, result.generation) != (
                mid,
                sid,
                effect.effect_id,
                effect.generation,
            ):
                raise ValueError("Unbound result")
        except asyncio.CancelledError:
            result = self.failure(effect, "cancelled")
        except TimeoutError:
            result = self.failure(effect, "timeout")
        except (ValidationError, ValueError, TypeError):
            result = self.failure(effect, "malformed")
        except Exception:  # noqa: BLE001 -- runner boundary must redact arbitrary failures.
            # Never persist exceptions, raw runner answers, reasoning or input text.
            result = self.failure(effect, "runner_error")
        finally:
            self.running.pop(mid, None)
        control = self.store.control(mid)
        if control["generation"] != effect.generation or control["stop_requested"]:
            result = self.failure(effect, "cancelled").model_copy(
                update={
                    "cleanup_confirmed": result.cleanup_confirmed,
                    "confirmed_input_transactions": result.confirmed_input_transactions,
                }
            )
        result = result.model_copy(
            update={"elapsed_ms": min(300000, max(0, int((time.monotonic() - started) * 1000)))}
        )
        status = {
            "verified_success": "observed_success",
            "verified_failure": "observed_failure",
        }.get(result.status, "uncertain")
        self.store.transition(effect, status, result)
        return self.update(state)

    @staticmethod
    def failure(effect, reason):
        return TaskResult(
            mission_id=effect.mission_id,
            subtask_id=effect.subtask_id,
            effect_id=effect.effect_id,
            generation=effect.generation,
            status="uncertain",
            reason=reason,
        )

    async def assess(self, state):
        guarded = self.guard(state)
        if guarded:
            return guarded
        effect = self.store.effect(state["mission_id"], state["current_subtask_id"])
        if effect.status == "observed_success" and effect.result is not None:
            return self.update(state)
        if effect.status == "dispatch_started":
            self.store.transition(effect, "uncertain")
        status = "partially_complete" if effect.status == "observed_failure" else "paused"
        return self.update(state, status=status, reason="reconciliation_required")

    async def checkpoint(self, state):
        return self.guard(state) or self.update(state, current_subtask_id=None)

    async def complete(self, state):
        guarded = self.guard(state)
        if guarded:
            return guarded
        mission = self.store.mission(state["mission_id"])
        effects = self.store.effects(mission.mission_id)
        verified = len(effects) == len(mission.subtasks) and all(
            e.status == "observed_success"
            and e.result is not None
            and e.result.status == "verified_success"
            and e.result.evidence_ref
            for e in effects
        )
        return self.update(
            state,
            status="completed" if verified else "partially_complete",
            reason="verified" if verified else "missing_evidence",
        )


def build_graph(nodes, checkpointer):
    graph = StateGraph(MissionState)

    def versioned(method):
        async def node(state):
            state = migrate_state(state)
            updated = await method(state)
            return {"schema_version": 1, "status": state["status"], **updated}

        return node

    def condition(source, route):
        graph.add_conditional_edges(source, lambda s: route(migrate_state(s)))

    for name in (
        "authorize",
        "plan",
        "choose",
        "preflight",
        "approval",
        "delegate",
        "assess",
        "checkpoint",
        "complete",
    ):
        graph.add_node(name, versioned(getattr(nodes, name)))
    graph.add_edge(START, "authorize")

    def route(next_node):
        return lambda s: END if s["status"] in TERMINAL | {"paused"} else next_node

    condition("authorize", route("plan"))
    condition("plan", route("choose"))
    condition(
        "choose",
        lambda s: (
            END
            if s["status"] in TERMINAL | {"paused"}
            else "preflight"
            if s["current_subtask_id"]
            else "complete"
        ),
    )
    condition(
        "preflight",
        lambda s: (
            END
            if s["status"] in TERMINAL | {"paused"}
            else "approval"
            if s["status"] == "waiting_approval"
            else "delegate"
        ),
    )
    condition("approval", route("delegate"))
    condition("delegate", route("assess"))
    condition("assess", route("checkpoint"))
    condition("checkpoint", route("choose"))
    graph.add_edge("complete", END)
    return graph.compile(checkpointer=checkpointer)
