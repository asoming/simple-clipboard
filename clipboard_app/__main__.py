"""Application entry point."""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from PyQt5.QtCore import QProcess, Qt
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import QApplication, QMessageBox

from .monitor import Monitor
from .input_method import prepare_input_method
from .instance import InstanceLock
from .migration import import_history
from .paths import default_data_dir, instance_socket, launch_arguments
from .platforms import create_backend, PlatformUnavailable
from .store import Store
from .preferences import initialize_startup
from .ui import Panel, app_icon


def main() -> int:
    parser = argparse.ArgumentParser(description="简洁的本地剪贴板管理器")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--hidden", action="store_true", help="在后台启动，通过快捷键打开")
    parser.add_argument("--import-history", type=Path, help="退出旧版后，将数据库导入空历史目录；原文件保留")
    parser.add_argument("--smoke-test", type=Path, metavar="REPORT", help="使用临时数据检查启动，将结果写入报告；不监听剪贴板")
    args = parser.parse_args()
    os.umask(0o077)
    prepare_input_method()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Clipboard")
    app.setApplicationDisplayName("剪贴板")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    if args.smoke_test:
        from .smoke import run
        return run(app, args.smoke_test)
    data_dir = (args.data_dir or default_data_dir()).resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = InstanceLock(data_dir / "instance.lock")
    socket_name = instance_socket(data_dir)
    if not lock.acquire():
        if args.import_history:
            QMessageBox.warning(None, "无法导入", "请先退出当前版本，再导入旧历史。")
            return 1
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
    store = None
    try:
        if args.import_history:
            import_history(args.import_history, data_dir / "history.sqlite3")
        store = Store(data_dir / "history.sqlite3")
        backend = create_backend()
    except (OSError, sqlite3.Error, ValueError, PlatformUnavailable) as error:
        QMessageBox.critical(None, "无法启动剪贴板", str(error))
        if store:
            store.close()
        lock.release()
        return 1
    monitor = Monitor(app.clipboard(), store)
    panel = Panel(store, monitor, backend)
    try:
        initialize_startup(store)
    except (OSError, ValueError):
        panel.recording_notice("未能开启登录自启动，请在设置中重试。")
    import_path = None

    def restart_for_import(filename):
        nonlocal import_path
        import_path = filename
        app.quit()

    panel.import_requested.connect(restart_for_import)
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
    if not args.hidden or panel.shortcut_error or panel.pending_notice:
        panel.open_panel()

    def report_error(error_type, error, traceback):
        # Do not print exception values or locals: clipboard content is private.
        if sys.stderr is not None:
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
    if import_path:
        command = launch_arguments() + ['--data-dir', str(data_dir), '--import-history', import_path]
        started, _ = QProcess.startDetached(command[0], command[1:])
        if not started:
            QMessageBox.critical(None, '无法重启', '未能重新打开应用。历史未修改，请手动启动后重试导入。')
            return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
