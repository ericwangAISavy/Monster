"""A replaceable, resolution-independent miniature character renderer."""

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from monster.geometry import beside_cursor, clamp_position, smooth
from monster.lifecycle import State

STATE_STYLE = {
    State.IDLE: ("Idle", "#9fafd0"),
    State.LISTENING: ("Ready", "#8ac8ff"),
    State.THINKING: ("Thinking", "#c5acff"),
    State.WORKING: ("Acting", "#6cf5ff"),
    State.WAITING: ("Review", "#ffd080"),
    State.COMPLETED: ("Done", "#91efb2"),
    State.ERROR: ("Stopped", "#ff9cae"),
}


def draw_monster(p: QPainter, bounds: QRectF, state: str, phase: float, reduced=False):
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(bounds.x(), bounds.y())
    p.scale(bounds.width() / 64, bounds.height() / 64)
    pulse = 0 if reduced else math.sin(phase * 2)
    lift = 0 if reduced else math.sin(phase * 2.2) * 1.1
    p.translate(0, lift)
    accent = QColor(STATE_STYLE[State(state)][1])
    intensity = 0.75 if state == State.IDLE else 1.0
    glow = QRadialGradient(32, 33, 29)
    glow.setColorAt(0, QColor(78, 48, 200, 75))
    glow.setColorAt(0.65, QColor(100, 71, 245, int(38 + pulse * 8)))
    glow.setColorAt(1, QColor(79, 45, 205, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(glow)
    p.drawEllipse(QRectF(2, 4, 60, 58))
    # Tiny feet and rounded side fins keep the silhouette legible at 48 DIP.
    p.setPen(QPen(QColor("#9e88ff"), 1.2))
    p.setBrush(QColor("#202149"))
    p.drawEllipse(QRectF(18, 47, 10, 8))
    p.drawEllipse(QRectF(36, 47, 10, 8))
    p.drawEllipse(QRectF(7, 30, 11, 12))
    p.drawEllipse(QRectF(46, 30, 11, 12))
    for mirror in (False, True):
        p.save()
        if mirror:
            p.translate(64, 0)
            p.scale(-1, 1)
        horn = QPainterPath(QPointF(18, 23))
        horn.cubicTo(8, 19, 10, 9, 16, 7)
        horn.cubicTo(12, 15, 20, 12, 23, 19)
        horn.closeSubpath()
        p.setBrush(QColor("#8af5ff"))
        p.setPen(QPen(QColor("#c2fdff"), 0.8))
        p.drawPath(horn)
        p.restore()
    body = QPainterPath(QPointF(12, 31))
    body.cubicTo(10, 19, 22, 15, 32, 16)
    body.cubicTo(46, 14, 54, 23, 52, 34)
    body.cubicTo(56, 46, 44, 53, 32, 52)
    body.cubicTo(19, 53, 9, 45, 12, 31)
    gradient = QRadialGradient(27, 21, 33)
    gradient.setColorAt(0, QColor("#33305f"))
    gradient.setColorAt(0.52, QColor("#121b39"))
    gradient.setColorAt(1, QColor("#080f23"))
    p.setBrush(gradient)
    p.setPen(QPen(QColor("#a394ef"), 1.2))
    p.drawPath(body)
    # A subtle tuft; no particles or oversized effects.
    p.setPen(QPen(QColor("#b7a6ff"), 1.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(27, 17), QPointF(31, 13))
    p.drawLine(QPointF(32, 16), QPointF(37, 13))
    # During work, the activity indicator stays legible instead of eye blinking.
    blinking = not reduced and state == State.IDLE and phase % 5.8 > 5.64
    for x in (23, 41):
        halo = QRadialGradient(x, 33, 9)
        halo.setColorAt(0, QColor(55, 223, 255, int(160 * intensity)))
        halo.setColorAt(1, QColor(55, 223, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QRectF(x - 9, 24, 18, 18))
        p.setBrush(accent)
        if state == State.COMPLETED:
            p.setPen(QPen(accent, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(x - 4, 30, 8, 7), 10 * 16, 160 * 16)
        else:
            p.drawEllipse(
                QRectF(x - 3.4, 33 - (0.7 if blinking else 4.4), 6.8, 1.4 if blinking else 8.8)
            )
            if not blinking:
                p.setBrush(QColor("#e1ffff"))
                p.drawEllipse(QRectF(x - 1.5, 29.8, 2.2, 2.5))
    mouth = QPainterPath(QPointF(28, 43))
    mouth.quadTo(32, 39 if state == State.ERROR else 47, 36, 43)
    p.setPen(QPen(accent, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(mouth)
    if state == State.THINKING:
        p.setPen(QPen(QColor(104, 80, 163, 100), 2.2))
        p.drawEllipse(QRectF(3, 3, 58, 58))
        p.setPen(QPen(accent, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        angle = 90 if reduced else (90 - phase * 110) % 360
        p.drawArc(QRectF(3, 3, 58, 58), int(angle * 16), 235 * 16)
        # Three thought dots give the state a shape cue as well as its color.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        for x in (25, 32, 39):
            p.drawEllipse(QRectF(x - 1.5, 56, 3, 3))
    p.restore()


class Companion(QWidget):
    def __init__(self, preferences):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle("Monster companion")
        self.preferences = preferences
        self.state = State.IDLE
        self._activity = ""
        self._stage = ""
        self._paused = False
        self.status_label = QLabel(self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._refresh_style()
        self._position = None
        self._last = time.monotonic()
        self._phase = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.tick()

    def set_state(self, state: str):
        self.state = State(state)
        self._stage = ""
        self._refresh_style()
        self.update()

    @property
    def visual_state(self):
        return State.THINKING if self._activity else self.state

    def set_activity(self, text):
        """Short, content-free request label; never displays model reasoning."""
        self._activity = text
        self._refresh_style()
        self.update()

    def set_paused(self, paused):
        self._paused = paused
        self._refresh_style()

    def set_stage(self, text):
        self._stage = text
        self._refresh_style()

    def _refresh_style(self):
        scale = self.preferences.companion_scale / 100
        self.resize(round(112 * scale), round(88 * scale))
        self.status_label.setGeometry(round(6 * scale), round(66 * scale),
                                      round(100 * scale), round(20 * scale))
        label, color = STATE_STYLE[self.visual_state]
        label = self._activity or ("Paused" if self._paused else self._stage or label)
        self.status_label.setText(label)
        self.status_label.setStyleSheet(
            f"color:{color};background:#141b30;border:1px solid {color};"
            f"border-radius:{round(9 * scale)}px;font:bold {round(11 * scale)}px 'Segoe UI';"
        )
        self.setAccessibleName(f"Monster · {label}")
        self.setToolTip(f"Monster · {label}")

    def apply_preferences(self, preferences):
        self.preferences = preferences
        self._position = None
        self._refresh_style()
        self.tick()

    def tick(self):
        now = time.monotonic()
        elapsed, self._last = now - self._last, now
        if not self.isVisible():
            self.timer.setInterval(120)
            return
        cursor = QCursor.pos()
        fixed = self.preferences.companion_position != "cursor"
        screen = QApplication.primaryScreen() if fixed else (
            QApplication.screenAt(cursor) or QApplication.primaryScreen()
        )
        if screen is None:
            return
        area = screen.availableGeometry()
        target = beside_cursor(
            cursor,
            self.size(),
            area,
            QPointF(self.preferences.offset_x, self.preferences.offset_y).toPoint(),
        )
        if fixed:
            target = area.topRight() if self.preferences.companion_position == "top_right" else area.bottomRight()
            target = clamp_position(target, self.size(), area)
        if fixed or self._position is None or self.preferences.reduced_motion:
            self._position = QPointF(target)
        else:
            self._position = smooth(self._position, target, elapsed)
        self._position = QPointF(clamp_position(self._position.toPoint(), self.size(), area))
        self.move(self._position.toPoint())
        self._phase += elapsed
        self.update()
        moving = (self._position - QPointF(target)).manhattanLength() > 1
        self.timer.setInterval(16 if moving and not self.preferences.reduced_motion else 66)

    def paintEvent(self, event):
        painter = QPainter(self)
        body_size = 64 * self.preferences.companion_scale / 100
        draw_monster(
            painter, QRectF((self.width() - body_size) / 2, 0, body_size, body_size),
            self.visual_state, self._phase, self.preferences.reduced_motion
        )
