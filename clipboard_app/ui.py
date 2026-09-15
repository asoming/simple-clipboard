"""One quiet panel: search, history, and explicit actions."""

import sys
import time
from datetime import datetime

from PyQt5.QtCore import QPointF, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QPainter, QPalette, QPen, QPixmap, QTextLayout, QTextOption
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QShortcut, QSpinBox, QStackedWidget,
    QFrame, QSizePolicy, QStyle, QStyleOptionComboBox, QStyledItemDelegate, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget,
)

from .monitor import Monitor
from .preferences import Appearance, Autostart
from .platforms import Target
from .store import Clip, Limits, Store


def app_icon() -> QIcon:
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#465a76"), 3))
    painter.setBrush(QColor("#f6f8fb"))
    painter.drawRoundedRect(11, 9, 27, 33, 4, 4)
    painter.drawRoundedRect(18, 5, 13, 8, 2, 2)
    painter.drawLine(18, 23, 31, 23)
    painter.drawLine(18, 30, 28, 30)
    painter.end()
    return QIcon(pixmap)


class SearchEdit(QLineEdit):
    move_selection = pyqtSignal(int)
    paste_selected = pyqtSignal()
    copy_selected = pyqtSignal()
    dismiss = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.composing = False
        self.last_commit = 0.0
        self.setPlaceholderText("搜索复制过的内容…")
        self.setClearButtonEnabled(True)
        self.setAccessibleName("搜索剪贴板历史")

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        if event.commitString():
            self.last_commit = time.monotonic()
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if self.composing:
            super().keyPressEvent(event)
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Some IMEs commit before forwarding their Enter key to the widget.
            if time.monotonic() - self.last_commit < 0.15:
                event.accept()
                return
            if event.modifiers() & Qt.ControlModifier:
                self.copy_selected.emit()
            else:
                self.paste_selected.emit()
            event.accept()
        elif event.key() in (Qt.Key_Down, Qt.Key_Up):
            self.move_selection.emit(1 if event.key() == Qt.Key_Down else -1)
            event.accept()
        elif event.key() == Qt.Key_Escape:
            self.dismiss.emit()
        else:
            super().keyPressEvent(event)


class FormatComboBox(QComboBox):
    """Keep a visible arrow when the platform's native combo is styled."""

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow = self.style().subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxArrow, self)
        center = arrow.center()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        group = QPalette.Active if self.isEnabled() else QPalette.Disabled
        painter.setPen(QPen(self.palette().color(group, QPalette.ButtonText), 1.4))
        painter.drawLine(QPointF(center.x() - 3, center.y() - 1), QPointF(center.x(), center.y() + 2))
        painter.drawLine(QPointF(center.x(), center.y() + 2), QPointF(center.x() + 3, center.y() - 1))


class HistoryList(QListWidget):
    paste_selected = pyqtSignal()
    copy_selected = pyqtSignal()
    dismiss = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ControlModifier:
                self.copy_selected.emit()
            else:
                self.paste_selected.emit()
        elif event.key() == Qt.Key_Escape:
            self.dismiss.emit()
        else:
            super().keyPressEvent(event)


