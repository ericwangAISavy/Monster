"""Explicit opt-in async API. Not instantiated by Monster's GUI in Phase 1."""

import asyncio
import time
from contextlib import AsyncExitStack
from functools import wraps
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from langsmith import tracing_context

from .contracts import ApprovalDecision, Mission, TaskResult, migrate_state
from .graph import TERMINAL, MissionNodes, build_graph
from .repository import Repository
from .runner import FakeGuiSubtaskRunner


def exclusive(method):
    """Reserve a mission before the first await, including checkpoint mutations."""

    @wraps(method)
    async def wrapped(self, mission, *args, **kwargs):
        mid = mission.mission_id if isinstance(mission, Mission) else mission
        if mid in self._busy:
            raise RuntimeError("Mission already running")
        self._busy.add(mid)
        try:
            return await method(self, mission, *args, **kwargs)
        finally:
            self._busy.remove(mid)

    return wrapped


class MissionCoordinator:
    def __init__(
        self,
        directory: Path,
        runner: FakeGuiSubtaskRunner | None = None,
        *,
        clock=time.time,
        experimental_gui=False,
    ):
        # A future Qt adapter requires a separately reviewed Phase 2 change.
        self.experimental_gui = experimental_gui
        if runner is not None and type(runner) is not FakeGuiSubtaskRunner:
            if not experimental_gui:
                raise ValueError("Real runner requires explicit experimental mode; fake is default")
            from .gui_subtask_adapter import AsyncGuiSubtaskRunner

            if type(runner) is not AsyncGuiSubtaskRunner:
                raise ValueError("Only the reviewed experimental GUI adapter is permitted")
        self.directory, self.runner, self.clock = (
            Path(directory),
            runner or FakeGuiSubtaskRunner(),
            clock,
        )
        self._active: dict[str, asyncio.Task] = {}
        self._busy: set[str] = set()
        self._stack = AsyncExitStack()
        self._restart_review = set()

    async def __aenter__(self):
        try:
            self.store = self._stack.enter_context(Repository(self.directory))
            self.checkpointer = await self._stack.enter_async_context(
                AsyncSqliteSaver.from_conn_string(str(self.directory / "checkpoints.sqlite3"))
            )
            self.nodes = MissionNodes(self.store, self.runner, self.clock)
            self.graph = build_graph(self.nodes, self.checkpointer)
            if self.experimental_gui:
                self._restart_review = self.store.invalidate_unstarted_authorizations()
            # Journal is authoritative, even when the last graph checkpoint is older.
            rows = self.store.connection.execute(
                "SELECT mission_id,subtask_id FROM effects WHERE status='dispatch_started'"
            ).fetchall()
            for row in rows:
                effect = self.store.effect(row[0], row[1])
                self.store.transition(effect, "uncertain")
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *_):
        # Never close checkpoint storage while a graph still owns it.
        for mid in list(self._active):
            await self.stop(mid)
        await self._stack.aclose()

    @staticmethod
    def config(mission_id):
        return {"configurable": {"thread_id": mission_id}, "recursion_limit": 1100, "callbacks": []}

    async def _invoke(self, mission_id, value):
        if mission_id in self._active:
            raise RuntimeError("Mission already running")
        self._active[mission_id] = asyncio.current_task()
        try:
            # Tracing is disabled even if a developer has global LangSmith env settings.
            with tracing_context(enabled=False):
                await self.graph.ainvoke(value, self.config(mission_id), durability="sync")
            snapshot = await self.snapshot(mission_id)
            if snapshot["status"] in TERMINAL:
                self.store.terminal(mission_id)
            return snapshot
        finally:
            self._active.pop(mission_id, None)

    @staticmethod
    def initial(mission_id):
        return {
            "schema_version": 1,
            "mission_id": mission_id,
            "status": "created",
            "current_subtask_id": None,
            "pending_approval_id": None,
            "transitions": 0,
            "reason": None,
        }

    @exclusive
    async def start(self, mission: Mission):
        # Revalidate frozen models: model_copy is intentionally not a validation API.
        mission = Mission.model_validate(mission.model_dump())
        self.store.create(mission)
        return await self._invoke(mission.mission_id, self.initial(mission.mission_id))

    async def snapshot(self, mission_id):
        control = self.store.control(mission_id)
        state = await self.graph.aget_state(self.config(mission_id))
        value = migrate_state(state.values) if state.values else self.initial(mission_id)
        if control["cancelled"]:
            value["status"], value["reason"] = "cancelled", "cancelled"
        elif control["stop_requested"] and value["status"] not in TERMINAL:
            value["status"], value["reason"] = "paused", "stopped"
        return value

    @exclusive
    async def continue_mission(self, mission_id):
        if mission_id in self._active:
            raise RuntimeError("Mission already running")
        snapshot = await self.snapshot(mission_id)
        if snapshot["status"] in TERMINAL:
            return snapshot
        if mission_id in self._restart_review:
            self._restart_review.remove(mission_id)
            self.store.clear_stop(mission_id)
            saved = await self.graph.aget_state(self.config(mission_id))
            if not saved.values:
                return await self._invoke(mission_id, self.initial(mission_id))
            await self.graph.aupdate_state(
                self.config(mission_id),
                {"status": "running", "pending_approval_id": None, "reason": None},
                as_node="checkpoint",
            )
            return await self._invoke(mission_id, None)
        if snapshot["status"] == "waiting_approval":
            return snapshot
        self.store.clear_stop(mission_id)
        saved = await self.graph.aget_state(self.config(mission_id))
        if not saved.values:
            return await self._invoke(mission_id, self.initial(mission_id))
        if saved.next:
            # Crash in a graph node: let that node consult the ledger before proceeding.
            return await self._invoke(mission_id, None)
        await self.graph.aupdate_state(
            self.config(mission_id), {"status": "running", "reason": None}, as_node="checkpoint"
        )
        return await self._invoke(mission_id, None)

    @exclusive
    async def resume(self, mission_id, decision: ApprovalDecision):
        if mission_id in self._active:
            raise RuntimeError("Mission already running")
        if mission_id in self._restart_review:
            raise ValueError("Restart requires fresh mission continuation")
        decision = ApprovalDecision.model_validate(decision.model_dump())
        snapshot = await self.snapshot(mission_id)
        if snapshot["status"] != "waiting_approval":
            raise ValueError("Mission is not awaiting approval")
        approval = self.store.get_approval(snapshot["pending_approval_id"])
        if (decision.approval_id, decision.effect_id, decision.generation) != (
            approval.approval_id,
            approval.effect_id,
            approval.generation,
        ):
            raise ValueError("Approval scope mismatch")
        return await self._invoke(mission_id, Command(resume=decision.model_dump(mode="json")))

    async def stop(self, mission_id, *, cancel=False):
        if self.experimental_gui and hasattr(self.runner, "invalidate_all"):
            self.runner.invalidate_all(mission_id)
        snapshot = await self.snapshot(mission_id)
        if snapshot["status"] in TERMINAL:
            return snapshot
        self.store.stop(mission_id, cancel=cancel)
        task = self.nodes.running.get(mission_id)
        if task is not None:
            task.cancel()
            await self.runner.cancel(mission_id, snapshot["current_subtask_id"])
        active = self._active.get(mission_id)
        if active is not None and active is not asyncio.current_task():
            await asyncio.shield(active)
        snapshot = await self.snapshot(mission_id)
        if snapshot["status"] == "cancelled":
            self.store.terminal(mission_id)
        return snapshot

    @exclusive
    async def reconcile(self, mission_id, result: TaskResult):
        """Explicit evidence-based resolution, never a request to replay input."""
        if mission_id in self._active:
            raise RuntimeError("Mission already running")
        result = TaskResult.model_validate(result.model_dump())
        effect = self.store.effect(mission_id, result.subtask_id)
        if effect is None or effect.status != "uncertain":
            raise ValueError("No uncertain effect to reconcile")
        if result.status not in ("verified_success", "verified_failure"):
            raise ValueError("Reconciliation requires verified evidence")
        self.store.transition(
            effect,
            "observed_success" if result.status == "verified_success" else "observed_failure",
            result,
        )
        return await self.snapshot(mission_id)

    async def prune(self, *, retention_days=30):
        if type(retention_days) is not int or not 1 <= retention_days <= 365:
            raise ValueError("Retention must be 1..365 days")
        if self._busy:
            raise RuntimeError("Mission already running")
        removed = []
        for mid in self.store.prune_candidates(self.clock() - retention_days * 86400):
            # Checkpoints first: a crash can leave metadata to prune again, not live
            # checkpoint references with an absent effect journal.
            await self.checkpointer.adelete_thread(mid)
            self.store.delete_terminal(mid)
            removed.append(mid)
        return removed
