"""Small native-platform boundary shared by the panel and clipboard actions."""

import os
import sys
from dataclasses import dataclass

from PyQt5.QtCore import QObject, QTimer, pyqtSignal


PASTE_CHECK_INTERVAL_MS = 30
PASTE_MAX_ATTEMPTS = 50


def paste_timeout_message(target_ready: bool) -> str:
    if target_ready:
        return '内容已复制，但仍有 Ctrl、Alt、Shift 或系统键未松开。请松开按键后重试，或手动粘贴。'
    return '内容已复制，但焦点未回到目标窗口。请先点目标输入框，再用全局快捷键打开后粘贴；也可手动粘贴。'


@dataclass(frozen=True)
class Target:
    window: int
    terminal: bool = False
    context: int = 0


class PlatformUnavailable(RuntimeError):
    pass


class NativeBackend(QObject):
    activated = pyqtSignal()
    paste_failed = pyqtSignal(str)
    paste_requested = pyqtSignal()
    default_shortcut = 'Ctrl+Alt+V'

    def __init__(self):
        super().__init__()
        self.pending_target = None
        self.paste_attempts = 0
        self.paste_timer = QTimer(self)
        self.paste_timer.setInterval(PASTE_CHECK_INTERVAL_MS)
        self.paste_timer.timeout.connect(self._finish_paste)

    def window_id(self, panel):
        return int(panel.winId())

    def permission_message(self):
        return ''

    def paste(self, target):
        self.paste_timer.stop()
        self.pending_target = None
        if target is None:
            self.paste_failed.emit('内容已复制。未找到原输入窗口，请手动粘贴。')
            return
        message = self.permission_message()
        if message:
            self.paste_failed.emit(message)
            return
        self.pending_target = target
        self.paste_attempts = 0
        self.activate(target)
        self.paste_timer.start()

    def target_ready(self, target):
        return self.belongs_to(self.focus(), target.window)

    def _finish_paste(self):
        target = self.pending_target
        if target is None:
            return
        self.paste_attempts += 1
        target_ready = self.target_ready(target)
        if target_ready and not self.modifiers_pressed():
            self.paste_timer.stop()
            self.pending_target = None
            message = self.permission_message()
            if message:
                self.paste_failed.emit(message)
            elif self.send_paste(target):
                self.paste_requested.emit()
            else:
                self.paste_failed.emit('内容已复制，但系统未接受自动粘贴。目标可能以更高权限运行，请手动粘贴。')
        elif self.paste_attempts >= PASTE_MAX_ATTEMPTS:
            self.paste_timer.stop()
            self.pending_target = None
            self.paste_failed.emit(paste_timeout_message(target_ready))

    def close(self):
        self.paste_timer.stop()


def parse_shortcut(shortcut, aliases):
    parts = shortcut.split('+')
    if len(parts) < 2 or any(part not in aliases for part in parts[:-1]):
        raise ValueError('请使用 ' + '、'.join(aliases) + ' 加英文字母。')
    letter = parts[-1].lower()
    if len(letter) != 1 or not letter.isascii() or not letter.isalpha():
        raise ValueError('快捷键最后一项需为英文字母。')
    if set(parts[:-1]) == {'Shift'}:
        raise ValueError('请增加 Ctrl、Alt 或系统修饰键，避免影响正常输入。')
    modifiers = 0
    for part in parts[:-1]:
        modifiers |= aliases[part]
    return letter, modifiers


def create_backend():
    if sys.platform == 'win32':
        from .windows import WindowsBackend
        return WindowsBackend()
    if sys.platform == 'darwin':
        from .macos import MacBackend
        return MacBackend()
    if sys.platform.startswith('linux'):
        if os.environ.get('XDG_SESSION_TYPE') == 'wayland':
            raise PlatformUnavailable('当前 Linux 版本仅支持 X11。请使用 Xorg 会话。')
        from .x11 import X11
        return X11()
    raise PlatformUnavailable('当前系统尚未适配。')
