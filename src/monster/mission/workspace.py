"""One session-wide writer. A scheduling lease is not an armed input lease."""

import ctypes
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Lease:
    attempt_id: str
    generation: int
    acquired_at: float


def session_lock_path() -> Path:
    if os.name == "nt":
        session = ctypes.c_ulong()
        if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            raise RuntimeError("Cannot identify interactive session")
        scope = str(session.value)
    else:
        scope = str(os.getsid(0))
    return Path(tempfile.gettempdir()) / f"monster-desktop-session-{scope}.lock"


class WorkspaceGate:
    """Qt-thread-owned gate, backed by an OS lock shared with other Monster instances."""

    def __init__(self, path: Path):
        self.path = path
        self.current: Lease | None = None
        self.quarantined = False
        self._file = None

    @property
    def busy(self):
        return self.current is not None or self.quarantined

    def acquire(self, attempt_id: str, generation: int) -> Lease | None:
        if self.busy:
            return None
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.write(b"0")
                handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        self._file = handle
        self.current = Lease(attempt_id, generation, time.monotonic())
        return self.current

    def release(self, token: Lease, *, cleanup_confirmed: bool):
        if self.quarantined or token != self.current:
            return False
        if not cleanup_confirmed:
            self.quarantined = True
            return False
        self.current = None
        self._file.close()
        self._file = None
        return True
