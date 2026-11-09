"""Action journal service; called only on the mission SQLite thread, never the GUI.

HMAC key exists in RAM only. After restart every old grant is unusable; no local
secret/key vault is introduced. SQLite never sees raw action or objective content.
"""

import hashlib
import hmac
import json
import secrets
import time
from uuid import uuid4

from .contracts import Approval, Effect, EffectEvidence
from .policy import POLICY_VERSION, classify, evaluate

UNRESOLVED = ("dispatch_intent_recorded", "dispatch_started", "awaiting_observation", "uncertain")
BINDING = (
    "mission_id",
    "subtask_id",
    "attempt_id",
    "workspace_id",
    "generation",
    "target_ref",
    "scene_ref",
)


def migrate_journal(c):
    c.execute(
        "CREATE TABLE IF NOT EXISTS action_effects (effect_id TEXT PRIMARY KEY, "
        "mission_id TEXT NOT NULL, attempt_id TEXT NOT NULL, record TEXT NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS action_approvals (approval_id TEXT PRIMARY KEY, "
        "effect_id TEXT UNIQUE NOT NULL, record TEXT NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS effect_audit (id INTEGER PRIMARY KEY, effect_id TEXT NOT NULL, "
        "mission_id TEXT NOT NULL, code TEXT NOT NULL, at REAL NOT NULL, "
        "source TEXT NOT NULL, observation_ref TEXT)"
    )
    c.execute(
        "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON effect_audit "
        "BEGIN SELECT RAISE(ABORT,'Audit is append only'); END"
    )
    c.execute(
        "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON effect_audit "
        "BEGIN SELECT RAISE(ABORT,'Audit is append only'); END"
    )
    c.execute("CREATE INDEX IF NOT EXISTS action_mission ON action_effects(mission_id)")


