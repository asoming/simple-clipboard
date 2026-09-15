"""Windows hotkeys, foreground verification and Ctrl+V using documented APIs."""

import ctypes as C
import itertools
from ctypes import wintypes as W
from pathlib import Path

from PyQt5.QtCore import QAbstractNativeEventFilter
from PyQt5.QtWidgets import QApplication

from .platforms import NativeBackend, Target, parse_shortcut


class KeyboardInput(C.Structure):
    _fields_ = [('vk', W.WORD), ('scan', W.WORD), ('flags', W.DWORD),
                ('time', W.DWORD), ('extra', C.c_size_t)]


class MouseInput(C.Structure):
    _fields_ = [('dx', W.LONG), ('dy', W.LONG), ('data', W.DWORD),
                ('flags', W.DWORD), ('time', W.DWORD), ('extra', C.c_size_t)]


class HardwareInput(C.Structure):
    _fields_ = [('message', W.DWORD), ('low', W.WORD), ('high', W.WORD)]


class InputData(C.Union):
    _fields_ = [('keyboard', KeyboardInput), ('mouse', MouseInput), ('hardware', HardwareInput)]


class Input(C.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', W.DWORD), ('data', InputData)]


HOTKEY_IDS = itertools.count(0x4000)


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, backend):
        super().__init__()
        self.backend = backend

    def nativeEventFilter(self, event_type, message):
        msg = C.cast(int(message), C.POINTER(W.MSG)).contents
        if msg.message == 0x0312 and msg.wParam == self.backend.hotkey_id:
            self.backend.activated.emit()
            return True, 0
        return False, 0


class WindowsBackend(NativeBackend):
    def __init__(self):
        super().__init__()
        self.user = C.WinDLL('user32', use_last_error=True)
        self.kernel = C.WinDLL('kernel32', use_last_error=True)
        definitions = {
            'RegisterHotKey': ([W.HWND, C.c_int, W.UINT, W.UINT], W.BOOL),
            'UnregisterHotKey': ([W.HWND, C.c_int], W.BOOL),
            'GetForegroundWindow': ([], W.HWND),
            'IsWindow': ([W.HWND], W.BOOL), 'IsIconic': ([W.HWND], W.BOOL),
            'ShowWindow': ([W.HWND, C.c_int], W.BOOL),
            'SetForegroundWindow': ([W.HWND], W.BOOL),
            'IsChild': ([W.HWND, W.HWND], W.BOOL),
            'GetClassNameW': ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
            'GetWindowThreadProcessId': ([W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            'GetAsyncKeyState': ([C.c_int], W.SHORT),
            'SendInput': ([W.UINT, C.POINTER(Input), C.c_int], W.UINT),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        self.kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.kernel.OpenProcess.restype = W.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = W.BOOL
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.kernel.CloseHandle.restype = W.BOOL
        self.hotkey_id = 0
        self.shortcut = ''
        self.event_filter = HotkeyFilter(self)
        QApplication.instance().installNativeEventFilter(self.event_filter)

    def register(self, shortcut):
        letter, modifiers = parse_shortcut(shortcut, {'Ctrl': 2, 'Alt': 1, 'Shift': 4, 'Super': 8})
        if shortcut == self.shortcut:
            return
        identifier = next(HOTKEY_IDS)
        if identifier > 0xBFFF or not self.user.RegisterHotKey(None, identifier, modifiers | 0x4000, ord(letter.upper())):
            raise ValueError('该快捷键已被系统或其他应用占用。原快捷键保持不变。')
        if self.hotkey_id:
            self.user.UnregisterHotKey(None, self.hotkey_id)
        self.hotkey_id, self.shortcut = identifier, shortcut

    def focus(self):
        return self.user.GetForegroundWindow() or 0

    def belongs_to(self, window, ancestor):
        return bool(window and ancestor and (window == ancestor or self.user.IsChild(ancestor, window)))

    def capture_target(self):
        window = self.focus()
        if not window:
            return None
        window_class = C.create_unicode_buffer(256)
        self.user.GetClassNameW(window, window_class, len(window_class))
        pid = W.DWORD()
        self.user.GetWindowThreadProcessId(window, C.byref(pid))
        process = self.kernel.OpenProcess(0x1000, False, pid.value)
        executable = ''
        if process:
            try:
                buffer = C.create_unicode_buffer(32768)
                length = W.DWORD(len(buffer))
                if self.kernel.QueryFullProcessImageNameW(process, 0, buffer, C.byref(length)):
                    executable = Path(buffer.value).name.lower()
            finally:
                self.kernel.CloseHandle(process)
        terminal = executable in {'windowsterminal.exe', 'cmd.exe', 'powershell.exe', 'pwsh.exe', 'mintty.exe', 'wezterm-gui.exe', 'alacritty.exe'}
        terminal = terminal or window_class.value.lower() in {'consolewindowclass', 'cascadia_hosting_window_class', 'mintty'}
        return Target(window, terminal, pid.value)

    def target_ready(self, target):
        pid = W.DWORD()
        self.user.GetWindowThreadProcessId(target.window, C.byref(pid))
        return (not target.context or pid.value == target.context) and self.belongs_to(self.focus(), target.window)

    def activate(self, target):
        if self.user.IsWindow(target.window):
            if self.user.IsIconic(target.window):
                self.user.ShowWindow(target.window, 9)
            # Respect foreground restrictions; do not attach input queues or elevate.
            self.user.SetForegroundWindow(target.window)

    def modifiers_pressed(self):
        return any(self.user.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def send_keys(self, keys):
        events = []
        for key, release in [(key, False) for key in keys] + [(key, True) for key in reversed(keys)]:
            event = Input()
            event.type = 1
            event.keyboard = KeyboardInput(key, 0, 2 if release else 0, 0, 0)
            events.append(event)
        array = (Input * len(events))(*events)
        sent = self.user.SendInput(len(array), array, C.sizeof(Input))
        if sent != len(array):
            releases = (Input * len(keys))()
            for event, key in zip(releases, reversed(keys)):
                event.type = 1
                event.keyboard = KeyboardInput(key, 0, 2, 0, 0)
            self.user.SendInput(len(releases), releases, C.sizeof(Input))
            return False
        return True

    def send_paste(self, target):
        return self.send_keys([0x11, ord('V')])

    def close(self):
        super().close()
        if self.hotkey_id:
            self.user.UnregisterHotKey(None, self.hotkey_id)
            self.hotkey_id = 0
        QApplication.instance().removeNativeEventFilter(self.event_filter)
