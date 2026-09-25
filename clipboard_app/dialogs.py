"""Small, scrollable dialogs with readable labels and fixed action buttons."""

from PyQt5.QtCore import QEvent, QSize, Qt
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLayout,
    QLineEdit, QScrollArea, QSizePolicy, QStyle, QStyleOptionButton,
    QStyleOptionFrame, QStyleOptionToolButton, QToolButton,
    QVBoxLayout, QWidget,
)

from .widgets import WrappedLabel


def fit_text_control(widget):
    """Include actual CJK glyph height and the current stylesheet's padding."""
    widget.ensurePolished()
    text = '中文Ag' if isinstance(widget, QLineEdit) else widget.text()
    metrics = widget.fontMetrics()
    glyphs = QSize(metrics.horizontalAdvance(text), max(metrics.height(), metrics.boundingRect(text).height()))
    widget.resize(widget.sizeHint())
    if isinstance(widget, QToolButton):
        option = QStyleOptionToolButton()
        widget.initStyleOption(option)
        size = widget.style().sizeFromContents(QStyle.CT_ToolButton, option, glyphs, widget)
        widget.setMinimumSize(size + QSize(4, 4))
        return
    if isinstance(widget, QLineEdit):
        option = QStyleOptionFrame()
        option.initFrom(widget)
        area = widget.style().subElementRect(QStyle.SE_LineEditContents, option, widget)
    else:
        option = QStyleOptionButton()
        option.initFrom(widget)
        option.text = widget.text()
        area = widget.style().subElementRect(QStyle.SE_PushButtonContents, option, widget)
        widget.setMinimumWidth(glyphs.width() + max(0, widget.width() - area.width()) + 4)
    widget.setMinimumHeight(max(widget.sizeHint().height(), glyphs.height() + max(0, widget.height() - area.height()) + 4))


class _OptionLabel(WrappedLabel):
    """Let a wrapped description toggle its native checkbox."""

    def __init__(self, text, checkbox, parent):
        super().__init__(text, parent)
        self.checkbox = checkbox
        self._pressed = False
        self.setBuddy(checkbox)
        checkbox.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.checkbox and event.type() == QEvent.EnabledChange:
            self.setEnabled(self.checkbox.isEnabled())
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            if self.rect().contains(event.pos()) and self.checkbox.isEnabled():
                self.checkbox.setFocus(Qt.MouseFocusReason)
                self.checkbox.click()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class _CheckOption(QWidget):
    """A native indicator beside a label that can grow to multiple lines."""

    def __init__(self, text, parent):
        super().__init__(parent)
        self.setFont(QFont(parent.font()))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.checkbox = QCheckBox(self)
        self.checkbox.setFont(QFont(self.font()))
        self.checkbox.setAccessibleName(text)
        self.label = _OptionLabel(text, self.checkbox, self)
        self.label.setFont(QFont(self.font()))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(10)
        layout.addWidget(self.checkbox, 0, Qt.AlignVCenter)
        layout.addWidget(self.label, 1)


class _FormDialog(QDialog):
    def __init__(self, parent, title):
        super().__init__(parent)
        self.setWindowTitle(title)
        if parent is not None:
            self.setFont(QFont(parent.font()))
        self.setMinimumSize(320, 230)
        self.resize(440, 300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget(self.scroll)
        self.body.setFont(QFont(self.font()))
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 8, 0)
        self.body_layout.setSpacing(12)
        self.body_layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.buttons.setFont(QFont(self.font()))
        self.buttons.setObjectName('dialogFooter')
        self.buttons.button(QDialogButtonBox.Ok).setText('确定')
        self.buttons.button(QDialogButtonBox.Ok).setObjectName('primary')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        for button in self.buttons.buttons():
            button.setIcon(QIcon())
            fit_text_control(button)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)


class TextInputDialog(_FormDialog):
    def __init__(self, parent, title, label, value=''):
        super().__init__(parent, title)
        self.label = WrappedLabel(label, self.body)
        self.label.setFont(QFont(self.font()))
        self.label.setObjectName('sectionDescription')
        self.editor = QLineEdit(value, self.body)
        self.editor.setFont(QFont(self.font()))
        self.editor.setAccessibleName(label)
        self.body_layout.addWidget(self.label)
        self.body_layout.addWidget(self.editor)
        self.body_layout.addStretch()
        fit_text_control(self.editor)
        self.editor.selectAll()
        self.editor.setFocus()


def text_input(parent, title, label, value='') -> tuple[str, bool]:
    dialog = TextInputDialog(parent, title, label, value)
    accepted = dialog.exec_() == QDialog.Accepted
    result = dialog.editor.text()
    dialog.deleteLater()
    return result, accepted


class ClearHistoryDialog(_FormDialog):
    def __init__(self, parent=None):
        super().__init__(parent, '清空历史')
        self.explanation = WrappedLabel('删除后无法恢复。默认保留收藏。', self.body)
        self.explanation.setFont(QFont(self.font()))
        self.explanation.setObjectName('sectionDescription')
        self.body_layout.addWidget(self.explanation)
        options = [_CheckOption(text, self.body) for text in (
            '同时删除全部收藏', '同时清空系统当前剪贴板', '同时重置设置、关闭自启动并退出',
        )]
        self.favorites, self.current, self.reset = (option.checkbox for option in options)
        for option in options:
            self.body_layout.addWidget(option)
        self.body_layout.addStretch()
        self.reset.toggled.connect(self._reset_toggled)
        self.buttons.button(QDialogButtonBox.Ok).setText('清空')

    def _reset_toggled(self, checked):
        if checked:
            self.favorites.setChecked(True)
        self.favorites.setEnabled(not checked)
