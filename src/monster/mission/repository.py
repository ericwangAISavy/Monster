"""Durable intent journal. Every transition commits before the runner may act.

The runtime lock is held for the coordinator's lifetime, including awaited graph
nodes. OS releases it on process death. It never steals an expired desktop lease.
"""

import os
import sqlite3
import time
from pathlib import Path

from .contracts import EFFECT_TRANSITIONS, Approval, Effect, Mission, TaskResult


class Repository:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.connection = None
        self._lock = None

    def __enter__(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = (self.directory / "runtime.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self._lock.seek(0)
                self._lock.write(b"0")
                self._lock.flush()
                self._lock.seek(0)
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise RuntimeError("Mission database already owned by a runtime") from None
        try:
            self.connection = sqlite3.connect(self.directory / "ledger.sqlite3")
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self._migrate()
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *_):
        if self.connection is not None:
            self.connection.close()
        if self._lock is not None:
            self._lock.close()

    def _migrate(self):
        c = self.connection
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version > 6:
            raise ValueError("Unsupported ledger schema")
        with c:
            c.execute("BEGIN IMMEDIATE")  # sqlite3 does not begin transactions for DDL.
            if version < 1:
                c.execute(
                    "CREATE TABLE missions (mission_id TEXT PRIMARY KEY, spec TEXT NOT NULL, "
                    "created_at REAL NOT NULL, terminal_at REAL)"
                )
            if version < 2:
                c.execute(
                    "ALTER TABLE missions ADD COLUMN stop_requested INTEGER NOT NULL DEFAULT 0"
                )
                c.execute("ALTER TABLE missions ADD COLUMN cancelled INTEGER NOT NULL DEFAULT 0")
                c.execute("ALTER TABLE missions ADD COLUMN generation INTEGER NOT NULL DEFAULT 1")
            c.execute(
                "CREATE TABLE IF NOT EXISTS effects (effect_id TEXT PRIMARY KEY, "
                "mission_id TEXT NOT NULL, subtask_id TEXT NOT NULL, generation INTEGER "
                "NOT NULL, status TEXT NOT NULL, target_ref TEXT NOT NULL, category TEXT "
                "NOT NULL, result TEXT, UNIQUE(mission_id, subtask_id))"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS approvals (approval_id TEXT PRIMARY KEY, "
                "mission_id TEXT NOT NULL, data TEXT NOT NULL)"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, "
                "mission_id TEXT NOT NULL, effect_id TEXT, code TEXT NOT NULL, "
                "at REAL NOT NULL, elapsed_ms INTEGER NOT NULL DEFAULT 0)"
            )
            from .effects import migrate_journal

            migrate_journal(c)
            if version < 3:
                c.execute(
                    "INSERT INTO effect_audit (effect_id,mission_id,code,at,source) "
                    "SELECT effect_id,mission_id,code,at,'legacy_v1' FROM events WHERE effect_id IS NOT NULL"
                )
            from .scheduling import migrate_scheduling

            migrate_scheduling(c)
            from .strategic.service import migrate

            migrate(c)
            from monster.workspaces.guest_service import migrate as migrate_workspaces

            migrate_workspaces(c)
            c.execute("PRAGMA user_version=6")

    def create(self, mission: Mission):
        with self.connection:
            self.connection.execute(
                "INSERT INTO missions (mission_id,spec,created_at) VALUES (?,?,?)",
                (mission.mission_id, mission.model_dump_json(), time.time()),
            )

    def mission(self, mission_id: str) -> Mission:
        row = self.control(mission_id)
        return Mission.model_validate_json(row["spec"])

    def control(self, mission_id: str):
        row = self.connection.execute(
            "SELECT * FROM missions WHERE mission_id=?", (mission_id,)
        ).fetchone()
        if row is None:
            raise KeyError("Unknown mission")
        return row

    def stop(self, mission_id: str, *, cancel=False):
        self.control(mission_id)
        with self.connection:
            self.connection.execute(
                "UPDATE missions SET stop_requested=1, cancelled=MAX(cancelled, ?), "
                "generation=generation+1 WHERE mission_id=?",
                (int(cancel), mission_id),
            )

    def clear_stop(self, mission_id: str):
        if self.control(mission_id)["cancelled"]:
            raise ValueError("Cancelled mission cannot resume")
        with self.connection:
            self.connection.execute(
                "UPDATE missions SET stop_requested=0 WHERE mission_id=?", (mission_id,)
            )

    def effect(self, mission_id: str, subtask_id: str) -> Effect | None:
        row = self.connection.execute(
            "SELECT * FROM effects WHERE mission_id=? AND subtask_id=?", (mission_id, subtask_id)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["result"] = TaskResult.model_validate_json(data["result"]) if data["result"] else None
        return Effect.model_validate(data)

    def effects(self, mission_id: str) -> list[Effect]:
        return [
            self.effect(mission_id, row[0])
            for row in self.connection.execute(
                "SELECT subtask_id FROM effects WHERE mission_id=? ORDER BY rowid", (mission_id,)
            )
        ]

    def prepare(self, effect: Effect):
        if effect.status != "prepared" or effect.result is not None:
            raise ValueError("Effect must begin prepared")
        with self.connection:
            self.connection.execute(
                "INSERT INTO effects VALUES (?,?,?,?,?,?,?,NULL)",
                (
                    effect.effect_id,
                    effect.mission_id,
                    effect.subtask_id,
                    effect.generation,
                    effect.status,
                    effect.target_ref,
                    effect.category,
                ),
            )
            self._event(effect.mission_id, effect.effect_id, "prepared")

    def transition(self, effect: Effect, status: str, result: TaskResult | None = None):
        expected = {
            "observed_success": "verified_success",
            "observed_failure": "verified_failure",
        }.get(status)
        if expected and (result is None or result.status != expected or not result.evidence_ref):
            raise ValueError("Observed outcomes require matching verified evidence")
        if status not in EFFECT_TRANSITIONS.get(effect.status, set()):
            raise ValueError("Invalid effect transition")
        if result is not None and (
            result.mission_id,
            result.subtask_id,
            result.effect_id,
            result.generation,
        ) != (effect.mission_id, effect.subtask_id, effect.effect_id, effect.generation):
            raise ValueError("Result binding mismatch")
        with self.connection:
            cur = self.connection.execute(
                "UPDATE effects SET status=?,result=? WHERE effect_id=? AND status=?",
                (
                    status,
                    result.model_dump_json() if result else None,
                    effect.effect_id,
                    effect.status,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("Stale effect transition")
            self._event(
                effect.mission_id, effect.effect_id, status, result.elapsed_ms if result else 0
            )

    def approval(self, approval: Approval):
        with self.connection:
            self.connection.execute(
                "INSERT INTO approvals VALUES (?,?,?) "
                "ON CONFLICT(approval_id) DO UPDATE SET data=excluded.data",
                (approval.approval_id, approval.mission_id, approval.model_dump_json()),
            )

    def get_approval(self, approval_id: str) -> Approval:
        row = self.connection.execute(
            "SELECT data FROM approvals WHERE approval_id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise KeyError("Unknown approval")
        return Approval.model_validate_json(row[0])

    def _event(self, mission_id, effect_id, code, elapsed_ms=0):
        self.connection.execute(
            "INSERT INTO events (mission_id,effect_id,code,at,elapsed_ms) VALUES (?,?,?,?,?)",
            (mission_id, effect_id, code, time.time(), elapsed_ms),
        )
        if effect_id:
            self.connection.execute(
                "INSERT INTO effect_audit (effect_id,mission_id,code,at,source) "
                "VALUES (?,?,?,?,'subtask')",
                (effect_id, mission_id, code, time.time()),
            )

    def events(self, mission_id):
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT code,effect_id,at,elapsed_ms FROM events WHERE mission_id=? ORDER BY id",
                (mission_id,),
            )
        ]

    def terminal(self, mission_id):
        with self.connection:
            self.connection.execute(
                "UPDATE missions SET terminal_at=? WHERE mission_id=?", (time.time(), mission_id)
            )

    def prune_candidates(self, before: float):
        from .effects import EffectCoordinator

        blocked = {e.mission_id for e in EffectCoordinator(self.connection).unresolved()}
        # Never discard live/paused missions or unresolved external outcomes.
        return [
            r[0]
            for r in self.connection.execute(
                "SELECT mission_id FROM missions WHERE terminal_at < ? AND NOT EXISTS "
                "(SELECT 1 FROM effects e WHERE e.mission_id=missions.mission_id "
                "AND e.status IN ('dispatch_started','uncertain'))",
                (before,),
            )
            if r[0] not in blocked
        ]

    def delete_terminal(self, mission_id):
        if mission_id not in self.prune_candidates(float("inf")):
            raise ValueError("Mission has live or unresolved state")
        with self.connection:
            for table in (
                "guest_control_challenges",
                "guest_mission_bindings",
                "strategic_evaluations",
                "strategic_blockers",
                "strategic_calls",
                "strategic_revisions",
                "strategic_sessions",
                "mission_progress",
                "mission_reservations",
                "mission_schedules",
                "events",
                "approvals",
                "effects",
                "missions",
            ):
                self.connection.execute(f"DELETE FROM {table} WHERE mission_id=?", (mission_id,))

    def metrics(self, mission_id):
        events = self.events(mission_id)
        return {
            "dispatches": sum(e["code"] == "dispatch_started" for e in events),
            "verified": sum(e["code"] == "observed_success" for e in events),
            "uncertain": sum(e["code"] == "uncertain" for e in events),
            "runner_elapsed_ms": sum(e["elapsed_ms"] for e in events),
        }

    def invalidate_unstarted_authorizations(self):
        """Real-runtime restart revokes old grants, never resets a dispatched effect."""
        mids = {
            r[0]
            for r in self.connection.execute(
                "SELECT mission_id FROM missions WHERE terminal_at IS NULL AND cancelled=0"
            )
        }
        with self.connection:
            for mid in mids:
                self.connection.execute(
                    "UPDATE missions SET generation=generation+1 WHERE mission_id=?", (mid,)
                )
                generation = self.control(mid)["generation"]
                self.connection.execute(
                    "UPDATE effects SET status='prepared',generation=? "
                    "WHERE mission_id=? AND status IN ('prepared','approved')",
                    (generation, mid),
                )
                rows = self.connection.execute(
                    "SELECT data FROM approvals WHERE mission_id=?", (mid,)
                ).fetchall()
                for row in rows:
                    approval = Approval.model_validate_json(row[0])
                    if approval.decision in ("pending", "approved"):
                        expired = approval.model_copy(update={"decision": "expired"})
                        self.connection.execute(
                            "UPDATE approvals SET data=? WHERE approval_id=?",
                            (expired.model_dump_json(), approval.approval_id),
                        )
        return mids
