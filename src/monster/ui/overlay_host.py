"""Visual-only UIAccess companion. Never creates an agent, provider or input worker."""

import argparse
import mmap
import re
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from monster.config import Preferences
from monster.native import exclude_capture
from monster.ui.companion import Companion
from monster.ui.overlay_mailbox import MAILBOX_SIZE, Mailbox, RendererLease
from monster.ui.overlay_windows import ui_access_enabled


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--mailbox', required=True)
    args = cli.parse_args()
    if not re.fullmatch(r'Local\\MonsterOverlay-[a-f0-9]{32}', args.mailbox) or not ui_access_enabled():
        return 2
    memory = mmap.mmap(-1, MAILBOX_SIZE, tagname=args.mailbox)
    box = Mailbox(memory)
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    companion = Companion(Preferences(), shell_avoidance=False)
    last_seq = 0
    last_appearance = None
    lease = RendererLease(time.monotonic_ns())

    def tick():
        nonlocal last_seq, last_appearance
        data = box.read(0)
        now = time.monotonic_ns()
        decision = lease.accept(data, now)
        if decision == 'close':
            companion.hide()
            app.quit()
            return
        if decision == 'wait':
            return
        last_seq = data['seq']
        appearance = {**data['appearance'], 'companion_character': data['character']}
        if appearance != last_appearance:
            prefs = Preferences(**appearance)
            prefs.validate()
            companion.apply_preferences(prefs)
            last_appearance = appearance
        if companion.state.value != data['state']:
            companion.set_state(data['state'])
        if companion._paused != data['paused']:
            companion.set_paused(data['paused'])
        visible = data['visible'] and not data['closing']
        if companion.isVisible() != visible:
            companion.setVisible(visible)
            if visible:
                exclude_capture(companion)
            QApplication.sync()
        box.write(1, {'seq': last_seq, 'hidden': not companion.isVisible(), 'ready': True})
        if data['closing']:
            app.quit()

    timer = QTimer()
    timer.setInterval(16)
    timer.timeout.connect(tick)
    timer.start()
    tick()
    try:
        return app.exec()
    finally:
        companion.close()
        memory.close()


if __name__ == "__main__":
    raise SystemExit(main())
