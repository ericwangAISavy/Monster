"""Local character thumbnails and a small animated settings preview."""

from PySide6.QtCore import QElapsedTimer, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from monster.lifecycle import State
from monster.ui.character import draw_monster


def character_icon(character):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    draw_monster(painter, QRectF(0, 0, 64, 64), State.LISTENING, 0,
                 reduced=True, character=character)
    painter.end()
    return QIcon(pixmap)


class CharacterPreview(QWidget):
    def __init__(self, character, reduced=False):
        super().__init__()
        self.character = character
        self.reduced = reduced
        self.setFixedSize(QSize(96, 96))
        self.setAccessibleName("Animated companion preview")
        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.update)

    def set_character(self, character):
        self.character = character
        self.update()

    def set_reduced(self, reduced):
        self.reduced = reduced
        self._sync_timer()
        self.update()

    def _sync_timer(self):
        if self.isVisible() and not self.reduced:
            self.timer.start()
        else:
            self.timer.stop()

    def showEvent(self, event):
        self._sync_timer()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        phase = self.clock.elapsed() / 1000
        states = (State.LISTENING, State.THINKING, State.WORKING, State.COMPLETED, State.IDLE)
        state = State.LISTENING if self.reduced else states[int(phase / 3) % len(states)]
        painter = QPainter(self)
        draw_monster(painter, QRectF(self.rect()), state, phase,
                     reduced=self.reduced, character=self.character)
