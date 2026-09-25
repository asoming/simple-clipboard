"""Dialog behavior and CJK layout without history or clipboard access."""

import unittest

from PyQt5.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QApplication, QDialogButtonBox, QStyle, QStyleOptionButton,
    QStyleOptionFrame, QWidget,
)

from clipboard_app.dialogs import ClearHistoryDialog, TextInputDialog, text_input
from clipboard_app.widgets import WrappedLabel


app = QApplication.instance() or QApplication([])


class DialogTests(unittest.TestCase):
    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_text_input_accept_and_cancel_keep_value_semantics(self):
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        for accept in (False, True):
            with self.subTest(accept=accept):
                def finish():
                    dialog = next(widget for widget in app.topLevelWidgets() if isinstance(widget, TextInputDialog))
                    dialog.editor.setText('新的收藏名称')
                    dialog.accept() if accept else dialog.reject()
                QTimer.singleShot(0, finish)
                value, accepted = text_input(parent, '修改收藏名称', '名称', '原名称')
                self.assertEqual(value, '新的收藏名称')
                self.assertEqual(accepted, accept)
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_clear_defaults_and_reset_favorites_link_preserve_existing_behavior(self):
        dialog = ClearHistoryDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.favorites.isChecked())
        self.assertFalse(dialog.current.isChecked())
        self.assertFalse(dialog.reset.isChecked())
        dialog.reset.setChecked(True)
        self.assertTrue(dialog.favorites.isChecked())
        self.assertFalse(dialog.favorites.isEnabled())
        self.assertFalse(dialog.current.isChecked())
        dialog.reset.setChecked(False)
        self.assertTrue(dialog.favorites.isChecked())
        self.assertTrue(dialog.favorites.isEnabled())
        dialog.favorites.setFocus()
        QTest.keyClick(dialog.favorites, Qt.Key_Space)
        self.assertFalse(dialog.favorites.isChecked())

    def test_wrapped_option_label_is_clickable(self):
        dialog = ClearHistoryDialog()
        dialog.show()
        app.processEvents()
        self.addCleanup(dialog.deleteLater)
        checkbox = dialog.current
        label = checkbox.parentWidget().label
        QTest.mouseClick(label, Qt.LeftButton, pos=label.rect().center())
        self.assertTrue(checkbox.isChecked())
        dialog.reset.setChecked(True)
        self.assertFalse(dialog.favorites.parentWidget().label.isEnabled())
        dialog.reset.setChecked(False)
        self.assertTrue(dialog.favorites.parentWidget().label.isEnabled())

    def test_large_font_narrow_dialogs_keep_text_and_footer_visible(self):
        for points in (10, 20, 26):
            for dialog_type in ('text', 'clear'):
                with self.subTest(points=points, dialog=dialog_type):
                    parent = QWidget()
                    font = QFont(parent.font())
                    font.setPointSize(points)
                    parent.setFont(font)
                    parent.setStyleSheet('QPushButton {padding: 7px 14px; border: 1px solid #ddd;} QLineEdit {padding: 8px 12px; border: 1px solid #ddd;}')
                    dialog = (TextInputDialog(parent, '修改名称', '这是可以换行的长说明，中文与 English 混排。' * 4, '中文输入')
                              if dialog_type == 'text' else ClearHistoryDialog(parent))
                    dialog.resize(320, 230)
                    dialog.show()
                    app.processEvents()
                    try:
                        self.assertEqual(dialog.font(), parent.font())
                        self.assertEqual(dialog.body.font().pointSizeF(), parent.font().pointSizeF())
                        self.assertLess(dialog.scroll.geometry().bottom(), dialog.buttons.geometry().top())
                        self.assertTrue(dialog.rect().contains(dialog.buttons.geometry()))
                        self.assertLessEqual(dialog.body.width(), dialog.scroll.viewport().width())
                        for label in dialog.findChildren(WrappedLabel):
                            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()), label.text())
                        for button in dialog.buttons.buttons():
                            option = QStyleOptionButton()
                            option.initFrom(button)
                            option.text = button.text()
                            area = button.style().subElementRect(QStyle.SE_PushButtonContents, option, button)
                            self.assertGreaterEqual(area.height(), button.fontMetrics().boundingRect(button.text()).height())
                            self.assertGreaterEqual(area.width(), button.fontMetrics().horizontalAdvance(button.text()))
                        if dialog_type == 'text':
                            self.assertEqual(dialog.editor.font().pointSizeF(), parent.font().pointSizeF())
                            option = QStyleOptionFrame()
                            option.initFrom(dialog.editor)
                            area = dialog.editor.style().subElementRect(QStyle.SE_LineEditContents, option, dialog.editor)
                            self.assertGreaterEqual(area.height(), dialog.editor.fontMetrics().boundingRect('中文').height())
                        else:
                            for checkbox in (dialog.favorites, dialog.current, dialog.reset):
                                row = checkbox.parentWidget()
                                self.assertEqual(checkbox.font().pointSizeF(), parent.font().pointSizeF())
                                self.assertEqual(row.label.font().pointSizeF(), parent.font().pointSizeF())
                                self.assertTrue(row.rect().contains(row.label.geometry()))
                                self.assertTrue(row.rect().contains(checkbox.geometry()))
                                self.assertGreater(checkbox.height(), 0)
                    finally:
                        parent.deleteLater()
                        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
