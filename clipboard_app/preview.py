"""On-demand, read-only previews. No clipboard or database access."""

from datetime import datetime

from PyQt5.QtCore import QByteArray, QBuffer, QEvent, QIODevice, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QImageReader, QPixmap
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout,
    QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QStyle, QToolButton,
    QVBoxLayout, QWidget,
)

from .content import decoded_image
from .dialogs import fit_text_control
from .store import Clip
from .widgets import WrappedLabel


INLINE_TEXT_CHARACTERS = 20_000


class PreviewPane(QWidget):
    paste_requested = pyqtSignal()
    copy_requested = pyqtSignal()
    dismiss_requested = pyqtSignal()
    full_requested = pyqtSignal()

    def __init__(self, parent=None, *, enable_actions=False):
        super().__init__(parent)
        self._font_source = parent
        self.enable_actions = enable_actions
        self.setObjectName('previewPane')
        if parent is not None:
            self.setFont(QFont(parent.font()))
            parent.installEventFilter(self)
        self.setMinimumSize(220, 160)
        self.clip_id = None
        self._pixmap = QPixmap()
        self._updating_image = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(0)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget(self.scroll)
        self.body.setFont(QFont(self.font()))
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        body.setSizeConstraint(QLayout.SetMinimumSize)
        self.stack = QStackedWidget(self.body)
        self.stack.setMinimumSize(0, 100)
        self.text = QPlainTextEdit(self.stack)
        self.text.setFont(QFont(self.font()))
        self.text.setObjectName('previewText')
        self.text.setReadOnly(True)
        self.text.setFrameShape(QFrame.NoFrame)
        self.text.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.text.setAccessibleName('完整文本预览')
        self.text.setMinimumSize(0, 100)
        self.image_scroll = QScrollArea(self.stack)
        self.image_scroll.setFrameShape(QFrame.NoFrame)
        self.image_scroll.setAlignment(Qt.AlignCenter)
        self.image_scroll.setMinimumSize(0, 100)
        self.image_label = QLabel(self.image_scroll)
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setAccessibleName('图片预览')
        self.image_scroll.setWidget(self.image_label)
        self.image_scroll.viewport().installEventFilter(self)
        for widget in (self.text, self.text.viewport(), self.image_scroll):
            widget.installEventFilter(self)
        self.empty = WrappedLabel('选择一条内容', self.stack)
        self.empty.setFont(QFont(self.font()))
        self.empty.setObjectName('sectionDescription')
        self.empty.setAlignment(Qt.AlignCenter)
        for widget in (self.empty, self.text, self.image_scroll):
            self.stack.addWidget(widget)
        body.addWidget(self.stack, 1)

        self.controls = QWidget(self.body)
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        self.full_button = QPushButton('完整查看', self.controls)
        self.full_button.setObjectName('quiet')
        self.full_button.setToolTip('打开完整内容；复制仍使用原内容')
        self.full_button.clicked.connect(self.full_requested)
        controls.addWidget(self.full_button)
        controls.addStretch()
        self.image_mode = QToolButton(self.controls)
        self.image_mode.setObjectName('quiet')
        self.image_mode.setText('原始尺寸')
        self.image_mode.setToolTip('关闭时适应窗口，开启后按原始尺寸滚动查看')
        self.image_mode.setCheckable(True)
        self.image_mode.toggled.connect(self._update_image)
        self.image_mode.installEventFilter(self)
        controls.addWidget(self.image_mode)
        for button in (self.full_button, self.image_mode):
            fit_text_control(button)
        body.addWidget(self.controls)

        self.metadata = QWidget(self.body)
        metadata = QVBoxLayout(self.metadata)
        metadata.setContentsMargins(0, 0, 0, 0)
        metadata.setSpacing(10)
        divider = QFrame(self.metadata)
        divider.setObjectName('previewDivider')
        divider.setFrameShape(QFrame.NoFrame)
        divider.setFixedHeight(1)
        metadata.addWidget(divider)
        details = QGridLayout()
        details.setContentsMargins(0, 0, 0, 0)
        details.setHorizontalSpacing(14)
        details.setVerticalSpacing(5)
        details.setColumnStretch(1, 1)
        self.details = []
        for row in range(4):
            label = QLabel(self.metadata)
            label.setObjectName('sectionDescription')
            value = WrappedLabel(parent=self.metadata)
            value.setObjectName('previewMetadataValue')
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.installEventFilter(self)
            details.addWidget(label, row, 0, Qt.AlignTop)
            details.addWidget(value, row, 1)
            self.details.append((label, value))
        metadata.addLayout(details)
        body.addWidget(self.metadata)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        self.clear()

    def clear(self):
        self.clip_id = None
        self._pixmap = QPixmap()
        self.image_label.clear()
        self.image_label.resize(1, 1)
        self.text.clear()
        self.text.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.text.setToolTip('')
        for label, value in self.details:
            label.clear()
            value.clear()
        self.metadata.hide()
        self.controls.hide()
        self.full_button.hide()
        self.image_mode.setChecked(False)
        self.image_mode.hide()
        self.empty.setText('选择一条内容')
        self.stack.setCurrentWidget(self.empty)
        self._fit_minimum_width()

    @staticmethod
    def _size_text(size):
        return f'{size / 1048576:.1f} MiB' if size >= 1048576 else f'{size / 1024:.1f} KiB'

    def _set_details(self, entries):
        for row, (label, value) in enumerate(self.details):
            visible = row < len(entries)
            label.setVisible(visible)
            value.setVisible(visible)
            if visible:
                name, text = entries[row]
                label.setText(name)
                value.setText(text)
        self.metadata.show()

    def set_summary(self, clip_id: int, summary_text: str, size: int, *, copied_at=None):
        self.clear()
        self.clip_id = clip_id
        details = [('预览', '摘要'), ('存储大小', self._size_text(size))]
        if copied_at is not None:
            details.append(('复制时间', datetime.fromtimestamp(copied_at).strftime('%Y-%m-%d %H:%M')))
        self._set_details(details)
        self.text.setPlainText(summary_text)
        self.stack.setCurrentWidget(self.text)
        self.full_button.show()
        self.controls.show()
        self._fit_minimum_width()

    def set_clip(self, clip: Clip | None):
        self.clear()
        if clip is None:
            return
        self.clip_id = clip.id
        if clip.image:
            self._pixmap = QPixmap.fromImage(decoded_image(clip.image))
            if self._pixmap.isNull():
                self.empty.setText('图片暂时无法显示')
                self._fit_minimum_width()
                return
            buffer = QBuffer()
            buffer.setData(QByteArray(clip.image))
            buffer.open(QIODevice.ReadOnly)
            format_name = bytes(QImageReader(buffer).format()).decode('ascii', errors='replace').upper()
            details = [('类型', format_name or '图片'),
                       ('尺寸', f'{self._pixmap.width()} × {self._pixmap.height()}')]
            self.image_mode.show()
            self.controls.show()
            self.stack.setCurrentWidget(self.image_scroll)
            self._update_image()
        else:
            kind = '网页文本' if clip.html else '文本'
            details = [('类型', f'{kind} · {len(clip.text)} 字符')]
            # Wrapping a huge single line can block Qt's layout engine. Large
            # explicit previews stay complete and scroll horizontally instead.
            wrap_mode = (QPlainTextEdit.NoWrap if len(clip.text) > INLINE_TEXT_CHARACTERS
                         else QPlainTextEdit.WidgetWidth)
            self.text.setLineWrapMode(wrap_mode)
            self.text.setPlainText(clip.text)
            self.stack.setCurrentWidget(self.text)
            self.text.setToolTip('已保留网页原格式；预览显示纯文本' if clip.html else '')
        details.extend([('存储大小', self._size_text(clip.size)),
                        ('复制时间', datetime.fromtimestamp(clip.copied_at).strftime('%Y-%m-%d %H:%M'))])
        self._set_details(details)
        self._fit_minimum_width()

    def _update_image(self, *_):
        if self._pixmap.isNull() or self._updating_image:
            return
        self._updating_image = True
        try:
            actual = self.image_mode.isChecked()
            policy = Qt.ScrollBarAsNeeded if actual else Qt.ScrollBarAlwaysOff
            self.image_scroll.setHorizontalScrollBarPolicy(policy)
            self.image_scroll.setVerticalScrollBarPolicy(policy)
            size = self.image_scroll.viewport().size()
            if actual or (self._pixmap.width() <= size.width() and self._pixmap.height() <= size.height()):
                shown = self._pixmap
            else:
                shown = self._pixmap.scaled(size.expandedTo(QSize(1, 1)), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(shown)
            self.image_label.resize(shown.size())
        finally:
            self._updating_image = False

    def eventFilter(self, watched, event):
        if watched is self._font_source and event.type() == QEvent.FontChange:
            self.setFont(QFont(watched.font()))
        elif hasattr(self, 'image_scroll'):
            if watched is self.image_scroll.viewport() and event.type() == QEvent.Resize:
                self._update_image()
            elif self.enable_actions and event.type() == QEvent.KeyPress:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    if event.modifiers() == Qt.NoModifier:
                        self.paste_requested.emit()
                        return True
                    if event.modifiers() == Qt.ControlModifier:
                        self.copy_requested.emit()
                        return True
                elif event.key() == Qt.Key_Escape and event.modifiers() == Qt.NoModifier:
                    self.dismiss_requested.emit()
                    return True
        return super().eventFilter(watched, event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange) and hasattr(self, 'details'):
            for widget in (self.body, self.text, self.image_mode, self.full_button,
                           self.metadata, self.empty):
                widget.setFont(QFont(self.font()))
            for label, value in self.details:
                label.setFont(QFont(self.font()))
                value.setFont(QFont(self.font()))
            for button in (self.image_mode, self.full_button):
                fit_text_control(button)
            self._fit_minimum_width()

    def _fit_minimum_width(self):
        self.body.layout().activate()
        margins = self.layout().contentsMargins()
        scrollbar = self.scroll.style().pixelMetric(QStyle.PM_ScrollBarExtent, None, self.scroll)
        # The body must still fit when a short pane needs a vertical scrollbar.
        body_width = max(self.body.minimumWidth(), self.body.minimumSizeHint().width())
        content_width = (body_width + margins.left() + margins.right()
                         + 2 * self.scroll.frameWidth() + scrollbar)
        self.setMinimumWidth(max(220, content_width))


class PreviewDialog(QDialog):
    def __init__(self, clip: Clip, parent=None):
        super().__init__(parent)
        self.setWindowTitle('完整预览')
        if parent is not None:
            self.setFont(QFont(parent.font()))
        self.setMinimumSize(320, 260)
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 12)
        self.pane = PreviewPane(self)
        self.pane.set_clip(clip)
        layout.addWidget(self.pane, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.buttons.setFont(QFont(self.font()))
        self.buttons.button(QDialogButtonBox.Close).setText('关闭')
        fit_text_control(self.buttons.button(QDialogButtonBox.Close))
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.finished.connect(self.pane.clear)
