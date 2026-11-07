"""Optional signed visual renderer bridge; the controller/worker stay unprivileged."""

import mmap
import sys
import time
import uuid

from monster.ui.overlay_mailbox import MAILBOX_SIZE, Mailbox, validate_state
from monster.ui.overlay_windows import OverlayProcess


class OverlayClient:
    def __init__(self, path):
        if sys.platform != 'win32':
            raise RuntimeError('UIAccess renderer requires Windows.')
        self.name = 'Local\\MonsterOverlay-' + uuid.uuid4().hex
        self.memory = mmap.mmap(-1, MAILBOX_SIZE, tagname=self.name)
        self.box = Mailbox(self.memory)
        self.seq = self.hide_seq = 0
        self.desired = None
        self.active = False
        self.error = None
        self.closed = False
        self.started = time.monotonic()
        self.last_published = 0.0
        self.ack = None
        self._last_visibility = None
        self._last_ack_seq = 0
        self._last_ack_at = self.started
        self.process = None
        try:
            self.process = OverlayProcess(path, self.name)
        except Exception:
            self.memory.close()
            raise

    def update(self, **state):
        if self.closed:
            return
        self.desired = state
        self._publish()

    def _publish(self, closing=False):
        if self.desired is None or self.closed:
            return
        self.seq += 1
        message = {'version': 1, 'seq': self.seq, 'timestamp_ns': time.monotonic_ns(),
                   **self.desired, 'closing': closing}
        message['visible'] = message['visible'] and self.active and not closing
        try:
            validate_state(message)
            self.box.write(0, message)
        except ValueError:
            self.error = 'invalid_appearance'
            self.active = False
            return
        if not message['visible'] and self._last_visibility is not False:
            self.hide_seq = self.seq
        self._last_visibility = message['visible']
        self.last_published = time.monotonic()

    def pump(self):
        if self.closed:
            return
        if not self.process.running:
            self.active = False
            self.error = self.error or 'renderer_exited'
            return
        ack = self.box.read(1)
        if (ack is not None and set(ack) == {'seq', 'hidden', 'ready'}
                and type(ack['seq']) is int and 1 <= ack['seq'] <= self.seq
                and type(ack['hidden']) is bool and ack['ready'] is True):
            if ack['seq'] > self._last_ack_seq:
                self._last_ack_seq = ack['seq']
                self._last_ack_at = time.monotonic()
            self.ack = ack
            self.active = bool(self.process.ui_access)
        elif self.ack is None and time.monotonic() - self.started > 5:
            self.error = 'renderer_startup_timeout'
            self.active = False
            self.process.close()
        if self.ack is not None and time.monotonic() - self._last_ack_at > 2.5:
            self.error = 'renderer_unresponsive'
            self.active = False
            self.process.close()
        if time.monotonic() - self.last_published >= .1:
            self._publish()

    @property
    def hidden_confirmed(self):
        if self.process is None or not self.process.running:
            return True
        ack = self.box.read(1)
        return bool(type(ack) is dict and type(ack.get('seq')) is int
                    and self.hide_seq <= ack['seq'] <= self.seq and ack.get('hidden') is True)

    def close(self):
        if self.closed:
            return
        self._publish(closing=True)
        # Rendering owns no target input; terminating an unresponsive renderer
        # cannot strand a key/button. The normal input worker is separate.
        confirmed = self.process.close() if self.process else True
        self.active = False
        if confirmed:
            self.memory.close()
            self.closed = True
        else:
            self.error = 'renderer_shutdown_unconfirmed'
