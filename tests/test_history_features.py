"""Synthetic regression cases for retention, original images, and direct actions."""
import tempfile
import time
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from PyQt5.QtCore import QBuffer, QIODevice, QPointF, QEvent, Qt
from PyQt5.QtGui import QColor, QImage, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QStyleOptionViewItem

from clipboard_app.content import Snapshot, as_mime, prepare
from clipboard_app.preferences import initialize_startup, LinuxAutostart
from clipboard_app.storage_view import StorageDialog, resident_bytes
from clipboard_app.store import CapacityError, Content, Limits, Store
from clipboard_app.ui import Panel
from test_ui_layout import app, PreviewMonitor


class HistoryFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'history.sqlite3'
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_defaults_and_auto_item_limit(self):
        self.assertEqual(self.store.limits, Limits(30, 0, 500 * 1048576, 0))
        self.store.set_limits(Limits(total_bytes=20))
        self.store.add('a' * 20)
        with self.assertRaises(CapacityError):
            self.store.add('a' * 21)
        for policy in (Limits(days=-1), Limits(total_bytes=0), Limits(item_bytes=-1)):
            with self.assertRaises(ValueError): policy.validate()

    def test_unlimited_time_remains_capacity_bounded_and_favorites_survive(self):
        self.store.set_limits(Limits(days=0, total_bytes=9))
        old = self.store.add_content(Content(text='old'), 1)
        self.store.favorite(old, True)
        self.store.add_content(Content(text='aaa'), 2)
        self.store.add_content(Content(text='bbb'), 3)
        self.store.add_content(Content(text='ccc'), 4)
        self.store.prune()
        self.assertEqual([c.text for c in self.store.list()], ['ccc', 'bbb', 'old'])
        self.assertEqual(self.store.usage(), 9)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.limits.days, 0)
        self.assertEqual(len(self.store.list()), 3)

    def test_upgrade_exact_old_defaults_but_keep_custom_policy(self):
        for saved in (dict(days=7, count=500, total_bytes=100 * 1048576, item_bytes=10 * 1048576),
                      asdict(Limits(days=365, count=123))):
            self.store.set_setting('limits', saved)
            self.store.set_setting('defaults_v04', False)
            self.store.close()
            self.store = Store(self.path)
            expected = Limits() if saved['count'] == 500 else Limits(**saved)
            self.assertEqual(self.store.limits, expected)

    def test_encoded_original_and_metadata_survive_database_and_mime_roundtrip(self):
        image = QImage(120, 80, QImage.Format_RGB32)
        image.fill(QColor('#748fbb'))
        image.setText('Author', 'Synthetic creator')
        for format_name, mime_type in (('PNG', 'image/png'), ('JPEG', 'image/jpeg'), ('BMP', 'image/bmp')):
            with self.subTest(format=format_name):
                buffer = QBuffer()
                buffer.open(QIODevice.WriteOnly)
                self.assertTrue(image.save(buffer, format_name))
                original = bytes(buffer.data())
                content = prepare(Snapshot(image=original), self.store.limits.capture_bytes)
                self.assertEqual(content.image, original)
                identifier = self.store.add_content(content)
                self.store.close()
                self.store = Store(self.path)
                restored = self.store.get(identifier)
                self.assertEqual(restored.image, original)
                mime = as_mime(restored.content)
                self.assertEqual(bytes(mime.data(mime_type)), original)
                self.assertEqual(mime.imageData().size(), image.size())

    def test_startup_default_once_and_explicit_opt_out_is_respected(self):
        manager = LinuxAutostart(self.path.parent, self.path.parent / 'config')
        initialize_startup(self.store, manager)
        self.assertTrue(manager.enabled())
        manager.set_enabled(False)
        initialize_startup(self.store, manager)
        self.assertFalse(manager.enabled())

    def test_startup_failure_is_retryable(self):
        manager = Mock()
        manager.set_enabled.side_effect = OSError('synthetic denied')
        with self.assertRaises(OSError): initialize_startup(self.store, manager)
        self.assertFalse(self.store.setting('startup_initialized', False))

    def test_date_groups_single_click_star_and_no_double_click_paste(self):
        midday = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
        for index, days in enumerate((2, 1, 0, 0)):
            self.store.add_content(Content(text=f'示例 {index}'), (midday - timedelta(days=days)).timestamp())
        self.store.set_setting('intro_seen', True)
        panel = Panel(self.store, PreviewMonitor())
        panel.show()
        app.processEvents()
        try:
            # Double-clicking window chrome/blank space must use QWidget behavior.
            panel.mouseDoubleClickEvent(QMouseEvent(QEvent.MouseButtonDblClick, QPointF(8, 8),
                                                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
            panel.shortcut_error = ""
            panel.recording_notice("Synthetic startup failure")
            panel.open_panel()
            self.assertEqual(panel.notice.text(), "Synthetic startup failure")
            groups = [panel.history.item(i).data(Qt.UserRole + 2) for i in range(4)]
            self.assertTrue(groups[0].startswith('今天'))
            self.assertIsNone(groups[1])
            self.assertTrue(groups[2].startswith('昨天'))
            pasted = []
            panel.history.itemDoubleClicked.connect(lambda *_: pasted.append(True))
            option = QStyleOptionViewItem()
            option.font = panel.history.font()
            index = panel.history.model().index(0, 0)
            option.rect = panel.history.visualRect(index)
            point = panel.history.itemDelegate().star_rect(option, index).center()
            clip_id = panel.history.item(0).data(Qt.UserRole)
            QTest.mouseClick(panel.history.viewport(), Qt.LeftButton, pos=point)
            self.assertTrue(self.store.get(clip_id).pinned)
            QTest.mouseDClick(panel.history.viewport(), Qt.LeftButton, pos=point)
            self.assertEqual(pasted, [])
            panel.filter('favorites')
            self.assertEqual(len(panel.clips), 1)
            panel.pin()
            self.assertEqual(len(panel.clips), 0)
        finally:
            panel.cleanup_timer.stop()
            panel.tray.hide()
            panel.hide()
            panel.deleteLater()
            app.processEvents()

    def test_memory_and_space_actions_do_not_delete_records_or_settings(self):
        identifier = self.store.add('keep me')
        self.store.favorite(identifier, True)
        self.store.add('ordinary')
        self.store.set_setting('paused', True)
        before = self.store.list()
        dialog = StorageDialog(self.store)
        try:
            self.assertGreater(resident_bytes(), 0)
            dialog.compact()
            dialog.release_cache()
            self.assertEqual(self.store.list(), before)
            self.assertTrue(self.store.setting('paused'))
            self.assertTrue(dialog.chart.samples)
        finally:
            dialog.reject()
            self.assertFalse(dialog.timer.isActive())
            dialog.deleteLater()
            app.processEvents()
