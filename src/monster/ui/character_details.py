"""Species silhouettes and secondary motion for the shared pocket companion rig."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen


def _ellipse(p, rect, color):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(QRectF(*rect))


def draw_silhouette(p, character, style, t):
    """Behind-body ears/tails, drawn inside the same bounded animated transform."""
    wag = math.sin(t * 3)
    if character in {"cat", "tiger", "monkey", "ox"}:
        tail = QPainterPath(QPointF(49, 47))
        tail.cubicTo(59, 50, 61, 39 + wag * 3, 55, 34 + wag * 2)
        if character == "monkey":
            tail.cubicTo(48, 29 + wag * 2, 50, 41, 56, 39)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(style.detail), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPath(tail)
        if character == "ox":
            _ellipse(p, (52, 32 + wag * 2, 6, 5), style.detail)
    for side in (-1, 1):
        p.save()
        p.translate(32 + side * 15, 20)
        p.rotate(side * (7 + 3 * math.sin(t * 2 + side)))
        if character == "rabbit":
            _ellipse(p, (-5, -17, 10, 23), style.shell[1])
            _ellipse(p, (-2.5, -14, 5, 16), style.inner)
        elif character == "cat":
            ear = QPainterPath(QPointF(-7, 5))
            ear.quadTo(-5, -13, -1, -11)
            ear.quadTo(3, -8, 8, 5)
            p.setBrush(QColor(style.shell[2]))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(ear)
            inner = QPainterPath(QPointF(-3, 0))
            inner.lineTo(-1, -7)
            inner.lineTo(4, 1)
            p.setBrush(QColor(style.inner))
            p.drawPath(inner)
        elif character in {"monkey", "tiger"}:
            _ellipse(p, (-8, -8, 15, 16), style.shell[2])
            _ellipse(p, (-5.5, -5.5, 10, 11), style.inner)
        elif character == "ox":
            _ellipse(p, (-9, -1, 16, 8), style.detail)
            _ellipse(p, (-6, 1, 10, 4), style.inner)
            horn = QPainterPath(QPointF(-4, -1))
            horn.cubicTo(-9, -7, -7, -13, -4, -12)
            horn.quadTo(-3, -6, 3, -4)
            horn.closeSubpath()
            p.setBrush(QColor("#fff0c8"))
            p.drawPath(horn)
        elif character == "monster":
            horn = QPainterPath(QPointF(-1, 3))
            horn.cubicTo(-9, -2, -6, -12, 0, -11)
            horn.cubicTo(-2, -6, 5, -8, 7, 0)
            horn.closeSubpath()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(style.inner))
            p.drawPath(horn)
        p.restore()
    if character == "pigeon":
        crest = QPainterPath(QPointF(27, 18))
        crest.cubicTo(24, 10, 28, 9, 31, 14)
        crest.cubicTo(29, 4, 35, 7, 36, 15)
        crest.quadTo(41, 10, 39, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(style.shell[1]))
        p.drawPath(crest)


def draw_markings(p, character, style, t):
    """Features outside the eye/mouth clip; no text, badges or spinning rings."""
    if character == "tiger":
        p.setPen(QPen(QColor(style.detail), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for x in (27, 32, 37):
            p.drawLine(QPointF(x, 18), QPointF(x + (32 - x) * .25, 23))
        for x, direction in ((13, 1), (51, -1)):
            for y in (31, 37):
                p.drawLine(QPointF(x, y), QPointF(x + direction * 4, y + 1.5))
    elif character == "cat":
        p.setPen(QPen(QColor(style.detail), .8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for side in (-1, 1):
            for y in (37, 40):
                p.drawLine(QPointF(32 + side * 13, y), QPointF(32 + side * 20, y - 1))
    elif character == "ox":
        _ellipse(p, (16, 18, 11, 6), style.detail)
        _ellipse(p, (29, 40, 6, 4), style.inner)
        _ellipse(p, (30, 41, 1, 1), "#9e7781")
        _ellipse(p, (33, 41, 1, 1), "#9e7781")
    elif character == "pigeon":
        # Small scarf and wing feathers give a distinct silhouette at 80 px.
        p.setPen(QPen(QColor("#c4a9df"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(24, 48), QPointF(41, 48))
        p.drawLine(QPointF(39, 48), QPointF(44, 53 + math.sin(t * 3)))
        beak = QPainterPath(QPointF(29, 38))
        beak.quadTo(32, 35.5, 35, 38)
        beak.quadTo(32, 42, 29, 38)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(style.inner))
        p.drawPath(beak)
    elif character in {"rabbit", "cat", "tiger", "monkey"}:
        _ellipse(p, (30.5, 37.5, 3, 2), style.inner)
