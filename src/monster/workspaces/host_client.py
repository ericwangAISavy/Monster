"""Host projection and human control only. No mission journal or input imports."""

import asyncio
import time
from collections import deque

from .contracts import (
    ApprovalResponse,
    Envelope,
    Heartbeat,
    Hello,
    Pause,
    PreviewRequest,
    Reconcile,
    Reply,
    ResumeMission,
    StartMission,
    StatusRequest,
    Stop,
    new_id,
)
from .protocol import LinkError, open_frame, seal


class HostWorkspaceClient:
    def __init__(self, transport, grant, binding, *, clock=time.time):
        self.transport, self.grant, self.binding, self.clock = transport, grant, binding, clock
        self.sequence = 0
        self.last_event = 0
        self.status = None
        self.challenge = None
        self.preview = None
        self.connected = False
        self.stop_state = "not_requested"
        self.audit = deque(maxlen=64)
        self.inflight = 0

    async def request(self, command, *, priority=False):
        if self.inflight >= 4 and not priority:
            raise LinkError("backpressure")
        self.inflight += 1
        self.sequence += 1
        now = self.clock()
        request = Envelope(
            request_id=new_id(),
            binding=self.binding,
            sequence=self.sequence,
            issued_at=now,
            expires_at=now + 3,
            command=command,
        )
        try:
            async with asyncio.timeout(1 if priority else 3):
                raw = await self.transport.exchange(seal(request, self.grant), priority=priority)
            reply = open_frame(raw, self.grant, Reply, self.clock())
            if self.clock() >= request.expires_at or self.clock() < request.issued_at:
                raise LinkError("reply_expired")
            if (
                reply.request_id != request.request_id
                or reply.binding.workspace_id != self.binding.workspace_id
                or reply.binding.guest_instance_id != self.binding.guest_instance_id
            ):
                raise LinkError("wrong_reply")
            if reply.event_sequence <= self.last_event:
                raise LinkError("stale_event")
            if reply.binding != self.binding:
                if command.op != "HELLO":
                    raise LinkError("stale_binding")
                self.binding = reply.binding
                self.challenge = None
            self.last_event = reply.event_sequence
            self.status = reply.status
            self.challenge = reply.challenge
            self.connected = True
            if reply.preview:
                if (
                    command.op != "PREVIEW_REQUEST"
                    or reply.preview.binding != self.binding
                    or self.clock() - reply.preview.captured_at > 3
                ):
                    raise LinkError("unsolicited_preview")
                self.preview = reply.preview
            if reply.result == "stop_ack":
                self.stop_state = (
                    "confirmed" if reply.status.cleanup == "confirmed" else "cleanup_unknown"
                )
            elif (
                command.op in ("START_MISSION", "RESUME_MISSION", "APPROVAL_RESPONSE")
                and reply.result in ("challenge", "accepted")
                and reply.error == "none"
            ):
                # A prior stop acknowledged a prior execution cycle, not this one.
                self.stop_state = "not_requested"
            self.audit.append({"operation": command.op, "result": reply.result})
            return reply
        except Exception:  # noqa: BLE001 -- arbitrary transport exceptions may contain secrets.
            self.connected = False
            self.challenge = None
            self.preview = None
            self.audit.append({"operation": command.op, "result": "unconfirmed"})
            if command.op == "STOP_IMMEDIATE":
                self.stop_state = "unconfirmed"
            raise LinkError("delivery_or_authentication_unconfirmed") from None
        finally:
            self.inflight -= 1

    async def connect(self):
        return await self.request(Hello())

    async def heartbeat(self):
        # Heartbeats must not clear a pending human review.
        challenge = self.challenge
        result = await self.request(Heartbeat())
        if challenge and challenge.binding == self.binding and challenge.expires_at > self.clock():
            self.challenge = challenge
        return result

    async def start(self):
        return await self.request(StartMission())

    async def resume(self, mission_id):
        return await self.request(ResumeMission(mission_id=mission_id))

    async def approve(self, *, human_choice):
        challenge = self.challenge
        if (
            human_choice not in ("approve", "deny")
            or not challenge
            or challenge.expires_at <= self.clock()
        ):
            raise LinkError("human_review_required")
        self.challenge = None
        return await self.request(
            ApprovalResponse(
                challenge_id=challenge.challenge_id,
                mission_id=challenge.mission_id,
                fingerprint=challenge.fingerprint,
                observation_id=challenge.observation_id,
                decision=human_choice,
            )
        )

    async def stop(self):
        self.stop_state = "pending"
        self.challenge = None
        self.preview = None
        return await self.request(Stop(), priority=True)

    async def pause(self):
        self.challenge = None
        return await self.request(Pause(), priority=True)

    async def reconcile(self, mission_id):
        return await self.request(Reconcile(mission_id=mission_id))

    async def request_preview(self, *, consent):
        if consent is not True:
            raise LinkError("preview_consent_required")
        return await self.request(PreviewRequest(consent=True))

    async def refresh(self):
        challenge = self.challenge
        result = await self.request(StatusRequest())
        if challenge and challenge.binding == self.binding and challenge.expires_at > self.clock():
            self.challenge = challenge
        return result

    def take_preview(self):
        frame, self.preview = self.preview, None
        return frame

    def projection(self):
        # A stale cached completed/stop state must never masquerade as current health.
        if not self.connected:
            text = (
                "Stop unconfirmed"
                if self.stop_state in ("pending", "unconfirmed")
                else "Guest offline / network disconnected"
            )
        elif self.stop_state == "cleanup_unknown" or (
            self.status and self.status.cleanup == "unknown"
        ):
            text = "Unknown cleanup; inspect VM before reuse"
        elif self.status and self.status.reason in (
            "session_changed",
            "display_changed",
            "provider_changed",
            "restored",
            "clock_changed",
        ):
            text = "Guest session unavailable; requalification required"
        elif self.stop_state == "confirmed":
            text = (
                "Stop confirmed by simulated guest; unresolved action requires reconciliation"
                if self.status and self.status.unresolved
                else "Stop confirmed by simulated guest"
            )
        elif self.challenge:
            text = "Waiting for your approval · simulation"
        elif self.status and self.status.unresolved:
            text = "Unresolved action; reconciliation required"
        elif self.status and self.status.state == "paused":
            text = "Paused · simulated guest disarmed"
        else:
            text = "Connected · simulation / observe only"
        return {
            "message": text,
            "stale": not self.connected,
            "stop": self.stop_state,
            "provenance": "simulated",
            "workspace_id": self.binding.workspace_id,
            "status": self.status.model_dump(mode="json") if self.status else None,
        }
