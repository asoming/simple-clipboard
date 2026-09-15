"""Linux appearance changes from the desktop Settings portal."""

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt5.QtDBus import QDBus, QDBusConnection, QDBusMessage, QDBusVariant
from PyQt5.QtGui import QPalette
from PyQt5.QtWidgets import QApplication

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
