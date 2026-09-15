"""macOS hotkeys and application activation; pasting requires Accessibility."""

import ctypes as C
import itertools
import os

import AppKit
import Quartz
from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices

from .platforms import NativeBackend, PlatformUnavailable, Target, parse_shortcut


class EventType(C.Structure):
    _fields_ = [('event_class', C.c_uint32), ('kind', C.c_uint32)]


class HotkeyID(C.Structure):
    _fields_ = [('signature', C.c_uint32), ('identifier', C.c_uint32)]


HOTKEY_IDS = itertools.count(1)
KEY_CODES = {'a':0, 's':1, 'd':2, 'f':3, 'h':4, 'g':5, 'z':6, 'x':7, 'c':8, 'v':9,
             'b':11, 'q':12, 'w':13, 'e':14, 'r':15, 'y':16, 't':17, 'o':31, 'u':32,
             'i':34, 'p':35, 'l':37, 'j':38, 'k':40, 'n':45, 'm':46}
SIGNATURE = int.from_bytes(b'SCBP', 'big')


class MacBackend(NativeBackend):
    default_shortcut = 'Cmd+Shift+V'

    def __init__(self):
        super().__init__()
        self.carbon = C.CDLL('/System/Library/Frameworks/Carbon.framework/Carbon')
        self.accessibility = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        self.accessibility.AXIsProcessTrusted.argtypes = []
        self.accessibility.AXIsProcessTrusted.restype = C.c_bool
        callback_type = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)
        self.callback = callback_type(self._hotkey_event)
        self.carbon.GetApplicationEventTarget.argtypes = []
        self.carbon.GetApplicationEventTarget.restype = C.c_void_p
        self.carbon.InstallEventHandler.argtypes = [C.c_void_p, callback_type, C.c_uint32, C.POINTER(EventType), C.c_void_p, C.POINTER(C.c_void_p)]
        self.carbon.InstallEventHandler.restype = C.c_int32
        self.carbon.RemoveEventHandler.argtypes = [C.c_void_p]
        self.carbon.RemoveEventHandler.restype = C.c_int32
        self.carbon.RegisterEventHotKey.argtypes = [C.c_uint32, C.c_uint32, HotkeyID, C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)]
        self.carbon.RegisterEventHotKey.restype = C.c_int32
        self.carbon.UnregisterEventHotKey.argtypes = [C.c_void_p]
        self.carbon.UnregisterEventHotKey.restype = C.c_int32
        self.carbon.GetEventParameter.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32, C.c_void_p, C.c_size_t, C.c_void_p, C.c_void_p]
        self.carbon.GetEventParameter.restype = C.c_int32
        self.handler = C.c_void_p()
        self.hotkey = C.c_void_p()
        self.identifier = 0
        self.shortcut = ''
        event = EventType(int.from_bytes(b'keyb', 'big'), 6)
        status = self.carbon.InstallEventHandler(self.carbon.GetApplicationEventTarget(), self.callback, 1, C.byref(event), None, C.byref(self.handler))
        if status:
            raise PlatformUnavailable('无法连接 macOS 快捷键事件。')

    def _hotkey_event(self, caller, event, context):
        identifier = HotkeyID()
        status = self.carbon.GetEventParameter(event, int.from_bytes(b'----', 'big'), int.from_bytes(b'hkid', 'big'), None, C.sizeof(identifier), None, C.byref(identifier))
        if not status and identifier.signature == SIGNATURE and identifier.identifier == self.identifier:
            self.activated.emit()
            return 0
        return -9874  # Let another registered event handler inspect the event.

    def register(self, shortcut):
        letter, modifiers = parse_shortcut(shortcut, {'Cmd':256, 'Super':256, 'Shift':512, 'Alt':2048, 'Ctrl':4096})
        if shortcut == self.shortcut:
            return
        new_hotkey = C.c_void_p()
        identifier = next(HOTKEY_IDS)
        status = self.carbon.RegisterEventHotKey(KEY_CODES[letter], modifiers, HotkeyID(SIGNATURE, identifier), self.carbon.GetApplicationEventTarget(), 0, C.byref(new_hotkey))
        if status:
            raise ValueError('该快捷键已被系统或其他应用占用。原快捷键保持不变。')
        if self.hotkey:
            self.carbon.UnregisterEventHotKey(self.hotkey)
        self.hotkey, self.identifier, self.shortcut = new_hotkey, identifier, shortcut

    def window_id(self, panel):
        return os.getpid()

    def focus(self):
        application = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(application.processIdentifier()) if application else 0

    def belongs_to(self, window, ancestor):
        return bool(window and window == ancestor)

    def capture_target(self):
        application = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if not application:
            return None
        bundle = str(application.bundleIdentifier() or '').lower()
        terminal = bundle in {'com.apple.terminal', 'com.googlecode.iterm2', 'net.kovidgoyal.kitty', 'org.alacritty', 'com.github.wez.wezterm'}
        pid = int(application.processIdentifier())
        return Target(pid, terminal, self.front_window(pid))

    def front_window(self, pid):
        windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements, Quartz.kCGNullWindowID)
        for window in windows or ():
            if int(window.get(Quartz.kCGWindowOwnerPID, 0)) == pid and int(window.get(Quartz.kCGWindowLayer, -1)) == 0:
                return int(window.get(Quartz.kCGWindowNumber, 0))
        return 0

    def target_ready(self, target):
        return bool(target.context and self.focus() == target.window and self.front_window(target.window) == target.context)

    def activate(self, target):
        application = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(target.window)
        if application and not application.isTerminated():
            application.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)

    def permission_message(self):
        if not self.accessibility.AXIsProcessTrusted():
            return '内容已复制。自动粘贴需要 macOS 辅助功能权限；可在「自动粘贴权限」中打开系统设置，或手动粘贴。'
        return ''

    def open_permissions(self):
        QDesktopServices.openUrl(QUrl('x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'))

    def modifiers_pressed(self):
        flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateCombinedSessionState)
        mask = Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskControl | Quartz.kCGEventFlagMaskShift | Quartz.kCGEventFlagMaskAlternate
        return bool(flags & mask)

    def send_paste(self, target):
        for pressed in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, 9, pressed)
            if event is None:
                return False
            Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        return True

    def close(self):
        super().close()
        if self.hotkey:
            self.carbon.UnregisterEventHotKey(self.hotkey)
            self.hotkey = C.c_void_p()
        if self.handler:
            self.carbon.RemoveEventHandler(self.handler)
            self.handler = C.c_void_p()
