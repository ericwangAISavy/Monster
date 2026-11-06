"""Reviewed Win32 primitives. Imported on all platforms, instantiated only on Windows."""

import ctypes
import sys
from ctypes import wintypes

from monster.computer.contracts import KEYS, Bounds, absolute_point

# Fixed-width layout is important: host c_long is not necessarily Windows LONG.
U32, I32, U16 = ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint16
ULONG_PTR = ctypes.c_size_t


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", I32),
        ("dy", I32),
        ("mouseData", U32),
        ("dwFlags", U32),
        ("time", U32),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", U16),
        ("wScan", U16),
        ("dwFlags", U32),
        ("time", U32),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", U32), ("wParamL", U16), ("wParamH", U16)]


class Payload(ctypes.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", U32), ("payload", Payload)]


def enable_dpi_awareness():
    if sys.platform != "win32":
        raise RuntimeError("Real computer tools require Windows.")
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    api.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    if not api.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        # Qt may already have set this. Verify thread context instead of applying scale twice.
        api.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        api.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        if not api.AreDpiAwarenessContextsEqual(
            api.GetThreadDpiAwarenessContext(), ctypes.c_void_p(-4)
        ):
            raise RuntimeError("Per-monitor DPI awareness V2 is required.")
    return api


class WindowsInput:
    def __init__(self):
        self.api = enable_dpi_awareness()
        self.api.SendInput.argtypes = [U32, ctypes.POINTER(Input), ctypes.c_int]
        self.api.SendInput.restype = U32
        self.api.OpenInputDesktop.argtypes = [U32, wintypes.BOOL, U32]
        self.api.OpenInputDesktop.restype = wintypes.HANDLE
        self.api.CloseDesktop.argtypes = [wintypes.HANDLE]
        self.api.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            U32,
            ctypes.POINTER(U32),
        ]
        self.api.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.api.GetAsyncKeyState.restype = ctypes.c_short

    def foreground(self):
        self.api.GetForegroundWindow.restype = wintypes.HWND
        return int(self.api.GetForegroundWindow() or 0)

    def restore_foreground(self, handle):
        self.api.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.api.SetForegroundWindow.restype = wintypes.BOOL
        return bool(handle and self.api.SetForegroundWindow(handle) and self.foreground() == handle)

    def desktop_ready(self):
        handle = self.api.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS
        if not handle:
            return False
        try:
            name = ctypes.create_unicode_buffer(256)
            required = U32()
            ok = self.api.GetUserObjectInformationW(
                handle, 2, name, ctypes.sizeof(name), ctypes.byref(required)
            )
            return bool(ok and name.value.casefold() == "default")
        finally:
            self.api.CloseDesktop(handle)

    def displays(self):
        from mss import mss

        with mss() as capture:
            return [
                Bounds(m["left"], m["top"], m["width"], m["height"]) for m in capture.monitors[1:]
            ]

    def bounds(self):
        monitors = self.displays()
        left, top = min(b.x for b in monitors), min(b.y for b in monitors)
        right = max(b.x + b.width for b in monitors)
        bottom = max(b.y + b.height for b in monitors)
        return Bounds(left, top, right - left, bottom - top)

    def send(self, event):
        if self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) != 1:
            raise RuntimeError("Windows rejected input (possible UIPI/elevation restriction).")

    def move(self, x, y):
        dx, dy = absolute_point(x, y, self.bounds())
        self.send(Input(0, Payload(mi=MouseInput(dx, dy, 0, 0x8000 | 0x4000 | 1, 0, 0))))

    def mouse(self, button, down):
        flags = {"left": (2, 4), "right": (8, 16), "middle": (32, 64)}
        self.send(Input(0, Payload(mi=MouseInput(0, 0, 0, flags[button][not down], 0, 0))))

    def scroll(self, delta, horizontal=False):
        self.send(
            Input(
                0,
                Payload(
                    mi=MouseInput(0, 0, delta & 0xFFFFFFFF, 0x1000 if horizontal else 0x0800, 0, 0)
                ),
            )
        )

    def key(self, key, down):
        vk = KEYS[key]
        extended = key in {
            "WIN",
            "LEFT",
            "UP",
            "RIGHT",
            "DOWN",
            "HOME",
            "END",
            "PAGEUP",
            "PAGEDOWN",
            "DELETE",
        }
        self.send(
            Input(1, Payload(ki=KeyboardInput(vk, 0, (0 if down else 2) | int(extended), 0, 0)))
        )

    def unicode(self, unit, down):
        self.send(Input(1, Payload(ki=KeyboardInput(0, unit, 4 | (0 if down else 2), 0, 0))))

    def physically_held(self):
        # Arming must not inherit human modifier/button state.
        return any(
            self.api.GetAsyncKeyState(vk) & 0x8000 for vk in (1, 2, 4, 0x10, 0x11, 0x12, 0x5B, 0x5C)
        )
