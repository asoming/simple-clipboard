"""Read-only previews use synthetic objects and never open the clipboard."""

import unittest
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QFont, QImage
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import (
    QApplication, QFrame, QPlainTextEdit, QProxyStyle, QStyle, QStyleFactory,
    QStyleOptionButton, QWidget,
)

from clipboard_app.content import png_bytes
from clipboard_app.preview import PreviewDialog, PreviewPane
from clipboard_app.store import Clip, Store


app = QApplication.instance() or QApplication([])


def text_clip(text='合成预览文本\n  English with whitespace  ', **kwargs):
    return Clip(id=1, text=text, name='', pinned=False, copied_at=0,
                size=len(text.encode('utf-8')), **kwargs)


def image_clip():
    image = QImage(640, 420, QImage.Format_ARGB32)
    image.fill(QColor('#667493'))
    raw = png_bytes(image)
    return Clip(id=2, text='', name='', pinned=False, copied_at=0,
                size=len(raw), image=raw, width=640, height=420)


class PreviewTests(unittest.TestCase):
    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def make_pane(self, width=300, height=300):
        pane = PreviewPane()
        pane.resize(width, height)
        pane.show()
        app.processEvents()
        self.addCleanup(pane.deleteLater)
        return pane

    def test_text_is_read_only_and_html_is_never_rendered(self):
        pane = self.make_pane()
        clip = text_clip('<b>这是文字</b>\n\t原样保留 ', html='<p>网页</p><img src="https://invalid.example/image">')
        pane.set_clip(clip)
        self.assertTrue(pane.text.isReadOnly())
        self.assertEqual(pane.text.toPlainText(), clip.text)
        details = {label.text(): value.text() for label, value in pane.details if label.isVisible()}
        self.assertIn('网页文本', details['类型'])
        self.assertIn('网页原格式', pane.text.toolTip())
        self.assertFalse(pane.controls.isVisible())
        QTest.keyClicks(pane.text, 'attempted edit')
        self.assertEqual(pane.text.toPlainText(), clip.text)

    def test_image_fit_resize_original_mode_and_payload_preservation(self):
        pane = self.make_pane()
        clip = image_clip()
        original = clip.image
        pane.set_clip(clip)
        app.processEvents()
        first = pane.image_label.pixmap().size()
        self.assertLessEqual(first.width(), pane.image_scroll.viewport().width())
        self.assertLessEqual(first.height(), pane.image_scroll.viewport().height())
        details = {label.text(): value.text() for label, value in pane.details if label.isVisible()}
        self.assertEqual(details['类型'], 'PNG')
        self.assertEqual(details['尺寸'], '640 × 420')
        pane.resize(520, 440)
        app.processEvents()
        self.assertGreater(pane.image_label.pixmap().width(), first.width())
        pane.image_mode.setChecked(True)
        app.processEvents()
        self.assertEqual(pane.image_label.pixmap().size(), pane._pixmap.size())
        self.assertEqual(pane.image_label.pixmap().width(), 640)
        self.assertGreater(pane.image_scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(clip.image, original)

    def test_switch_and_clear_release_old_preview_content(self):
        pane = self.make_pane()
        pane.set_clip(image_clip())
        pane.set_clip(text_clip())
        self.assertTrue(pane._pixmap.isNull())
        self.assertFalse(pane.image_label.pixmap())
        self.assertFalse(pane.image_mode.isVisible())
        pane.clear()
        self.assertIsNone(pane.clip_id)
        self.assertEqual(pane.text.toPlainText(), '')
        self.assertTrue(pane._pixmap.isNull())
        pane.set_clip(None)
        self.assertEqual(pane.stack.currentWidget(), pane.empty)

    def test_invalid_image_has_recoverable_empty_state(self):
        pane = self.make_pane()
        pane.set_clip(replace(image_clip(), image=b'not an image'))
        self.assertTrue(pane._pixmap.isNull())
        self.assertIn('无法显示', pane.empty.text())
        pane.set_clip(text_clip('下一条仍可显示'))
        self.assertEqual(pane.text.toPlainText(), '下一条仍可显示')

    def test_large_font_narrow_pane_keeps_content_and_metadata_readable(self):
        for points in (10, 20, 26):
            with self.subTest(points=points):
                parent = QWidget()
                font = QFont(parent.font())
                font.setPointSize(points)
                parent.setFont(font)
                pane = PreviewPane(parent)
                pane.setWindowFlag(Qt.Window)
                pane.resize(220, 160)
                pane.move(137, 83)
                pane.set_clip(text_clip('中文与 English\n' * 40, html='<p>合成网页</p>'))
                pane.show()
                app.processEvents()
                try:
                    self.assertEqual(pane.font(), parent.font())
                    self.assertLess(pane.stack.geometry().bottom(), pane.metadata.geometry().top())
                    for label, value in pane.details:
                        if label.isVisible():
                            value_rect = QRect(value.mapTo(pane.metadata, QPoint()), value.size())
                            self.assertTrue(pane.metadata.rect().contains(value_rect))
                            self.assertGreaterEqual(value.height(), value.heightForWidth(value.width()))
                            self.assertGreaterEqual(label.height(), label.fontMetrics().boundingRect(label.text()).height())
                    self.assertLessEqual(pane.body.width(), pane.scroll.viewport().width())
                    self.assertGreater(pane.scroll.verticalScrollBar().maximum(), 0)
                    pane.set_summary(8, '已有摘要，完整内容等待主动打开。', 42 * 1048576)
                    app.processEvents()
                    self.assertLessEqual(pane.body.width(), pane.scroll.viewport().width())
                    option = QStyleOptionButton()
                    option.initFrom(pane.full_button)
                    option.text = pane.full_button.text()
                    area = pane.full_button.style().subElementRect(QStyle.SE_PushButtonContents, option, pane.full_button)
                    self.assertGreaterEqual(area.width(), pane.full_button.fontMetrics().horizontalAdvance(option.text))
                    self.assertGreaterEqual(area.height(), pane.full_button.fontMetrics().boundingRect(option.text).height())
                finally:
                    parent.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_full_preview_reuses_pane_and_clears_on_close(self):
        parent = QWidget()
        font = QFont(parent.font())
        font.setPointSize(20)
        parent.setFont(font)
        dialog = PreviewDialog(text_clip(), parent)
        dialog.show()
        app.processEvents()
        try:
            self.assertEqual(dialog.font(), parent.font())
            self.assertEqual(dialog.pane.text.toPlainText(), text_clip().text)
            dialog.reject()
            self.assertEqual(dialog.pane.text.toPlainText(), '')
        finally:
            parent.deleteLater()

    def test_summary_body_fits_with_wide_scrollbar_and_frame(self):
        class WideScrollbarStyle(QProxyStyle):
            def pixelMetric(self, metric, option=None, widget=None):
                if metric == QStyle.PM_ScrollBarExtent:
                    return 29
                return super().pixelMetric(metric, option, widget)

        for points in (20, 26):
            with self.subTest(points=points):
                parent = QWidget()
                font = QFont(parent.font())
                font.setPointSize(points)
                parent.setFont(font)
                pane = PreviewPane(parent)
                pane.setWindowFlag(Qt.Window)
                pane.resize(220, 160)
                style = WideScrollbarStyle(QStyleFactory.create('Fusion'))
                style.setParent(pane)
                pane.scroll.setStyle(style)
                pane.scroll.verticalScrollBar().setStyle(style)
                pane.scroll.setFrameShape(QFrame.StyledPanel)
                pane.set_summary(8, '已有摘要，完整内容等待主动打开。', 42 * 1048576)
                pane.show()
                app.processEvents()
                try:
                    self.assertGreater(pane.scroll.verticalScrollBar().maximum(), 0)
                    self.assertLessEqual(pane.body.width(), pane.scroll.viewport().width())
                finally:
                    parent.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_inline_preview_routes_paste_copy_and_dismiss_without_rewriting_text(self):
        pane = PreviewPane(enable_actions=True)
        pane.set_clip(text_clip())
        pane.show()
        self.addCleanup(pane.deleteLater)
        paste = QSignalSpy(pane.paste_requested)
        copy = QSignalSpy(pane.copy_requested)
        dismiss = QSignalSpy(pane.dismiss_requested)
        for widget in (pane.text, pane.image_mode, pane.image_scroll, pane.details[0][1]):
            QTest.keyClick(widget, Qt.Key_Return)
            QTest.keyClick(widget, Qt.Key_Enter, Qt.ControlModifier)
            QTest.keyClick(widget, Qt.Key_Escape)
        self.assertEqual((len(paste), len(copy), len(dismiss)), (4, 4, 4))
        QTest.keyClick(pane.text, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(len(copy), 4)
        self.assertEqual(pane.text.toPlainText(), text_clip().text)

    def test_standalone_preview_does_not_request_clipboard_actions(self):
        dialog = PreviewDialog(text_clip())
        dialog.show()
        self.addCleanup(dialog.deleteLater)
        paste = QSignalSpy(dialog.pane.paste_requested)
        copy = QSignalSpy(dialog.pane.copy_requested)
        QTest.keyClick(dialog.pane.text, Qt.Key_Return)
        QTest.keyClick(dialog.pane.text, Qt.Key_Enter, Qt.ControlModifier)
        self.assertEqual((len(paste), len(copy)), (0, 0))
        QTest.keyClick(dialog.pane.text, Qt.Key_Escape)
        self.assertFalse(dialog.isVisible())

    def test_large_content_summary_requires_explicit_full_preview_request(self):
        pane = self.make_pane(width=220, height=180)
        copied_at = datetime(2026, 9, 25, 16, 8).timestamp()
        pane.set_summary(8, '已有摘要中的中文和 English…', 42 * 1048576, copied_at=copied_at)
        requested = QSignalSpy(pane.full_requested)
        app.processEvents()
        self.assertEqual(pane.clip_id, 8)
        self.assertEqual(pane.text.toPlainText(), '已有摘要中的中文和 English…')
        details = {label.text(): value.text() for label, value in pane.details if label.isVisible()}
        self.assertEqual(details, {'预览': '摘要', '存储大小': '42.0 MiB', '复制时间': '2026-09-25 16:08'})
        self.assertIn('复制仍使用原内容', pane.full_button.toolTip())
        self.assertEqual(len(requested), 0)
        pane.full_button.click()
        self.assertEqual(len(requested), 1)
        pane.set_clip(text_clip())
        self.assertFalse(pane.full_button.isVisible())
        pane.clear()
        self.assertFalse(pane.full_button.isVisible())

    def test_large_full_text_uses_horizontal_scroll_and_small_text_restores_wrap(self):
        pane = self.make_pane()
        text = 'a' * 250_000
        pane.set_clip(text_clip(text))
        app.processEvents()
        self.assertEqual(pane.text.toPlainText(), text)
        self.assertEqual(pane.text.lineWrapMode(), QPlainTextEdit.NoWrap)
        self.assertGreater(pane.text.horizontalScrollBar().maximum(), 0)
        pane.set_clip(text_clip('一段中文与 English'))
        self.assertEqual(pane.text.lineWrapMode(), QPlainTextEdit.WidgetWidth)
        pane.set_clip(text_clip(text))
        pane.clear()
        self.assertEqual(pane.text.toPlainText(), '')
        self.assertEqual(pane.text.lineWrapMode(), QPlainTextEdit.WidgetWidth)
        pane.set_summary(8, '合成摘要', len(text))
        self.assertEqual(pane.text.lineWrapMode(), QPlainTextEdit.WidgetWidth)

    def test_pane_tracks_real_panel_font_after_build_and_style_changes(self):
        from clipboard_app.ui import Panel
        from tests.test_ui_layout import PreviewMonitor
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        original = QFont(app.font())
        try:
            with tempfile.TemporaryDirectory() as directory:
                store = Store(Path(directory) / 'history.sqlite3')
                with patch('clipboard_app.ui.Autostart', return_value=Mock()):
                    panel = Panel(store, PreviewMonitor())
                try:
                    for points in (12, 22):
                        font = QFont(original)
                        font.setPointSize(points)
                        app.setFont(font)
                        panel._style()
                        app.processEvents()
                        pane = panel.preview_pane
                        self.assertEqual(pane.font().family(), panel.font().family())
                        self.assertEqual(pane.font().pointSizeF(), panel.font().pointSizeF())
                        self.assertEqual(pane.text.font().pointSizeF(), panel.history.font().pointSizeF())
                finally:
                    panel.cleanup_timer.stop()
                    panel.tray.hide()
                    panel.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                    store.close()
        finally:
            app.setFont(original)
