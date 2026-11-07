"""Qt host facade. Fake guest DB and async transport stay on the worker thread."""

import asyncio
import threading

from PySide6.QtCore import QObject, Signal

from .fake_transport import FakeTransport
from .guest_service import GuestService
from .host_client import HostWorkspaceClient


class WorkspaceRuntime(QObject):
    changed = Signal(object)
    review_requested = Signal(object)
    pairing_offer = Signal(object)
    preview_ready = Signal(object)
    retired = Signal()

    def __init__(self, directory, descriptor, *, parent=None):
        super().__init__(parent)
        self.directory, self.descriptor = directory, descriptor
        self.client = self.guest = self.loop = None
        self.closed = False
        self.engaged = False
        self.last_projection = {
            "message": "Guest simulation not paired",
            "stale": True,
            "stop": "not_requested",
            "provenance": "simulated",
            "status": None,
        }
        self.thread = threading.Thread(target=self._run, daemon=True, name="monster-fake-guest")
        self._jobs = set()
        self._stop_latched = threading.Event()

    def start(self):
        self.thread.start()

    def _run(self):
        asyncio.run(self._main())

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self._exit = asyncio.Event()
        try:
            async with GuestService(self.directory, self.descriptor) as guest:
                self.guest = guest
                if self.closed:
                    return
                last_heartbeat = 0.0
                while not self._exit.is_set():
                    await guest.tick()
                    if (
                        self.client
                        and self.client.connected
                        and self.loop.time() - last_heartbeat >= 1
                    ):
                        last_heartbeat = self.loop.time()
                        try:
                            await self.client.heartbeat()
                        except ValueError:
                            pass  # fixed unconfirmed projection; guest watchdog remains authoritative
                    self._emit()
                    try:
                        await asyncio.wait_for(self._exit.wait(), 0.25)
                    except TimeoutError:
                        pass
                self.client and self.client.transport.disconnect()
                await guest.disarm("link_lost")
                for job in list(self._jobs):
                    job.cancel()
                await asyncio.gather(*self._jobs, return_exceptions=True)
        except Exception:  # noqa: BLE001 -- cross-thread errors must never carry secrets to Qt.
            self.changed.emit(
                {
                    "message": "Guest simulation unavailable · no native input",
                    "stale": True,
                    "stop": "unconfirmed",
                    "provenance": "simulated",
                    "status": None,
                }
            )
        finally:
            self.client = self.guest = self.loop = None
            self.engaged = False
            self.retired.emit()

    def _emit(self):
        if self.closed:
            return
        if self.client:
            projection = self.client.projection()
            self.last_projection = projection
            status = projection["status"]
            self.engaged = bool(status and status["state"] in ("running", "waiting_approval"))
            self.changed.emit(projection)

    def _schedule(self, operation, *, priority=False):
        loop = self.loop
        if self.closed or loop is None:
            return

        async def execute():
            try:
                await operation()
            except Exception:  # noqa: BLE001 -- no raw frame or arbitrary transport error in UI.
                if self.client:
                    self.client.connected = False
                    self.client.challenge = None
                self.changed.emit(
                    {
                        "message": "Guest request unconfirmed; inspect workspace status",
                        "stale": True,
                        "stop": "unconfirmed",
                        "provenance": "simulated",
                        "status": None,
                    }
                )
            finally:
                self._emit()

        def launch():
            if not self.closed and (priority or len(self._jobs) < 4):
                job = asyncio.create_task(execute())
                self._jobs.add(job)
                job.add_done_callback(self._jobs.discard)

        try:
            loop.call_soon_threadsafe(launch)
        except RuntimeError:
            pass

    def begin_pairing(self):
        async def begin():
            if self.guest:
                if (
                    self.client
                    and not self.client.grant.revoked
                    and self.client.grant.expires_at > self.guest.clock()
                ):
                    self.client.transport.reconnect()
                    await self.client.connect()
                    return
                self.pairing_offer.emit(self.guest.pairing.begin())

        self._schedule(begin)

    def confirm_pairing(self, offer):
        async def confirm():
            if self.guest:
                grant = self.guest.pairing.confirm(
                    offer.offer_id,
                    code=offer.code,
                    host_fingerprint=offer.host_fingerprint,
                    guest_fingerprint=offer.guest_fingerprint,
                    human_verified=True,
                )
                self.client = HostWorkspaceClient(
                    FakeTransport(self.guest, grant), grant, self.guest.binding
                )
                await self.client.connect()
                self._stop_latched.clear()

        self._schedule(confirm)

    def start_mission(self):
        async def start():
            if self.client and not self._stop_latched.is_set():
                result = await self.client.start()
                if result.challenge:
                    self.review_requested.emit(result.challenge)

        self._schedule(start)

    def resume_mission(self):
        async def resume():
            if self.client and self.client.status and self.client.status.mission_id:
                self._stop_latched.clear()
                result = await self.client.resume(self.client.status.mission_id)
                if result.challenge:
                    self.review_requested.emit(result.challenge)

        self._schedule(resume)

    def answer(self, challenge_id, answer):
        async def approve():
            if (
                self.client
                and self.client.challenge
                and self.client.challenge.challenge_id == challenge_id
                and not self._stop_latched.is_set()
            ):
                await self.client.approve(human_choice="approve" if answer == "approve" else "deny")

        self._schedule(approve)

    def stop(self):
        self._stop_latched.set()  # invalidates queued UI approval before the thread switch

        async def stop():
            if self.client:
                await self.client.stop()

        self._schedule(stop, priority=True)

    def pause(self):
        self._stop_latched.set()

        async def pause():
            if self.client:
                await self.client.pause()

        self._schedule(pause, priority=True)

    def preview(self):
        async def preview():
            if self.client:
                await self.client.request_preview(consent=True)
                frame = self.client.take_preview()
                if frame:
                    self.preview_ready.emit(frame)

        self._schedule(preview)

    def reconcile(self):
        async def reconcile():
            if self.client and self.client.status and self.client.status.mission_id:
                await self.client.reconcile(self.client.status.mission_id)

        self._schedule(reconcile)

    def revoke(self):
        self._stop_latched.set()

        async def revoke():
            if self.guest:
                await self.guest.disarm("stopped")
                self.guest.pairing.revoke()
            if self.client:
                self.client.connected = False
                self.client.challenge = None
                self.client.preview = None

        self._schedule(revoke, priority=True)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._stop_latched.set()
        if self.loop:
            try:
                self.loop.call_soon_threadsafe(self._exit.set)
            except RuntimeError:
                pass
