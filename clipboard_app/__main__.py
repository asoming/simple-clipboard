"""Application entry point."""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import QApplication, QMessageBox

from .monitor import Monitor
from .input_method import prepare_input_method
from .instance import InstanceLock
from .store import Store
from .ui import Panel, app_icon
from .x11 import X11, X11Unavailable


def main() -> int:
    parser = argparse.ArgumentParser(description="简洁的本地剪贴板管理器")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    parser.add_argument("--hidden", action="store_true", help="在后台启动，通过快捷键打开")
    args = parser.parse_args()
    os.umask(0o077)
    prepare_input_method()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Clipboard")
    app.setApplicationDisplayName("剪贴板")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    data_dir = args.data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = InstanceLock(data_dir / "instance.lock")
    socket_name = str(data_dir / "instance.sock")
    if not lock.acquire():
        socket = QLocalSocket()
        socket.connectToServer(socket_name)
        if socket.waitForConnected(1000):
            socket.write(b"show")
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
            return 0
        QMessageBox.warning(None, "剪贴板", "应用已在运行或上一次仍未退出，请稍后重试。")
        return 1
    backend = None
    try:
        store = Store(data_dir / "history.sqlite3")
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            raise X11Unavailable("当前版本仅支持 X11。请在登录界面选择 Ubuntu on Xorg 后运行。")
        backend = X11()
    except (OSError, sqlite3.Error, ValueError, X11Unavailable) as error:
        QMessageBox.critical(None, "无法启动剪贴板", str(error))
        lock.release()
        return 1
    monitor = Monitor(app.clipboard(), store)
    panel = Panel(store, monitor, backend)
    QLocalServer.removeServer(socket_name)
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.UserAccessOption)

    def reveal():
        socket = server.nextPendingConnection()
        if socket:
            panel.open_panel()
            socket.disconnectFromServer()
            socket.deleteLater()

    server.newConnection.connect(reveal)
    if not server.listen(socket_name):
        panel.show_notice("重复启动时无法自动打开窗口，请使用全局快捷键。")
    if not args.hidden or panel.shortcut_error:
        panel.open_panel()

    def report_error(error_type, error, traceback):
        # Do not print exception values or locals: clipboard content is private.
        print(f"Clipboard operation failed: {error_type.__name__}", file=sys.stderr)
        panel.show_notice("操作未完成，请检查存储空间并重试。已有历史不会被自动重建。")

    sys.excepthook = report_error
    result = app.exec_()
    monitor.stop()
    server.close()
    if backend:
        backend.close()
    store.close()
    lock.release()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
