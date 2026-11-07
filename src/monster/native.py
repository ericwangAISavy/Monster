"""Windows window integration only. No cursor movement or keyboard injection."""

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

IS_WINDOWS = sys.platform == "win32"
MODIFIERS = {"alt": 1, "ctrl": 2, "shift": 4, "win": 8}


def parse_hotkey(text: str) -> tuple[int, int]:
    parts = [part.strip().lower() for part in text.split("+")]
    if len(parts) < 2 or len(set(parts)) != len(parts):
        raise ValueError("Use modifiers and a letter, digit, or F1–F24, e.g. Ctrl+Alt+M.")
    modifiers = 0
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise ValueError(f"Unknown shortcut modifier: {part}")
        modifiers |= MODIFIERS[part]
    key = parts[-1]
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    else:
        raise ValueError("Shortcut key must be A–Z, 0–9, or F1–F24.")
    return modifiers, vk


def user32():
    if not IS_WINDOWS:
        return None
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    api.SetWindowDisplayAffinity.restype = wintypes.BOOL
    api.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    api.RegisterHotKey.restype = wintypes.BOOL
    api.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    api.UnregisterHotKey.restype = wintypes.BOOL
    api.SetForegroundWindow.argtypes = [wintypes.HWND]
    api.SetForegroundWindow.restype = wintypes.BOOL
    return api


def exclude_capture(window) -> bool:
    api = user32()
    # API acceptance is a hint, never proof that a particular capture backend honors it.
    return bool(api and api.SetWindowDisplayAffinity(int(window.winId()), 0x11))


def foreground_window() -> int:
    """Focus identity only; never inspect target titles, controls or contents."""
    api = user32()
    if not api:
        return 0
    api.GetForegroundWindow.restype = wintypes.HWND
    return int(api.GetForegroundWindow() or 0)


def approval_keys_released():
    """Read only the approval gesture/modifier/button states; never move input."""
    api = user32()
    if not api:
        return False
    api.GetAsyncKeyState.argtypes = [ctypes.c_int]
    api.GetAsyncKeyState.restype = ctypes.c_short
    keys = (0x10, 0x11, 0x12, 0x5B, 0x5C, ord("Y"), 0x01, 0x02, 0x04, 0x05, 0x06)
    return all(not (api.GetAsyncKeyState(key) & 0x8000) for key in keys)


def focus_window(window):
    api = user32()
    if api:
        api.SetForegroundWindow(int(window.winId()))
    window.raise_()
    window.activateWindow()


class GlobalHotkeys(QAbstractNativeEventFilter):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.callbacks = {}
        self.ids = []
        self.next_id = 0x4D01
        app.installNativeEventFilter(self)

    def register(self, bindings: dict[str, tuple[str, object]]) -> list[str]:
        self.clear()
        errors = []
        api = user32()
        if not api:
            return ["Global shortcuts require Windows. Use the system tray on this platform."]
        for name, (shortcut, callback) in bindings.items():
            if self.next_id > 0xBFFF:
                errors.append(f"{name}: restart Monster to renew shortcut registrations.")
                continue
            index = self.next_id
            self.next_id += 1
            modifiers, vk = parse_hotkey(shortcut)
            if api.RegisterHotKey(None, index, modifiers | 0x4000, vk):  # MOD_NOREPEAT
                self.ids.append(index)
                self.callbacks[index] = callback
            else:
                errors.append(
                    f"{name}: {shortcut} is unavailable (Windows {ctypes.get_last_error()})."
                )
        return errors

    def nativeEventFilter(self, event_type, message):
        if IS_WINDOWS and event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam in self.callbacks:  # WM_HOTKEY
                self.callbacks[msg.wParam]()
                return True, 0
        return False, 0

    def clear(self):
        api = user32()
        if api:
            for identifier in self.ids:
                api.UnregisterHotKey(None, identifier)
        self.ids.clear()
        self.callbacks.clear()

    def close(self):
        self.clear()
        self.app.removeNativeEventFilter(self)


def keep_companion_topmost(window):
    """Reassert ordinary topmost z-order without focus, resizing or hidden revival."""
    if not window.isVisible():
        return False
    api = user32()
    if not api:
        return False
    api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
    api.SetWindowPos.restype = wintypes.BOOL
    # NOACTIVATE | NOMOVE | NOSIZE | NOOWNERZORDER; deliberately no SHOWWINDOW.
    return bool(api.SetWindowPos(int(window.winId()), -1, 0, 0, 0, 0, 0x213))


def shell_occlusion(screen):
    """Overlay geometry only; never read app titles, controls or accessibility data.

    Windows can place shell surfaces in bands above ordinary topmost windows.
    Return their rectangle in this Qt screen's coordinates so the passive mascot
    can stay beside them. Never elevate, use UIAccess or impersonate shell bands.
    """
    from PySide6.QtCore import QRect

    api = user32()
    if not api:
        return None
    api.GetForegroundWindow.restype = wintypes.HWND
    handle = api.GetForegroundWindow()
    if not handle:
        return None
    api.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetClassNameW.restype = ctypes.c_int
    name = ctypes.create_unicode_buffer(256)
    if not api.GetClassNameW(handle, name, len(name)):
        return None
    if name.value not in {
        'Windows.UI.Core.CoreWindow', 'XamlExplorerHostIslandWindow',
        'Windows.UI.Composition.DesktopWindowContentBridge',
        'Shell_TrayWnd', 'Shell_SecondaryTrayWnd',
    }:
        return None

    class MonitorInfoEx(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                    ('work', wintypes.RECT), ('flags', wintypes.DWORD),
                    ('device', wintypes.WCHAR * 32)]

    api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.GetWindowRect.restype = wintypes.BOOL
    api.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    api.MonitorFromWindow.restype = wintypes.HANDLE
    api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfoEx)]
    api.GetMonitorInfoW.restype = wintypes.BOOL
    rect = wintypes.RECT()
    info = MonitorInfoEx()
    info.size = ctypes.sizeof(info)
    monitor = api.MonitorFromWindow(handle, 2)
    if not monitor or not api.GetWindowRect(handle, ctypes.byref(rect)) or not api.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    # Device identity prevents applying one monitor's scaling/origin to another.
    if screen.name().rsplit("\\", 1)[-1].casefold() != info.device.rsplit("\\", 1)[-1].casefold():
        return None
    ratio = screen.devicePixelRatio()
    if ratio <= 0:
        return None
    geometry = screen.geometry()
    result = QRect(round(geometry.x() + (rect.left - info.monitor.left) / ratio),
                   round(geometry.y() + (rect.top - info.monitor.top) / ratio),
                   round((rect.right - rect.left) / ratio),
                   round((rect.bottom - rect.top) / ratio)).intersected(geometry)
    return result if result.isValid() else None
