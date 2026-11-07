"""Passive cursor companion; rendering lives in character.py."""

import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from monster.geometry import beside_cursor, beside_occlusion, clamp_position, smooth
from monster.lifecycle import State
from monster.native import keep_companion_topmost, shell_occlusion
from monster.ui.character import STATE_STYLE, draw_monster


class Companion(QWidget):
    def __init__(self, preferences, *, shell_avoidance=True):
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
        self._renderer = None
        self._shell_avoidance = shell_avoidance
        self.state = State.IDLE
        self._activity = ""
        self._stage = ""
        self._paused = False
        self._refresh_style()
        self._position = None
        self._last = time.monotonic()
        self._phase = 0.0
        self._zorder_at = 0.0
        self._shell_cover = None
        self._shell_screen = None
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
        return State.THINKING if self._activity else State.WAITING if self._paused else self.state

    def set_activity(self, text):
        """Request activity changes pose; its description is accessibility-only."""
        self._activity = text
        self._refresh_style()
        self.update()

    def set_paused(self, paused):
        self._paused = paused
        self._refresh_style()
        self.update()

    def set_stage(self, text):
        self._stage = text
        self._refresh_style()

    def _refresh_style(self):
        scale = self.preferences.companion_scale / 100
        self.resize(round(80 * scale), round(80 * scale))
        label = STATE_STYLE[self.visual_state][0]
        label = self._activity or ("Paused" if self._paused else self._stage or label)
        self.setAccessibleName(f"Monster · {label}")
        self._sync_renderer()

    def attach_renderer(self, renderer):
        if self._renderer is not None:
            self._renderer.close()
        self._renderer = renderer
        self._sync_renderer()
        self.update()

    def _sync_renderer(self):
        if self._renderer is not None:
            from monster.ui.overlay_mailbox import APPEARANCE
            self._renderer.update(state=self.visual_state.value,
                                  character=self.preferences.companion_character,
                                  visible=self.isVisible(), paused=bool(self._paused and not self._activity),
                                  appearance={key: getattr(self.preferences, key) for key in APPEARANCE})

    def capture_isolation_ready(self):
        return self._renderer is None or self._renderer.hidden_confirmed

    def visibility_status(self):
        return {"backend": "uiaccess" if self._renderer and self._renderer.active else "ordinary_topmost",
                "helper_configured": self._renderer is not None,
                "helper_error": getattr(self._renderer, "error", None)}

    def apply_preferences(self, preferences):
        self.preferences = preferences
        self._position = None
        self._refresh_style()
        self.tick()

    def tick(self):
        now = time.monotonic()
        elapsed, self._last = now - self._last, now
        if self._renderer is not None:
            self._renderer.pump()
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
        if now - self._zorder_at >= .25 or self._shell_screen is not screen:
            self._zorder_at = now
            keep_companion_topmost(self)
            self._shell_cover = shell_occlusion(screen) if self._shell_avoidance else None
            self._shell_screen = screen
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
        target = beside_occlusion(target, self.size(), area, self._shell_cover)
        if fixed or self._position is None or self.preferences.reduced_motion:
            self._position = QPointF(target)
        else:
            self._position = smooth(self._position, target, elapsed)
        self._position = QPointF(clamp_position(self._position.toPoint(), self.size(), area))
        unobscured = beside_occlusion(self._position.toPoint(), self.size(), area, self._shell_cover)
        if unobscured != self._position.toPoint():
            # Do not animate through a protected panel or stick at its opposite edge.
            self._position = QPointF(target)
        self.move(self._position.toPoint())
        self._phase += elapsed
        self.update()
        moving = (self._position - QPointF(target)).manhattanLength() > 1
        animated = not self.preferences.reduced_motion and not (self._paused and not self._activity)
        self.timer.setInterval(16 if moving and animated else
                               33 if animated and self.visual_state != State.IDLE else 66)

    def hideEvent(self, event):
        self._sync_renderer()
        super().hideEvent(event)

    def closeEvent(self, event):
        if self._renderer is not None:
            self._renderer.close()
        super().closeEvent(event)

    def showEvent(self, event):
        self._sync_renderer()
        self._zorder_at = 0.0
        self.tick()
        super().showEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._renderer is not None and self._renderer.active:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            return
        body_size = self.width()
        draw_monster(
            painter, QRectF((self.width() - body_size) / 2, 0, body_size, body_size),
            self.visual_state, self._phase,
            self.preferences.reduced_motion or (self._paused and not self._activity),
            character=self.preferences.companion_character,
        )
