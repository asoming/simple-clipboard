"""Layout regression checks use synthetic data and never connect a clipboard."""

import tempfile
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from PyQt5.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QFontMetrics, QImage
from PyQt5.QtWidgets import QApplication, QStyle, QStyleOptionButton, QStyleOptionComboBox, QStyleOptionToolButton, QStyleOptionViewItem

from clipboard_app.store import Content, Store
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
                            for combo in (panel.type_filter, panel.paste_mode):
                                for selection in range(combo.count()):
                                    combo.setCurrentIndex(selection)
                                    app.processEvents()
                                    option = QStyleOptionComboBox()
                                    combo.initStyleOption(option)
                                    area = combo.style().subControlRect(
                                        QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
                                    metrics = combo.fontMetrics()
                                    self.assertGreaterEqual(area.width(), metrics.horizontalAdvance(combo.currentText()))
                                    self.assertGreaterEqual(area.height(), max(metrics.height(), metrics.boundingRect(combo.currentText()).height()))
                                combo.setCurrentIndex(0)
                            button = panel.paste_button
                            option = QStyleOptionButton()
                            option.initFrom(button)
                            option.text = button.text()
                            area = button.style().subElementRect(QStyle.SE_PushButtonContents, option, button)
                            self.assertGreaterEqual(area.width(), button.fontMetrics().horizontalAdvance(button.text()))
                            self.assertGreaterEqual(area.height(), max(button.fontMetrics().height(), button.fontMetrics().boundingRect(button.text()).height()))
                            for button in (panel.preview_button, panel.copy_button, panel.more):
                                self.assertEqual(button.toolButtonStyle(), Qt.ToolButtonIconOnly)
                                self.assertTrue(button.accessibleName())
                                self.assertTrue(button.toolTip())
                                self.assertFalse(button.icon().isNull())
                                option = QStyleOptionToolButton()
                                button.initStyleOption(option)
                                area = button.style().subControlRect(
                                    QStyle.CC_ToolButton, option, QStyle.SC_ToolButton, button)
                                self.assertGreaterEqual(area.width(), button.iconSize().width())
                                self.assertGreaterEqual(area.height(), button.iconSize().height())
                            for control in (panel.search, panel.type_filter, panel.paste_mode,
                                            panel.preview_button, panel.copy_button, panel.paste_button, panel.more):
                                self.assertTrue(panel.rect().contains(QRect(control.mapTo(panel, QPoint()), control.size())), control.objectName())
                            self.assertFalse(panel.state.isVisible())
                            panel.monitor.toggle_ignore()
                            app.processEvents()
                            self.assertGreaterEqual(panel.state.width(), panel.state.fontMetrics().horizontalAdvance(panel.state.text()))
                            self.assertTrue(panel.state.isVisible())
                            panel.monitor.toggle_ignore()
                            app.processEvents()
                            self.assertFalse(panel.state.isVisible())
                        finally:
                            panel.cleanup_timer.stop()
                            panel.tray.hide()
                            panel.hide()
                            panel.deleteLater()
                            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                            store.close()
        finally:
            app.setFont(original)

    def test_default_preview_and_search_share_a_compact_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / 'history.sqlite3')
            store.add('默认可见的合成预览')
            panel = Panel(store, PreviewMonitor())
            try:
                panel.show()
                panel.resize(820, 530)
                app.processEvents()
                self.assertTrue(panel.preview_button.isChecked())
                self.assertTrue(panel.preview_pane.isVisible())
                self.assertEqual(panel.preview_pane.text.toPlainText(), '默认可见的合成预览')
                self.assertEqual(panel.splitter.orientation(), Qt.Horizontal)
                search = QRect(panel.search.mapTo(panel, QPoint()), panel.search.size())
                types = QRect(panel.type_filter.mapTo(panel, QPoint()), panel.type_filter.size())
                self.assertLess(search.right(), types.left())
                self.assertTrue(search.top() <= types.center().y() <= search.bottom())
                self.assertLess(max(search.bottom(), types.bottom()), panel.splitter.geometry().top())
                self.assertLess(panel.stack.width(), panel.preview_pane.width())
            finally:
                panel.cleanup_timer.stop()
                panel.tray.hide()
                panel.hide()
                panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                store.close()

    def test_type_dropdown_filters_records_and_stays_in_sync_with_open_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / 'history.sqlite3')
            store.set_setting('preview_visible', False)
            ordinary = store.add('普通合成文本')
            favorite = store.add('收藏合成文本')
            store.favorite(favorite, True)
            image = QImage(4, 4, QImage.Format_RGB32)
            image.fill(Qt.white)
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            self.assertTrue(image.save(buffer, 'PNG'))
            picture = store.add_content(Content(image=bytes(buffer.data()), width=4, height=4))
            panel = Panel(store, PreviewMonitor())
            try:
                for kind, expected in (
                    ('text', {ordinary, favorite}), ('image', {picture}),
                    ('favorites', {favorite}), ('all', {ordinary, favorite, picture}),
                ):
                    with self.subTest(kind=kind):
                        index = panel.type_filter.findData(kind)
                        self.assertGreaterEqual(index, 0)
                        panel.type_filter.setCurrentIndex(index)
                        self.assertEqual({clip.id for clip in panel.clips}, expected)
                panel.filter('favorites')
                self.assertEqual(panel.type_filter.currentData(), 'favorites')
                panel.search.setText('收藏')
                self.assertEqual({clip.id for clip in panel.clips}, {favorite})
                panel.type_filter.setCurrentIndex(panel.type_filter.findData('image'))
                self.assertEqual(panel.clips, [])
                self.assertEqual(panel.stack.currentWidget(), panel.empty)
                self.assertFalse(panel.copy_button.isEnabled())
                self.assertFalse(panel.paste_button.isEnabled())
                panel.open_panel()
                self.assertEqual(panel.search.text(), '')
                self.assertEqual(panel.type_filter.currentData(), 'all')
                self.assertEqual(len(panel.clips), 3)
            finally:
                panel.cleanup_timer.stop()
                panel.tray.hide()
                panel.hide()
                panel.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                store.close()

    def test_single_line_summary_elides_unicode_and_expands_with_font(self):
        text = '中文搜索 🧑‍💻 与 English，数字 0123456789，完整内容可预览。' * 6
        for pixels in (14, 24, 32):
            font = QFont(app.font())
            font.setPixelSize(pixels)
            for width in (120, 300, 500):
                with self.subTest(pixels=pixels, width=width):
                    lines = HistoryDelegate.title_lines(text, font, width)
                    self.assertEqual(len(lines), 1)
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
            store.set_setting('preview_visible', False)
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
            store.add('窄窗口中的中文预览。' * 30)
            panel = Panel(store, PreviewMonitor())
            try:
                panel.show()
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
                store.set_setting('preview_visible', False)
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
            content = '完整长文本合成测试。' * 50_000
            identity = store.add(content)
            panel = Panel(store, PreviewMonitor())
            try:
                with patch.object(store, 'get', wraps=store.get) as get:
                    panel.show()
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

    def test_long_unbroken_text_stays_lazy_until_explicit_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / 'history.sqlite3')
            content = 'abcdefghij' * 25_000
            identity = store.add(content)
            monitor = PreviewMonitor()
            monitor.copy = Mock()
            panel = Panel(store, monitor)
            try:
                with patch.object(store, 'get', wraps=store.get) as get:
                    panel.show()
                    app.processEvents()
                    self.assertEqual(get.call_count, 0)
                    self.assertEqual(panel.preview_pane.clip_id, identity)
                    self.assertTrue(panel.preview_pane.full_button.isVisible())
                    self.assertLess(len(panel.preview_pane.text.toPlainText()), 1000)
                    panel.copy_only()
                    self.assertEqual(get.call_count, 1)
                self.assertEqual(monitor.copy.call_args.args[0].text, content)
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
