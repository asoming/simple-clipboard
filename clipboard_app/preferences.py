"""Opt-in desktop startup and a read-only GNOME appearance observer."""

import os
import tempfile
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt5.QtDBus import QDBus, QDBusConnection, QDBusMessage, QDBusVariant
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication


MARKER = 'X-Clipboard-Managed=true'


def exec_argument(value: str) -> str:
    if '\n' in value or '\r' in value:
        raise ValueError('路径中包含换行，无法设置自启动。')
    # Exec quoting first, then desktop-entry string escaping. No shell is used.
    escaped = ''.join('\\' + c if c in '\\"`$' else c for c in value)
    return '"' + escaped.replace('\\', '\\\\').replace('%', '%%') + '"'


class Autostart:
    def __init__(self, data_dir: Path, config_dir: Path | None = None):
        directory = config_dir or Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
        self.path = directory / 'autostart' / 'codex-clipboard.desktop'
        self.data_dir = data_dir.resolve()

    def document(self) -> str:
        launcher = Path(__file__).resolve().parents[1] / 'start.sh'
        command = f'{exec_argument(str(launcher))} --hidden --data-dir {exec_argument(str(self.data_dir))}'
        return ('[Desktop Entry]\nType=Application\nName=剪贴板\n'
                f'Exec={command}\nTerminal=false\n{MARKER}\n')

    def enabled(self) -> bool:
        return self.path.is_file() and self.path.read_text() == self.document()

    def set_enabled(self, enabled: bool):
        if self.path.exists() and MARKER not in self.path.read_text().splitlines():
            raise ValueError('此自启动文件属于其他配置，未覆盖。')
        if not enabled:
            if self.path.exists():
                self.path.unlink()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(self.document())
            temporary.replace(self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


class Appearance(QObject):
    changed = pyqtSignal()
    service = 'org.freedesktop.portal.Desktop'
    path = '/org/freedesktop/portal/desktop'
    interface = 'org.freedesktop.portal.Settings'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scheme = 0
        self.bus = QDBusConnection.sessionBus()
        message = QDBusMessage.createMethodCall(self.service, self.path, self.interface, 'Read')
        message.setArguments(['org.freedesktop.appearance', 'color-scheme'])
        reply = self.bus.call(message, QDBus.Block, 500)
        if reply.type() == QDBusMessage.ReplyMessage and reply.arguments():
            self.scheme = self.unwrap(reply.arguments()[0])
        self.bus.connect(self.service, self.path, self.interface, 'SettingChanged', self.on_setting)
        QApplication.instance().paletteChanged.connect(self.changed)

    @staticmethod
    def unwrap(value):
        while isinstance(value, QDBusVariant):
            value = value.variant()
        return value if value in (0, 1, 2) else 0

    @property
    def dark(self):
        if self.scheme:
            return self.scheme == 1
        return QApplication.palette().color(QPalette.Window).lightness() < 128

    @pyqtSlot(str, str, QDBusVariant)
    def on_setting(self, namespace, key, value):
        if namespace == 'org.freedesktop.appearance' and key == 'color-scheme':
            scheme = self.unwrap(value)
            if scheme != self.scheme:
                self.scheme = scheme
                self.changed.emit()
