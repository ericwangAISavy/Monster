"""Guest-owned portable service over the existing coordinator; no input/network API.

Every handler is a fixed control operation. Only the existing fake runner is
constructed here. Native execution cannot be enabled with a remote message.
"""

import asyncio
import base64
import hashlib
import math
import sqlite3
import struct
import time
import zlib
from collections import OrderedDict

from monster.mission.coordinator import MissionCoordinator
from monster.mission.plan_templates import editor_plan
from monster.mission.runner import FakeGuiSubtaskRunner
from monster.mission.scheduling import canonical_digest

from .contracts import (
    ApprovalChallenge,
    DisplayRect,
    Envelope,
    GuestCapabilities,
    GuestStatus,
    PreviewFrame,
    Reply,
    SessionBinding,
    new_id,
)
from .pairing import SimulatedPairing
from .protocol import LinkError, open_frame, seal


def migrate(c):
    c.execute(
        "CREATE TABLE IF NOT EXISTS guest_workspace_sessions ("
        "workspace_id TEXT PRIMARY KEY, generation INTEGER NOT NULL)"
    )
    if "quarantined" not in {
        r[1] for r in c.execute("PRAGMA table_info(guest_workspace_sessions)")
    }:
        c.execute(
            "ALTER TABLE guest_workspace_sessions ADD COLUMN quarantined INTEGER NOT NULL DEFAULT 0"
        )
    c.execute(
        "CREATE TABLE IF NOT EXISTS guest_mission_bindings ("
        "mission_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, "
        "guest_instance_id TEXT NOT NULL, binding TEXT NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS guest_control_requests ("
        "request_id TEXT PRIMARY KEY, grant_id TEXT NOT NULL, sequence INTEGER NOT NULL, "
        "operation TEXT NOT NULL, fingerprint TEXT NOT NULL, UNIQUE(grant_id,sequence))"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS guest_control_challenges ("
        "challenge_id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, payload TEXT NOT NULL, "
        "consumed INTEGER NOT NULL DEFAULT 0)"
    )


class GuestService:
    def __init__(
        self, directory, descriptor, *, runner=None, clock=time.time, heartbeat_timeout=5.0
    ):
        if not 2 <= heartbeat_timeout <= 10:
            raise ValueError("invalid_watchdog_limit")
        self.directory, self.descriptor, self.clock = directory, descriptor, clock
        self.runner = runner or FakeGuiSubtaskRunner()
        if type(self.runner) is not FakeGuiSubtaskRunner:
            raise ValueError("portable_fake_runner_required")
        self.heartbeat_timeout = heartbeat_timeout
        self.binding = None
        self.coordinator = None
        self.mission_id = None
        self.challenge = None
        self._mission_task = None
        self._busy = False
        self._cache = OrderedDict()
        self._event_sequence = 0
        self.last_authenticated = self.last_tick = self.clock()
        self.connected = False
        self.session_ready = True
        self.capture_ready = True  # synthetic fixture only
        self.cleanup_unknown = False
        self.reason = "ready"
        self.audit = []  # bounded allowlisted categories only, never frames/content
        self.fault = None
        self.active_binding = None

    async def __aenter__(self):
        self.coordinator = await MissionCoordinator(
            self.directory, self.runner, clock=self.clock
        ).__aenter__()
        try:
            c = self.coordinator.store.connection
            workspace_id = self.descriptor.workspace_id
            with c:
                c.execute(
                    "INSERT INTO guest_workspace_sessions(workspace_id,generation) VALUES (?,1) "
                    "ON CONFLICT(workspace_id) DO UPDATE SET generation=generation+1",
                    (workspace_id,),
                )
                generation = c.execute(
                    "SELECT generation FROM guest_workspace_sessions WHERE workspace_id=?",
                    (workspace_id,),
                ).fetchone()[0]
                self.cleanup_unknown = bool(
                    c.execute(
                        "SELECT quarantined FROM guest_workspace_sessions WHERE workspace_id=?",
                        (workspace_id,),
                    ).fetchone()[0]
                )
                c.execute("UPDATE guest_control_challenges SET consumed=1")
            self.binding = SessionBinding(
                workspace_id=workspace_id,
                guest_instance_id=new_id(),
                generation=generation,
                session_ref=new_id(),
                display_digest="1" * 64,
                provider_digest="2" * 64,
            )
            self.pairing = SimulatedPairing(self.binding, clock=self.clock)
            self.coordinator.scheduler.workspace_guard = self._execution_guard
            old = c.execute(
                "SELECT m.mission_id FROM missions m JOIN guest_mission_bindings g "
                "ON m.mission_id=g.mission_id WHERE g.workspace_id=? "
                "AND m.terminal_at IS NULL AND m.cancelled=0 ORDER BY m.created_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
            if old:
                self.mission_id = old[0]
                self.reason = "consent_required"
            # Existing histories stay in the guest. No mission starts on boot.
            self.log("boot_disarmed")
        except BaseException:
            await self.coordinator.__aexit__()
            raise
        return self

    async def __aexit__(self, *_):
        try:
            self.pairing.revoke()
            await self.disarm("link_lost", rotate=False)
            if self._mission_task:
                await asyncio.gather(self._mission_task, return_exceptions=True)
        finally:
            await self.coordinator.__aexit__()
            self.coordinator = None

    def log(self, code):
        self.audit.append({"category": code, "generation": self.binding.generation})
        self.audit[:] = self.audit[-64:]

    def _unresolved(self):
        c = self.coordinator
        return bool(
            c.effects.unresolved()
            or c.store.connection.execute(
                "SELECT 1 FROM effects WHERE status IN ('dispatch_started','uncertain') LIMIT 1"
            ).fetchone()
        )

    def _mission_bound(self, mid):
        row = self.coordinator.store.connection.execute(
            "SELECT * FROM guest_mission_bindings WHERE mission_id=?", (mid,)
        ).fetchone()
        if row is None or row["workspace_id"] != self.binding.workspace_id:
            raise LinkError("cross_workspace_mission")
        # Resume is allowed to rebind session identity only after review, never copy a mission.
        return row

    async def status(self):
        c = self.coordinator
        state = "idle"
        verified = inputs = models = 0
        if self.mission_id:
            snapshot = await c.snapshot(self.mission_id)
            state = snapshot["status"]
            if state == "failed":
                state = "partially_complete"
            if state == "created":
                state = "idle"
            progress = c.scheduler.progress(self.mission_id)
            verified = len(progress.verified_subtask_ids)
            usage = progress.usage
            inputs, models = usage.input_charged, usage.models_charged
        if self.challenge:
            state = "waiting_approval"
        active = self._mission_task is not None and not self._mission_task.done()
        if state == "running" and not active:
            state = "paused"
        cleanup = "unknown" if self.cleanup_unknown else "active" if active else "confirmed"
        if self.cleanup_unknown:
            state = "quarantined"
        unresolved = self._unresolved()
        reason = "reconciliation_required" if unresolved else self.reason
        if not self.session_ready and state not in ("quarantined", "completed"):
            state = "paused"
        return GuestStatus(
            binding=self.binding,
            state=state,
            reason=reason,
            mission_id=self.mission_id,
            verified_stages=verified,
            cleanup=cleanup,
            unresolved=unresolved,
            input_charged=inputs,
            models_charged=models,
        )

    async def _reply(self, request, result, *, challenge=None, preview=None, error="none"):
        self._event_sequence += 1
        capabilities = (
            GuestCapabilities(
                binding=self.binding, capture_available=self.capture_ready and self.session_ready
            )
            if result == "capabilities"
            else None
        )
        return Reply(
            request_id=request.request_id,
            binding=self.binding,
            event_sequence=self._event_sequence,
            result=result,
            status=await self.status(),
            challenge=challenge,
            preview=preview,
            capabilities=capabilities,
            error=error,
        )

    def _reserve_request(self, request, grant):
        c = self.coordinator.store.connection
        fingerprint = canonical_digest(request.model_dump(mode="json"))
        previous = c.execute(
            "SELECT * FROM guest_control_requests WHERE request_id=?", (request.request_id,)
        ).fetchone()
        if previous:
            if previous["grant_id"] != grant.grant_id:
                raise LinkError("replayed")
            if previous["fingerprint"] == fingerprint and request.command.op in (
                "HELLO",
                "HEARTBEAT",
                "STATUS",
            ):
                cached = self._cache.get(request.request_id)
                if cached:
                    return cached
            raise LinkError("replayed")
        sequence = c.execute(
            "SELECT COALESCE(MAX(sequence),0),COUNT(*) FROM guest_control_requests WHERE grant_id=?",
            (grant.grant_id,),
        ).fetchone()
        limit = 264 if request.command.op == "STOP_IMMEDIATE" else 256
        total = c.execute("SELECT COUNT(*) FROM guest_control_requests").fetchone()[0]
        if total >= (8200 if request.command.op == "STOP_IMMEDIATE" else 8192):
            raise LinkError("journal_request_limit")
        if request.sequence <= sequence[0] or sequence[1] >= limit:
            raise LinkError("replayed_or_request_limit")
        with c:
            c.execute(
                "INSERT INTO guest_control_requests VALUES (?,?,?,?,?)",
                (
                    request.request_id,
                    grant.grant_id,
                    request.sequence,
                    request.command.op,
                    fingerprint,
                ),
            )
        return None

    async def receive(self, raw, grant):
        await self.tick()
        now = self.clock()
        request = open_frame(raw, grant, Envelope, now)
        if (
            grant.workspace_id != self.binding.workspace_id
            or grant.guest_instance_id != self.binding.guest_instance_id
            or self.pairing.grant is not grant
            or grant.host_fingerprint != self.pairing.host_fingerprint
            or grant.guest_fingerprint != self.pairing.guest_fingerprint
        ):
            raise LinkError("peer_identity_changed")
        if request.issued_at > now or request.expires_at <= now:
            raise LinkError("expired")
        # A signed hello may discover a newer *session* generation, never a new instance.
        hello_refresh = (
            request.command.op == "HELLO"
            and request.binding.workspace_id == self.binding.workspace_id
            and request.binding.guest_instance_id == self.binding.guest_instance_id
        )
        if request.binding != self.binding and not hello_refresh:
            raise LinkError("stale_binding")
        cached = self._reserve_request(request, grant)
        if cached:
            return cached
        self.connected = True
        self.last_authenticated = now
        op = request.command.op
        try:
            if op in ("HELLO", "HEARTBEAT", "STATUS"):
                response = await self._reply(request, "capabilities" if op == "HELLO" else "status")
            elif op == "STOP_IMMEDIATE":
                await self.disarm("stopped", rotate=False)
                response = await self._reply(request, "stop_ack")
            elif op == "PAUSE_AFTER_STAGE":
                self.challenge = None
                self._revoke_challenges()
                if self.mission_id:
                    await self.coordinator.request_pause(self.mission_id)
                response = await self._reply(request, "status")
            elif op == "START_MISSION":
                response = await self._start(request)
            elif op == "RESUME_MISSION":
                response = await self._resume(request, request.command.mission_id)
            elif op == "APPROVAL_RESPONSE":
                response = await self._approve(request)
            elif op == "RECONCILE_REQUEST":
                self._mission_bound(request.command.mission_id)
                response = await self._reply(request, "reconciliation")
            elif op == "PREVIEW_REQUEST":
                response = await self._preview(request)
            else:
                raise LinkError("invalid_request")
        except (ValueError, RuntimeError, OSError, sqlite3.Error):
            # Errors are not responses from target applications, nor model instructions.
            # No arbitrary exception text is retained or sent to the host.
            self.log("request_refused")
            response = await self._reply(request, "error", error="unavailable")
        encoded = seal(response, grant)
        if op in ("HELLO", "HEARTBEAT", "STATUS"):
            self._cache[request.request_id] = encoded
            while len(self._cache) > 32:
                self._cache.popitem(last=False)
        return encoded

    def _safe_to_start(self):
        if (
            self.cleanup_unknown
            or not self.session_ready
            or not self.capture_ready
            or not self.connected
            or self._unresolved()
        ):
            raise LinkError("guest_unavailable_or_reconciliation")
        if self._busy or (self._mission_task and not self._mission_task.done()):
            raise LinkError("guest_busy")

    async def _start(self, request):
        self._safe_to_start()
        if self.fault == "CRASH_BEFORE_DISPATCH":
            self.fault = None
            raise RuntimeError("injected")
        if self.mission_id and (await self.coordinator.snapshot(self.mission_id))["status"] not in (
            "completed",
            "partially_complete",
            "cancelled",
            "failed",
        ):
            raise LinkError("active_mission_exists")
        self._busy = True
        epoch = self.binding
        try:
            mid = new_id()
            plan = editor_plan(mission_id=mid, clock=self.clock)
            plan = plan.model_copy(
                update={
                    "mission": plan.mission.model_copy(
                        update={"workspace_id": self.binding.workspace_id}
                    )
                }
            )
            with self.coordinator.store.connection:
                self.coordinator.store.connection.execute(
                    "INSERT INTO guest_mission_bindings VALUES (?,?,?,?)",
                    (
                        mid,
                        self.binding.workspace_id,
                        self.binding.guest_instance_id,
                        self.binding.model_dump_json(),
                    ),
                )
            self.mission_id = mid
            await self.coordinator.start_scheduled(plan)
            if epoch != self.binding or not self.connected:
                await self.disarm("session_changed", rotate=False)
                raise LinkError("stale_session")
            challenge = self._challenge(mid)
            return await self._reply(request, "challenge", challenge=challenge)
        finally:
            self._busy = False

    def _challenge(self, mid):
        consent = self.coordinator.scheduler.consent_request(mid)
        now = self.clock()
        challenge = ApprovalChallenge(
            challenge_id=new_id(),
            binding=self.binding,
            mission_id=mid,
            effect_id=consent.plan_digest,
            fingerprint=canonical_digest(
                {
                    "consent": consent.model_dump(mode="json"),
                    "binding": self.binding.model_dump(mode="json"),
                }
            ),
            observation_id=new_id(),
            scene_digest=self.binding.display_digest,
            expires_at=min(now + 30, consent.expires_at),
        )
        with self.coordinator.store.connection:
            self._revoke_challenges(commit=False)
            self.coordinator.store.connection.execute(
                "INSERT INTO guest_control_challenges VALUES (?,?,?,0)",
                (challenge.challenge_id, mid, challenge.model_dump_json()),
            )
        self.challenge = challenge
        return challenge

    async def _approve(self, request):
        command, challenge = request.command, self.challenge
        self._safe_to_start()
        if (
            challenge is None
            or command.challenge_id != challenge.challenge_id
            or command.mission_id != challenge.mission_id
            or command.fingerprint != challenge.fingerprint
            or command.observation_id != challenge.observation_id
            or challenge.binding != self.binding
            or challenge.scene_digest != self.binding.display_digest
            or challenge.expires_at <= self.clock()
        ):
            raise LinkError("invalid_challenge")
        self._mission_bound(command.mission_id)
        with self.coordinator.store.connection:
            changed = self.coordinator.store.connection.execute(
                "UPDATE guest_control_challenges SET consumed=1 WHERE challenge_id=? AND consumed=0",
                (challenge.challenge_id,),
            ).rowcount
            if changed != 1:
                raise LinkError("consumed_challenge")
        self.challenge = None
        if command.decision == "deny":
            await self.disarm("denied", rotate=False)
            return await self._reply(request, "status")
        consent = self.coordinator.scheduler.consent_request(self.mission_id)
        fingerprint = canonical_digest(
            {
                "consent": consent.model_dump(mode="json"),
                "binding": self.binding.model_dump(mode="json"),
            }
        )
        if fingerprint != challenge.fingerprint:
            raise LinkError("consent_changed")
        self._mission_task = asyncio.create_task(self._execute(consent))
        self.active_binding = self.binding
        return await self._reply(request, "accepted")

    async def _execute(self, consent):
        try:
            await self.coordinator.consent_and_continue(self.mission_id, consent)
            self.reason = (
                "verified"
                if (await self.coordinator.snapshot(self.mission_id))["status"] == "completed"
                else "reconciliation_required"
            )
        except BaseException:  # noqa: BLE001 -- includes deliberate hard-crash simulation; redact errors.
            self.reason = "reconciliation_required"
            self.log("mission_interrupted")

    async def _resume(self, request, mid):
        self._safe_to_start()
        self._mission_bound(mid)
        self.mission_id = mid
        state = await self.coordinator.continue_mission(mid)
        if state["status"] in ("completed", "partially_complete", "cancelled", "failed"):
            return await self._reply(request, "status")
        return await self._reply(request, "challenge", challenge=self._challenge(mid))

    def _revoke_challenges(self, *, commit=True):
        c = self.coordinator.store.connection
        if commit:
            with c:
                c.execute("UPDATE guest_control_challenges SET consumed=1 WHERE consumed=0")
        else:
            c.execute("UPDATE guest_control_challenges SET consumed=1 WHERE consumed=0")

    async def disarm(self, reason, *, rotate=True):
        self.active_binding = None
        self.connected = False
        self.reason = reason
        self.challenge = None
        self._revoke_challenges()
        if self.mission_id:
            try:
                await self.coordinator.stop(self.mission_id)
            except (ValueError, RuntimeError, KeyError, sqlite3.Error):
                self.cleanup_unknown = True
        if self._mission_task and not self._mission_task.done():
            await asyncio.gather(self._mission_task, return_exceptions=True)
        if self.cleanup_unknown:
            with self.coordinator.store.connection:
                self.coordinator.store.connection.execute(
                    "UPDATE guest_workspace_sessions SET quarantined=1 WHERE workspace_id=?",
                    (self.binding.workspace_id,),
                )
        if rotate:
            self.binding = self.binding.model_copy(
                update={"generation": self.binding.generation + 1, "session_ref": new_id()}
            )
            with self.coordinator.store.connection:
                self.coordinator.store.connection.execute(
                    "UPDATE guest_workspace_sessions SET generation=? WHERE workspace_id=?",
                    (self.binding.generation, self.binding.workspace_id),
                )
        self.log("guest_disarmed" if not self.cleanup_unknown else "cleanup_unknown")

    def _execution_guard(self, mid):
        if not math.isfinite(self.clock()) or self.clock() < self.last_authenticated:
            return "guest_clock_unavailable"
        if not self.connected or self.clock() - self.last_authenticated >= self.heartbeat_timeout:
            return "guest_link_unavailable"
        if self.cleanup_unknown or not self.session_ready or not self.capture_ready:
            return "guest_session_unavailable"
        if self.active_binding != self.binding:
            return "guest_consent_required"
        if (
            not self.pairing.grant
            or self.pairing.grant.revoked
            or self.pairing.grant.expires_at <= self.clock()
        ):
            return "guest_pairing_required"
        try:
            self._mission_bound(mid)
        except LinkError:
            return "guest_workspace_mismatch"
        return None

    async def tick(self):
        now = self.clock()
        if not math.isfinite(now) or now < self.last_tick or now - self.last_tick > 30:
            await self.disarm("clock_changed")
        elif self.connected and now - self.last_authenticated >= self.heartbeat_timeout:
            await self.disarm("link_lost")
        self.last_tick = now
        if self.challenge and self.challenge.expires_at <= now:
            self.challenge = None
            self._revoke_challenges()
        grant = self.pairing.grant
        if self.connected and (grant is None or grant.revoked or grant.expires_at <= now):
            await self.disarm("expired")

    async def session_event(self, kind):
        reasons = {
            "SESSION_LOCK": "session_changed",
            "DISPLAY_CHANGE": "display_changed",
            "PROVIDER_CHANGE": "provider_changed",
            "SUSPEND_VM": "clock_changed",
            "RESTORE_SNAPSHOT": "restored",
            "CLEANUP_UNKNOWN": "worker_failed",
            "WORKER_CRASH": "worker_failed",
            "SECURE_DESKTOP": "session_changed",
            "BLACK_CAPTURE": "display_changed",
            "TAKEOVER": "session_changed",
        }
        if kind not in reasons:
            raise ValueError("unknown_session_event")
        if kind in ("CLEANUP_UNKNOWN", "WORKER_CRASH"):
            self.cleanup_unknown = True
        if kind in ("SESSION_LOCK", "SECURE_DESKTOP", "TAKEOVER", "SUSPEND_VM", "RESTORE_SNAPSHOT"):
            self.session_ready = False
        if kind == "BLACK_CAPTURE":
            self.capture_ready = False
        await self.disarm(reasons[kind])
        if kind in ("DISPLAY_CHANGE", "BLACK_CAPTURE"):
            self.binding = self.binding.model_copy(
                update={"display_digest": hashlib.sha256(new_id().encode()).hexdigest()}
            )
        if kind == "PROVIDER_CHANGE":
            self.binding = self.binding.model_copy(
                update={"provider_digest": hashlib.sha256(new_id().encode()).hexdigest()}
            )
        if kind == "RESTORE_SNAPSHOT":
            self.binding = self.binding.model_copy(update={"guest_instance_id": new_id()})
            self.pairing.rotate(self.binding)

    async def _preview(self, request):
        if not self.session_ready or not self.capture_ready or self.cleanup_unknown:
            raise LinkError("preview_unavailable")
        # Synthetic pixels, generated locally. Never a host VMConnect screenshot.
        width, height = 96, 32

        def chunk(name, data):
            return (
                struct.pack(">I", len(data))
                + name
                + data
                + struct.pack(">I", zlib.crc32(name + data))
            )

        rows = b"".join(b"\x00" + bytes((40, 160, 190)) * width for _ in range(height))
        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b"")
        )
        preview = PreviewFrame(
            binding=self.binding,
            frame_id=new_id(),
            captured_at=self.clock(),
            source_bounds=DisplayRect(x=-1920, y=0, width=1920, height=1080),
            width=width,
            height=height,
            png_base64=base64.b64encode(png).decode("ascii"),
        )
        return await self._reply(request, "preview", preview=preview)
