"""Readable settings with a scrollable form and a persistent action row."""

import sqlite3
from pathlib import Path

from PyQt5.QtCore import QPointF, Qt, QTimer
from PyQt5.QtGui import QFont, QIcon, QPainter, QPalette, QPen
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QLayout, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QStyle, QStyleOptionButton, QStyleOptionComboBox, QStyleOptionSpinBox,
    QStyleOptionFrame, QVBoxLayout, QWidget,
)

from .store import Limits
from .ui import FormatComboBox
from .widgets import WrappedLabel


def fit_control(widget, text):
    """Reserve the actual CJK glyph height inside the styled content rectangle."""
    widget.ensurePolished()
    metrics = widget.fontMetrics()
    texts = [text] if isinstance(text, str) else text
    glyph_height = max(metrics.height(), *(metrics.boundingRect(value).height() for value in texts))
    glyph_width = max(metrics.horizontalAdvance(value) for value in texts)
    widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    widget.resize(max(widget.sizeHint().width(), 180), widget.sizeHint().height())
    style = widget.style()
    if isinstance(widget, QComboBox):
        option = QStyleOptionComboBox()
        widget.initStyleOption(option)
        content = style.subControlRect(QStyle.CC_ComboBox, option, QStyle.SC_ComboBoxEditField, widget)
    elif isinstance(widget, QSpinBox):
        option = QStyleOptionSpinBox()
        widget.initStyleOption(option)
        content = style.subControlRect(QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxEditField, widget)
    elif isinstance(widget, QLineEdit):
        option = QStyleOptionFrame()
        option.initFrom(widget)
        content = style.subElementRect(QStyle.SE_LineEditContents, option, widget)
    else:
        option = QStyleOptionButton()
        option.initFrom(widget)
        option.text = text
        content = style.subElementRect(QStyle.SE_PushButtonContents, option, widget)
    chrome_height = max(0, widget.height() - content.height())
    chrome_width = max(0, widget.width() - content.width())
    widget.setMinimumWidth(glyph_width + chrome_width + 4)
    widget.setMinimumHeight(max(widget.sizeHint().height(), glyph_height + chrome_height + 4))


class SettingsSpinBox(QSpinBox):
    """Draw simple arrows instead of mixing native steppers with rounded borders."""

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for control, direction, enabled in (
            (QStyle.SC_SpinBoxUp, -1, self.value() < self.maximum()),
            (QStyle.SC_SpinBoxDown, 1, self.value() > self.minimum()),
        ):
            rect = self.style().subControlRect(QStyle.CC_SpinBox, option, control, self)
            center = rect.center()
            group = QPalette.Active if enabled and self.isEnabled() else QPalette.Disabled
            painter.setPen(QPen(self.palette().color(group, QPalette.ButtonText), 1.3))
            painter.drawLine(QPointF(center.x() - 3, center.y() - direction), QPointF(center.x(), center.y() + direction))
            painter.drawLine(QPointF(center.x(), center.y() + direction), QPointF(center.x() + 3, center.y() - direction))


