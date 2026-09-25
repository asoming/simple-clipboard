"""Folder controls use synthetic stores and never move a user's history."""

import sqlite3
import sys
import tempfile
import traceback
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QLineEdit,
    QPushButton, QSpinBox, QStyle, QStyleOptionButton, QStyleOptionComboBox,
    QStyleOptionFrame, QStyleOptionSpinBox,
)

from clipboard_app.settings_dialog import SettingsDialog
from clipboard_app.platforms import paste_timeout_message
from clipboard_app.storage_view import StorageDialog
from clipboard_app.store import Store
from clipboard_app.ui import Panel
from tests.test_ui_layout import PreviewMonitor, app


class FolderSettingsTests(unittest.TestCase):
    def setUp(self):
        self.gui_errors = []
        previous_hook = sys.excepthook

        def record_gui_error(kind, value, stack):
            self.gui_errors.append(''.join(traceback.format_exception(kind, value, stack)))

        sys.excepthook = record_gui_error
        self.addCleanup(setattr, sys, 'excepthook', previous_hook)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(self.gui_errors, '\n'.join(self.gui_errors))

    @contextmanager
    def fixture(self, theme='light', unavailable=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = Store(root / 'current' / 'history.sqlite3')
            store.set_setting('intro_seen', True)
            store.set_setting('theme', theme)
            monitor = PreviewMonitor()
            monitor.cancel_pending = Mock()
            panel = Panel(store, monitor, relocate_history=None if unavailable else Mock())
            panel.autostart = Mock()
            panel.autostart.enabled.return_value = False
            try:
                yield panel, store, root
            finally:
                for dialog in panel.findChildren(QDialog):
                    dialog.reject()
                panel.cleanup_timer.stop()
                panel.tray.hide()
                panel.hide()
                panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()
                store.close()

    def choose(self, dialog, folder):
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(folder)):
            dialog.choose_folder()
        app.processEvents()

    def test_picker_cancel_preserves_folder_and_selection_explains_restart(self):
        with self.fixture() as (panel, store, root):
            dialog = SettingsDialog(panel)
            original = store.path.parent.resolve()
            self.choose(dialog, '')
            self.assertEqual(dialog.folder, original)
            self.assertEqual(dialog.buttons.button(QDialogButtonBox.Save).text(), '保存')
            destination = root / '新的历史文件夹'
            destination.mkdir()
            self.choose(dialog, destination)
            self.assertEqual(dialog.folder, destination.resolve())
            self.assertEqual(dialog.folder_path.text(), str(destination.resolve()))
            self.assertEqual(dialog.folder_path.toolTip(), str(destination.resolve()))
            self.assertTrue(dialog.folder_path.isReadOnly())
            self.assertEqual(dialog.buttons.button(QDialogButtonBox.Save).text(), '保存并重启')
            self.choose(dialog, '')
            self.assertEqual(dialog.folder, destination.resolve())
            panel.relocate_history.assert_not_called()

    def test_save_selected_folder_calls_relocation_once_with_resolved_destination(self):
        with self.fixture() as (panel, store, root):
            dialog = SettingsDialog(panel)
            destination = root / 'destination'
            destination.mkdir()
            self.choose(dialog, destination)
            dialog.save()
            panel.relocate_history.assert_called_once_with(destination.resolve())
            self.assertEqual(dialog.result(), QDialog.Accepted)

    def test_relocation_errors_keep_dialog_open_and_explain_failed_folder_switch(self):
        for error in (OSError('目标文件夹不可写，请选择其他目录。'),
                      ValueError('目标已有历史，请选择空文件夹。'),
                      sqlite3.DatabaseError('无法复制历史，请检查磁盘空间。')):
            with self.subTest(error=type(error).__name__), self.fixture() as (panel, store, root):
                panel.relocate_history.side_effect = error
                dialog = SettingsDialog(panel)
                dialog.show()
                destination = root / 'destination'
                destination.mkdir()
                self.choose(dialog, destination)
                dialog.save()
                app.processEvents()
                panel.relocate_history.assert_called_once_with(destination.resolve())
                self.assertTrue(dialog.isVisible())
                self.assertTrue(dialog.error.isVisible())
                self.assertIn('文件夹操作未完成', dialog.error.text())
                self.assertIn(str(error), dialog.error.text())
                self.assertNotEqual(dialog.result(), QDialog.Accepted)

    def test_unavailable_relocation_blocks_other_setting_changes_first(self):
        with self.fixture(unavailable=True) as (panel, store, root):
            previous_limits = store.limits
            dialog = SettingsDialog(panel)
            dialog.show()
            destination = root / 'destination'
            destination.mkdir()
            self.choose(dialog, destination)
            dialog.fields[1].setValue(750)
            dialog.theme.setCurrentIndex(dialog.theme.findData('dark'))
            dialog.save()
            app.processEvents()
            self.assertEqual(store.limits, previous_limits)
            self.assertEqual(store.setting('theme'), 'light')
            self.assertFalse(store.setting('startup_initialized', False))
            panel.monitor.cancel_pending.assert_not_called()
            panel.autostart.set_enabled.assert_not_called()
            self.assertTrue(dialog.isVisible())
            self.assertIn('正常启动应用后重试', dialog.error.text())

    def test_selecting_current_folder_saves_normally_without_relocation(self):
        with self.fixture() as (panel, store, root):
            dialog = SettingsDialog(panel)
            self.choose(dialog, store.path.parent)
            self.assertEqual(dialog.buttons.button(QDialogButtonBox.Save).text(), '保存')
            dialog.save()
            self.assertEqual(dialog.result(), QDialog.Accepted)
            panel.relocate_history.assert_not_called()

    def test_keyboard_capacity_change_persists_when_settings_reopen(self):
        with self.fixture() as (panel, store, root):
            dialog = SettingsDialog(panel)
            dialog.show_section('history')
            dialog.show()
            capacity = dialog.fields[1]
            self.assertEqual(capacity.value(), 500)
            editor = capacity.findChild(QLineEdit)
            QTest.keyClick(editor, Qt.Key_A, Qt.ControlModifier)
            QTest.keyClicks(editor, '750')
            QTest.keyClick(editor, Qt.Key_Tab)
            dialog.save()
            self.assertEqual(dialog.result(), QDialog.Accepted)
            self.assertEqual(store.limits.total_bytes, 750 * 1048576)
            reopened = SettingsDialog(panel)
            self.assertEqual(reopened.fields[1].value(), 750)
            persisted = Store(store.path)
            try:
                self.assertEqual(persisted.limits.total_bytes, 750 * 1048576)
            finally:
                persisted.close()
            panel.relocate_history.assert_not_called()

    def test_storage_folder_link_opens_settings_at_folder_controls(self):
        with self.fixture() as (panel, store, root):
            panel.settings = Mock()
            dialog = StorageDialog(store, panel)
            self.assertEqual(dialog.folder_path.text(), str(store.path.parent.resolve()))
            dialog.folder_button.click()
            panel.settings.assert_called_once_with(focus_folder=True)

    def assert_control_text_fits(self, widget, area, text, check_width=True):
        metrics = widget.fontMetrics()
        required_height = max(metrics.height(), metrics.boundingRect(text).height())
        self.assertGreaterEqual(area.height(), required_height, text)
        if check_width:
            self.assertGreaterEqual(area.width(), metrics.horizontalAdvance(text), text)

    def check_dialog_layout(self, dialog):
        for combo in dialog.findChildren(QComboBox):
            if not combo.isVisible():
                continue
            option = QStyleOptionComboBox()
            combo.initStyleOption(option)
            area = combo.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
            self.assert_control_text_fits(combo, area, combo.currentText())
        for spin in dialog.findChildren(QSpinBox):
            if not spin.isVisible():
                continue
            option = QStyleOptionSpinBox()
            spin.initStyleOption(option)
            area = spin.style().subControlRect(QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxEditField, spin)
            self.assert_control_text_fits(spin, area, spin.text())
        for editor in dialog.findChildren(QLineEdit):
            if not editor.isVisible():
                continue
            option = QStyleOptionFrame()
            option.initFrom(editor)
            area = editor.style().subElementRect(QStyle.SE_LineEditContents, option, editor)
            # Long folder names scroll within the read-only field; the tooltip keeps the full path.
            is_folder = editor.isReadOnly() and editor.accessibleName() == '历史保存文件夹'
            self.assert_control_text_fits(editor, area, editor.text(), check_width=not is_folder)
        for button in dialog.findChildren(QPushButton):
            if not button.isVisible():
                continue
            option = QStyleOptionButton()
            option.initFrom(button)
            option.text = button.text()
            area = button.style().subElementRect(QStyle.SE_PushButtonContents, option, button)
            self.assert_control_text_fits(button, area, button.text())
        footer = QRect(dialog.buttons.mapTo(dialog, QPoint()), dialog.buttons.size())
        scroll = QRect(dialog.scroll.mapTo(dialog, QPoint()), dialog.scroll.size())
        self.assertTrue(dialog.rect().contains(footer))
        self.assertLess(scroll.bottom(), footer.top())
        for button in dialog.buttons.buttons():
            self.assertTrue(button.isVisible())
            self.assertTrue(dialog.rect().contains(QRect(button.mapTo(dialog, QPoint()), button.size())))
        labels = [label for label in dialog.body.findChildren(QLabel)
                  if label.isVisible() and label.wordWrap() and label.text()]
        self.assertTrue(labels)
        for label in labels:
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()), label.text())
        last = max(labels, key=lambda label: label.mapTo(dialog.body, QPoint()).y() + label.height())
        bar = dialog.scroll.verticalScrollBar()
        if not isinstance(dialog, SettingsDialog) or dialog.section != 'general':
            self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum())
        end = last.mapTo(dialog.body, last.rect().bottomLeft())
        dialog.scroll.ensureVisible(end.x(), end.y(), 0, last.fontMetrics().height())
        app.processEvents()
        bottom = last.mapTo(dialog.scroll.viewport(), last.rect().bottomLeft()).y()
        self.assertGreaterEqual(bottom, 0)
        self.assertLessEqual(bottom, dialog.scroll.viewport().rect().bottom())
        self.assertEqual(dialog.scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(footer, QRect(dialog.buttons.mapTo(dialog, QPoint()), dialog.buttons.size()))

    def test_both_dialogs_fit_chinese_controls_with_large_fonts_and_small_windows(self):
        original_font = QFont(app.font())
        try:
            for points in (10, 20):
                for theme in ('light', 'dark'):
                    with self.subTest(points=points, theme=theme):
                        font = QFont(original_font)
                        font.setPointSize(points + 2)
                        app.setFont(font)
                        with self.fixture(theme=theme) as (panel, store, root):
                            settings = SettingsDialog(panel)
                            destination = root / '中文保存文件夹'
                            destination.mkdir()
                            self.choose(settings, destination)
                            storage = StorageDialog(store, panel)
                            views = [(settings, section) for section in settings.section_names] + [(storage, None)]
                            for dialog, section in views:
                                if section is not None:
                                    dialog.show_section(section)
                                    if section == 'history':
                                        dialog.advanced_toggle.setChecked(True)
                                dialog.resize(440, 400)
                                dialog.show()
                                app.processEvents()
                                self.assertEqual(dialog.font(), panel.font())
                                self.check_dialog_layout(dialog)
                                if dialog is settings:
                                    dialog.show_error('测试错误，请选择其他文件夹后重试。' * 3)
                                else:
                                    dialog.result.setText('测试操作结果：当前记录保留，请检查上方读数。' * 3)
                                app.processEvents()
                                self.check_dialog_layout(dialog)
                                dialog.hide()
        finally:
            app.setFont(original_font)

    def test_sections_preserve_pending_fields_and_folder_focus(self):
        with self.fixture() as (panel, store, root):
            dialog = SettingsDialog(panel)
            dialog.show()
            self.assertEqual(dialog.section, 'general')
            dialog.theme.setCurrentIndex(dialog.theme.findData('dark'))
            dialog.show_section('history')
            self.assertFalse(dialog.advanced_fields.isVisible())
            dialog.advanced_toggle.click()
            self.assertTrue(dialog.advanced_fields.isVisible())
            dialog.retention.setCurrentIndex(dialog.retention.findData(365))
            for field, value in zip(dialog.fields, (120, 750, 50)):
                field.setValue(value)
            dialog.show_section('space')
            self.assertEqual(store.limits.total_bytes, 500 * 1048576)
            dialog.focus_folder()
            app.processEvents()
            self.assertEqual(dialog.section, 'history')
            self.assertTrue(dialog.folder_path.isVisible())
            self.assertEqual([field.value() for field in dialog.fields], [120, 750, 50])
            dialog.save()
            self.assertEqual(store.limits.days, 365)
            self.assertEqual(store.limits.count, 120)
            self.assertEqual(store.limits.total_bytes, 750 * 1048576)
            self.assertEqual(store.limits.item_bytes, 50 * 1048576)
            self.assertEqual(store.setting('theme'), 'dark')

    def test_memory_sampling_starts_on_visible_space_page_and_stops_on_exit(self):
        with self.fixture() as (panel, store, root), patch(
                'clipboard_app.storage_view.resident_bytes', return_value=80 * 1048576) as sample:
            dialog = SettingsDialog(panel)
            page = dialog.space_page
            page.timer.setInterval(5)
            self.assertFalse(page.timer.isActive())
            sample.assert_not_called()
            dialog.show()
            dialog.show_section('history')
            app.processEvents()
            sample.assert_not_called()
            dialog.show_section('space')
            app.processEvents()
            self.assertTrue(page.timer.isActive())
            count = sample.call_count
            QTest.qWait(20)
            self.assertGreater(sample.call_count, count)
            dialog.show_section('general')
            self.assertFalse(page.timer.isActive())
            count = sample.call_count
            QTest.qWait(20)
            self.assertEqual(sample.call_count, count)
            dialog.show_section('space')
            dialog.reject()
            self.assertFalse(page.timer.isActive())
            count = sample.call_count
            QTest.qWait(20)
            self.assertEqual(sample.call_count, count)
            legacy = StorageDialog(store, panel)
            self.assertFalse(legacy.timer.isActive())
            legacy.show()
            app.processEvents()
            self.assertTrue(legacy.timer.isActive())
            legacy.hide()
            self.assertFalse(legacy.timer.isActive())

    def test_storage_wrapping_reflows_with_fallback_font_and_dynamic_results(self):
        original_font = QFont(app.font())
        try:
            app.setFont(QFont('DejaVu Sans', 26))
            with patch('clipboard_app.ui.QFontDatabase.families', return_value=[]), self.fixture() as (panel, store, root):
                dialog = StorageDialog(store, panel)
                for width in (440, 720, 440):
                    with self.subTest(width=width):
                        dialog.resize(width, 400)
                        dialog.show()
                        app.processEvents()
                        self.assertGreaterEqual(dialog.legend.font().pointSizeF(), 20)
                        self.check_dialog_layout(dialog)
                        dialog.result.setText('测试操作结果：当前记录保留，请检查上方读数。' * 3)
                        app.processEvents()
                        self.check_dialog_layout(dialog)
                        dialog.result.setText('已完成。')
                        app.processEvents()
                        self.check_dialog_layout(dialog)
        finally:
            app.setFont(original_font)

    def test_paste_timeout_notice_keeps_every_line_with_fallback_font(self):
        original_font = QFont(app.font())
        try:
            app.setFont(QFont('DejaVu Sans', 26))
            with patch('clipboard_app.ui.QFontDatabase.families', return_value=[]), self.fixture() as (panel, store, root):
                panel.resize(440, 460)
                for target_ready in (True, False):
                    with self.subTest(target_ready=target_ready):
                        panel.paste_failed(paste_timeout_message(target_ready))
                        app.processEvents()
                        notice = panel.notice
                        self.assertGreaterEqual(notice.font().pointSizeF(), 20)
                        self.assertGreaterEqual(notice.height(), notice.heightForWidth(notice.width()))
                        self.assertTrue(panel.rect().contains(notice.geometry()))
                        copy_top = panel.copy_button.mapTo(panel, QPoint()).y()
                        self.assertLess(notice.geometry().bottom(), copy_top)
        finally:
            app.setFont(original_font)
