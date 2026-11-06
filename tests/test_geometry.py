from PySide6.QtCore import QPoint, QPointF, QRect, QSize

from monster.geometry import beside_cursor, clamp_position, smooth
from monster.native import parse_hotkey


def test_screen_edges_and_negative_monitor():
    for area in (QRect(0, 0, 1920, 1040), QRect(-1920, -200, 1920, 1080)):
        for cursor in (area.topLeft(), area.bottomRight(), area.center()):
            for size in (QSize(60, 64), QSize(324, 112)):
                p = beside_cursor(cursor, size, area, QPoint(26, 20))
                assert area.contains(QRect(p, size))
                assert not QRect(p, size).contains(cursor)


def test_smooth_is_bounded_and_snaps_monitor_jump():
    result = smooth(QPointF(0, 0), QPoint(100, 80), 0.016)
    assert 0 < result.x() < 100
    assert 0 < result.y() < 80
    assert smooth(QPointF(0, 0), QPoint(-1800, 0), 0.016) == QPointF(-1800, 0)
    assert clamp_position(QPoint(-999, 999), QSize(60, 64), QRect(0, 0, 100, 100)) == QPoint(6, 30)


def test_hotkey_parser():
    import pytest

    assert parse_hotkey("Ctrl+Alt+M") == (3, ord("M"))
    assert parse_hotkey("Shift+Ctrl+F12") == (6, 0x7B)
    for bad in ("M", "Ctrl+Ctrl+X", "Ctrl+F25", "Ctrl+💫", "Foo+M", "Ctrl+"):
        with pytest.raises(ValueError):
            parse_hotkey(bad)
