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


def test_native_topmost_reassertion_never_activates_or_shows_hidden_window(monkeypatch):
    from types import SimpleNamespace

    from monster import native
    calls = []

    def position(*args):
        calls.append(args)
        return True

    monkeypatch.setattr(native, 'user32', lambda: SimpleNamespace(SetWindowPos=position))
    hidden = SimpleNamespace(isVisible=lambda: False, winId=lambda: 123)
    visible = SimpleNamespace(isVisible=lambda: True, winId=lambda: 123)
    assert native.keep_companion_topmost(hidden) is False and calls == []
    assert native.keep_companion_topmost(visible) is True
    assert calls[0][:2] == (123, -1)
    flags = calls[0][-1]
    assert flags & 0x10  # SWP_NOACTIVATE
    assert flags & 0x3 == 0x3  # no move/resize
    assert not flags & 0x40  # no SWP_SHOWWINDOW


def test_shell_geometry_uses_monitor_identity_origin_and_dpi(monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtCore import QRect

    from monster import native

    def foreground():
        return 77

    def classname(handle, buffer, length):
        buffer.value = 'Windows.UI.Core.CoreWindow'
        return len(buffer.value)

    def rectangle(handle, pointer):
        r = pointer._obj
        r.left, r.top, r.right, r.bottom = -1795, 125, -1170, 875
        return True

    def monitor(handle, flags):
        return 88

    def information(handle, pointer):
        obj = pointer._obj
        obj.monitor.left, obj.monitor.top = -1920, 0
        obj.device = 'DISPLAY1'
        return True

    api = SimpleNamespace(GetForegroundWindow=foreground, GetClassNameW=classname,
                          GetWindowRect=rectangle, MonitorFromWindow=monitor,
                          GetMonitorInfoW=information)
    monkeypatch.setattr(native, 'user32', lambda: api)
    screen = SimpleNamespace(name=lambda: 'DISPLAY1', devicePixelRatio=lambda: 1.25,
                             geometry=lambda: QRect(-1920, 0, 1536, 864))
    assert native.shell_occlusion(screen) == QRect(-1820, 100, 500, 600)
    screen.name = lambda: 'DISPLAY2'
    assert native.shell_occlusion(screen) is None


def test_fullscreen_shell_host_uses_a_stable_corner_fallback():
    from monster.geometry import beside_occlusion
    area = QRect(0, 0, 1000, 700)
    result = beside_occlusion(QPoint(400, 300), QSize(80, 80), area, area)
    assert result == clamp_position(area.topRight(), QSize(80, 80), area)
