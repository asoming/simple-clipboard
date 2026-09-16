"""Settings layout checks use disposable storage and never open a clipboard."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PyQt5.QtCore import QPoint, QRect
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QDialogButtonBox, QLabel, QLineEdit, QPushButton, QStyle,
    QStyleFactory, QStyleOptionButton, QStyleOptionComboBox, QStyleOptionFrame,
    QStyleOptionSpinBox, QWidget,
)

from clipboard_app.settings_dialog import SettingsDialog
from clipboard_app.store import Store
from clipboard_app.ui import Panel
from tests.test_ui_layout import PreviewMonitor


app = QApplication.instance() or QApplication([])


class SettingsLayoutTests(unittest.TestCase):
    def assert_text_fits(self, widget, area, text):
        metrics = widget.fontMetrics()
        # A Latin default font can report a shorter height than its CJK fallback.
        height = max(metrics.height(), metrics.boundingRect(text).height())
        context = f"{widget.objectName() or type(widget).__name__}: {text!r}"
        self.assertGreaterEqual(area.height(), height, context)
        self.assertGreaterEqual(area.width(), metrics.horizontalAdvance(text), context)

    def check_controls(self, dialog):
        for combo in (dialog.retention, dialog.theme):
            original = combo.currentIndex()
            for index in range(combo.count()):
                combo.setCurrentIndex(index)
                app.processEvents()
                option = QStyleOptionComboBox()
                combo.initStyleOption(option)
                area = combo.style().subControlRect(
                    QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, combo)
                self.assert_text_fits(combo, area, combo.currentText())
            combo.setCurrentIndex(original)

        for spin in dialog.fields:
            original = spin.value()
            for value in (spin.minimum(), original, spin.maximum()):
                spin.setValue(value)
                app.processEvents()
                option = QStyleOptionSpinBox()
                spin.initStyleOption(option)
                area = spin.style().subControlRect(
                    QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxEditField, spin)
                self.assert_text_fits(spin, area, spin.text())
                editor = spin.findChild(QLineEdit)
                frame = QStyleOptionFrame()
                frame.initFrom(editor)
                editor_area = editor.style().subElementRect(
                    QStyle.SE_LineEditContents, frame, editor)
                self.assert_text_fits(editor, editor_area, spin.text())
            spin.setValue(original)

        for button in dialog.findChildren(QPushButton):
            option = QStyleOptionButton()
            option.initFrom(button)
            option.text = button.text()
            area = button.style().subElementRect(QStyle.SE_PushButtonContents, option, button)
            self.assert_text_fits(button, area, button.text())

    def check_scroll_and_footer(self, dialog):
        footer = dialog.findChild(QWidget, "settingsFooter")
        self.assertIsNotNone(footer)
        footer_rect = QRect(footer.mapTo(dialog, QPoint()), footer.size())
        scroll_rect = QRect(dialog.scroll.mapTo(dialog, QPoint()), dialog.scroll.size())
        self.assertTrue(dialog.rect().contains(footer_rect))
        self.assertLess(scroll_rect.bottom(), footer_rect.top())
        for role in (QDialogButtonBox.Save, QDialogButtonBox.Cancel):
            button = dialog.buttons.button(role)
            self.assertTrue(button.isVisible())
            rect = QRect(button.mapTo(dialog, QPoint()), button.size())
            self.assertTrue(dialog.rect().contains(rect))

        scrollbar = dialog.scroll.verticalScrollBar()
        self.assertGreater(scrollbar.maximum(), 0, "Small dialogs must scroll instead of squeezing rows")
        labels = [label for label in dialog.body.findChildren(QLabel)
                  if label.isVisible() and label.text() and label.wordWrap()]
        self.assertTrue(labels, "Settings explanations should be inside the scrolling body")
        for label in labels:
            required = label.heightForWidth(label.width())
            self.assertGreaterEqual(label.height(), required, label.text())
        last_label = max(labels, key=lambda label: label.mapTo(dialog.body, QPoint()).y() + label.height())
        scrollbar.setValue(scrollbar.maximum())
        app.processEvents()
        bottom = last_label.mapTo(dialog.scroll.viewport(), last_label.rect().bottomLeft())
        self.assertLessEqual(bottom.y(), dialog.scroll.viewport().rect().bottom())
        self.assertGreaterEqual(bottom.y(), 0, "The end of the explanation must be reachable")
        self.assertEqual(footer_rect, QRect(footer.mapTo(dialog, QPoint()), footer.size()))
        self.assertEqual(dialog.scroll.horizontalScrollBar().maximum(), 0)

    def test_settings_keep_chinese_controls_readable_and_footer_visible(self):
        original_font = QFont(app.font())
        original_style = app.style().objectName()
        available = {style.lower() for style in QStyleFactory.keys()}
        # Pair both themes and two Qt styles with each actual panel font size.
        scenarios = ((10, "Fusion", "light"), (10, "Windows", "dark"),
                     (14, "Fusion", "dark"), (14, "Windows", "light"),
                     (20, "Fusion", "light"), (20, "Windows", "dark"))
        try:
            for points, style, theme in scenarios:
                if style.lower() not in available:
                    continue
                with self.subTest(points=points, style=style, theme=theme):
                    app.setStyle(style)
                    font = QFont(original_font)
                    font.setPointSize(points + 2)  # Panel applies its two-point reduction.
                    app.setFont(font)
                    with tempfile.TemporaryDirectory() as temporary:
                        store = Store(Path(temporary) / "history.sqlite3")
                        store.set_setting("intro_seen", True)
                        store.set_setting("theme", theme)
                        panel = Panel(store, PreviewMonitor())
                        panel.autostart = Mock()
                        panel.autostart.enabled.return_value = True
                        dialog = SettingsDialog(panel)
                        try:
                            dialog.resize(440, 400)
                            dialog.show()
                            app.processEvents()
                            self.assertEqual(dialog.font(), panel.font())
                            self.check_controls(dialog)
                            self.check_scroll_and_footer(dialog)
                            dialog.show_error("测试错误：设置无法保存，请检查后重试。" * 3)
                            app.processEvents()
                            self.assertTrue(dialog.error.isVisible())
                            self.check_scroll_and_footer(dialog)
                            if points == 20:
                                dialog.resize(dialog.minimumWidth(), 400)
                                app.processEvents()
                                self.check_controls(dialog)
                                self.check_scroll_and_footer(dialog)
                                panel.change_shortcut = Mock(side_effect=lambda: setattr(
                                    panel, "shortcut", "Ctrl+Alt+Shift+Super+V"))
                                dialog.change_shortcut()
                                app.processEvents()
                                panel.change_shortcut.assert_called_once_with()
                                self.assertEqual(dialog.shortcut_button.text(),
                                                 "Ctrl+Alt+Shift+Super+V · 修改")
                                self.check_controls(dialog)
                                self.check_scroll_and_footer(dialog)
                            panel.autostart.set_enabled.assert_not_called()
                        finally:
                            dialog.reject()
                            dialog.deleteLater()
                            panel.cleanup_timer.stop()
                            panel.tray.hide()
                            panel.hide()
                            panel.deleteLater()
                            app.processEvents()
                            store.close()
        finally:
            app.setFont(original_font)
            app.setStyle(original_style)
