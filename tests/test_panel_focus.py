"""Panel target selection uses synthetic window IDs, without clipboard access."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QEvent, QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication, QDialog

from clipboard_app.platforms import Target
from clipboard_app.store import Store
from clipboard_app.ui import Panel
from tests.test_ui_layout import PreviewMonitor


app = QApplication.instance() or QApplication([])


class FocusBackend(QObject):
    activated = pyqtSignal()
    paste_failed = pyqtSignal(str)
    default_shortcut = 'Ctrl+Alt+V'

    def __init__(self):
        super().__init__()
        self.current = Target(101000)
        self.activations = []

    def register(self, shortcut):
        pass

    def capture_target(self):
        return self.current

    def focus(self):
        return self.current.window if self.current else 0

    def belongs_to(self, window, ancestor):
        return bool(window and window == ancestor)

    def window_id(self, widget):
        return int(widget.winId())

    def activate(self, target):
        self.activations.append(target)
        self.current = target


class PanelFocusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'history.sqlite3')
        self.backend = FocusBackend()
        with patch('clipboard_app.ui.Autostart', return_value=Mock()):
            self.panel = Panel(self.store, PreviewMonitor(), self.backend)

    def tearDown(self):
        self.panel.cleanup_timer.stop()
        self.panel.tray.hide()
        self.panel.hide()
        self.panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.store.close()
        self.temp.cleanup()

    def test_hotkey_retargets_visible_panel_from_another_application(self):
        self.panel.open_panel()
        self.assertEqual(self.panel.target, Target(101000))
        next_target = Target(202000, terminal=True, context=456)
        self.backend.current = next_target
        self.backend.activated.emit()
        self.assertTrue(self.panel.isVisible())
        self.assertEqual(self.panel.target, next_target)
        self.assertEqual(self.backend.current.window, self.backend.window_id(self.panel))

    def test_repeated_open_with_panel_active_keeps_external_target(self):
        self.panel.open_panel()
        self.panel.open_panel()
        self.assertEqual(self.panel.target, Target(101000))

    def test_own_dialog_is_never_captured_as_a_paste_target(self):
        self.panel.open_panel()
        dialog = QDialog(self.panel)
        self.backend.current = Target(self.backend.window_id(dialog))
        self.panel.open_panel()
        self.assertEqual(self.panel.target, Target(101000))
        dialog.deleteLater()

    def test_hotkey_dismisses_when_panel_itself_is_active(self):
        self.panel.open_panel()
        self.backend.activated.emit()
        self.assertFalse(self.panel.isVisible())
        self.assertEqual(self.backend.current, Target(101000))

    def test_reopen_from_external_window_refreshes_target_even_when_visible(self):
        self.panel.open_panel()
        self.backend.current = Target(303000)
        self.panel.open_panel()
        self.assertEqual(self.panel.target, Target(303000))
