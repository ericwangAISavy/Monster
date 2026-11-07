"""Opt-in UI facade. SQLite and LangGraph live exclusively on its async worker thread."""

import asyncio
import threading
import time
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from .contracts import Approval, ApprovalDecision, Mission, Subtask
from .coordinator import MissionCoordinator

EDITOR_OBJECTIVES = {
    "editor-a": 'Type "Monster mission A. " into the currently focused disposable Notepad '
    "document. Do not send. Do not replace existing text.",
    "editor-b": 'Type "Monster mission B." into the currently focused disposable Notepad '
    "document. Preserve the first sentence. Do not send. Do not replace existing text.",
}


def editor_mission():
    return Mission(
        mission_id="editor-" + uuid4().hex,
        objective_ref="editor-demo",
        workspace_id="interactive-desktop",
        authorized=True,
        deadline_utc=time.time() + 900,
        subtasks=[
            Subtask(
                subtask_id=f"editor-{key}",
                objective_ref=f"editor-{key}",
                success_refs=[f"visible-{key}"],
                category="local_reversible",
                depends_on=["editor-a"] if key == "b" else [],
            )
            for key in ("a", "b")
        ],
    )


class MissionRuntime(QObject):
    changed = Signal(object)
    retired = Signal()

    def __init__(self, directory, runner, bridge, *, plan=None, resume_id=None, parent=None):
        super().__init__(parent)
        self.directory, self.runner, self.bridge = directory, runner, bridge
        self.plan, self.resume_id = plan, resume_id
        self.mission_id = plan.mission_id if plan else resume_id
        self.loop = None
        self.coordinator = None
        self.engaged = True
        self.closed = False
        self.stopped = threading.Event()
        self.pending = None
        self._job = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="monster-mission")
        self.changed.connect(self._observe)
        self.retired.connect(self._dispose)

    def start(self):
        self.thread.start()

    def _observe(self, update):
        self.pending = update.get("approval")
        self.engaged = update["state"]["status"] in ("running", "waiting_approval", "created")

    def _run(self):
        asyncio.run(self._main())

    async def _main(self):
        loop = asyncio.get_running_loop()
        self._exit = asyncio.Event()
        self.loop = loop
        try:
            async with MissionCoordinator(self.directory, self.runner, experimental_gui=True) as c:
                self.coordinator = c
                if self.closed:
                    return
                if self.plan is None and self.resume_id is None:
                    row = c.store.connection.execute(
                        "SELECT mission_id FROM missions "
                        "WHERE terminal_at IS NULL AND cancelled=0 ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                    if row is None:
                        raise ValueError("No resumable mission")
                    self.mission_id = self.resume_id = row[0]
                if self.stopped.is_set():
                    if self.plan is not None:
                        c.store.create(self.plan)
                    await self._operation(c.stop(self.mission_id, cancel=True))
                    await self._exit.wait()
                    return
                self._job = asyncio.create_task(
                    self._operation(
                        c.start(self.plan) if self.plan else c.continue_mission(self.resume_id)
                    )
                )
                await self._exit.wait()
                if self._job and not self._job.done():
                    await c.stop(self.mission_id, cancel=True)
                    await asyncio.gather(self._job, return_exceptions=True)
        except (RuntimeError, OSError, ValueError, KeyError):
            self.changed.emit(
                {
                    "state": {
                        "mission_id": self.mission_id,
                        "status": "paused",
                        "reason": "runtime_unavailable",
                    },
                    "approval": None,
                }
            )
        finally:
            self.coordinator = None
            self.loop = None
            self.retired.emit()

    def _dispose(self):
        self.closed = True
        self.engaged = False
        self.bridge.shutdown()
        self.runner.detach()
        self.bridge.deleteLater()
        self.deleteLater()

    async def _operation(self, operation):
        try:
            state = await operation
            aid = state.get("pending_approval_id")
            approval = (
                self.coordinator.store.get_approval(aid)
                if (state["status"] == "waiting_approval" and aid)
                else None
            )
            self.changed.emit({"state": state, "approval": approval})
        except (RuntimeError, OSError, ValueError, KeyError):
            self.changed.emit(
                {
                    "state": {
                        "mission_id": self.mission_id,
                        "status": "paused",
                        "reason": "intervention_required",
                    },
                    "approval": None,
                }
            )

    def _call(self, callback):
        loop = self.loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(callback)
            except RuntimeError:
                pass  # Retirement won the race; Qt cancellation already latched.

    def answer(self, answer):
        approval = self.pending
        if not isinstance(approval, Approval) or self.closed or self.loop is None:
            return
        self.pending = None
        decision = ApprovalDecision(
            approval_id=approval.approval_id,
            effect_id=approval.effect_id,
            generation=approval.generation,
            decision="approve" if answer == "approve" else "deny",
        )

        def schedule():
            if self.coordinator and (self._job is None or self._job.done()):
                self._job = asyncio.create_task(
                    self._operation(self.coordinator.resume(self.mission_id, decision))
                )

        self._call(schedule)

    def stop(self):
        if self.closed:
            return
        self.stopped.set()
        self.runner.invalidate_all(self.mission_id)
        self.bridge.halt()
        if self.loop is None:
            return

        def schedule():
            if self.coordinator:
                asyncio.create_task(
                    self._operation(self.coordinator.stop(self.mission_id, cancel=True))
                )

        self._call(schedule)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.engaged = False
        self.runner.invalidate_all()
        self.bridge.shutdown()  # Called on Qt thread; resolves every waiting runner.
        if self.loop is not None:
            self._call(self._exit.set)