class EffectCoordinator:
    def __init__(self, connection, *, key=None, clock=time.time, live=False, monotonic=time.monotonic):
        self.c, self.clock, self.live = connection, clock, live
        self.monotonic = monotonic
        self.key = key or secrets.token_bytes(32)
        self.expected = {}
        self.mission_guard = None

    def digest(self, value):
        canonical = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        )
        return hmac.new(self.key, canonical.encode(), hashlib.sha256).hexdigest()

    def _event(self, e, code, source="policy", observation_ref=None):
        if source in ("worker", "visual", "human_attested"):
            EffectEvidence(
                source=source,
                observed_at=self.clock(),
                observation_ref=observation_ref,
                category={
                    "worker": "input_receipt",
                    "visual": "visible_outcome",
                    "human_attested": "human_attestation",
                }[source],
            )
        self.c.execute(
            "INSERT INTO effect_audit (effect_id,mission_id,code,at,source,observation_ref) "
            "VALUES (?,?,?,?,?,?)",
            (e.effect_id, e.mission_id, code, self.clock(), source, observation_ref),
        )

    def _save(self, e):
        self.c.execute(
            "UPDATE action_effects SET record=? WHERE effect_id=?",
            (e.model_dump_json(), e.effect_id),
        )

    def get(self, eid):
        row = self.c.execute(
            "SELECT record FROM action_effects WHERE effect_id=?", (eid,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown action effect")
        return Effect.model_validate_json(row[0])

    def history(self, eid):
        columns = ("code", "at", "source", "observation_ref")
        return [
            dict(zip(columns, row, strict=True))
            for row in self.c.execute(
                "SELECT code,at,source,observation_ref FROM effect_audit WHERE effect_id=? ORDER BY id",
                (eid,),
            )
        ]

    def records(self, mission_id=None):
        rows = self.c.execute(
            "SELECT record FROM action_effects" + (" WHERE mission_id=?" if mission_id else ""),
            (mission_id,) if mission_id else (),
        ).fetchall()
        return [Effect.model_validate_json(row[0]) for row in rows]

    def _healthy(self, ctx):
        if self.mission_guard is not None and self.mission_guard(ctx.get("mission_id")):
            raise ValueError("Mission scheduling consent or budget unavailable")
        allowed = set(BINDING) | {
            "observation_ref",
            "objective_ref",
            "active",
            "lease_valid",
            "cleanup_healthy",
            "controller_generation",
        }
        if type(ctx) is not dict or set(ctx) - allowed:
            raise ValueError("Unsupported permission scope fields")
        if not all(ctx.get(k) is True for k in ("active", "lease_valid", "cleanup_healthy")):
            raise ValueError("Inactive or unhealthy authorization scope")
        row = self.c.execute(
            "SELECT generation,stop_requested,cancelled FROM missions WHERE mission_id=?",
            (ctx["mission_id"],),
        ).fetchone()
        if row is not None and (row[0] != ctx["generation"] or row[1] or row[2]):
            raise ValueError("Mission stopped or generation changed")
        if self.live and row is None:
            raise ValueError("No durable mission consent")

    @staticmethod
    def _contract_scene(frame):
        geometry = ([vars(b) for b in frame.displays], vars(frame.bounds), frame.foreground_guard)
        return hashlib.sha256(json.dumps(geometry, sort_keys=True).encode()).hexdigest()

    def _proposal_binding(self, proposal, ctx, action):
        # Lazy imports keep the fake mission core independent of native/UI modules.
        from monster.agent.contracts import ActionProposal

        if not isinstance(proposal, ActionProposal):
            raise ValueError("Missing operational proposal")  # noqa: TRY004 -- authorization refusals use ValueError
        proposal = ActionProposal.model_validate(proposal)
        if (proposal.preview_only or proposal.action.to_action() != action
                or type(ctx.get("controller_generation")) is not int
                or proposal.frame.controller_generation != ctx.get("controller_generation")
                or proposal.frame.capture_ref != ctx["observation_ref"]
                or self._contract_scene(proposal.frame) != ctx["scene_ref"]
                or proposal.mission_ref not in (None, ctx["mission_id"])
                or proposal.subtask_ref not in (None, ctx["subtask_id"])):
            raise ValueError("Operational proposal scope mismatch")
        return proposal

    def _proposal_digest(self, proposal):
        # Only this process-keyed digest persists, never transient payload/criteria.
        return self.digest({"metadata": proposal.model_dump(mode="json"),
                            "action": proposal.action.to_action(),
                            "target": proposal.target_description,
                            "expected": proposal.expected_result,
                            "foreground": proposal.frame.foreground_guard})

    def _dispatch_contract(self, effect, ctx, action, grounded):
        from monster.agent.contracts import GroundedAction

        if not isinstance(grounded, GroundedAction):
            raise ValueError("Missing mapped action contract")  # noqa: TRY004 -- revoke on authorization refusal
        grounded = GroundedAction.model_validate(grounded)
        proposal, checked = grounded.proposal, grounded.checked_frame
        if (grounded.effect_ref != effect.effect_id
                or type(ctx.get("controller_generation")) is not int
                or proposal.action_id != effect.action_ref or proposal.task_ref != effect.task_ref
                or checked.controller_generation != effect.controller_generation
                or checked.controller_generation != ctx.get("controller_generation")
                or proposal.action.to_action() != action
                or self._proposal_digest(proposal) != effect.action_contract_digest
                or checked.capture_ref != ctx["observation_ref"]
                or self._contract_scene(checked) != ctx["scene_ref"]
                or not 0 <= self.monotonic() - checked.captured_at <= 2):
            raise ValueError("Mapped action does not match the reviewed operational contract")

    def prepare(self, ctx, action, *, expected_text, review_id, model_risk=None, risk=None, proposal=None):
        self._healthy(ctx)
        if any(e.workspace_id == ctx["workspace_id"] for e in self.unresolved()):
            raise ValueError("Unresolved effect requires reconciliation")
        assigned = (
            classify(action, expected_text=expected_text, model_risk=model_risk)
            if risk is None
            else risk
        )
        # Explicit risk fixtures are allowed only in non-live policy simulations.
        if risk is not None and self.live:
            assigned = "unknown"
        contract = self._proposal_binding(proposal, ctx, action) if proposal is not None else None
        if self.live and contract is None:
            raise ValueError("Live effects require an operational proposal")
        if contract and contract.model_risk_hint in ("sensitive", "uncertain"):
            assigned = "unknown"  # Transport hints cannot erase the proposal's caution.
        decision = evaluate(assigned, active=True, live=self.live)
        contract_digest = self._proposal_digest(contract) if contract else None
        action_digest = self.digest(action)
        binding = {k: ctx[k] for k in BINDING}
        fingerprint = self.digest(
            {
                "binding": binding,
                "action": action_digest,
                "policy": POLICY_VERSION,
                "observation": ctx["observation_ref"],
                "risk": assigned,
                **({"contract": contract_digest} if contract else {}),
            }
        )
        e = Effect(
            schema_version=3 if contract else 2,
            action_contract_digest=contract_digest,
            action_ref=contract.action_id if contract else None,
            task_ref=contract.task_ref if contract else None,
            controller_generation=contract.frame.controller_generation if contract else None,
            effect_id=uuid4().hex,
            mission_id=ctx["mission_id"],
            subtask_id=ctx["subtask_id"],
            attempt_id=ctx["attempt_id"],
            workspace_id=ctx["workspace_id"],
            generation=ctx["generation"],
            target_ref=ctx["target_ref"],
            category=assigned,
            action_kind=action.get("op")
            if action.get("op") in ("text", "press", "move", "click", "chord")
            else "unsupported",
            objective_ref=ctx.get("objective_ref", "scratch-objective"),
            status="prepared",
            fingerprint=fingerprint,
            action_digest=action_digest,
            observation_ref=ctx["observation_ref"],
            scene_ref=ctx["scene_ref"],
            policy_version=POLICY_VERSION,
            created_at=self.clock(),
            expires_at=self.clock() + 60,
        )
        a = Approval(
            schema_version=2,
            approval_id=uuid4().hex,
            mission_id=e.mission_id,
            subtask_id=e.subtask_id,
            effect_id=e.effect_id,
            generation=e.generation,
            expires_at=e.expires_at,
            fingerprint=fingerprint,
            workspace_id=e.workspace_id,
            policy_version=POLICY_VERSION,
            review_digest=self.digest({"fingerprint": fingerprint, "review_id": review_id}),
            ui_request_id=review_id,
        )
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            if contract and any(prior.action_ref == contract.action_id for prior in self.records()):
                raise ValueError("Operational action was already registered; reobserve with a new proposal")
            self.c.execute(
                "INSERT INTO action_effects VALUES (?,?,?,?)",
                (e.effect_id, e.mission_id, e.attempt_id, e.model_dump_json()),
            )
            self._event(e, "prepared")
            status = "approval_pending" if decision.decision in ("review", "allow") else "denied"
            e = e.model_copy(update={"status": status})
            self._save(e)
            self._event(e, status)
            self.c.execute(
                "INSERT INTO action_approvals VALUES (?,?,?)",
                (a.approval_id, e.effect_id, a.model_dump_json()),
            )
        self.expected[e.effect_id] = expected_text
        return e

    def approval(self, eid):
        row = self.c.execute(
            "SELECT record FROM action_approvals WHERE effect_id=?", (eid,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown exact-effect approval")
        return Approval.model_validate_json(row[0])

    def _scope(self, e, ctx):
        self._healthy(ctx)
        for field in BINDING:
            if getattr(e, field) != ctx.get(field):
                raise ValueError("Effect scope changed")
        if e.policy_version != POLICY_VERSION or e.expires_at <= self.clock():
            raise ValueError("Effect expired or policy changed")

    def approve(self, eid, ctx, *, review_id):
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            e, a = self.get(eid), self.approval(eid)
            self._scope(e, ctx)
            if (
                e.status != "approval_pending"
                or a.ui_request_id != review_id
                or a.decision != "pending"
            ):
                raise ValueError("Approval is stale or mismatched")
            a = a.model_copy(
                update={
                    "decision": "approved",
                    "issued_at": self.clock(),
                    "actor_ref": "local-user",
                }
            )
            self.c.execute(
                "UPDATE action_approvals SET record=? WHERE effect_id=?", (a.model_dump_json(), eid)
            )
            e = e.model_copy(update={"status": "approved"})
            self._save(e)
            self._event(e, "approved", "human")
        return e

    def dispatch(self, eid, ctx, action, *, grounded=None):
        try:
            with self.c:
                self.c.execute("BEGIN IMMEDIATE")
                e, a = self.get(eid), self.approval(eid)
                self._scope(e, ctx)
                if any(
                    other.effect_id != eid and other.workspace_id == ctx["workspace_id"]
                    for other in self.unresolved()
                ):
                    raise ValueError("Unresolved effect requires reconciliation")
                if (
                    e.status != "approved"
                    or a.decision != "approved"
                    or a.consumed_at is not None
                    or a.fingerprint != e.fingerprint
                    or a.generation != e.generation
                    or a.policy_version != POLICY_VERSION
                    or a.workspace_id != e.workspace_id
                    or a.expires_at <= self.clock()
                    or self.digest(action) != e.action_digest
                ):
                    raise ValueError("Exact effect authorization is invalid")
                if evaluate(e.category, active=True, live=self.live).decision not in (
                    "review",
                    "allow",
                ):
                    raise ValueError("Policy denied dispatch")
                if self.live and e.schema_version != 3:
                    raise ValueError("Legacy grants require fresh operational review")
                if e.schema_version == 3:
                    self._dispatch_contract(e, ctx, action, grounded)
                # Atomic consumption and intent precede every possible input handoff.
                a = a.model_copy(update={"consumed_at": self.clock()})
                self.c.execute(
                    "UPDATE action_approvals SET record=? WHERE effect_id=?",
                    (a.model_dump_json(), eid),
                )
                e = e.model_copy(update={"status": "dispatch_intent_recorded"})
                self._save(e)
                self._event(e, "dispatch_intent_recorded")
            return e
        except ValueError:
            if self.get(eid).status in ("prepared", "approval_pending", "approved"):
                self.revoke(eid)
            raise

    def revoke(self, eid, *, decision="revoked"):
        if decision not in ("revoked", "denied", "expired"):
            raise ValueError("Invalid revocation decision")
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            e = self.get(eid)
            if e.status in (
                "observed_success",
                "observed_failure",
                "denied",
                "not_dispatched",
                "uncertain",
            ):
                return e
            existing = self.approval(eid)
            resolved_decision = "expired" if existing.expires_at <= self.clock() else decision
            a = existing.model_copy(update={"decision": resolved_decision})
            self._event(
                e,
                "approval_" + resolved_decision,
                "human" if resolved_decision == "denied" else "policy",
            )
            self.c.execute(
                "UPDATE action_approvals SET record=? WHERE effect_id=?", (a.model_dump_json(), eid)
            )
            e = e.model_copy(
                update={"status": "uncertain" if e.status in UNRESOLVED else "not_dispatched"}
            )
            self._save(e)
            self._event(e, e.status)
        return e

    def started(self, eid):
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            e = self.get(eid)
            if e.status == "dispatch_intent_recorded":
                e = e.model_copy(update={"status": "dispatch_started"})
                self._save(e)
                self._event(e, "dispatch_started", "worker")

    def receipt(self, eid, *, inserted):
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            e = self.get(eid)
            if e.status in ("dispatch_intent_recorded", "dispatch_started"):
                e = e.model_copy(
                    update={"status": "awaiting_observation" if inserted else "uncertain"}
                )
                self._save(e)
            self._event(e, "input_inserted" if inserted else "input_unknown", "worker")
        return e

    def observe(self, eid, status, *, observation_ref):
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            e = self.get(eid)
            if e.status != "awaiting_observation":
                self._event(e, "late_observation", "visual", observation_ref)
                return e
            mapped = {"succeeded": "observed_success", "failed": "observed_failure"}.get(
                status, "uncertain"
            )
            e = e.model_copy(update={"status": mapped})
            self._save(e)
            self._event(e, mapped, "visual", observation_ref)
        return e

    def attest(self, eid, outcome, *, observation_ref):
        if outcome not in ("success", "failure", "unresolved"):
            raise ValueError("Invalid attestation")
        with self.c:
            self.c.execute("BEGIN IMMEDIATE")
            e = self.get(eid)
            if e.status != "uncertain":
                raise ValueError("Only uncertain effects need reconciliation")
            status = {"success": "observed_success", "failure": "observed_failure"}.get(
                outcome, "uncertain"
            )
            e = e.model_copy(update={"status": status})
            self._save(e)
            self._event(e, status, "human_attested", observation_ref)
        return e

    def recover(self):
        for e in self.records():
            if e.status in ("prepared", "approval_pending", "approved") + UNRESOLVED:
                self.revoke(e.effect_id)

    def unresolved(self, mission_id=None):
        return [e for e in self.records(mission_id) if e.status in UNRESOLVED]

    def finish_attempt(self, attempt_id):
        for e in self.records():
            if (
                e.attempt_id == attempt_id
                and e.status in ("prepared", "approval_pending", "approved") + UNRESOLVED
            ):
                self.revoke(e.effect_id)
        return [e for e in self.records() if e.attempt_id == attempt_id]
