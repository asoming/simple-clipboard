"""Space and resident memory are separate measurements, with separate actions."""

import ctypes
import gc
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

from PyQt5.QtCore import QPointF, QRectF, QTimer, Qt
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmapCache
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QFrame, QLayout, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .settings_dialog import fit_control
from .widgets import WrappedLabel


MIB = 1048576


def resident_bytes():
    """Current RSS/working set, not peak memory or the database's disk size."""
    if sys.platform.startswith('linux'):
        return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    if sys.platform == 'win32':
        from ctypes import wintypes as W
        class Counters(ctypes.Structure):
            _fields_ = [('cb', W.DWORD), ('PageFaultCount', W.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = W.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [W.HANDLE, ctypes.POINTER(Counters), W.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.WorkingSetSize
    if sys.platform == 'darwin':
        result = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(os.getpid())],
                                capture_output=True, text=True, check=True, timeout=2)
        return int(result.stdout.strip()) * 1024
    raise OSError('Resident memory is unavailable on this platform')


class SpaceBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.total = 1
        self.ordinary = self.favorites = 0
        self.setMinimumHeight(16)
        self.setAccessibleName('历史容量：普通记录、收藏、剩余空间')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = QRectF(0, 4, self.width(), 8)
        clip = QPainterPath()
        clip.addRoundedRect(area, 4, 4)
        painter.setClipPath(clip)
        surface = self.palette().color(QPalette.Window)
        dark = surface.lightness() < 128
        painter.fillRect(area, surface.lighter(125) if dark else surface.darker(106))
        favorite_width = self.width() * self.favorites / max(1, self.total)
        ordinary_width = self.width() * self.ordinary / max(1, self.total)
        painter.fillRect(QRectF(0, 4, favorite_width, 8), QColor('#dcb764' if dark else '#c89a32'))
        painter.fillRect(QRectF(favorite_width, 4, ordinary_width, 8), QColor('#8da9cb' if dark else '#627d9e'))


class MemoryChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples = deque(maxlen=60)
        self.setMinimumHeight(100)
        self.setAccessibleName('运行内存趋势，最多显示最近两分钟')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        area = self.rect().adjusted(4, 8, -4, -8)
        painter.setPen(QPen(QColor('#8691a0'), 1))
        painter.drawLine(area.bottomLeft(), area.bottomRight())
        if not self.samples:
            return
        ceiling = max(self.samples) * 1.2 or 1
        path = QPainterPath()
        for index, sample in enumerate(self.samples):
            point = QPointF(area.left() + index * area.width() / 59,
                            area.bottom() - sample / ceiling * area.height())
            if index:
                path.lineTo(point)
            else:
                path.moveTo(point)
        painter.setPen(QPen(QColor('#789bc4'), 2))
        painter.drawPath(path)
        painter.drawEllipse(point, 2, 2)


class StoragePage(QWidget):
    """Reusable space view; sampling runs only while this page is visible."""

    def __init__(self, store, parent=None, open_folder=None):
        super().__init__(parent)
        self.store = store
        self.open_folder = open_folder
        if parent is not None:
            self.setFont(QFont(parent.font()))
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName('storageScroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.body.setObjectName('storageBody')
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(2, 4, 12, 8)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        layout.setSpacing(10)
        heading = WrappedLabel('历史空间')
        heading.setObjectName('sectionTitle')
        self.compact_button = QPushButton('整理文件空间')
        self.compact_button.setObjectName('quiet')
        self.compact_button.setToolTip('整理数据库已空出的页面，保留全部现有记录和收藏。')
        self.compact_button.clicked.connect(self.compact)
        space_heading = QFormLayout()
        space_heading.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        space_heading.setRowWrapPolicy(QFormLayout.WrapLongRows)
        space_heading.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        space_heading.addRow(heading, self.compact_button)
        layout.addLayout(space_heading)
        self.space = WrappedLabel()
        layout.addWidget(self.space)
        self.bar = SpaceBar()
        layout.addWidget(self.bar)
        self.legend = WrappedLabel()
        self.legend.setObjectName('sectionDescription')
        layout.addWidget(self.legend)
        divider = QFrame()
        divider.setObjectName('divider')
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        heading = WrappedLabel('运行内存')
        heading.setObjectName('sectionTitle')
        self.release_button = QPushButton('释放可回收缓存')
        self.release_button.setObjectName('quiet')
        self.release_button.clicked.connect(self.release_cache)
        memory_heading = QFormLayout()
        memory_heading.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)
        memory_heading.setRowWrapPolicy(QFormLayout.WrapLongRows)
        memory_heading.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        memory_heading.addRow(heading, self.release_button)
        layout.addLayout(memory_heading)
        self.memory = WrappedLabel()
        layout.addWidget(self.memory)
        self.chart = MemoryChart()
        layout.addWidget(self.chart)
        self.scale = WrappedLabel('每 2 秒采样 · 纵轴从 0 起，随峰值调整')
        self.scale.setObjectName('sectionDescription')
        layout.addWidget(self.scale)
        self.note = WrappedLabel('历史占用磁盘，运行内存由系统分配。整理空间和释放缓存都不删除记录；内存占用不一定立即下降。')
        self.note.setObjectName('sectionDescription')
        layout.addWidget(self.note)
        divider = QFrame()
        divider.setObjectName('divider')
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        label = WrappedLabel('保存位置')
        label.setObjectName('sectionTitle')
        layout.addWidget(label)
        self.folder_path = QLineEdit(str(store.path.parent.resolve()))
        self.folder_path.setReadOnly(True)
        self.folder_path.setAccessibleName('历史保存文件夹')
        self.folder_path.setToolTip(self.folder_path.text())
        self.folder_path.setCursorPosition(0)
        layout.addWidget(self.folder_path)
        self.folder_button = QPushButton('修改保存文件夹…')
        self.folder_button.setObjectName('quiet')
        self.folder_button.setEnabled(open_folder is not None)
        self.folder_button.clicked.connect(self.change_folder)
        layout.addWidget(self.folder_button, 0, Qt.AlignLeft)
        self.result = WrappedLabel()
        self.result.setObjectName('notice')
        layout.addWidget(self.result)
        layout.addStretch()
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)
        self.ensurePolished()
        fit_control(self.folder_path, '历史保存文件夹')
        for button in (self.folder_button, self.compact_button, self.release_button):
            fit_control(button, button.text())
            button.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.body.layout().activate()
        root.activate()
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.update_memory)
        self.update_space()
        self.memory.setText('当前运行内存 —')

    def set_sampling(self, active):
        if active and not self.timer.isActive():
            self.chart.samples.clear()
            self.update_space()
            self.update_memory()
            self.timer.start()
        elif not active:
            self.timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        self.set_sampling(True)

    def hideEvent(self, event):
        self.set_sampling(False)
        super().hideEvent(event)

    def change_folder(self):
        if self.open_folder is None:
            return
        self.open_folder()
        self.update_space()

    def update_space(self):
        usage, favorites, total = self.store.usage(), self.store.pinned_usage(), self.store.limits.total_bytes
        self.space.setText(f'历史内容  {usage / MIB:.2f} / {total / MIB:g} MiB')
        self.bar.total, self.bar.favorites, self.bar.ordinary = total, favorites, usage - favorites
        self.bar.update()
        self.legend.setText(f'蓝色 · 普通 {(usage - favorites) / MIB:.2f} MiB   黄色 · 收藏 {favorites / MIB:.2f} MiB\n'
                            f'剩余 {max(0, total - usage) / MIB:.2f} MiB · 数据库文件 {self.store.disk_usage() / MIB:.2f} MiB（含索引等）')

    def update_memory(self):
        try:
            value = resident_bytes()
        except (OSError, ValueError, subprocess.SubprocessError):
            self.memory.setText('运行内存：暂时无法读取')
            return
        self.memory.setText(f'运行内存  {value / MIB:.1f} MiB')
        self.chart.samples.append(value)
        self.chart.update()
        self.scale.setText(f'每 2 秒采样 · 图表范围 0–{max(self.chart.samples) * 1.2 / MIB:.1f} MiB')

    def compact(self):
        before = self.store.disk_usage()
        self.store.compact()
        self.update_space()
        self.result.setText(f'已回收 {max(0, before - self.store.disk_usage()) / MIB:.2f} MiB 文件空间，现有记录全部保留。')
        self.result.show()
        self.scroll.ensureWidgetVisible(self.result)

    def release_cache(self):
        QPixmapCache.clear()
        self.store.release_cache()
        gc.collect()
        self.update_memory()
        self.result.setText('已释放可回收缓存。当前记录、收藏和系统剪贴板均保留；实际占用见上方读数。')
        self.result.show()
        self.scroll.ensureWidgetVisible(self.result)