class SettingsDialog(QDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.store = panel.store
        self.setObjectName('settingsDialog')
        self.setWindowTitle('设置')
        # Top-level dialogs do not normally inherit their parent's explicit font.
        self.setFont(QFont(panel.font()))
        self.setMinimumSize(360, 280)
        self.setSizeGripEnabled(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        self.scroll = QScrollArea()
        self.scroll.setObjectName('settingsScroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.body.setObjectName('settingsBody')
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 12, 0)
        body_layout.setSpacing(14)
        body_layout.setSizeConstraint(QLayout.SetMinimumSize)
        self.form = QFormLayout()
        self.form.setSpacing(12)
        self.form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        limits = self.store.limits
        self.retention = FormatComboBox()
        self.retention.setObjectName("settingsChoice")
        self.retention.setAccessibleName('保留时间')
        for label, days in (('1 天', 1), ('7 天', 7), ('1 个月（30 天）', 30), ('1 年（365 天）', 365), ('无限期', 0)):
            self.retention.addItem(label, days)
        index = self.retention.findData(limits.days)
        if index < 0:
            self.retention.addItem(f'原设置：{limits.days} 天', limits.days)
            index = self.retention.count() - 1
        self.retention.setCurrentIndex(index)
        self.form.addRow('保留时间', self.retention)

        self.fields = []
        for name, label, value, maximum in (
            ('普通历史条数', '历史条数', limits.count, 100000),
            ('总内容容量（MiB）', '内容容量（MiB）', limits.total_bytes // 1048576, 10240),
            ('单条上限（MiB）', '单条上限（MiB）', limits.item_bytes // 1048576, 10240),
        ):
            field = SettingsSpinBox()
            field.setObjectName("settingsNumber")
            field.setRange(1 if name == '总内容容量（MiB）' else 0, maximum)
            if name != '总内容容量（MiB）':
                field.setSpecialValueText('自动' if '单条' in name else '不限')
            field.setValue(value)
            field.setAccessibleName(name)
            self.form.addRow(label, field)
            self.fields.append(field)

        self.theme = FormatComboBox()
        self.theme.setObjectName("settingsChoice")
        self.theme.setAccessibleName('外观')
        for label, value in (('跟随系统', 'system'), ('浅色', 'light'), ('深色', 'dark')):
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(self.store.setting('theme', 'system'))))
        self.form.addRow('外观', self.theme)
        self.shortcut_button = QPushButton(panel.shortcut + ' · 修改')
        self.shortcut_button.setAccessibleName('全局快捷键')
        self.shortcut_button.clicked.connect(self.change_shortcut)
        self.form.addRow('快捷键', self.shortcut_button)
        self.startup = QCheckBox('登录后在后台启动')
        try:
            self.startup.setChecked(panel.autostart.enabled())
        except (OSError, ValueError):
            self.startup.setEnabled(False)
            self.startup.setToolTip('无法读取系统自启动目录。')
        self.startup.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.startup.setMinimumHeight(max(self.startup.sizeHint().height(), self.fontMetrics().height() + 8))
        self.form.addRow('启动', self.startup)

        self.folder = self.store.path.parent.resolve()
        folder_box = QWidget()
        folder_layout = QVBoxLayout(folder_box)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(6)
        self.folder_path = QLineEdit(str(self.folder))
        self.folder_path.setReadOnly(True)
        self.folder_path.setAccessibleName('历史保存文件夹')
        self.folder_path.setToolTip(str(self.folder))
        self.folder_path.setCursorPosition(0)
        folder_layout.addWidget(self.folder_path)
        self.folder_button = QPushButton('选择文件夹…')
        self.folder_button.clicked.connect(self.choose_folder)
        folder_layout.addWidget(self.folder_button)
        self.form.addRow('保存文件夹', folder_box)
        body_layout.addLayout(self.form)

        self.folder_note = self.note('更换文件夹后，保存会复制现有历史并重启；原目录保留备份，不覆盖目标目录已有的历史。',
                                     'folderExplanation', body_layout)

        self.usage = self.note(
            f'当前内容 {self.store.usage() / 1048576:.2f} MiB · 数据库 {self.store.disk_usage() / 1048576:.2f} MiB',
            'settingsUsage', body_layout)
        self.note('无限期仍受容量限制，收藏不会自动清理。单条填 0 表示自动，条数填 0 表示不限；自动上限为内容总容量。数据库索引额外占用空间。',
                  'settingsExplanation', body_layout)
        self.warning = self.note('缩短期限或减小容量后，超出限制的普通记录会被清理，删除无法恢复。',
                                 'settingsWarning', body_layout)
        self.error = self.note('', 'notice', body_layout)
        self.error.hide()
        body_layout.addStretch()
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        footer = QWidget()
        footer.setObjectName('settingsFooter')
        footer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText('保存')
        self.buttons.button(QDialogButtonBox.Save).setObjectName('primary')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        for button in self.buttons.buttons():
            button.setIcon(QIcon())
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        footer_layout.addWidget(self.buttons)
        root.addWidget(footer)
        self.ensurePolished()
        for combo in (self.retention, self.theme):
            fit_control(combo, [combo.itemText(i) for i in range(combo.count())])
        for field in self.fields:
            fit_control(field, [field.text(), field.specialValueText(), str(field.maximum())])
        fit_control(self.folder_path, '历史保存文件夹')
        for button in (self.shortcut_button, self.folder_button, *self.buttons.buttons()):
            fit_control(button, button.text())
        for button in self.buttons.buttons():
            button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.fit_width()
        screen = self.screen().availableGeometry()
        self.resize(min(520, max(360, screen.width() - 80)), min(570, max(280, screen.height() - 100)))

    @staticmethod
    def note(text, name, layout):
        label = WrappedLabel(text)
        label.setObjectName(name)
        layout.addWidget(label)
        return label

    def fit_width(self):
        self.body.layout().activate()
        self.layout().activate()
        self.setMinimumWidth(max(360, self.body.minimumSizeHint().width() + 60))

    def change_shortcut(self):
        self.panel.change_shortcut()
        self.shortcut_button.setText(self.panel.shortcut + ' · 修改')
        fit_control(self.shortcut_button, self.shortcut_button.text())
        self.fit_width()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '选择历史保存文件夹', str(self.folder))
        if folder:
            self.folder = Path(folder).resolve()
            self.folder_path.setText(str(self.folder))
            self.folder_path.setToolTip(str(self.folder))
            self.folder_path.setCursorPosition(0)
            changed = self.folder != self.store.path.parent.resolve()
            self.buttons.button(QDialogButtonBox.Save).setText('保存并重启' if changed else '保存')
            fit_control(self.buttons.button(QDialogButtonBox.Save), self.buttons.button(QDialogButtonBox.Save).text())
            self.fit_width()

    def show_error(self, message):
        self.error.setText(message)
        self.error.show()
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.error))

    def save(self):
        moving = self.folder != self.store.path.parent.resolve()
        if moving and self.panel.relocate_history is None:
            self.show_error('当前运行模式无法更换数据目录，请正常启动应用后重试。')
            return
        count, total, item = (field.value() for field in self.fields)
        try:
            limits = Limits(self.retention.currentData(), count, total * 1048576, item * 1048576)
            if limits != self.store.limits:
                self.store.set_limits(limits)
                self.panel.monitor.cancel_pending()
            self.store.set_setting('theme', self.theme.currentData())
            self.panel._style()
            self.panel.refresh()
        except ValueError as error:
            self.show_error(str(error))
            return
        try:
            if self.startup.isEnabled() and (self.startup.isChecked() or self.panel.autostart.enabled()):
                self.panel.autostart.set_enabled(self.startup.isChecked())
            self.store.set_setting('startup_initialized', True)
        except (OSError, ValueError):
            self.show_error('保存规则和外观已更新，但无法修改自启动。请检查目录权限，或取消勾选自启动后重试。')
            return
        if moving:
            try:
                self.panel.relocate_history(self.folder)
            except (OSError, ValueError, sqlite3.Error) as error:
                self.show_error(f'其他设置已保存，文件夹操作未完成：{error}')
                return
        self.accept()
        self.panel.show_notice('历史已复制，正在重启…' if moving else '设置已保存。')
