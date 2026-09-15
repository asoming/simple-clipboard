"""Layout regression checks use synthetic data and never connect a clipboard."""

import tempfile
import unittest
from pathlib import Path

from PyQt5.QtCore import QObject, Qt, pyqtSignal
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
                            self.assertLess(panel.stack.geometry().bottom(), panel.preview_button.geometry().top())
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
                            for button in (*panel.filter_buttons, panel.preview_button, panel.pin_button,
                                           panel.copy_button, panel.paste_button):
                                option = QStyleOptionButton()
                                option.initFrom(button)
                                option.text = button.text()
                                area = button.style().subElementRect(QStyle.SE_PushButtonContents, option, button)
                                self.assertGreaterEqual(area.width(), button.fontMetrics().horizontalAdvance(button.text()), button.text())
                                self.assertGreaterEqual(area.height(), button.fontMetrics().height(), button.text())
                                self.assertTrue(panel.rect().contains(button.geometry()), button.text())
                            panel.monitor.toggle_ignore()
                            app.processEvents()
                            self.assertGreaterEqual(panel.state.width(), panel.state.fontMetrics().horizontalAdvance(panel.state.text()))
                        finally:
                            panel.cleanup_timer.stop()
                            panel.tray.hide()
                            panel.hide()
                            panel.deleteLater()
                            app.processEvents()
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
                app.processEvents()
                store.close()
