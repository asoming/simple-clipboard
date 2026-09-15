"""External clipboard observation with bounded, cancellable image processing."""

import sqlite3
import sys
import time
from collections import deque

from PyQt5.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal
from PyQt5.QtGui import QClipboard

from .content import as_mime, prepare, snapshot
from .store import CapacityError, Clip, Content, Store


class ResultSignals(QObject):
    ready = pyqtSignal(int, object, float, str)


class PrepareTask(QRunnable):
    def __init__(self, generation, value, timestamp, limit, signals):
        super().__init__()
        self.generation, self.value, self.timestamp = generation, value, timestamp
        self.limit, self.signals = limit, signals

    def run(self):
        try:
            content = prepare(self.value, self.limit)
        except (ValueError, MemoryError) as error:
            message = str(error) if isinstance(error, ValueError) else "可用内存不足，已跳过这张图片。"
            self.signals.ready.emit(self.generation, None, self.timestamp, message)
        except Exception:
            # Always complete the queue item; never expose clipboard bytes in errors.
            self.signals.ready.emit(self.generation, None, self.timestamp, "这条内容无法处理，已跳过。")
        else:
            self.signals.ready.emit(self.generation, content, self.timestamp, "")


class Monitor(QObject):
    changed = pyqtSignal()
    state_changed = pyqtSignal()
    notice = pyqtSignal(str)

    def __init__(self, clipboard: QClipboard, store: Store):
        super().__init__()
        self.clipboard, self.store = clipboard, store
        self.paused = bool(store.setting("paused", False))
        self.ignore_next = False
        self._writing = False
        self._stopped = False
        self.sequence = None
        self.last_sequence = None
        self.poll_timer = QTimer(self)
        if sys.platform == 'darwin':
            from AppKit import NSPasteboard
            pasteboard = NSPasteboard.generalPasteboard()
            self.sequence = pasteboard.changeCount
            self.last_sequence = self.sequence()
            self.poll_timer.setInterval(150)
            self.poll_timer.timeout.connect(lambda: self._changed(QClipboard.Clipboard))
            self.poll_timer.start()
        self.generation = 0
        self.processing = False
        self.queue = deque()
        self.queue_bytes = 0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.signals = ResultSignals(self)
        self.signals.ready.connect(self._ready)
        clipboard.changed.connect(self._changed)

    def cancel_pending(self):
        self.generation += 1
        self.queue.clear()
        self.queue_bytes = 0

    def pause(self, value: bool):
        self.store.set_setting("paused", value)
        self.paused = value
        if self.sequence:
            self.last_sequence = self.sequence()
        self.ignore_next = False
        self.cancel_pending()
        self.state_changed.emit()

    def toggle_ignore(self):
        if not self.paused:
            self.ignore_next = not self.ignore_next
            self.state_changed.emit()

    def _changed(self, mode):
        if self.sequence and mode == QClipboard.Clipboard:
            current = self.sequence()
            if current == self.last_sequence:
                return
            self.last_sequence = current
        if mode != QClipboard.Clipboard or self._writing or self.clipboard.ownsClipboard() or self.paused:
            return
        mime = self.clipboard.mimeData()
        if mime is None or not mime.formats():
            return
        if self.ignore_next:
            self.ignore_next = False
            self.state_changed.emit()
            return
        try:
            value = snapshot(mime, self.store.limits.item_bytes)
        except UnicodeError:
            self.notice.emit("这条文本的字符编码无法处理，已跳过。")
            return
        except ValueError as error:
            self.notice.emit(str(error))
            return
        if value is None:
            return
        # A bounded queue prevents rapid repeated large copies from exhausting RAM.
        from PyQt5.QtGui import QImage
        size = value.image.sizeInBytes() if isinstance(value.image, QImage) else len(value.image)
        size += len(value.text.encode("utf-8")) + len(value.html.encode("utf-8"))
        if self.processing and (len(self.queue) >= 8 or self.queue_bytes + size > 32 * 1024 * 1024):
            self.notice.emit("正在处理较多内容，这次复制未记录。请稍后重试。")
            return
        self.queue.append((self.generation, value, time.time(), size))
        self.queue_bytes += size
        self._next()

    def _next(self):
        if self.processing or not self.queue:
            return
        generation, value, timestamp, size = self.queue.popleft()
        self.queue_bytes -= size
        self.processing = True
        self.state_changed.emit()
        self.pool.start(PrepareTask(generation, value, timestamp, self.store.limits.item_bytes, self.signals))

    def _ready(self, generation, content, timestamp, message):
        self.processing = False
        if generation == self.generation and not self.paused:
            if message:
                self.notice.emit(message)
            elif content is not None:
                try:
                    self.store.add_content(content, timestamp)
                except CapacityError as error:
                    self.notice.emit(str(error))
                except (sqlite3.Error, UnicodeError):
                    self.notice.emit("未能保存内容，请检查存储空间。系统剪贴板不受影响。")
                else:
                    self.changed.emit()
        self.state_changed.emit()
        self._next()

    def copy(self, value: str | Clip | Content, plain: bool = False):
        content = Content(text=value) if isinstance(value, str) else value.content if isinstance(value, Clip) else value
        self._writing = True
        try:
            self.clipboard.setMimeData(as_mime(content, plain))
        finally:
            self._writing = False
            if self.sequence:
                self.last_sequence = self.sequence()

    def clear_current(self):
        self._writing = True
        try:
            self.clipboard.clear()
        finally:
            self._writing = False
            if self.sequence:
                self.last_sequence = self.sequence()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self.poll_timer.stop()
        self.cancel_pending()
        self.clipboard.changed.disconnect(self._changed)
        self.signals.ready.disconnect(self._ready)
        self.pool.waitForDone()
