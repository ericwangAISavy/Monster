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
        bridge.resolved.connect(self._result)

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
                return await asyncio.wait_for(
                    asyncio.shield(future), request.subtask.deadline_seconds + 7
                )
            except (asyncio.CancelledError, TimeoutError):
                handoff.cancelled.set()
                self.bridge.cancel_requested.emit(key)
                try:
                    result = await asyncio.wait_for(asyncio.shield(future), self.cleanup_seconds)
                    return result.model_copy(
                        update={
                            "status": "uncertain",
                            "evidence_ref": None,
                            "evidence_kind": "none",
                            "reason": "cancelled",
                        }
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

    def invalidate_all(self, mission_id=None):
        """Thread-safe latch: no Qt calls and no await before cancelling queued handoffs."""
        with self.lock:
            for _, _, handoff in self.pending.values():
                if mission_id is None or handoff.request.mission_id == mission_id:
                    handoff.cancelled.set()

    def detach(self):
        if self.attached:
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
