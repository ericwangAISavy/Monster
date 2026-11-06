"""Cancellable asynchronous HTTP jobs, isolated from the Qt event loop."""

import asyncio
import threading

from PySide6.QtCore import QObject, Signal

from monster.providers.base import ProviderError


class ProviderJobs(QObject):
    completed = Signal(int, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(
            target=self._run_loop, daemon=True, name="monster-model-http"
        )
        self.thread.start()
        self.next_id = 0
        self.futures = {}
        self.closing = False

    def _run_loop(self):
        self.loop.run_forever()
        self.loop.close()

    def start(self, operation):
        if self.closing:
            raise RuntimeError("Provider jobs are closed.")
        self.next_id += 1
        job = self.next_id

        async def run():
            result, error = None, ""
            try:
                result = await operation()
            except asyncio.CancelledError:
                return
            except ProviderError as exc:
                error = str(exc)
            except Exception:  # noqa: BLE001 - Never leak provider payloads through UI errors.
                error = "Provider operation failed unexpectedly; nothing was executed."
            if not self.closing:
                self.completed.emit(job, result, error)

        self.futures[job] = asyncio.run_coroutine_threadsafe(run(), self.loop)
        return job

    def cancel(self, job):
        future = self.futures.pop(job, None)
        if future:
            future.cancel()

    def close(self):
        if self.closing:
            return
        self.closing = True
        for future in self.futures.values():
            future.cancel()
        self.futures.clear()

        async def stop_loop():
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.loop.stop()

        asyncio.run_coroutine_threadsafe(stop_loop(), self.loop)
        self.thread.join(timeout=0.2)
