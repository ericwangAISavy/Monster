"""Placement uses Qt device-independent desktop coordinates, not agent pixel coordinates."""

from PySide6.QtCore import QPoint, QPointF, QRect, QSize


def clamp_position(point: QPoint, size: QSize, area: QRect, margin: int = 6) -> QPoint:
    left, top = area.left() + margin, area.top() + margin
    right = max(left, area.right() - size.width() + 1 - margin)
    bottom = max(top, area.bottom() - size.height() + 1 - margin)
    return QPoint(min(max(point.x(), left), right), min(max(point.y(), top), bottom))


def beside_cursor(cursor: QPoint, size: QSize, area: QRect, offset: QPoint) -> QPoint:
    x, y = cursor.x() + offset.x(), cursor.y() + offset.y()
    if x + size.width() + 6 > area.right():
        x = cursor.x() - size.width() - offset.x()
    if y + size.height() + 6 > area.bottom():
        y = cursor.y() - size.height() - offset.y()
    return clamp_position(QPoint(x, y), size, area)


def smooth(current: QPointF, target: QPoint, elapsed: float) -> QPointF:
    # Frame-rate-independent exponential smoothing; snap after a monitor jump.
    import math

    if (current - QPointF(target)).manhattanLength() > 500:
        return QPointF(target)
    weight = 1 - math.exp(-max(0.0, min(elapsed, 0.1)) / 0.045)
    return current + (QPointF(target) - current) * weight