class StorageDialog(QDialog):
    """Compatibility window around the same embeddable storage page."""

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle('空间与内存')
        if parent is not None:
            self.setFont(QFont(parent.font()))
        self.setMinimumSize(360, 280)
        self.resize(460, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        open_folder = (lambda: parent.settings(focus_folder=True)) if parent is not None else None
        self.page = StoragePage(store, self, open_folder=open_folder)
        root.addWidget(self.page, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close = self.buttons.button(QDialogButtonBox.Close)
        close.setText('关闭')
        close.setIcon(QIcon())
        fit_control(close, close.text())
        close.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.finished.connect(lambda: self.page.set_sampling(False))
        # Existing callers keep the original dialog's controls and action API.
        for name in ('scroll', 'body', 'space', 'bar', 'legend', 'folder_path', 'folder_button',
                     'memory', 'chart', 'scale', 'note', 'result', 'timer'):
            setattr(self, name, getattr(self.page, name))
        self.compact = self.page.compact
        self.release_cache = self.page.release_cache
        self.update_space = self.page.update_space
        self.update_memory = self.page.update_memory
        self.change_folder = self.page.change_folder
        self.body.layout().activate()
        root.activate()
        self.setMinimumWidth(max(360, self.body.minimumSizeHint().width() + 60))
