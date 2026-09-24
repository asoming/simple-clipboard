"""User-controllable startup and system appearance on each supported desktop."""

import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication


from .paths import launch_arguments


MARKER = 'X-Clipboard-Managed=true'


def exec_argument(value: str) -> str:
    if '\n' in value or '\r' in value:
        raise ValueError('路径中包含换行，无法设置自启动。')
    # Exec quoting first, then desktop-entry string escaping. No shell is used.
    escaped = ''.join('\\' + c if c in '\\"`$' else c for c in value)
    return '"' + escaped.replace('\\', '\\\\').replace('%', '%%') + '"'


class LinuxAutostart:
    def __init__(self, data_dir: Path, config_dir: Path | None = None):
        directory = config_dir or Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
        self.path = directory / 'autostart' / 'codex-clipboard.desktop'
        self.data_dir = data_dir.resolve()

    def document(self) -> str:
        command = ' '.join(exec_argument(arg) for arg in launch_arguments() + ['--hidden', '--require-history', '--data-dir', str(self.data_dir)])
        return ('[Desktop Entry]\nType=Application\nName=剪贴板\n'
                f'Exec={command}\nTerminal=false\n{MARKER}\n')

    def enabled(self) -> bool:
        if not self.path.is_file():
            return False
        lines = self.path.read_text(encoding='utf-8').splitlines()
        return MARKER in lines and 'Hidden=true' not in lines

    def set_enabled(self, enabled: bool):
        if self.path.exists() and MARKER not in self.path.read_text(encoding='utf-8').splitlines():
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


class MacAutostart:
    label = 'io.github.asoming.simpleclipboard'

    def __init__(self, data_dir, config_dir=None):
        self.data_dir = data_dir.resolve()
        directory = Path.home() / 'Library' / 'LaunchAgents' if config_dir is None else config_dir
        self.path = directory / (self.label + '.plist')

    def document(self):
        return {'Label': self.label, 'ProgramArguments': launch_arguments() + ['--hidden', '--require-history', '--data-dir', str(self.data_dir)], 'RunAtLoad': True}

    def enabled(self):
        if not self.path.exists():
            return False
        document = self.read_document()
        return document.get('Label') == self.label and bool(document.get('RunAtLoad'))

    def read_document(self):
        try:
            document = plistlib.loads(self.path.read_bytes())
            if not isinstance(document, dict):
                raise ValueError('启动项格式不正确，未修改。')
            return document
        except plistlib.InvalidFileException as error:
            raise ValueError('启动项格式不正确，未修改。') from error

    def set_enabled(self, enabled):
        if self.path.exists() and self.read_document().get('Label') != self.label:
            raise ValueError('此启动项不属于剪贴板，未覆盖。')
        if not enabled:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(plistlib.dumps(self.document()))
            temporary.replace(self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)


class WindowsAutostart:
    value_name = 'AsomingSimpleClipboard'

    def __init__(self, data_dir, registry_key=None):
        import winreg
        self.registry = winreg
        self.data_dir = data_dir.resolve()
        self.key = registry_key or r'Software\Microsoft\Windows\CurrentVersion\Run'

    def document(self):
        return subprocess.list2cmdline(launch_arguments() + ['--hidden', '--require-history', '--data-dir', str(self.data_dir)])

    def enabled(self):
        r = self.registry
        try:
            with r.OpenKey(r.HKEY_CURRENT_USER, self.key) as key:
                value, kind = r.QueryValueEx(key, self.value_name)
                return kind in (r.REG_SZ, r.REG_EXPAND_SZ) and bool(value)
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled):
        r = self.registry
        if enabled:
            with r.CreateKeyEx(r.HKEY_CURRENT_USER, self.key, 0, r.KEY_SET_VALUE) as key:
                r.SetValueEx(key, self.value_name, 0, r.REG_SZ, self.document())
        else:
            try:
                with r.OpenKey(r.HKEY_CURRENT_USER, self.key, 0, r.KEY_SET_VALUE) as key:
                    r.DeleteValue(key, self.value_name)
            except FileNotFoundError:
                pass


class SystemAppearance(QObject):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        QApplication.instance().paletteChanged.connect(self.changed)
        self.last_dark = self.dark
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.check)
        if sys.platform == 'win32':
            self.timer.start()

    @property
    def dark(self):
        if sys.platform == 'win32':
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                    return not bool(winreg.QueryValueEx(key, 'AppsUseLightTheme')[0])
            except OSError:
                pass
        return QApplication.palette().color(QPalette.Window).lightness() < 128

    def check(self):
        dark = self.dark
        if dark != self.last_dark:
            self.last_dark = dark
            self.changed.emit()


if sys.platform.startswith('linux'):
    from .appearance_linux import Appearance
    Autostart = LinuxAutostart
else:
    Appearance = SystemAppearance
    Autostart = WindowsAutostart if sys.platform == 'win32' else MacAutostart


def initialize_startup(store, manager=None):
    """Apply the requested default once; an explicit later opt-out stays off."""
    if store.setting('startup_initialized', False):
        return
    manager = manager or Autostart(store.path.parent)
    manager.set_enabled(True)
    store.set_setting('startup_initialized', True)
