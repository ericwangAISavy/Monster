"""Supported Windows UIAccess process launch/query; never injects target input."""

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path


def ui_access_enabled(process=None):
    if sys.platform != 'win32':
        return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    token, enabled, size = wintypes.HANDLE(), wintypes.DWORD(), wintypes.DWORD()
    if not advapi.OpenProcessToken(process if process is not None else kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        return False
    try:
        return bool(advapi.GetTokenInformation(token, 26, ctypes.byref(enabled),
                    ctypes.sizeof(enabled), ctypes.byref(size)) and enabled.value)
    finally:
        kernel.CloseHandle(token)


def trusted_helper(path):
    if sys.platform != 'win32':
        raise RuntimeError('UIAccess renderer requires Windows.')
    import winreg
    path = Path(path).resolve(strict=True)
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                       r'SOFTWARE\Microsoft\Windows\CurrentVersion', 0,
                       winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        roots = []
        for name in ('ProgramFilesDir', 'ProgramFilesDir (x86)'):
            try:
                roots.append(Path(winreg.QueryValueEx(key, name)[0]).resolve())
            except FileNotFoundError:
                pass
    if (path.name.casefold() != 'monsteroverlay.exe' or not path.is_file()
            or not any(path.is_relative_to(root) for root in roots)):
        raise RuntimeError('Signed renderer must be installed beneath Program Files.')
    return path


class OverlayProcess:
    def __init__(self, path, mailbox):
        path = trusted_helper(path)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        shell = ctypes.WinDLL('shell32', use_last_error=True)

        class ExecuteInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('mask', wintypes.ULONG), ('window', wintypes.HWND),
                        ('verb', wintypes.LPCWSTR), ('file', wintypes.LPCWSTR),
                        ('parameters', wintypes.LPCWSTR), ('directory', wintypes.LPCWSTR),
                        ('show', ctypes.c_int), ('instance', wintypes.HINSTANCE),
                        ('idlist', ctypes.c_void_p), ('class_name', wintypes.LPCWSTR),
                        ('class_key', wintypes.HKEY), ('hotkey', wintypes.DWORD),
                        ('icon', wintypes.HANDLE), ('process', wintypes.HANDLE)]

        shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ExecuteInfo)]
        shell.ShellExecuteExW.restype = wintypes.BOOL
        info = ExecuteInfo()
        info.size, info.mask = ctypes.sizeof(info), 0x40 | 0x100 | 0x400
        info.verb, info.file, info.directory = 'open', str(path), str(path.parent)
        info.parameters, info.show = '--mailbox ' + mailbox, 4
        if not shell.ShellExecuteExW(ctypes.byref(info)) or not info.process:
            raise RuntimeError('Signed UIAccess renderer could not be launched.')
        self.handle = info.process
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetExitCodeProcess.restype = wintypes.BOOL
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.ui_access = ui_access_enabled(self.handle)
        if not self.ui_access:
            self.close()
            raise RuntimeError('Renderer was not granted UIAccess by Windows.')

    @property
    def running(self):
        if not self.handle:
            return False
        code = wintypes.DWORD()
        return not self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)) or code.value == 259

    def close(self):
        if not self.handle:
            return True
        if self.running:
            self.kernel.TerminateProcess(self.handle, 0)
            self.kernel.WaitForSingleObject(self.handle, 500)
        if self.running:
            return False
        self.kernel.CloseHandle(self.handle)
        self.handle = None
        return True
