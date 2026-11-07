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


def beside_occlusion(target, size, area, occlusion):
    """Prefer visible placement beside a shell surface, with a stable corner fallback."""
    if occlusion is None:
        return target
    cover = occlusion.adjusted(-6, -6, 6, 6)
    if not QRect(target, size).intersects(cover):
        return target
    candidates = [
        QPoint(cover.right() + 1, target.y()),
        QPoint(cover.left() - size.width(), target.y()),
        QPoint(target.x(), cover.top() - size.height()),
        QPoint(target.x(), cover.bottom() + 1),
    ]
    candidates = [clamp_position(point, size, area) for point in candidates]
    visible = [point for point in candidates if not QRect(point, size).intersects(cover)]
    if not visible:
        # Some shell hosts expose a full-monitor rectangle with transparent edges.
        # A corner is a stable best effort; secure/fullscreen surfaces can still win.
        return clamp_position(area.topRight(), size, area)
    return min(visible, key=lambda point: (point - target).manhattanLength())
