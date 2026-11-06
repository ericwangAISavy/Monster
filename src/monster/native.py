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
        app.installNativeEventFilter(self)

    def register(self, bindings: dict[str, tuple[str, object]]) -> list[str]:
        self.clear()
        errors = []
        api = user32()
        if not api:
            return ["Global shortcuts require Windows. Use the system tray on this platform."]
        for index, (name, (shortcut, callback)) in enumerate(bindings.items(), 0x4D01):
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
