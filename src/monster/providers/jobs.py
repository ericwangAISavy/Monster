"""Cancellable asynchronous HTTP jobs, isolated from the Qt event loop."""

import asyncio
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from monster.agent.diagnostics import exception_evidence
from monster.providers.base import ProviderError


def unexpected_error(exc: Exception) -> str:
    """Keep exception class/source location, never exception text, locals or payloads."""
    root = Path(__file__).resolve().parent.parent
    location = "background job"
    for frame, line in traceback.walk_tb(exc.__traceback__):
        path = Path(frame.f_code.co_filename).resolve()
        if path.is_relative_to(root):
            location = f"{path.relative_to(root).as_posix()}:{line}"
    return f"Unexpected {type(exc).__name__} at {location}. Open tray → Last failure for details."


class ProviderJobs(QObject):
    completed = Signal(int, object, str)
    diagnostic = Signal(object)

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
            except Exception as exc:  # noqa: BLE001 - Expose safe metadata, never provider payloads.
                self.diagnostic.emit({"job": job, "evidence": exception_evidence(exc)})
                error = unexpected_error(exc)
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
