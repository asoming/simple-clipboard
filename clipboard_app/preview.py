"""On-demand, read-only previews. No clipboard or database access."""

from PyQt5.QtCore import QByteArray, QBuffer, QEvent, QIODevice, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QImageReader, QPixmap
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLayout,
    QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from .content import decoded_image
from .dialogs import fit_text_control
from .store import Clip
from .widgets import WrappedLabel


class PreviewPane(QWidget):
    close_requested = pyqtSignal()
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
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.title = QLabel('内容预览', self)
        self.title.setObjectName('sectionTitle')
        header.addWidget(self.title)
        header.addStretch()
        self.close_button = QToolButton(self)
        self.close_button.setObjectName('quiet')
        self.close_button.setText('×')
        self.close_button.setAccessibleName('关闭预览')
        self.close_button.setToolTip('关闭预览')
        self.close_button.clicked.connect(self._close)
        fit_text_control(self.close_button)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget(self.scroll)
        self.body.setFont(QFont(self.font()))
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 4, 0)
        body.setSpacing(8)
        body.setSizeConstraint(QLayout.SetMinimumSize)
        self.metadata = WrappedLabel(parent=self.body)
        self.metadata.setFont(QFont(self.font()))
        self.metadata.setObjectName('sectionDescription')
        body.addWidget(self.metadata)
        self.note = WrappedLabel(parent=self.body)
        self.note.setFont(QFont(self.font()))
        self.note.setObjectName('sectionDescription')
        body.addWidget(self.note)
        self.full_button = QPushButton('查看完整内容', self.body)
        self.full_button.setObjectName('quiet')
        self.full_button.clicked.connect(self.full_requested)
        fit_text_control(self.full_button)
        body.addWidget(self.full_button, 0, Qt.AlignLeft)
        self.image_mode = QToolButton(self.body)
        self.image_mode.setObjectName('quiet')
        self.image_mode.setText('原始尺寸')
        self.image_mode.setToolTip('关闭时适应窗口，开启后按原始尺寸滚动查看')
        self.image_mode.setCheckable(True)
        self.image_mode.toggled.connect(self._update_image)
        fit_text_control(self.image_mode)
        body.addWidget(self.image_mode, 0, Qt.AlignLeft)

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
        for widget in (self.text, self.text.viewport(), self.image_scroll, self.image_mode):
            widget.installEventFilter(self)
        self.empty = WrappedLabel('选择一条内容进行预览。', self.stack)
        self.empty.setFont(QFont(self.font()))
        self.empty.setObjectName('sectionDescription')
        self.empty.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        for widget in (self.empty, self.text, self.image_scroll):
            self.stack.addWidget(widget)
        body.addWidget(self.stack, 1)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        self.clear()

    def clear(self):
        self.clip_id = None
        self._pixmap = QPixmap()
        self.image_label.clear()
        self.image_label.resize(1, 1)
        self.text.clear()
        self.metadata.clear()
        self.metadata.hide()
        self.note.clear()
        self.note.hide()
        self.full_button.hide()
        self.image_mode.setChecked(False)
        self.image_mode.hide()
        self.empty.setText('选择一条内容进行预览。')
        self.stack.setCurrentWidget(self.empty)
        self._fit_minimum_width()

    def set_summary(self, clip_id: int, summary_text: str, size: int):
        self.clear()
        self.clip_id = clip_id
        self.metadata.setText(f'内容摘要 · {size / 1048576:.1f} MiB')
        self.metadata.show()
        self.note.setText('内容较大，当前显示摘要。原内容已完整保留。')
        self.note.show()
        self.text.setPlainText(summary_text)
        self.stack.setCurrentWidget(self.text)
        self.full_button.show()
        self._fit_minimum_width()

    def set_clip(self, clip: Clip | None):
        self.clear()
        if clip is None:
            return
        self.clip_id = clip.id
        size = f'{clip.size / 1048576:.1f} MiB' if clip.size >= 1048576 else f'{clip.size / 1024:.1f} KiB'
        if clip.image:
            self._pixmap = QPixmap.fromImage(decoded_image(clip.image))
            if self._pixmap.isNull():
                self.empty.setText('这张图片暂时无法显示。可以关闭预览后重试。')
                return
            buffer = QBuffer()
            buffer.setData(QByteArray(clip.image))
            buffer.open(QIODevice.ReadOnly)
            format_name = bytes(QImageReader(buffer).format()).decode('ascii', errors='replace').upper()
            self.metadata.setText(f'{format_name or "图片"} · {self._pixmap.width()} × {self._pixmap.height()} 像素 · {size}')
            self.image_mode.show()
            self.stack.setCurrentWidget(self.image_scroll)
            self._update_image()
        else:
            self.metadata.setText(f'{"网页文本" if clip.html else "文本"} · {len(clip.text)} 字符 · {size}')
            self.text.setPlainText(clip.text)
            self.stack.setCurrentWidget(self.text)
            if clip.html:
                self.note.setText('已保留网页原格式，此处仅显示文字。')
                self.note.show()
        self.metadata.show()

    def _close(self):
        self.clear()
        self.close_requested.emit()

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
        if event.type() in (QEvent.FontChange, QEvent.StyleChange) and hasattr(self, 'empty'):
            for widget in (self.body, self.text, self.title, self.close_button, self.image_mode,
                           self.full_button, self.metadata, self.note, self.empty):
                widget.setFont(QFont(self.font()))
            for button in (self.close_button, self.image_mode, self.full_button):
                fit_text_control(button)
            self._fit_minimum_width()

    def _fit_minimum_width(self):
        width = self.title.minimumSizeHint().width() + self.close_button.minimumWidth() + 44
        if not self.full_button.isHidden():
            width = max(width, self.full_button.minimumWidth() + 52)
        self.setMinimumWidth(max(220, width))


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
        self.pane.close_button.hide()
        self.pane.set_clip(clip)
        layout.addWidget(self.pane, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.buttons.setFont(QFont(self.font()))
        self.buttons.button(QDialogButtonBox.Close).setText('关闭')
        fit_text_control(self.buttons.button(QDialogButtonBox.Close))
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.finished.connect(self.pane.clear)
