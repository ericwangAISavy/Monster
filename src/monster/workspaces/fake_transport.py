"""Deterministic in-memory fault link. Cannot open sockets or execute native input."""

import asyncio

from .protocol import LinkError


class FakeTransport:
    def __init__(self, guest, grant):
        self.guest, self.grant = guest, grant
        self.connected = True
        self.fault = None
        self.last_request = None
        self.last_response = None
        self.delayed = asyncio.Event()
        self.frames_sent = 0

    async def exchange(self, raw, *, priority=False):
        if self.fault == "RECONNECT":
            self.connected = True
        if not self.connected:
            raise LinkError("offline")
        self.frames_sent += 1
        fault, self.fault = self.fault, None
        self.last_request = raw
        if fault in (
            "SESSION_LOCK",
            "DISPLAY_CHANGE",
            "SUSPEND_VM",
            "RESTORE_SNAPSHOT",
            "CLEANUP_UNKNOWN",
            "WORKER_CRASH",
            "BLACK_CAPTURE",
            "PROVIDER_CHANGE",
            "TAKEOVER",
        ):
            await self.guest.session_event(fault)
        if fault in ("ROTATE_IDENTITY", "GUEST_REBOOT"):
            await self.guest.session_event("RESTORE_SNAPSHOT")
        if fault == "HOST_RESTART":
            self.disconnect()
            raise LinkError("offline")
        if fault == "RECONNECT":
            self.reconnect()
        if fault == "CRASH_BEFORE_DISPATCH":
            self.guest.fault = fault
        if fault in ("CRASH_AFTER_INTENT", "CRASH_AFTER_RECEIPT"):
            self.guest.runner.outcomes["pilot-a"] = (
                "crash" if fault == "CRASH_AFTER_INTENT" else "receipt_only"
            )
        if fault == "DROP":
            raise LinkError("delivery_unconfirmed")
        if fault == "DELAY" and not priority:
            await self.delayed.wait()
        if fault == "CORRUPT":
            raw = raw[:-8] + b"corrupt"
        if fault == "REPLAY":
            if self.last_response is None:
                raise LinkError("no_response")
            return self.last_response
        if fault == "DISCONNECT":
            self.connected = False
            raise LinkError("offline")
        response = await self.guest.receive(raw, self.grant)
        if fault == "DUPLICATE":
            # Mutations reject the second delivery instead of executing again.
            try:
                await self.guest.receive(raw, self.grant)
            except LinkError:
                pass
        if fault in ("DROP_ACK", "REORDER"):
            raise LinkError("delivery_unconfirmed")
        self.last_response = response
        return response

    def disconnect(self):
        # Drop traffic without notifying guest: the watchdog must discover silence.
        self.connected = False

    def reconnect(self):
        self.connected = True  # restores transport only, never execution consent
