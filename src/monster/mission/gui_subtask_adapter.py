"""Async half of the queued bridge. No controller or QWidget method calls here."""

import asyncio
import threading

from .contracts import TaskResult
from .gui_bridge import Handoff


class AsyncGuiSubtaskRunner:
    def __init__(self, bridge, *, cleanup_seconds=6):
        self.bridge = bridge
        self.cleanup_seconds = cleanup_seconds
        self.pending = {}
        self.lock = threading.Lock()
        self.attached = True
        self.strict_actions = hasattr(bridge.controller, "effect_requested")
        self.effects = None
        bridge.resolved.connect(self._result)
        bridge.policy_requested.connect(self._policy_request)

    def _result(self, result):
        with self.lock:
            pending = self.pending.get((result.effect_id, result.generation))
        if pending is None:
            return
        loop, future, handoff = pending
        req = handoff.request
        if (req.mission_id, req.subtask.subtask_id) != (result.mission_id, result.subtask_id):
            return

        def deliver():
            if not future.done():
                future.set_result(result)

        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            pass  # Closing loop: the Qt side still owns cleanup/quarantine.

    async def run(self, request):
        key = request.effect_id, request.generation
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        handoff = Handoff(request, threading.Event())
        with self.lock:
            if key in self.pending:
                raise ValueError("Duplicate live attempt")
            self.pending[key] = loop, future, handoff
        try:
            self.bridge.start_requested.emit(handoff)
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(future), request.subtask.deadline_seconds + 7
                )
                return self._finalize(request, result)
            except (asyncio.CancelledError, TimeoutError):
                handoff.cancelled.set()
                self.bridge.cancel_requested.emit(key)
                try:
                    result = await asyncio.wait_for(asyncio.shield(future), self.cleanup_seconds)
                    return self._finalize(
                        request,
                        result.model_copy(
                            update={
                                "status": "uncertain",
                                "evidence_ref": None,
                                "evidence_kind": "none",
                                "reason": "cancelled",
                            }
                        ),
                    )
                except TimeoutError:
                    return TaskResult(
                        mission_id=request.mission_id,
                        subtask_id=request.subtask.subtask_id,
                        effect_id=request.effect_id,
                        generation=request.generation,
                        status="uncertain",
                        reason="cancelled",
                    )
        finally:
            with self.lock:
                self.pending.pop(key, None)

    def configure_effects(self, effects):
        self.effects = effects

    def _finalize(self, request, result):
        if self.effects is None:
            if self.strict_actions:
                return result.model_copy(
                    update={
                        "status": "uncertain",
                        "evidence_ref": None,
                        "evidence_kind": "none",
                        "reason": "runner_error",
                    }
                )
            return result
        try:
            records = self.effects.finish_attempt(request.effect_id)
            if (
                self.strict_actions
                and result.status == "verified_success"
                and (
                    not records
                    or any(
                        e.status != "observed_success"
                        for e in records
                        if e.status not in ("not_dispatched", "denied")
                    )
                )
            ):
                return result.model_copy(
                    update={
                        "status": "uncertain",
                        "evidence_ref": None,
                        "evidence_kind": "none",
                        "reason": "not_observed",
                    }
                )
        except Exception:  # noqa: BLE001 -- persistence boundary fails closed, never records raw errors.
            return result.model_copy(
                update={
                    "status": "uncertain",
                    "evidence_ref": None,
                    "evidence_kind": "none",
                    "reason": "runner_error",
                }
            )
        return result

    def _policy_request(self, packet):
        with self.lock:
            pending = self.pending.get(packet["request_key"])
        if pending is None:
            return
        loop = pending[0]
        try:
            loop.call_soon_threadsafe(lambda: asyncio.create_task(self._policy_operation(packet)))
        except RuntimeError:
            pass

    async def _policy_operation(self, packet):
        reply = {k: packet[k] for k in ("nonce", "operation", "request_key")}
        reply["allowed"] = False
        try:
            if self.effects is None:
                raise ValueError("No durable policy service")
            operation, ctx, eid = packet["operation"], packet["context"], packet.get("effect_id")
            if operation == "prepare":
                from .runtime import EXPECTED_TEXT

                expected = EXPECTED_TEXT.get(packet["objective_ref"])
                if packet.get("schedule_ref"):
                    from .plan_templates import PILOT_TEXT

                    if not self.effects.schedule_lookup(packet["context"]["mission_id"]):
                        raise ValueError("No admitted scheduling scope")
                    expected = PILOT_TEXT.get(packet["objective_ref"])
                if expected is None:
                    raise ValueError("No reviewed scratch objective")
                e = self.effects.prepare(
                    ctx,
                    packet["action"],
                    expected_text=expected,
                    review_id=packet["review_id"],
                    model_risk=packet.get("model_risk"),
                )
                reply.update(effect_id=e.effect_id, risk=e.category, fingerprint=e.fingerprint)
                if e.status != "approval_pending":
                    raise ValueError("Policy denied action")
            elif operation == "approve":
                self.effects.approve(eid, ctx, review_id=packet["review_id"])
            elif operation == "dispatch":
                self.effects.dispatch(eid, ctx, packet["action"])
            elif operation == "receipt":
                self.effects.receipt(eid, inserted=packet["inserted"])
            elif operation == "started":
                self.effects.started(eid)
            elif operation == "observe":
                observed = self.effects.observe(
                    eid, packet["status"], observation_ref=packet["observation_ref"]
                )
                if observed.status == "uncertain":
                    raise ValueError("Visible result requires reconciliation")
            elif operation == "revoke":
                self.effects.revoke(eid, decision=packet.get("decision", "revoked"))
            else:
                raise ValueError("Unsupported policy operation")
            reply["allowed"] = True
        except Exception:  # noqa: BLE001 -- fixed result category, no raw content/error logging.
            reply["reason"] = "policy_or_journal_refused"
        self.bridge.policy_completed.emit(reply)

    def invalidate_all(self, mission_id=None):
        """Thread-safe latch: no Qt calls and no await before cancelling queued handoffs."""
        with self.lock:
            for _, _, handoff in self.pending.values():
                if mission_id is None or handoff.request.mission_id == mission_id:
                    handoff.cancelled.set()

    def detach(self):
        if self.attached:
            self.bridge.policy_requested.disconnect(self._policy_request)
            self.bridge.resolved.disconnect(self._result)
            self.attached = False

    async def cancel(self, mission_id, subtask_id):
        with self.lock:
            pending = list(self.pending.items())
        for key, (loop, future, handoff) in pending:
            req = handoff.request
            if (req.mission_id, req.subtask.subtask_id) == (mission_id, subtask_id):
                handoff.cancelled.set()
                self.bridge.cancel_requested.emit(key)
                try:
                    await asyncio.wait_for(asyncio.shield(future), self.cleanup_seconds)
                except TimeoutError:
                    pass
