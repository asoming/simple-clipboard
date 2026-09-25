"""Layout regression checks use synthetic data and never connect a clipboard."""

import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from PyQt5.QtCore import QCoreApplication, QEvent, QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtWidgets import QApplication, QStyle, QStyleOptionButton, QStyleOptionComboBox, QStyleOptionViewItem

from clipboard_app.store import Store
from clipboard_app.ui import HistoryDelegate, Panel


app = QApplication.instance() or QApplication([])


class PreviewMonitor(QObject):
    changed = pyqtSignal()
    state_changed = pyqtSignal()
    notice = pyqtSignal(str)
    paused = ignore_next = processing = False

    def pause(self, value):
        self.paused = value
        self.state_changed.emit()

    def toggle_ignore(self):
        self.ignore_next = not self.ignore_next
        self.state_changed.emit()


class LayoutTests(unittest.TestCase):
    def test_controls_fit_chinese_labels_at_narrow_width_and_larger_fonts(self):
        original = QFont(app.font())
        try:
            for points in (10, 16, 20):
                for theme in ('light', 'dark'):
                    with self.subTest(points=points, theme=theme), tempfile.TemporaryDirectory() as temporary:
                        font = QFont(original)
                        font.setPointSize(points)
                        app.setFont(font)
                        store = Store(Path(temporary) / 'history.sqlite3')
                        store.set_setting('intro_seen', True)
                        store.set_setting('theme', theme)
                        clip = store.add('中文与 English 🧑‍💻 混合的长摘要。' * 8)
                        store.favorite(clip, True)
                        panel = Panel(store, PreviewMonitor())
                        try:
                            panel.resize(440, 460)
                            panel.show()
                            app.processEvents()
                            self.assertGreaterEqual(panel.history.viewport().height(),
                                                    panel.history.visualItemRect(panel.history.item(0)).height() + 4)
                            self.assertLess(panel.splitter.geometry().bottom(), panel.footer.geometry().top())
                            for mode in (0, 1):
                                panel.paste_mode.setCurrentIndex(mode)
                                app.processEvents()
                                option = QStyleOptionComboBox()
                                panel.paste_mode.initStyleOption(option)
                                area = panel.paste_mode.style().subControlRect(
                                    QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, panel.paste_mode)
                                metrics = panel.paste_mode.fontMetrics()
                                self.assertGreaterEqual(area.width(), metrics.horizontalAdvance(panel.paste_mode.currentText()))
                                self.assertGreaterEqual(area.height(), metrics.height())
                            for button in (*panel.filter_buttons, panel.preview_button,
                                           panel.copy_button, panel.paste_button):
                                option = QStyleOptionButton()
                                option.initFrom(button)
                                option.text = button.text()
                                area = button.style().subElementRect(QStyle.SE_PushButtonContents, option, button)
                                self.assertGreaterEqual(area.width(), button.fontMetrics().horizontalAdvance(button.text()), button.text())
                                self.assertGreaterEqual(area.height(), button.fontMetrics().height(), button.text())
                                self.assertTrue(panel.rect().contains(QRect(button.mapTo(panel, QPoint()), button.size())), button.text())
                            panel.monitor.toggle_ignore()
                            app.processEvents()
                            self.assertGreaterEqual(panel.state.width(), panel.state.fontMetrics().horizontalAdvance(panel.state.text()))
                        finally:
                            panel.cleanup_timer.stop()
                            panel.tray.hide()
                            panel.hide()
                            panel.deleteLater()
                            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                            store.close()
        finally:
            app.setFont(original)

    def test_two_line_summary_preserves_unicode_and_expands_with_font(self):
        text = '中文搜索 🧑‍💻 与 English，数字 0123456789，完整内容可预览。' * 6
        for pixels in (14, 24, 32):
            font = QFont(app.font())
            font.setPixelSize(pixels)
            for width in (120, 300, 500):
                with self.subTest(pixels=pixels, width=width):
                    lines = HistoryDelegate.title_lines(text, font, width)
                    self.assertEqual(len(lines), 2)
                    for line in lines:
                        line.encode('utf-8')
                        self.assertLessEqual(QFontMetrics(font).horizontalAdvance(line), width)
                    self.assertTrue(lines[-1].endswith('…'))
        font = QFont(app.font())
        option = QStyleOptionViewItem()
        option.font = font
        option.rect.setWidth(200)
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / 'history.sqlite3')
            store.add(text)
            panel = Panel(store, PreviewMonitor())
            try:
                index = panel.history.model().index(0, 0)
                delegate = panel.history.itemDelegate()
                small = delegate.sizeHint(option, index).height()
                option.font.setPixelSize(32)
                large = delegate.sizeHint(option, index).height()
                self.assertGreater(large, small)
            finally:
                panel.cleanup_timer.stop()
                panel.tray.hide()
                panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                store.close()

    def test_preview_loads_only_when_open_and_releases_hidden_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / 'history.sqlite3')
            store.set_setting('intro_seen', True)
            store.add('第一条合成内容')
            store.add('第二条合成内容')
            panel = Panel(store, PreviewMonitor())
            try:
                with patch.object(store, 'get', wraps=store.get) as get:
                    panel.show()
                    panel.refresh()
                    panel.move_selection(1)
                    app.processEvents()
                    self.assertEqual(get.call_count, 0)
                    panel.preview()
                    app.processEvents()
                    self.assertEqual(get.call_count, 1)
                    self.assertEqual(panel.preview_pane.text.toPlainText(), '第一条合成内容')
                    panel.refresh()
                    self.assertEqual(get.call_count, 1)
                    panel.move_selection(-1)
                    self.assertEqual(get.call_count, 2)
                    self.assertEqual(panel.preview_pane.text.toPlainText(), '第二条合成内容')
                    panel.hide()
                    self.assertEqual(panel.preview_pane.text.toPlainText(), '')
                    self.assertIsNone(panel.preview_pane.clip_id)
                    panel.show()
                    self.assertEqual(get.call_count, 3)
                    panel.preview()
                    panel.move_selection(1)
                    self.assertEqual(get.call_count, 3)
                    self.assertEqual(panel.preview_pane.text.toPlainText(), '')
            finally:
                panel.cleanup_timer.stop()
                panel.tray.hide()
                panel.hide()
                panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                store.close()

    def test_preview_and_footer_adapt_without_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / 'history.sqlite3')
            store.set_setting('intro_seen', True)
            store.add('窄窗口中的中文预览。' * 30)
            panel = Panel(store, PreviewMonitor())
            try:
                panel.show()
                panel.preview()
                for width, orientation in ((880, Qt.Horizontal), (440, Qt.Vertical)):
                    panel.resize(width, 680)
                    app.processEvents()
                    self.assertEqual(panel.splitter.orientation(), orientation)
                    self.assertTrue(panel.preview_pane.isVisible())
                    self.assertFalse(panel.stack.geometry().intersects(panel.preview_pane.geometry()))
                    self.assertLess(panel.splitter.geometry().bottom(), panel.footer.geometry().top())
                panel.search.setText('无匹配内容')
                self.assertIsNone(panel.preview_pane.clip_id)
                self.assertEqual(panel.preview_pane.text.toPlainText(), '')
            finally:
                panel.cleanup_timer.stop()
                panel.tray.hide()
                panel.hide()
                panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                store.close()

    def test_open_preview_stays_inside_short_high_scale_screen(self):
        original = QFont(app.font())
        font = QFont(original)
        font.setPointSize(10)
        app.setFont(font)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                store = Store(Path(temporary) / 'history.sqlite3')
                store.set_setting('intro_seen', True)
                store.add('合成短屏幕内容')
                panel = Panel(store, PreviewMonitor())
                screen = Mock()
                screen.availableGeometry.return_value = QRect(0, 0, 960, 540)
                try:
                    with patch.object(panel, 'screen', return_value=screen):
                        panel.resize(440, 460)
                        panel.show()
                        app.processEvents()
                        panel.preview()
                        app.processEvents()
                        self.assertTrue(screen.availableGeometry().contains(panel.frameGeometry()),
                                        panel.frameGeometry())
                finally:
                    panel.cleanup_timer.stop()
                    panel.tray.hide()
                    panel.hide()
                    panel.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    store.close()
        finally:
            app.setFont(original)

    def test_large_inline_preview_keeps_navigation_lazy_and_original_intact(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / 'history.sqlite3')
            store.set_setting('intro_seen', True)
            content = '完整长文本合成测试。' * 50_000
            identity = store.add(content)
            panel = Panel(store, PreviewMonitor())
            try:
                panel.show()
                with patch.object(store, 'get', wraps=store.get) as get:
                    panel.preview()
                    app.processEvents()
                    self.assertEqual(get.call_count, 0)
                    self.assertEqual(panel.preview_pane.clip_id, identity)
                    self.assertTrue(panel.preview_pane.full_button.isVisible())
                    self.assertLess(len(panel.preview_pane.text.toPlainText()), 1000)
                self.assertEqual(store.get(identity).text, content)
            finally:
                panel.cleanup_timer.stop()
                panel.tray.hide()
                panel.hide()
                panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                store.close()

    def test_wrapped_label_shrinks_after_width_grows(self):
        from clipboard_app.widgets import WrappedLabel
        label = WrappedLabel('这是一段用于验证换行高度能够回落的中文说明。' * 4)
        try:
            label.resize(130, 100)
            label.show()
            app.processEvents()
            narrow = label.minimumHeight()
            label.resize(700, narrow)
            app.processEvents()
            self.assertLess(label.minimumHeight(), narrow)
            self.assertGreaterEqual(label.minimumHeight(), label.fontMetrics().height())
            label.setText('短说明')
            self.assertLessEqual(label.minimumHeight(), max(label.fontMetrics().height(), label.fontMetrics().boundingRect('短说明').height()) + 4)
        finally:
            label.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
