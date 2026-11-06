"""Asynchronous overlay isolation for a future screenshot service.

Always hide both surfaces for now: successful WDA_EXCLUDEFROMCAPTURE registration
alone is not proof that a chosen backend honors it. A future backend can opt into
native-only exclusion after Windows qualification. This module performs no capture
unless an explicit backend callable is supplied.
"""

import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from monster.agent.diagnostics import exception_evidence
from monster.native import exclude_capture


class CaptureIsolation(QObject):
    started = Signal()
    ended = Signal()
    completed = Signal(object)
    failed = Signal(str)
    diagnostic = Signal(object)

    def __init__(self, windows, parent=None):
        super().__init__(parent)
        self.windows = windows
        self.busy = False
        self.affinity_accepted = []
        self._restore = {}

    def suppress_restore(self, window):
        """A stop/dismiss during capture must not resurrect a stale prompt."""
        self._restore[window] = False

    def apply_native_hints(self):
        self.affinity_accepted = [exclude_capture(window) for window in self.windows]
        return self.affinity_accepted

    def capture(self, backend: Callable[[], object], settle_ms: int = 100):
        if self.busy:
            raise RuntimeError("An isolated capture is already pending.")
        self.busy = True
        self._restore = {window: window.isVisible() for window in self.windows}
        self.diagnostic.emit({"event": "overlay_isolation_started", "settle_ms": max(50, settle_ms),
                              "visible_before": list(self._restore.values()),
                              "native_affinity_hints": list(self.affinity_accepted)})
        started_at = time.monotonic()
        self.started.emit()
        for window in self.windows:
            window.hide()
        QApplication.sync()

        def take():
            result = None
            error = None
            try:
                if any(window.isVisible() for window in self.windows):
                    raise RuntimeError("Overlay became visible during capture; discard this frame.")
                self.diagnostic.emit({"event": "overlay_isolation_capture", "overlays_hidden": True,
                                      "elapsed_since_hide_s": time.monotonic() - started_at})
                result = backend()
            except Exception as exc:  # noqa: BLE001 - Restore UI for any backend failure.
                self.diagnostic.emit(exception_evidence(exc))
                error = f"Capture failed: {type(exc).__name__}"
            finally:
                for window, was_visible in self._restore.items():
                    if was_visible:
                        window.show()
                self.busy = False
                self.ended.emit()
            if error:
                self.failed.emit(error)
            else:
                self.completed.emit(result)

        # Allow the compositor to process removal. Actual backend behavior must
        # still be qualified on Windows; this delay is not a universal guarantee.
        QTimer.singleShot(max(50, settle_ms), self, take)
