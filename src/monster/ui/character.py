"""Animated pastel pocket monster; body language carries state without labels."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient

from monster.lifecycle import State

STATE_STYLE = {
    State.IDLE: ("Idle", "#a5bfd5"),
    State.LISTENING: ("Ready", "#8feaff"),
    State.THINKING: ("Thinking", "#d3baff"),
    State.WORKING: ("Acting", "#7af9df"),
    State.WAITING: ("Review", "#ffd99b"),
    State.COMPLETED: ("Done", "#a0f8c4"),
    State.ERROR: ("Stopped", "#ffb1c1"),
}


def _line(p, path, color, width=1.2):
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    p.drawPath(path)


def _glint(p, x, y, radius, color):
    path = QPainterPath(QPointF(x, y - radius))
    path.quadTo(x + radius * .25, y - radius * .25, x + radius, y)
    path.quadTo(x + radius * .25, y + radius * .25, x, y + radius)
    path.quadTo(x - radius * .25, y + radius * .25, x - radius, y)
    path.quadTo(x - radius * .25, y - radius * .25, x, y - radius)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def _paw(p, x, y, tilt=0):
    p.save()
    p.translate(x, y)
    p.rotate(tilt)
    gradient = QLinearGradient(-3, -4, 3, 4)
    gradient.setColorAt(0, QColor("#f7f0ff"))
    gradient.setColorAt(1, QColor("#a692d5"))
    p.setBrush(gradient)
    p.setPen(QPen(QColor("#dfd5f6"), .65))
    p.drawRoundedRect(QRectF(-4.5, -3.8, 9, 7.6), 3.8, 3.8)
    p.restore()


def draw_monster(p: QPainter, bounds: QRectF, state: str, phase: float, reduced=False):
    """Draw a complete pose in a 64-unit canvas, with bounded, non-flashing motion."""
    state = State(state)
    t = 0 if reduced else phase
    asleep = state == State.IDLE
    thinking = state == State.THINKING
    working = state == State.WORKING
    waiting = state == State.WAITING
    happy = state == State.COMPLETED
    stopped = state == State.ERROR
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(bounds.x(), bounds.y())
    p.scale(bounds.width() / 64, bounds.height() / 64)

    # A contact shadow and restrained bloom give volume on light or dark desktops.
    shadow = QRadialGradient(QPointF(32, 57), 22)
    shadow.setColorAt(0, QColor(14, 14, 36, 75))
    shadow.setColorAt(1, QColor(14, 14, 36, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(shadow)
    p.drawEllipse(QRectF(8, 53, 48, 9))
    bloom = QRadialGradient(QPointF(32, 34), 30)
    bloom.setColorAt(0, QColor(155, 131, 231, 38))
    bloom.setColorAt(1, QColor(155, 131, 231, 0))
    p.setBrush(bloom)
    p.drawEllipse(QRectF(2, 4, 60, 58))

    # Continuous body animation, even when the cursor and window are stationary.
    # All motion is smooth and bounded; reduced motion freezes every component.
    rhythm = math.sin(t * (1.7 if asleep else 5 if working else 3 if happy else 2.4))
    breath = rhythm * (1.15 if asleep else 2.25 if working or happy else 1.1)
    sway = math.sin(t * (2.8 if happy else 2 if thinking else 1.8))
    lean = -10 if asleep else -8 if thinking else 7 if working else 5 if waiting else -4 if stopped else 0
    p.translate(32, 36 + (3 if asleep else -1 if working else 0) + breath)
    p.rotate(lean + sway * (5 if thinking or happy else 2.5))
    squash = rhythm * (.035 if asleep or working or happy else .018)
    p.scale((1.06 if asleep else 1) + squash, (.88 if asleep else 1) - squash)
    p.translate(-32, -36)

    # Rounded horns retain Monster's identity, with soft rather than sharp tips.
    for mirror in (False, True):
        p.save()
        if mirror:
            p.translate(64, 0)
            p.scale(-1, 1)
        horn = QPainterPath(QPointF(17, 23))
        horn.cubicTo(9, 18, 12, 8, 18, 9)
        horn.cubicTo(16, 14, 23, 12, 25, 20)
        horn.closeSubpath()
        horn_fill = QLinearGradient(14, 9, 25, 23)
        horn_fill.setColorAt(0, QColor("#ffe6ec"))
        horn_fill.setColorAt(1, QColor("#d4aff2"))
        p.setBrush(horn_fill)
        p.setPen(QPen(QColor("#d7fffb"), .7))
        p.drawPath(horn)
        p.restore()

    for x in (24, 40):
        step = math.sin(t * 7 + (0 if x < 32 else math.pi)) * (1.5 if working else .35)
        _paw(p, x, 52.5 + step, (-12 if x < 32 else 12) + step * 4)
    body = QPainterPath(QPointF(12, 34))
    body.cubicTo(11, 21, 19, 16, 32, 16)
    body.cubicTo(45, 16, 53, 23, 52, 36)
    body.cubicTo(54, 49, 46, 54, 32, 54)
    body.cubicTo(18, 54, 10, 48, 12, 34)
    shell = QRadialGradient(QPointF(24, 21), 39)
    shell.setColorAt(0, QColor("#fff4f5"))
    shell.setColorAt(.42, QColor("#f1dff6"))
    shell.setColorAt(.78, QColor("#d5b5ed"))
    shell.setColorAt(1, QColor("#a78bcb"))
    p.setBrush(shell)
    p.setPen(QPen(QColor("#efe7ff"), .9))
    p.drawPath(body)
    # Specular crown, clipped to the shell, rather than an outer circular effect.
    p.save()
    p.setClipPath(body)
    p.setBrush(QColor(255, 255, 255, 50))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(16, 17, 34, 14))
    p.restore()

    visor = QPainterPath()
    visor.addRoundedRect(QRectF(15, 25, 34, 23), 10, 10)
    glass = QLinearGradient(18, 26, 43, 47)
    glass.setColorAt(0, QColor("#fff6fb"))
    glass.setColorAt(.6, QColor("#f5e9fa"))
    glass.setColorAt(1, QColor("#e9d4f4"))
    p.setBrush(glass)
    p.setPen(QPen(QColor("#f0eaff"), .85))
    p.drawPath(visor)
    p.save()
    p.setClipPath(visor)
    sheen = QLinearGradient(17, 26, 30, 41)
    sheen.setColorAt(0, QColor(218, 241, 255, 30))
    sheen.setColorAt(1, QColor(218, 241, 255, 0))
    p.setBrush(sheen)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(13, 24, 36, 12))

    blink_phase = t % 4.6
    blink = max(0, 1 - abs(blink_phase - 4.3) / .13) if not reduced else 0
    gaze_x = -1 + .7 * math.sin(t * 1.7) if thinking else .7 if working else .35 * sway
    gaze_y = -1.5 if thinking else .15
    for x in (24, 40):
        eye = QPainterPath()
        if asleep or happy or blink > .8:
            eye.moveTo(x - 3.2, 33.5)
            eye.quadTo(x, 36 if asleep or blink > .8 else 29.2, x + 3.2, 33.5)
            _line(p, eye, "#62416f", 1.8)
        else:
            height = (10 if waiting else 9) * (1 - blink * .85)
            eye_x, eye_y = x + gaze_x, 33.3 + gaze_y
            eye_fill = QLinearGradient(x, 28, x, 39)
            eye_fill.setColorAt(0, QColor("#483056"))
            eye_fill.setColorAt(1, QColor("#88619d"))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(eye_fill)
            p.drawEllipse(QRectF(eye_x - 3.7, eye_y - height / 2, 7.4, height))
            if blink < .4:
                p.setBrush(QColor("#ffffff"))
                p.drawEllipse(QRectF(eye_x - 2.2, eye_y - 3, 2.7, 2.7))
                p.setBrush(QColor("#f9daff"))
                p.drawEllipse(QRectF(eye_x + .7, eye_y + 1.3, 1.3, 1.3))
    # A tiny muzzle and soft cheek lights keep the face friendly at small sizes.
    mouth = QPainterPath(QPointF(29.5, 40.5))
    if asleep:
        mouth.quadTo(32, 42, 34.5, 40.5)
    elif stopped:
        mouth.quadTo(32, 38.5, 34.5, 40.5)
    elif thinking:
        mouth.quadTo(31, 41, 32.5, 40)
    else:
        mouth.quadTo(32, 43.2 if happy else 42, 34.5, 40.5)
    _line(p, mouth, "#805276", 1.15)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(247, 145, 183, 125 if asleep else 155))
    p.drawEllipse(QRectF(17, 37.5, 6, 2.7))
    p.drawEllipse(QRectF(41, 37.5, 6, 2.7))
    if working:
        # Linear data strokes; never an orbit, progress ring, or text glyph.
        p.setBrush(QColor(122, 249, 223, 95))
        for i in range(3):
            p.drawRoundedRect(QRectF(28 + i * 3, 43, 1.7, 1.2), .6, .6)
    p.restore()

    if asleep:
        _paw(p, 24, 47 + rhythm * .3, 15)
        _paw(p, 40, 47 + rhythm * .3, -15)
    elif thinking:
        _paw(p, 12.5, 43, -25)
        _paw(p, 49, 29 + .9 * math.sin(t * 3), -32 + 9 * math.sin(t * 3))
        for i, (x, y, r) in enumerate(((48, 12, 2.4), (54, 17, 1.6))):
            color = QColor("#d9c6ff")
            color.setAlpha(int(175 + 25 * math.sin(t * 2 + i)))
            _glint(p, x, y + .5 * math.sin(t * 2 + i), r, color)
    elif working:
        tap = math.sin(t * 7) * 1.8
        _paw(p, 24, 48 + tap, -18 + tap * 6)
        _paw(p, 41, 48 - tap, 18 - tap * 6)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            color = QColor("#9ef9ec")
            color.setAlpha(120 + i * 25)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(3 + i * 1.5, 35 + i * 4, 5 - i, 1.4), .7, .7)
    elif happy:
        _paw(p, 10, 30 + sway, -40 + 16 * math.sin(t * 7))
        _paw(p, 54, 30 - sway, 40 - 16 * math.sin(t * 7))
        _glint(p, 50, 12, 3, QColor("#b8ffe0"))
    elif stopped:
        _paw(p, 12, 44, -10)
        _paw(p, 51, 34 + .5 * sway, -65 + 4 * sway)
    else:
        _paw(p, 11.5, (39 if waiting else 42) + .8 * sway, -25 + 8 * sway)
        _paw(p, 52.5, (39 if waiting else 42) - .8 * sway, 25 - 8 * sway)
    p.restore()
