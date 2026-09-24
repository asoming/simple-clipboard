"""X11 hotkeys and verified focus restoration. No shell commands or clipboard reads."""

import ctypes as C
from .platforms import (
    PASTE_CHECK_INTERVAL_MS, PASTE_MAX_ATTEMPTS, PlatformUnavailable, Target,
    paste_timeout_message,
)

from PyQt5.QtCore import QObject, QSocketNotifier, QTimer, pyqtSignal


class KeyEvent(C.Structure):
    _fields_ = [
        ("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
        ("display", C.c_void_p), ("window", C.c_ulong), ("root", C.c_ulong),
        ("subwindow", C.c_ulong), ("time", C.c_ulong), ("x", C.c_int),
        ("y", C.c_int), ("x_root", C.c_int), ("y_root", C.c_int),
        ("state", C.c_uint), ("keycode", C.c_uint), ("same_screen", C.c_int),
    ]


class ClientData(C.Union):
    _fields_ = [("b", C.c_char * 20), ("s", C.c_short * 10), ("l", C.c_long * 5)]


class ClientMessage(C.Structure):
    _fields_ = [
        ("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
        ("display", C.c_void_p), ("window", C.c_ulong), ("message_type", C.c_ulong),
        ("format", C.c_int), ("data", ClientData),
    ]


class Event(C.Union):
    _fields_ = [("type", C.c_int), ("key", KeyEvent), ("client", ClientMessage), ("pad", C.c_long * 24)]


class ErrorEvent(C.Structure):
    _fields_ = [
        ("type", C.c_int), ("display", C.c_void_p), ("resourceid", C.c_ulong),
        ("serial", C.c_ulong), ("error_code", C.c_ubyte),
        ("request_code", C.c_ubyte), ("minor_code", C.c_ubyte),
    ]


class ClassHint(C.Structure):
    _fields_ = [("res_name", C.c_void_p), ("res_class", C.c_void_p)]


class ModifierMap(C.Structure):
    _fields_ = [("max_keypermod", C.c_int), ("modifiermap", C.POINTER(C.c_ubyte))]


X11Unavailable = PlatformUnavailable

class X11(QObject):
    activated = pyqtSignal()
    paste_failed = pyqtSignal(str)
    paste_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.lib = C.CDLL("libX11.so.6")
        self.xtst = C.CDLL("libXtst.so.6")
        self._bind()
        self.display = self.lib.XOpenDisplay(None)
        if not self.display:
            raise X11Unavailable("无法连接 X11 桌面。请在 Ubuntu 的 Xorg 会话中运行。")
        self.root = self.lib.XDefaultRootWindow(self.display)
        self.error_code = 0
        self._callback_type = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(ErrorEvent))
        self._error_callback = self._callback_type(self._on_error)
        self.previous_handler = self.lib.XSetErrorHandler(C.cast(self._error_callback, C.c_void_p))
        self.keycode = 0
        self.modifiers = 0
        self.last_timestamp = 0
        self.ignored_modifiers = self._lock_modifiers()
        self.notifier = QSocketNotifier(self.lib.XConnectionNumber(self.display), QSocketNotifier.Read, self)
        self.notifier.activated.connect(self._events)
        self.pending_target = None
        self.paste_attempts = 0
        self.paste_timer = QTimer(self)
        self.paste_timer.setInterval(PASTE_CHECK_INTERVAL_MS)
        self.paste_timer.timeout.connect(self._finish_paste)

    default_shortcut = "Ctrl+Alt+V"

    def window_id(self, panel):
        return int(panel.winId())

    def permission_message(self):
        return ""

    def _bind(self):
        d, w, i, u = C.c_void_p, C.c_ulong, C.c_int, C.c_uint
        signatures = {
            "XOpenDisplay": ([C.c_char_p], d), "XCloseDisplay": ([d], i),
            "XDefaultRootWindow": ([d], w), "XConnectionNumber": ([d], i),
            "XInternAtom": ([d, C.c_char_p, i], w), "XStringToKeysym": ([C.c_char_p], w),
            "XKeysymToKeycode": ([d, w], C.c_ubyte),
            "XGrabKey": ([d, i, u, w, i, i, i], i), "XUngrabKey": ([d, i, u, w], i),
            "XPending": ([d], i), "XNextEvent": ([d, C.POINTER(Event)], i),
            "XFlush": ([d], i), "XSync": ([d, i], i),
            "XSetErrorHandler": ([d], d),
            "XGetInputFocus": ([d, C.POINTER(w), C.POINTER(i)], i),
            "XSetInputFocus": ([d, w, i, w], i), "XRaiseWindow": ([d, w], i),
            "XSendEvent": ([d, w, i, C.c_long, C.POINTER(Event)], i),
            "XGetWindowProperty": ([d, w, w, C.c_long, C.c_long, i, w, C.POINTER(w),
                                    C.POINTER(i), C.POINTER(w), C.POINTER(w), C.POINTER(d)], i),
            "XQueryTree": ([d, w, C.POINTER(w), C.POINTER(w), C.POINTER(C.POINTER(w)), C.POINTER(u)], i),
            "XGetClassHint": ([d, w, C.POINTER(ClassHint)], i), "XFree": ([d], i),
            "XQueryKeymap": ([d, C.c_char_p], i),
            "XGetModifierMapping": ([d], C.POINTER(ModifierMap)),
            "XFreeModifiermap": ([C.POINTER(ModifierMap)], i),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, result
        self.xtst.XTestFakeKeyEvent.argtypes = [d, u, i, w]
        self.xtst.XTestFakeKeyEvent.restype = i
        self.xtst.XTestQueryExtension.argtypes = [d] + [C.POINTER(i)] * 4
        self.xtst.XTestQueryExtension.restype = i

    def _on_error(self, display, event):
        if display == self.display:
            self.error_code = event.contents.error_code
        elif self.previous_handler:
            self._callback_type(self.previous_handler)(display, event)
        return 0

    def atom(self, name: str) -> int:
        return self.lib.XInternAtom(self.display, name.encode(), False)

    def code(self, key: str) -> int:
        return self.lib.XKeysymToKeycode(self.display, self.lib.XStringToKeysym(key.encode()))

    def _lock_modifiers(self) -> list[int]:
        mapping = self.lib.XGetModifierMapping(self.display)
        masks = {2}  # Caps Lock.
        if mapping:
            lock_codes = {self.code("Num_Lock"), self.code("Scroll_Lock")}
            for mod in range(8):
                for slot in range(mapping.contents.max_keypermod):
                    index = mod * mapping.contents.max_keypermod + slot
                    if mapping.contents.modifiermap[index] in lock_codes - {0}:
                        masks.add(1 << mod)
            self.lib.XFreeModifiermap(mapping)
        combinations = {0}
        for mask in masks:
            combinations |= {value | mask for value in list(combinations)}
        return sorted(combinations)

    def register(self, shortcut: str):
        parts = shortcut.split("+")
        aliases = {"Ctrl": 4, "Alt": 8, "Shift": 1, "Super": 64}
        if len(parts) < 2 or any(part not in aliases for part in parts[:-1]):
            raise ValueError("请使用 Ctrl、Alt、Shift、Super 加字母，例如 Ctrl+Alt+V。")
        key = parts[-1].lower()
        if len(key) != 1 or not key.isascii() or not key.isalpha():
            raise ValueError("快捷键最后一项需为英文字母。")
        mods = 0
        for part in parts[:-1]:
            mods |= aliases[part]
        if not mods & (4 | 8 | 64):
            raise ValueError("请至少包含 Ctrl、Alt 或 Super，避免影响正常打字。")
        code = self.code(key)
        if not code:
            raise ValueError("当前键盘布局不支持这个快捷键。")
        if (code, mods) == (self.keycode, self.modifiers):
            return
        self.lib.XSync(self.display, False)
        self.error_code = 0
        for extra in self.ignored_modifiers:
            self.lib.XGrabKey(self.display, code, mods | extra, self.root, False, 1, 1)
        self.lib.XSync(self.display, False)
        if self.error_code:
            for extra in self.ignored_modifiers:
                self.lib.XUngrabKey(self.display, code, mods | extra, self.root)
            self.lib.XSync(self.display, False)
            raise ValueError("该快捷键已被占用或注册失败，请换一个。原快捷键保持不变。")
        if self.keycode:
            for extra in self.ignored_modifiers:
                self.lib.XUngrabKey(self.display, self.keycode, self.modifiers | extra, self.root)
        self.keycode, self.modifiers = code, mods
        self.lib.XFlush(self.display)
        self._events()

    def _events(self, *_):
        while self.display and self.lib.XPending(self.display):
            event = Event()
            self.lib.XNextEvent(self.display, C.byref(event))
            if event.type == 2 and event.key.keycode == self.keycode:
                self.last_timestamp = event.key.time
                self.activated.emit()

    def property_window(self, window: int, name: str) -> int:
        actual_type, count, after = C.c_ulong(), C.c_ulong(), C.c_ulong()
        fmt, data = C.c_int(), C.c_void_p()
        self.lib.XGetWindowProperty(self.display, window, self.atom(name), 0, 1, False, 0,
                                   C.byref(actual_type), C.byref(fmt), C.byref(count),
                                   C.byref(after), C.byref(data))
        value = 0
        if data:
            if fmt.value == 32 and count.value:
                value = C.cast(data, C.POINTER(C.c_ulong))[0]
            self.lib.XFree(data)
        return value

    def focus(self) -> int:
        window, revert = C.c_ulong(), C.c_int()
        self.lib.XGetInputFocus(self.display, C.byref(window), C.byref(revert))
        return window.value

    def parent(self, window: int) -> int:
        root, parent, count = C.c_ulong(), C.c_ulong(), C.c_uint()
        children = C.POINTER(C.c_ulong)()
        status = self.lib.XQueryTree(self.display, window, C.byref(root), C.byref(parent), C.byref(children), C.byref(count))
        if children:
            self.lib.XFree(children)
        return parent.value if status else 0

    def belongs_to(self, window: int, ancestor: int) -> bool:
        for _ in range(32):
            if window == ancestor:
                return True
            if window in (0, 1, self.root):
                return False
            window = self.parent(window)
        return False

    def capture_target(self) -> Target | None:
        window = self.property_window(self.root, "_NET_ACTIVE_WINDOW") or self.focus()
        if window in (0, 1, self.root):
            return None
        hint = ClassHint()
        classes = []
        if self.lib.XGetClassHint(self.display, window, C.byref(hint)):
            for value in (hint.res_name, hint.res_class):
                if value:
                    classes.append(C.string_at(value).decode(errors="replace").lower())
                    self.lib.XFree(value)
        terminal = any(token in " ".join(classes) for token in (
            "terminal", "xterm", "alacritty", "kitty", "konsole", "tilix", "terminator", "wezterm",
        ))
        return Target(window, terminal)

    def activate(self, target: Target):
        if self.property_window(self.root, "_NET_SUPPORTING_WM_CHECK"):
            event = Event()
            event.client.type = 33
            event.client.window = target.window
            event.client.message_type = self.atom("_NET_ACTIVE_WINDOW")
            event.client.format = 32
            event.client.data.l[0] = 2
            # A hotkey timestamp becomes stale while the user searches the panel.
            event.client.data.l[1] = 0  # CurrentTime, source=2 (desktop utility).
            self.lib.XSendEvent(self.display, self.root, False, (1 << 20) | (1 << 19), C.byref(event))
        else:
            self.lib.XRaiseWindow(self.display, target.window)
            self.lib.XSetInputFocus(self.display, target.window, 2, 0)
        self.lib.XFlush(self.display)

    def paste(self, target: Target | None):
        self.paste_timer.stop()
        self.pending_target = None
        if target is None:
            self.paste_failed.emit("内容已复制。未找到原输入窗口，请回到目标位置手动粘贴。")
            return
        info = [C.c_int() for _ in range(4)]
        if not self.xtst.XTestQueryExtension(self.display, *(C.byref(value) for value in info)):
            self.paste_failed.emit("内容已复制。系统不支持自动粘贴，请手动粘贴。")
            return
        self.pending_target = target
        self.paste_attempts = 0
        self.activate(target)
        self.paste_timer.start()

    def modifiers_pressed(self) -> bool:
        state = C.create_string_buffer(32)
        self.lib.XQueryKeymap(self.display, state)
        for key in ("Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R", "Super_L", "Super_R"):
            code = self.code(key)
            if code and state.raw[code // 8] & (1 << (code % 8)):
                return True
        return False

    def _finish_paste(self):
        target = self.pending_target
        if target is None:
            return
        self.paste_attempts += 1
        target_ready = self.belongs_to(self.focus(), target.window)
        if target_ready and not self.modifiers_pressed():
            self.paste_timer.stop()
            keys = ["Control_L", "Shift_L", "v"] if target.terminal else ["Control_L", "v"]
            # Never synthesize Return/Enter: pasting must not submit a message or command.
            for key in keys:
                self.xtst.XTestFakeKeyEvent(self.display, self.code(key), True, 0)
            for key in reversed(keys):
                self.xtst.XTestFakeKeyEvent(self.display, self.code(key), False, 0)
            self.lib.XFlush(self.display)
            self.pending_target = None
            self.paste_requested.emit()
        elif self.paste_attempts >= PASTE_MAX_ATTEMPTS:
            self.paste_timer.stop()
            self.pending_target = None
            self.paste_failed.emit(paste_timeout_message(target_ready))

    def close(self):
        self.paste_timer.stop()
        self.notifier.setEnabled(False)
        if self.display:
            self.lib.XSync(self.display, False)
            self.lib.XCloseDisplay(self.display)
            self.display = None
            self.lib.XSetErrorHandler(self.previous_handler)
