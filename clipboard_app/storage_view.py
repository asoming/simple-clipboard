"""Space and resident memory are separate measurements, with separate actions."""

import ctypes
import gc
import os
import subprocess
import sys
from collections import deque
from pathlib import Path

from PyQt5.QtCore import QPointF, QRectF, QTimer, Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmapCache
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout, QWidget


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
        area = QRectF(0, 2, self.width(), 12)
        clip = QPainterPath()
        clip.addRoundedRect(area, 6, 6)
        painter.setClipPath(clip)
        painter.fillRect(area, QColor('#dce2ea'))
        favorite_width = self.width() * self.favorites / max(1, self.total)
        ordinary_width = self.width() * self.ordinary / max(1, self.total)
        painter.fillRect(QRectF(0, 2, favorite_width, 12), QColor('#e9b540'))
        painter.fillRect(QRectF(favorite_width, 2, ordinary_width, 12), QColor('#627e9f'))


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


class StorageDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle('空间与内存')
        self.resize(460, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        self.space = QLabel()
        self.space.setWordWrap(True)
        layout.addWidget(self.space)
        self.bar = SpaceBar()
        layout.addWidget(self.bar)
        self.legend = QLabel()
        self.legend.setWordWrap(True)
        layout.addWidget(self.legend)
        compact = QPushButton('回收历史文件空闲空间')
        compact.setToolTip('整理数据库已空出的页面，保留全部现有记录和收藏。')
        compact.clicked.connect(self.compact)
        layout.addWidget(compact)
        self.memory = QLabel()
        layout.addWidget(self.memory)
        self.chart = MemoryChart()
        layout.addWidget(self.chart)
        self.scale = QLabel('每 2 秒采样 · 纵轴从 0 起，随峰值调整')
        self.scale.setWordWrap(True)
        layout.addWidget(self.scale)
        release = QPushButton('释放可回收缓存')
        release.clicked.connect(self.release_cache)
        layout.addWidget(release)
        note = QLabel('历史容量使用磁盘，运行内存由系统分配。释放缓存不删除历史，内存占用不一定立即下降。删除记录请使用主菜单「清空历史」。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.result = QLabel()
        self.result.setWordWrap(True)
        layout.addWidget(self.result)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText('关闭')
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.update_memory)
        self.finished.connect(self.timer.stop)
        self.update_space()
        self.update_memory()
        self.timer.start()

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

    def release_cache(self):
        QPixmapCache.clear()
        self.store.release_cache()
        gc.collect()
        self.update_memory()
        self.result.setText('已释放可回收缓存。当前记录、收藏和系统剪贴板均保留；实际占用见上方读数。')