class HistoryDelegate(QStyledItemDelegate):
    """Measure every line with its actual font, including Chinese fallback glyphs."""

    @staticmethod
    def caption_font(font):
        caption = QFont(font)
        if font.pixelSize() > 0:
            caption.setPixelSize(max(11, font.pixelSize() - 2))
        else:
            caption.setPointSizeF(max(9, font.pointSizeF() - 1))
        return caption

    @staticmethod
    def title_lines(text, font, width):
        width = max(1, width)
        layout = QTextLayout(text, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        line = layout.createLine()
        if not line.isValid():
            layout.endLayout()
            return [""]
        line.setLineWidth(width)
        # Qt string positions are UTF-16 offsets, not Python Unicode indices.
        end = line.textLength() * 2
        layout.endLayout()
        encoded = text.encode("utf-16-le")
        first = encoded[:end].decode("utf-16-le").rstrip()
        rest = encoded[end:].decode("utf-16-le").lstrip()
        return [first, QFontMetrics(font).elidedText(rest, Qt.ElideRight, width)] if rest else [first]

    def dimensions(self, option, index, width):
        title = index.data(Qt.DisplayRole).partition("\n")[0]
        icon = index.data(Qt.DecorationRole)
        inset = 70 if isinstance(icon, QIcon) and not icon.isNull() else 0
        lines = self.title_lines(title, option.font, width - 32 - inset)
        line_height = QFontMetrics(option.font).height() + 2
        caption_height = QFontMetrics(self.caption_font(option.font)).height() + 2
        return lines, line_height, caption_height, inset

    def sizeHint(self, option, index):
        width = option.widget.viewport().width() if option.widget else option.rect.width()
        lines, line_height, caption_height, inset = self.dimensions(option, index, width)
        return QSize(200, max(64, 18 + len(lines) * line_height + 5 + caption_height))

    def paint(self, painter, option, index):
        _, _, description = index.data(Qt.DisplayRole).partition("\n")
        icon = index.data(Qt.DecorationRole)
        dark = bool(option.widget.property("dark"))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        selected = option.state & QStyle.State_Selected
        hovered = option.state & QStyle.State_MouseOver
        if selected or hovered:
            background = ("#303e52" if dark else "#edf2f8") if selected else ("#2b333e" if dark else "#f5f7fa")
            painter.setBrush(QColor(background))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(option.rect.adjusted(1, 1, -1, -1), 8, 8)
            if selected:
                painter.setBrush(QColor("#90acd0" if dark else "#4b6485"))
                painter.drawRoundedRect(QRect(option.rect.left() + 1, option.rect.top() + 14,
                                              3, option.rect.height() - 28), 1, 1)
        lines, line_height, caption_height, inset = self.dimensions(option, index, option.rect.width())
        rect = option.rect.adjusted(16, 9, -16, -9)
        if inset:
            icon.paint(painter, rect.left(), rect.center().y() - 22, 56, 44)
            rect.setLeft(rect.left() + inset)
        painter.setFont(option.font)
        painter.setPen(QColor("#edf1f6" if dark else "#222a35"))
        for number, text in enumerate(lines):
            painter.drawText(QRect(rect.left(), rect.top() + number * line_height, rect.width(), line_height),
                             Qt.AlignVCenter | Qt.TextSingleLine, text)
        painter.setFont(self.caption_font(option.font))
        painter.setPen(QColor("#9aa6b6" if dark else "#77808e"))
        painter.drawText(QRect(rect.left(), rect.top() + len(lines) * line_height + 5,
                              rect.width(), caption_height), Qt.AlignVCenter | Qt.TextSingleLine,
                         painter.fontMetrics().elidedText(description, Qt.ElideRight, rect.width()))
        painter.restore()


class Panel(QWidget):
    import_requested = pyqtSignal(str)

    def __init__(self, store: Store, monitor: Monitor, backend=None):
        super().__init__()
        self.store = store
        self.monitor = monitor
        self.backend = backend
        self.target = None
        self.clips = []
        self.favorites_only = False
        self.kind = "all"
        self.appearance = Appearance(self)
        self.autostart = Autostart(store.path.parent)
        self.shortcut = store.setting("shortcut", backend.default_shortcut if backend else "Ctrl+Alt+V")
        self.shortcut_error = ""
        self.pending_notice = ""
        self.resetting = False
        self.setWindowTitle("剪贴板")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(440, 460)
        self.resize(540, 620)
        self._build()
        self._style()
        self.appearance.changed.connect(self._style)
        self._tray()
        monitor.changed.connect(self.history_changed)
        monitor.state_changed.connect(self.update_state)
        monitor.notice.connect(self.recording_notice)
        if backend:
            backend.activated.connect(self.toggle)
            backend.paste_failed.connect(self.paste_failed)
            try:
                backend.register(self.shortcut)
            except ValueError as error:
                self.shortcut_error = str(error)
        else:
            self.shortcut_error = "当前为界面预览模式，全局快捷键和自动粘贴不可用。"
        self.refresh()
        self.update_state()
        self.cleanup_timer = QTimer(self)
        self.cleanup_timer.setInterval(60_000)
        self.cleanup_timer.timeout.connect(self.clean_expired)
        self.cleanup_timer.start()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 14)
        root.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("剪贴板")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self.state = QLabel()
        self.state.setObjectName("subtle")
        header.addWidget(self.state)
        self.more = QToolButton()
        self.more.setObjectName("more")
        self.more.setText("···")
        self.more.setAccessibleName("更多设置")
        self.more.setToolTip("暂停、设置与数据管理")
        self.more.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self)
        self.pause_action = self.menu.addAction("暂停记录")
        self.pause_action.setCheckable(True)
        self.pause_action.triggered.connect(self.monitor.pause)
        self.ignore_action = self.menu.addAction("忽略下一次复制")
        self.ignore_action.triggered.connect(self.monitor.toggle_ignore)
        self.menu.addSeparator()
        self.menu.addAction("设置…", self.settings)
        self.menu.addAction("保存与隐私…", self.privacy)
        self.menu.addAction("从旧版导入…", self.import_old_history)
        if self.backend and hasattr(self.backend, "open_permissions"):
            self.menu.addAction("自动粘贴权限…", self.backend.open_permissions)
        self.menu.addAction("清空历史…", self.clear_history)
        self.menu.addSeparator()
        self.menu.addAction("退出", QApplication.instance().quit)
        self.more.setMenu(self.menu)
        header.addWidget(self.more)
        root.addLayout(header)
        self.search = SearchEdit()
        self.search.setMinimumHeight(44)
        root.addWidget(self.search)
        self.intro = QLabel("仅在本机保存历史。可随时暂停记录或删除；首次启动不读取已有内容。")
        self.intro.setWordWrap(True)
        self.intro.setObjectName("intro")
        self.intro.setVisible(not self.store.setting("intro_seen", False))
        root.addWidget(self.intro)
        filters = QHBoxLayout()
        self.all_button = QPushButton("全部")
        self.text_button = QPushButton("文本")
        self.image_button = QPushButton("图片")
        self.favorite_button = QPushButton("收藏")
        self.filter_buttons = (self.all_button, self.text_button, self.image_button, self.favorite_button)
        for button, value in zip(self.filter_buttons, ("all", "text", "image", "favorites")):
            button.setCheckable(True)
            button.setObjectName("filter")
            button.clicked.connect(lambda checked, value=value: self.filter(value))
            filters.addWidget(button)
        self.all_button.setChecked(True)
        filters.addStretch()
        self.count = QLabel()
        self.count.setObjectName("subtle")
        filters.addWidget(self.count)
        root.addLayout(filters)
        self.stack = QStackedWidget()
        self.history = HistoryList()
        self.history.setItemDelegate(HistoryDelegate(self.history))
        self.history.setSpacing(2)
        self.history.setIconSize(QSize(64, 44))
        self.history.setUniformItemSizes(False)
        self.history.setResizeMode(QListWidget.Adjust)
        self.history.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.history.setAccessibleName("历史记录")
        self.history.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.history.setContextMenuPolicy(Qt.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self.context_menu)
        self.empty = QLabel("还没有记录\n复制文字或图片，就会出现在这里。")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setObjectName("empty")
        self.empty.setWordWrap(True)
        self.stack.addWidget(self.history)
        self.stack.addWidget(self.empty)
        root.addWidget(self.stack, 1)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        root.addWidget(divider)
        utilities = QHBoxLayout()
        utilities.setSpacing(4)
        self.preview_button = QPushButton("预览")
        self.pin_button = QPushButton("收藏")
        self.copy_button = QPushButton("仅复制")
        self.paste_button = QPushButton("粘贴")
        self.paste_button.setObjectName("primary")
        for button in (self.preview_button, self.pin_button):
            button.setObjectName("quiet")
            utilities.addWidget(button)
        utilities.addStretch()
        mode_label = QLabel("格式")
        mode_label.setObjectName("subtle")
        utilities.addWidget(mode_label)
        self.paste_mode = FormatComboBox()
        self.paste_mode.addItem("原格式", False)
        self.paste_mode.addItem("纯文本", True)
        self.paste_mode.setAccessibleName("复制与粘贴格式")
        self.paste_mode.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.paste_mode.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.paste_mode.currentIndexChanged.connect(self._fit_paste_mode)
        self.paste_mode.setToolTip("原格式保留图片或 HTML；纯文本只输出文字。两者都适用于「仅复制」。")
        utilities.addWidget(self.paste_mode)
        root.addLayout(utilities)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setObjectName("notice")
        self.notice.hide()
        root.addWidget(self.notice)
        actions = QHBoxLayout()
        self.hint = QLabel("↑ ↓ 选择  ·  Esc 关闭")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        self.hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        actions.addWidget(self.hint, 1)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.paste_button)
        root.addLayout(actions)
        self.paste_button.setToolTip("粘贴到原窗口 · Enter")
        self.copy_button.setToolTip("仅复制 · " + ("⌘" if sys.platform == "darwin" else "Ctrl+") + "Enter")
        self.preview_button.setToolTip("查看完整内容 · " + ("⌘" if sys.platform == "darwin" else "Ctrl+") + "Space")
        self.pin_button.setToolTip("收藏或取消收藏 · " + ("⌘D" if sys.platform == "darwin" else "Ctrl+D"))
        self.search.textChanged.connect(self.refresh)
        self.search.move_selection.connect(self.move_selection)
        self.search.paste_selected.connect(self.paste)
        self.search.copy_selected.connect(self.copy_only)
        self.search.dismiss.connect(self.dismiss)
        self.history.paste_selected.connect(self.paste)
        self.history.copy_selected.connect(self.copy_only)
        self.history.dismiss.connect(self.dismiss)
        self.history.itemDoubleClicked.connect(self.paste)
        self.history.currentRowChanged.connect(self.selection_changed)
        self.preview_button.clicked.connect(self.preview)
        self.pin_button.clicked.connect(self.pin)
        self.copy_button.clicked.connect(self.copy_only)
        self.paste_button.clicked.connect(self.paste)
        self._shortcuts = []
        for key, handler in (("Ctrl+F", self.search.setFocus), ("Ctrl+D", self.pin),
                             ("Ctrl+Delete", self.delete_selected), ("Ctrl+Space", self.preview)):
            shortcut = QShortcut(key, self)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    def _fit_paste_mode(self):
        # Native styles disagree on how much width a custom combo's arrow uses.
        metrics = self.paste_mode.fontMetrics()
        text_width = max(metrics.horizontalAdvance(self.paste_mode.itemText(i))
                         for i in range(self.paste_mode.count()))
        self.paste_mode.setMinimumWidth(text_width + 56)

    def _style(self):
        theme = self.store.setting("theme", "system")
        dark = theme == "dark" or (theme == "system" and self.appearance.dark)
        if dark:
            canvas, surface, ink, muted, line, accent, selection = (
                "#1d2229", "#252c35", "#edf1f6", "#9aa6b6", "#394351", "#90acd0", "#303e52")
            hover, warning, warning_ink = "#2b333e", "#403726", "#ebca93"
        else:
            canvas, surface, ink, muted, line, accent, selection = (
                "#f5f6f8", "#ffffff", "#222a35", "#77808e", "#e3e7ed", "#4b6485", "#edf2f8")
            hover, warning, warning_ink = "#f5f7fa", "#fff5e4", "#805e2d"
        font = QFont(QApplication.font())
        family = {"win32": "Microsoft YaHei UI", "darwin": "PingFang SC"}.get(sys.platform, "Noto Sans CJK SC")
        if family in QFontDatabase().families():
            font.setFamily(family)
        font.setPointSizeF(max(10, font.pointSizeF() - 2))
        self.setFont(font)
        caption = max(9, font.pointSizeF() - 1.5)
        stylesheet = f"""
            QWidget {{ color: {ink}; background: {surface}; }}
            QLabel#title {{ font-size: {font.pointSizeF() + 2}pt; font-weight: 600; }}
            QLabel#subtle, QLabel#hint {{ color: {muted}; font-size: {caption}pt; }}
            QLabel#empty {{ color: {muted}; padding: 16px; }}
            QLabel#intro {{ color: {muted}; background: {canvas}; padding: 10px; border-radius: 6px; font-size: {caption}pt; }}
            QLabel#notice {{ color: {warning_ink}; background: {warning}; padding: 8px; border-radius: 6px; font-size: {caption}pt; }}
            QFrame#divider {{ background: {line}; border: none; }}
            QLineEdit {{ background: {canvas}; border: 1px solid {line}; border-radius: 8px; padding: 8px 12px; }}
            QLineEdit:focus {{ background: {surface}; border-color: {accent}; }}
            QComboBox, QSpinBox {{ background: {surface}; border: 1px solid {line}; border-radius: 6px; padding: 6px 10px; }}
            QComboBox {{ padding-right: 30px; }}
            QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 24px; border: none; }}
            QComboBox::down-arrow {{ image: none; }}
            QComboBox:focus, QSpinBox:focus {{ border-color: {accent}; }}
            QComboBox QAbstractItemView {{ background: {surface}; color: {ink}; selection-background-color: {selection}; selection-color: {ink}; }}
            QListWidget {{ background: {surface}; border: none; outline: none; }}
            QPushButton, QToolButton {{ background: {surface}; border: 1px solid {line}; border-radius: 6px; padding: 7px 14px; }}
            QPushButton:hover, QToolButton:hover {{ background: {hover}; border-color: {accent}; }}
            QPushButton:focus, QToolButton:focus {{ border-color: {accent}; }}
            QPushButton:disabled {{ color: {muted}; background: {canvas}; border-color: {line}; }}
            QPushButton#primary {{ background: #4b6485; color: #ffffff; border-color: #4b6485; }}
            QPushButton#primary:hover {{ background: #3a5375; border-color: #3a5375; }}
            QPushButton#primary:disabled {{ background: {line}; color: {muted}; border-color: {line}; }}
            QPushButton#filter {{ border: 1px solid transparent; background: transparent; color: {muted}; padding: 5px 12px; }}
            QPushButton#filter:checked {{ background: {selection}; color: {accent}; }}
            QPushButton#filter:focus {{ border-color: {accent}; }}
            QPushButton#quiet, QToolButton#more {{ background: transparent; color: {muted}; border-color: transparent; padding: 6px 9px; }}
            QPushButton#quiet:hover, QToolButton#more:hover {{ background: {hover}; color: {ink}; }}
            QPushButton#quiet:focus, QToolButton#more:focus {{ border-color: {accent}; }}
            QPushButton#quiet:disabled {{ color: {muted}; }}
            QToolButton::menu-indicator {{ image: none; }}
            QMenu {{ background: {surface}; border: 1px solid {line}; padding: 5px; }}
            QMenu::item {{ padding: 8px 20px; }}
            QMenu::item:selected {{ background: {selection}; }}
            QPlainTextEdit {{ background: {surface}; border: 1px solid {line}; border-radius: 6px; padding: 8px; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {line}; border-radius: 3px; min-height: 30px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """
        palette = QPalette(QApplication.palette())
        for role, color in ((QPalette.Window, surface), (QPalette.Base, surface),
                            (QPalette.Text, ink), (QPalette.WindowText, ink),
                            (QPalette.Button, surface), (QPalette.ButtonText, ink),
                            (QPalette.Highlight, selection), (QPalette.HighlightedText, ink)):
            palette.setColor(role, QColor(color))
        self.history.setProperty("dark", dark)
        self.setPalette(palette)
        self.setStyleSheet(stylesheet)
        self.ensurePolished()
        self._fit_paste_mode()
        self.history.doItemsLayout()

    def _tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray_menu = QMenu()
        self.tray_menu.addAction("打开剪贴板", self.open_panel)
        self.tray_menu.addAction(self.pause_action)
        self.tray_menu.addAction(self.ignore_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("退出", QApplication.instance().quit)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda reason: self.toggle() if reason == QSystemTrayIcon.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def selected(self) -> Clip | None:
        item = self.history.currentItem()
        return self.store.get(item.data(Qt.UserRole)) if item else None

    def refresh(self, *_):
        previous = self.history.currentItem()
        previous_id = previous.data(Qt.UserRole) if previous else None
        self.clips = self.store.summaries(self.search.text(), self.favorites_only, self.kind)
        self.history.blockSignals(True)
        self.history.clear()
        for clip in self.clips:
            text = clip.name or clip.preview or ("图片" if clip.kind == "image" else "空白文本")
            title = text
            copied = datetime.fromtimestamp(clip.copied_at)
            stamp = copied.strftime("今天 %H:%M" if copied.date() == datetime.now().date() else "%m-%d %H:%M")
            detail = f"{clip.width} × {clip.height}" if clip.kind == "image" else "网页" if clip.rich else "文本"
            description = ("已收藏 · " if clip.pinned else "") + f"{stamp} · {detail}"
            item = QListWidgetItem(title + "\n" + description)
            if clip.thumbnail:
                thumbnail = QPixmap()
                thumbnail.loadFromData(clip.thumbnail, "PNG")
                item.setIcon(QIcon(thumbnail))
            item.setData(Qt.UserRole, clip.id)
            self.history.addItem(item)
            if clip.id == previous_id:
                self.history.setCurrentItem(item)
        if self.history.currentRow() < 0 and self.clips:
            self.history.setCurrentRow(0)
        self.history.blockSignals(False)
        self.count.setText(f"{len(self.clips)} 条")
        self.stack.setCurrentWidget(self.history if self.clips else self.empty)
        if self.search.text():
            self.empty.setText("没有找到匹配内容\n试试更短的关键词。")
        elif self.favorites_only:
            self.empty.setText("还没有收藏\n选择一条记录，点击「收藏」。")
        elif self.monitor.paused:
            self.empty.setText("记录已暂停\n在右上角菜单恢复后，新的复制会出现在这里。")
        elif self.kind == "image":
            self.empty.setText("还没有图片\n复制截图或图片后会出现在这里；文件复制不记录。")
        else:
            self.empty.setText("还没有记录\n复制文字或图片，就会出现在这里。")
        self.selection_changed()

    def history_changed(self):
        if self.isVisible():
            self.refresh()

    def filter(self, value):
        # Accept the previous boolean API for opening the panel and test callers.
        value = "favorites" if value is True else "all" if value is False else value
        self.favorites_only = value == "favorites"
        self.kind = value if value in ("text", "image") else "all"
        for button, name in zip(self.filter_buttons, ("all", "text", "image", "favorites")):
            button.setChecked(value == name)
        self.refresh()

    def selection_changed(self, *_):
        row = self.history.currentRow()
        clip = self.clips[row] if 0 <= row < len(self.clips) else None
        for button in (self.preview_button, self.pin_button, self.copy_button, self.paste_button):
            button.setEnabled(clip is not None)
        self.pin_button.setText("取消收藏" if clip and clip.pinned else "收藏")
        if clip and clip.kind == "image":
            self.paste_mode.setCurrentIndex(0)
        self.paste_mode.setEnabled(clip is not None and clip.kind != "image")

    def move_selection(self, delta: int):
        if self.history.count():
            row = max(0, min(self.history.count() - 1, self.history.currentRow() + delta))
            self.history.setCurrentRow(row)

    def update_state(self):
        state = "已暂停" if self.monitor.paused else ("将忽略下次复制" if self.monitor.ignore_next else "正在处理…" if self.monitor.processing else "正在记录")
        self.state.setText(state)
        self.tray.setToolTip(f"剪贴板 · {state} · {self.shortcut}")
        self.pause_action.setChecked(self.monitor.paused)
        self.ignore_action.setEnabled(not self.monitor.paused)
        self.ignore_action.setText("取消忽略下一次复制" if self.monitor.ignore_next else "忽略下一次复制")
        if not self.clips:
            self.refresh()

    def open_panel(self):
        if not self.isVisible() and self.backend:
            target = self.backend.capture_target()
            self.target = target if target and target.window != self.backend.window_id(self) else None
        self.store.prune()
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.paste_mode.setCurrentIndex(0)
        self.filter(False)
        if self.history.count():
            self.history.setCurrentRow(0)
        self.notice.hide()
        if self.pending_notice:
            self.show_notice(self.pending_notice)
            self.pending_notice = ""
        if self.shortcut_error:
            self.show_notice(self.shortcut_error)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()
        if self.backend:
            QApplication.sync()
            self.backend.activate(Target(self.backend.window_id(self)))

    def toggle(self):
        if self.isVisible():
            self.dismiss()
        else:
            self.open_panel()

    def dismiss(self):
        self.store.set_setting("intro_seen", True)
        self.intro.hide()
        self.hide()
        if self.target and self.backend:
            self.backend.activate(self.target)

    def closeEvent(self, event):
        if self.resetting:
            event.accept()
            return
        # With no tray AND no working hotkey, hiding would make the app inaccessible.
        if not self.tray.isVisible() and (self.backend is None or self.shortcut_error):
            QApplication.instance().quit()
        else:
            self.dismiss()
        event.ignore()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(event)

    def paste(self, *_):
        clip = self.selected()
        if not clip:
            return
        # Multi-line terminal input may execute even without a synthesized Enter.
        self.monitor.copy(clip, plain=self.paste_mode.currentData())
        if self.target and self.target.terminal and (clip.image or any(c in clip.text for c in ("\n", "\r"))):
            message = "图片已复制。终端通常不接收图片，请切换到支持图片的应用粘贴。" if clip.image else "多行文本已复制。为避免终端执行其中的换行，请回到终端检查后手动粘贴。"
            self.show_notice(message)
            return
        self.dismiss()
        if self.backend:
            self.backend.paste(self.target)
        else:
            self.paste_failed("内容已复制。当前模式不支持自动粘贴，请手动粘贴。")

    def copy_only(self):
        clip = self.selected()
        if clip:
            self.monitor.copy(clip, plain=self.paste_mode.currentData())
            self.show_notice(f"已按{self.paste_mode.currentText()}复制，可回到目标位置手动粘贴。")

    def paste_failed(self, message: str):
        self.show_notice(message)
        self.show()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    def show_notice(self, message: str):
        self.notice.setText(message)
        self.notice.show()

    def recording_notice(self, message: str):
        self.pending_notice = message
        self.show_notice(message)

    def pin(self):
        clip = self.selected()
        if not clip:
            return
        if clip.pinned:
            self.store.favorite(clip.id, False)
        else:
            name, accepted = QInputDialog.getText(self, "收藏", "名称（可留空）", text=clip.name)
            if not accepted:
                return
            self.store.favorite(clip.id, True, name)
        self.refresh()

    def rename(self):
        clip = self.selected()
        if clip:
            name, accepted = QInputDialog.getText(self, "修改收藏名称", "名称", text=clip.name)
            if accepted:
                self.store.favorite(clip.id, True, name)
                self.refresh()

    def delete_selected(self):
        clip = self.selected()
        if clip:
            self.store.delete(clip.id)
            self.refresh()

    def preview(self):
        clip = self.selected()
        if not clip:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(clip.name or ("图片预览" if clip.image else "完整文本"))
        dialog.resize(580, 420)
        layout = QVBoxLayout(dialog)
        if clip.image:
            pixmap = QPixmap()
            pixmap.loadFromData(clip.image, "PNG")
            scroll = QScrollArea()
            scroll.setAlignment(Qt.AlignCenter)
            label = QLabel()
            label.setAccessibleName("图片原图预览")
            label.setAlignment(Qt.AlignCenter)
            scroll.setWidget(label)
            layout.addWidget(QLabel(f"{clip.width} × {clip.height} 像素 · {clip.size / 1024:.0f} KiB"))
            layout.addWidget(scroll, 1)
            actual = QCheckBox("原始尺寸（可滚动查看）")
            def scale():
                shown = pixmap if actual.isChecked() else pixmap.scaled(scroll.viewport().size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                label.setPixmap(shown)
                label.resize(shown.size())
            actual.toggled.connect(scale)
            layout.addWidget(actual)
            QTimer.singleShot(0, scale)
        else:
            if clip.html:
                note = QLabel("已保存 HTML 原格式。此处以纯文本预览，避免加载网页资源。")
                note.setWordWrap(True)
                layout.addWidget(note)
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setPlainText(clip.text)
            text.setAccessibleName("完整文本预览")
            layout.addWidget(text)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText("关闭")
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec_()
        dialog.deleteLater()

    def context_menu(self, point):
        item = self.history.itemAt(point)
        if item is None:
            return
        self.history.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("粘贴", self.paste)
        menu.addAction("仅复制", self.copy_only)
        menu.addAction("预览", self.preview)
        menu.addSeparator()
        clip = self.selected()
        menu.addAction("取消收藏" if clip.pinned else "收藏…", self.pin)
        if clip.pinned:
            menu.addAction("修改名称…", self.rename)
        menu.addAction("删除", self.delete_selected)
        menu.exec_(self.history.mapToGlobal(point))

    def change_shortcut(self):
        if self.backend is None:
            self.show_notice("当前模式不支持全局快捷键。")
            return
        value, accepted = QInputDialog.getText(self, "设置快捷键", f"例如 {self.backend.default_shortcut}", text=self.shortcut)
        if not accepted:
            return
        value = value.strip()
        try:
            self.backend.register(value)
        except ValueError as error:
            self.show_notice(str(error))
        else:
            self.shortcut = value
            self.shortcut_error = ""
            self.store.set_setting("shortcut", value)
            self.update_state()
            self.show_notice(f"快捷键已设为 {value}")

    def settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("设置")
        dialog.resize(440, 480)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        fields = []
        limits = self.store.limits
        for label, value, maximum in (("保留天数", limits.days, 3650),
                                      ("普通历史条数", limits.count, 10000),
                                      ("总内容容量（MiB）", limits.total_bytes // 1048576, 10240),
                                      ("单条上限（MiB）", limits.item_bytes // 1048576, 100)):
            field = QSpinBox()
            field.setRange(1, maximum)
            field.setValue(value)
            field.setAccessibleName(label)
            form.addRow(label, field)
            fields.append(field)
        theme = QComboBox()
        for label, value in (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")):
            theme.addItem(label, value)
        theme.setCurrentIndex(max(0, theme.findData(self.store.setting("theme", "system"))))
        theme.setAccessibleName("外观")
        form.addRow("外观", theme)
        shortcut = QPushButton(self.shortcut + " · 修改")
        shortcut.clicked.connect(lambda: (self.change_shortcut(), shortcut.setText(self.shortcut + " · 修改")))
        form.addRow("全局快捷键", shortcut)
        startup = QCheckBox("登录桌面后在后台启动")
        try:
            startup.setChecked(self.autostart.enabled())
        except (OSError, ValueError):
            startup.setEnabled(False)
            startup.setToolTip("无法读取系统自启动目录。")
        form.addRow("启动", startup)
        layout.addLayout(form)
        usage = QLabel(f"内容 {self.store.usage() / 1048576:.2f} MiB · 数据库文件 {self.store.disk_usage() / 1048576:.2f} MiB\n"
                       "容量按文字、HTML、图片和缩略图计算，数据库索引会额外占用空间。\n\n"
                       "保存后立即按期限、条数和容量清理普通历史，删除无法恢复。收藏不自动清理。图片最多 2400 万像素。")
        usage.setWordWrap(True)
        layout.addWidget(usage)
        error_label = QLabel()
        error_label.setWordWrap(True)
        error_label.setObjectName("notice")
        error_label.hide()
        layout.addWidget(error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        def save():
            days, count, total, item = (field.value() for field in fields)
            try:
                new_limits = Limits(days, count, total * 1048576, item * 1048576)
                if new_limits != self.store.limits:
                    self.store.set_limits(new_limits)
                    self.monitor.cancel_pending()
                self.store.set_setting("theme", theme.currentData())
                self._style()
                self.refresh()
            except ValueError as error:
                error_label.setText(str(error))
                error_label.show()
                return
            try:
                # Saving an enabled entry also refreshes a path left by an old
                # source launcher or a moved installation.
                if startup.isEnabled() and (startup.isChecked() or self.autostart.enabled()):
                    self.autostart.set_enabled(startup.isChecked())
            except (OSError, ValueError):
                error_label.setText("保存规则和外观已更新，但无法修改自启动文件。请检查目录权限后重试，或取消勾选自启动。")
                error_label.show()
                return
            dialog.accept()
            self.show_notice("设置已保存。")
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec_()
        dialog.deleteLater()

    def privacy(self):
        mb = self.store.usage() / 1024 / 1024
        QMessageBox.information(self, "保存与隐私", (
            "历史仅在本机保存，不上传、不登录。\n\n"
            f"普通历史：{self.store.limits.days} 天，最多 {self.store.limits.count} 条。\n"
            f"收藏不自动清理；总容量 {self.store.limits.total_bytes / 1048576:g} MiB，单条 {self.store.limits.item_bytes / 1048576:g} MiB。\n"
            f"当前内容占用：{mb:.2f} MiB（不含数据库额外空间）。\n\n"
            f"数据目录：{self.store.path.parent}\n卸载应用默认保留历史；可先在清空历史中删除全部收藏并重置设置。\n\n"
            "可通过菜单暂停记录、忽略下一次复制或清空历史。\n"
            "支持文本、静态图片及 HTML；不记录文件，不识别图片中的文字。\n"
            "带支持的密码标记的内容会跳过，但来源应用不一定提供标记。\n"
            "本地历史未加密，无法识别所有密码；复制敏感内容前请暂停。"
        ))

    def import_old_history(self):
        if self.store.summaries():
            self.show_notice("当前历史不为空，无法覆盖导入。旧版与当前历史可分别保留在各自的数据目录。")
            return
        filename, _ = QFileDialog.getOpenFileName(self, "选择旧版 history.sqlite3", "", "剪贴板历史 (*.sqlite3)")
        if not filename:
            return
        answer = QMessageBox.question(self, "导入旧版历史", "请先退出旧版。应用将重启，校验后导入历史及设置；旧文件保留不变。\n当前空历史的设置会被替换，过期的普通记录仍按旧版保存规则清理。", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.import_requested.emit(filename)

    def clear_history(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("清空历史")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("删除后无法恢复。默认保留收藏。"))
        favorites = QCheckBox("同时删除全部收藏")
        current = QCheckBox("同时清空系统当前剪贴板")
        reset = QCheckBox("同时重置设置、关闭自启动并退出")
        reset.toggled.connect(lambda checked: (favorites.setChecked(True) if checked else None, favorites.setEnabled(not checked)))
        layout.addWidget(favorites)
        layout.addWidget(current)
        layout.addWidget(reset)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("清空")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() == QDialog.Accepted:
            if reset.isChecked():
                try:
                    self.autostart.set_enabled(False)
                except (OSError, ValueError):
                    self.show_notice("无法关闭自启动，尚未清空。请检查系统启动项后重试。")
                    dialog.deleteLater()
                    return
            self.monitor.cancel_pending()
            if reset.isChecked():
                self.store.reset()
                self.monitor.stop()
                self.cleanup_timer.stop()
            else:
                self.store.clear(favorites.isChecked())
            if current.isChecked():
                self.monitor.clear_current()
            self.refresh()
            if reset.isChecked():
                self.resetting = True
                QApplication.instance().quit()

        dialog.deleteLater()

    def clean_expired(self):
        self.store.prune()
        self.history_changed()
