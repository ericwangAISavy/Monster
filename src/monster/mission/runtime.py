"""Opt-in UI facade. SQLite and LangGraph live exclusively on its async worker thread."""

import asyncio
import sqlite3
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


EXPECTED_TEXT = {"editor-a": "Monster mission A. ", "editor-b": "Monster mission B."}


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
    reconciliation_ready = Signal(object)
    reconciliation_error = Signal(str)

    def __init__(
        self,
        directory,
        runner,
        bridge,
        *,
        plan=None,
        resume_id=None,
        reconciliation_only=False,
        scheduled_plan=None,
        native_qualification=None,
        scheduled_resume_mode=None,
        strategic_text=None,
        strategic_resume=False,
        parent=None,
    ):
        super().__init__(parent)
        self.directory, self.runner, self.bridge = directory, runner, bridge
        self.plan, self.resume_id = plan, resume_id
        self.scheduled_plan = scheduled_plan
        self.native_qualification = native_qualification
        self.scheduled_resume_mode = scheduled_resume_mode
        self.strategic_text, self.strategic_resume = strategic_text, strategic_resume
        self.last_report = None
        self.reconciliation_only = reconciliation_only
        self.mission_id = (
            scheduled_plan.mission.mission_id
            if scheduled_plan
            else (plan.mission_id if plan else resume_id)
        )
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
        if not update.get("requested_progress"):
            self.pending = update.get("strategic_consent") or update.get("consent") or update.get("approval")
        if update.get("report"):
            self.last_report = update["report"]
        self.engaged = update["state"]["status"] in ("running", "waiting_approval", "created")

    def _run(self):
        asyncio.run(self._main())

    async def _main(self):
        loop = asyncio.get_running_loop()
        self._exit = asyncio.Event()
        self.loop = loop
        try:
            async with MissionCoordinator(
                self.directory,
                self.runner,
                experimental_gui=hasattr(self.runner, "bridge"),
                native_qualification=self.native_qualification,
            ) as c:
                self.coordinator = c
                c.scheduler.observer = lambda progress: self.changed.emit(
                    {
                        "state": {
                            "mission_id": progress.mission_id,
                            "status": "running",
                            "reason": progress.reason_code,
                        },
                        "approval": None,
                        "progress": progress.model_dump(),
                    }
                )
                if self.closed:
                    return
                if self.strategic_text is not None:
                    self._job = asyncio.create_task(self._prepare_strategy())
                    await self._exit.wait()
                    if not self._job.done():
                        self._job.cancel()
                        await asyncio.gather(self._job, return_exceptions=True)
                    return
                if self.reconciliation_only:
                    records = c.effects.unresolved()
                    self.engaged = False
                    if records:
                        self.mission_id = records[0].mission_id
                    self.reconciliation_ready.emit(records)
                    await self._exit.wait()
                    return
                if self.plan is None and self.scheduled_plan is None and self.resume_id is None:
                    if self.strategic_resume:
                        row = c.store.connection.execute(
                            "SELECT m.mission_id FROM missions m JOIN strategic_sessions s ON m.mission_id=s.mission_id "
                            "WHERE m.terminal_at IS NULL AND m.cancelled=0 AND s.stopped=0 "
                            "AND s.active_digest IS NOT NULL ORDER BY m.created_at DESC LIMIT 1"
                        ).fetchone()
                    elif self.scheduled_resume_mode:
                        row = c.store.connection.execute(
                            "SELECT m.mission_id FROM missions m JOIN mission_schedules s ON m.mission_id=s.mission_id "
                            "WHERE m.terminal_at IS NULL AND m.cancelled=0 AND json_extract(s.spec,'$.mode')=? "
                            "AND json_extract(s.spec,'$.template_ref')='editor-six-v1' "
                            "ORDER BY m.created_at DESC LIMIT 1",
                            (self.scheduled_resume_mode,),
                        ).fetchone()
                    else:
                        row = c.store.connection.execute(
                            "SELECT mission_id FROM missions WHERE terminal_at IS NULL AND cancelled=0 "
                            "AND NOT EXISTS (SELECT 1 FROM mission_schedules s WHERE s.mission_id=missions.mission_id) "
                            "ORDER BY created_at DESC LIMIT 1"
                        ).fetchone()
                    if row is None:
                        raise ValueError("No resumable mission")
                    self.mission_id = self.resume_id = row[0]
                if self.stopped.is_set():
                    if self.plan is not None or self.scheduled_plan is not None:
                        c.store.create(self.plan or self.scheduled_plan.mission)
                    await self._operation(c.stop(self.mission_id, cancel=True))
                    await self._exit.wait()
                    return
                self._job = asyncio.create_task(
                    self._operation(
                        c.start_scheduled(self.scheduled_plan)
                        if self.scheduled_plan
                        else (
                            c.start(self.plan) if self.plan else c.continue_mission(self.resume_id)
                        )
                    )
                )
                await self._exit.wait()
                if self._job and not self._job.done():
                    await c.stop(self.mission_id, cancel=True)
                    await asyncio.gather(self._job, return_exceptions=True)
        except (RuntimeError, OSError, ValueError, KeyError, sqlite3.Error):
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
        if self.bridge is not None:
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
            consent = None
            report = None
            if self.coordinator.scheduler.exists(self.mission_id):
                report = await self.coordinator.mission_report(self.mission_id)
                if state.get("reason") == "mission_consent_required":
                    consent = self.coordinator.scheduler.consent_request(self.mission_id)
            self.changed.emit(
                {"state": state, "approval": approval, "consent": consent, "report": report}
            )
        except (RuntimeError, OSError, ValueError, KeyError, sqlite3.Error):
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

    def review_effects(self):
        async def review():
            if self.coordinator:
                try:
                    records = self.coordinator.effects.unresolved(
                        None if self.reconciliation_only else self.mission_id
                    )
                    self.reconciliation_ready.emit(records)
                except (ValueError, OSError, RuntimeError, sqlite3.Error):
                    self.reconciliation_error.emit(
                        "Journal unavailable; unresolved effects remain blocked."
                    )

        self._call(lambda: asyncio.create_task(review()))

    async def _attest_effect(self, effect_id, outcome):
        if self.coordinator:
            from .reconciliation import HumanEffectReconciler

            try:
                HumanEffectReconciler(self.coordinator.effects).attest(effect_id, outcome)
                if outcome == "unresolved":
                    self.reconciliation_error.emit(
                        "Effect remains unresolved. No input or retry authorized."
                    )
                    return
                records = self.coordinator.effects.unresolved(
                    None if self.reconciliation_only else self.mission_id
                )
                if self.reconciliation_only and records:
                    self.mission_id = records[0].mission_id
                self.reconciliation_ready.emit(records)
            except (ValueError, OSError, RuntimeError, sqlite3.Error):
                self.reconciliation_error.emit(
                    "Journal update failed; inspect and review again. No input authorized."
                )

    def attest_effect(self, effect_id, outcome):
        self._call(lambda: asyncio.create_task(self._attest_effect(effect_id, outcome)))

    def _call(self, callback):
        loop = self.loop
        if loop is not None:
            try:
                loop.call_soon_threadsafe(callback)
            except RuntimeError:
                pass  # Retirement won the race; Qt cancellation already latched.

    def answer(self, answer):
        from .scheduling import MissionConsent
        from .strategic.contracts import StrategicConsent

        if isinstance(self.pending, StrategicConsent):
            consent, self.pending = self.pending, None
            if answer != "approve":
                self.stop()
                return

            def schedule_strategic():
                if self.coordinator and not self.stopped.is_set() and (self._job is None or self._job.done()):
                    self._job = asyncio.create_task(self._operation(
                        self.coordinator.approve_strategic(self.mission_id, consent)))

            self._call(schedule_strategic)
            return

        if isinstance(self.pending, MissionConsent):
            consent, self.pending = self.pending, None
            if answer != "approve":
                self.stop()
                return

            def schedule():
                if self.coordinator and (self._job is None or self._job.done()):
                    self._job = asyncio.create_task(
                        self._operation(
                            self.coordinator.consent_and_continue(self.mission_id, consent)
                        )
                    )

            self._call(schedule)
            return
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

    def request_boundary_pause(self):
        async def pause():
            if self.coordinator:
                await self._operation(self.coordinator.request_pause(self.mission_id))

        self._call(lambda: asyncio.create_task(pause()))

    async def _prepare_strategy(self):
        from .strategic.planning import FakeStrategicPlanner, PlannerFailure
        from .strategic.service import interpret_design_goal

        try:
            goal = interpret_design_goal(self.strategic_text)
            self.strategic_text = None
            self.mission_id, _ = await self.coordinator.strategy.prepare(goal, FakeStrategicPlanner())
            if self.closed or self.stopped.is_set():
                await self.coordinator.stop(self.mission_id, cancel=True)
                return
            self._emit_strategic_review()
        except (ValueError, PlannerFailure, sqlite3.Error, OSError):
            self.changed.emit({'state': {'mission_id': self.mission_id, 'status': 'paused',
                              'reason': 'unsupported_goal_or_planner_unavailable'}, 'approval': None})
        finally:
            self.strategic_text = None

    def _emit_strategic_review(self):
        service = self.coordinator.strategy
        self.changed.emit({'state': {'mission_id': self.mission_id, 'status': 'waiting_approval',
                                    'reason': 'strategic_review_required'}, 'approval': None,
                           'strategic_consent': service.consent_request(self.mission_id),
                           'strategic_preview': service.pending(self.mission_id)})

    def request_strategic_revision(self):
        async def propose():
            from .strategic.planning import FakeStrategicPlanner

            try:
                c = self.coordinator
                proof = c.store.connection.execute(
                    'SELECT subtask_id FROM strategic_blockers WHERE mission_id=? AND attempted=0',
                    (self.mission_id,)).fetchone()
                if proof is None:
                    raise ValueError('No safe blocker')
                await c.strategy.propose_revision(self.mission_id, FakeStrategicPlanner(), proof[0])
                self._emit_strategic_review()
            except (ValueError, RuntimeError, sqlite3.Error):
                self.changed.emit({'state': {'mission_id': self.mission_id, 'status': 'paused',
                                            'reason': 'safe_revision_unavailable'}, 'approval': None})

        def schedule():
            if self.coordinator and (self._job is None or self._job.done()):
                self._job = asyncio.create_task(propose())

        self._call(schedule)

    def request_progress(self):
        async def report():
            if self.coordinator and self.coordinator.scheduler.exists(self.mission_id):
                state = await self.coordinator.snapshot(self.mission_id)
                data = await self.coordinator.mission_report(self.mission_id)
                self.changed.emit(
                    {"state": state, "approval": None, "report": data, "requested_progress": True}
                )

        self._call(lambda: asyncio.create_task(report()))

    def request_resume(self):
        def schedule():
            if self.coordinator and (self._job is None or self._job.done()):
                self._job = asyncio.create_task(
                    self._operation(self.coordinator.continue_mission(self.mission_id))
                )

        self._call(schedule)

    def stop(self):
        if self.closed:
            return
        self.stopped.set()
        if hasattr(self.runner, "invalidate_all"):
            self.runner.invalidate_all(self.mission_id)
        if self.bridge is not None:
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
        if hasattr(self.runner, "invalidate_all"):
            self.runner.invalidate_all()
        if self.bridge is not None:
            self.bridge.shutdown()  # Called on Qt thread; resolves every waiting runner.
        if self.loop is not None:
            self._call(self._exit.set)
