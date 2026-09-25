"""Read-only previews use synthetic objects and never open the clipboard."""

import unittest
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QFont, QImage
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QStyle, QStyleOptionButton, QWidget

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
        self.assertTrue(pane.note.isVisible())
        self.assertIn('网页文本', pane.metadata.text())
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
        self.assertIn('PNG', pane.metadata.text())
        self.assertIn('640 × 420', pane.metadata.text())
        pane.resize(520, 440)
        app.processEvents()
        self.assertGreater(pane.image_label.pixmap().width(), first.width())
        pane.image_mode.setChecked(True)
        app.processEvents()
        self.assertEqual(pane.image_label.pixmap().size(), pane._pixmap.size())
        self.assertEqual(pane.image_label.pixmap().width(), 640)
        self.assertGreater(pane.image_scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(clip.image, original)

    def test_switch_and_close_release_old_preview_content(self):
        pane = self.make_pane()
        pane.set_clip(image_clip())
        pane.set_clip(text_clip())
        self.assertTrue(pane._pixmap.isNull())
        self.assertFalse(pane.image_label.pixmap())
        self.assertFalse(pane.image_mode.isVisible())
        requested = QSignalSpy(pane.close_requested)
        pane.close_button.click()
        self.assertEqual(len(requested), 1)
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

    def test_large_font_narrow_pane_keeps_header_and_wrapped_metadata_readable(self):
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
                    close_rect = QRect(pane.close_button.mapTo(pane, QPoint()), pane.close_button.size())
                    self.assertTrue(pane.rect().contains(close_rect))
                    self.assertLess(pane.close_button.geometry().bottom(), pane.scroll.geometry().top())
                    for label in (pane.metadata, pane.note):
                        self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
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
            self.assertFalse(dialog.pane.close_button.isVisible())
            dialog.reject()
            self.assertEqual(dialog.pane.text.toPlainText(), '')
        finally:
            parent.deleteLater()

    def test_inline_preview_routes_paste_copy_and_dismiss_without_rewriting_text(self):
        pane = PreviewPane(enable_actions=True)
        pane.set_clip(text_clip())
        pane.show()
        self.addCleanup(pane.deleteLater)
        paste = QSignalSpy(pane.paste_requested)
        copy = QSignalSpy(pane.copy_requested)
        dismiss = QSignalSpy(pane.dismiss_requested)
        for widget in (pane.text, pane.image_mode, pane.image_scroll):
            QTest.keyClick(widget, Qt.Key_Return)
            QTest.keyClick(widget, Qt.Key_Enter, Qt.ControlModifier)
            QTest.keyClick(widget, Qt.Key_Escape)
        self.assertEqual((len(paste), len(copy), len(dismiss)), (3, 3, 3))
        QTest.keyClick(pane.text, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual(len(copy), 3)
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
        pane.set_summary(8, '已有摘要中的中文和 English…', 42 * 1048576)
        requested = QSignalSpy(pane.full_requested)
        app.processEvents()
        self.assertEqual(pane.clip_id, 8)
        self.assertEqual(pane.text.toPlainText(), '已有摘要中的中文和 English…')
        self.assertIn('原内容已完整保留', pane.note.text())
        self.assertIn('42.0 MiB', pane.metadata.text())
        self.assertEqual(len(requested), 0)
        pane.full_button.click()
        self.assertEqual(len(requested), 1)
        pane.set_clip(text_clip())
        self.assertFalse(pane.full_button.isVisible())
        pane.clear()
        self.assertFalse(pane.full_button.isVisible())

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
