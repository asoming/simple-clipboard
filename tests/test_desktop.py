"""Run on an isolated X server, never against the user's clipboard."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import threading
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtCore import Qt, QTimer
if sys.platform.startswith('linux'):
    from PyQt5.QtDBus import QDBusVariant
from PyQt5.QtGui import QInputMethodEvent, QPalette
from PyQt5.QtNetwork import QLocalSocket
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QDialog, QDialogButtonBox, QSpinBox

from clipboard_app.monitor import Monitor
from clipboard_app.preferences import LinuxAutostart
from clipboard_app.store import Store
from clipboard_app.ui import Panel
from clipboard_app.x11 import Target, X11


app = QApplication.instance() or QApplication([])


def wait_until(condition, timeout=2500):
    start = time.monotonic()
    while time.monotonic() - start < timeout / 1000:
        app.processEvents()
        if condition():
            return True
        QTest.qWait(10)
    return False


@unittest.skipUnless(sys.platform.startswith('linux') and os.environ.get("CLIPBOARD_ISOLATED_TEST") == "1", "Requires isolated X11 desktop")
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.socket_path = str(Path(cls.temp.name) / "peer.sock")
        cls.peer = subprocess.Popen([sys.executable, "tests/peer.py", cls.socket_path], env={**os.environ, "PYTHONPATH": "."})
        assert wait_until(lambda: Path(cls.socket_path).exists())
        cls.backend = X11()

    @classmethod
    def tearDownClass(cls):
        cls.backend.close()
        cls.peer.terminate()
        cls.peer.wait(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.db_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.db_dir.name) / "history.sqlite3")
        self.monitor = Monitor(app.clipboard(), self.store)
        self.panel = Panel(self.store, self.monitor, self.backend)
        self.panel.autostart = LinuxAutostart(Path(self.db_dir.name), Path(self.db_dir.name) / "config")
        self.panel.show()
        QTest.qWait(80)

    def tearDown(self):
        self.monitor.stop()
        self.backend.activated.disconnect(self.panel.toggle)
        self.backend.paste_failed.disconnect(self.panel.paste_failed)
        self.panel.cleanup_timer.stop()
        self.panel.tray.hide()
        self.panel.hide()
        self.panel.deleteLater()
        app.processEvents()
        self.store.close()
        self.db_dir.cleanup()

    def peer_call(self, action, wait_processing=True, **kwargs):
        socket = QLocalSocket()
        socket.connectToServer(self.socket_path)
        self.assertTrue(socket.waitForConnected(1000))
        socket.write(json.dumps({"action": action, **kwargs}).encode() + b"\n")
        socket.flush()
        self.assertTrue(wait_until(lambda: socket.bytesAvailable() > 0))
        result = json.loads(bytes(socket.readAll()))
        socket.close()
        QTest.qWait(60)
        if wait_processing:
            self.assertTrue(wait_until(lambda: not self.monitor.processing and not self.monitor.queue))
        return result

    def pasted_text(self, expected, field="text"):
        # Key injection finishes before the target has received all X11 MIME data.
        deadline = time.monotonic() + 3
        result = self.peer_call("read")
        while result[field] != expected and time.monotonic() < deadline:
            result = self.peer_call("read")
        self.assertEqual(result[field], expected)
        return result

    def test_external_clipboard_preserves_unicode_whitespace_and_order(self):
        text = "  中文\n\tHello  \n"
        self.peer_call("copy", text=text)
        self.assertTrue(wait_until(lambda: len(self.store.list()) == 1))
        self.assertEqual(self.store.list()[0].text, text)
        self.peer_call("copy", text="second")
        self.peer_call("copy", text=text)
        self.assertEqual([clip.text for clip in self.store.list()], [text, "second"])

    def test_pause_resume_ignores_previous_content(self):
        self.monitor.pause(True)
        self.peer_call("copy", text="not saved")
        self.monitor.pause(False)
        QTest.qWait(100)
        self.assertEqual(self.store.list(), [])
        self.peer_call("copy", text="saved")
        self.assertEqual([clip.text for clip in self.store.list()], ["saved"])

    def test_ignore_next_external_change_not_internal_copy(self):
        self.monitor.toggle_ignore()
        self.monitor.copy("internal")
        self.assertTrue(self.monitor.ignore_next)
        self.peer_call("copy", text="skip")
        self.assertFalse(self.monitor.ignore_next)
        self.peer_call("copy", text="keep")
        self.assertEqual([clip.text for clip in self.store.list()], ["keep"])

    def test_unsupported_file_and_secret_do_not_pollute_history(self):
        self.peer_call("copy", text="file:///tmp/test.txt", file=True)
        self.peer_call("copy", text="secret", secret=True)
        self.assertEqual(self.store.list(), [])

    def test_own_copy_does_not_reinsert_deleted_history(self):
        clip_id = self.store.add("sample")
        self.monitor.copy("sample")
        self.store.delete(clip_id)
        QTest.qWait(80)
        self.assertEqual(self.store.list(), [])

    def test_search_and_esc_do_not_change_clipboard(self):
        self.peer_call("copy", text="unchanged")
        self.store.add("中文 World")
        self.panel.search.setText("中文 WORLD")
        self.assertEqual(self.panel.history.count(), 1)
        QTest.keyClick(self.panel.search, Qt.Key_Escape)
        self.assertEqual(self.peer_call("clipboard")["text"], "unchanged")

    def test_ime_preedit_and_commit_enter_do_not_paste(self):
        spy = QSignalSpy(self.panel.search.paste_selected)
        app.sendEvent(self.panel.search, QInputMethodEvent("zhong", []))
        QTest.keyClick(self.panel.search, Qt.Key_Return)
        self.assertEqual(len(spy), 0)
        commit = QInputMethodEvent()
        commit.setCommitString("中")
        app.sendEvent(self.panel.search, commit)
        QTest.keyClick(self.panel.search, Qt.Key_Return)
        self.assertEqual(len(spy), 0)
        QTest.qWait(180)
        QTest.keyClick(self.panel.search, Qt.Key_Return)
        self.assertEqual(len(spy), 1)

    def test_keyboard_paste_returns_to_external_editor(self):
        self.store.add("clipboard 中文")
        self.panel.hide()
        window = self.peer_call("focus")["window"]
        self.panel.open_panel()
        self.assertEqual(self.panel.target.window, window)
        self.assertTrue(wait_until(lambda: self.backend.belongs_to(self.backend.focus(), int(self.panel.winId()))))
        QTest.keyClick(self.panel.search, Qt.Key_Return)
        self.assertTrue(wait_until(lambda: not self.backend.paste_timer.isActive()))
        self.pasted_text("clipboard 中文")

    def test_chat_paste_does_not_submit(self):
        self.store.add("chat text")
        self.panel.hide()
        self.peer_call("focus", chat=True)
        self.panel.open_panel()
        self.assertTrue(wait_until(lambda: self.backend.belongs_to(self.backend.focus(), int(self.panel.winId()))))
        QTest.keyClick(self.panel.search, Qt.Key_Return)
        self.assertTrue(wait_until(lambda: not self.backend.paste_timer.isActive()))
        result = self.pasted_text("chat text", "chat")
        self.assertEqual(result["submissions"], [])

    def test_multiline_terminal_requires_manual_paste(self):
        self.store.add("echo one\necho two")
        self.panel.refresh()
        self.panel.target = Target(999, terminal=True)
        self.panel.paste()
        self.assertFalse(self.backend.paste_timer.isActive())
        self.assertIn("多行文本已复制", self.panel.notice.text())

    def test_invalid_target_falls_back_without_typing_elsewhere(self):
        failure = QSignalSpy(self.backend.paste_failed)
        self.backend.paste(Target(0x7FFFFFFF))
        self.assertTrue(wait_until(lambda: len(failure) == 1))
        self.assertIn("手动粘贴", failure[0][0])

    def test_global_hotkey_and_conflict_keeps_original(self):
        self.panel.hide()
        self.peer_call("focus")
        for key in ["Control_L", "Alt_L", "v"]:
            self.backend.xtst.XTestFakeKeyEvent(self.backend.display, self.backend.code(key), True, 0)
        for key in ["v", "Alt_L", "Control_L"]:
            self.backend.xtst.XTestFakeKeyEvent(self.backend.display, self.backend.code(key), False, 0)
        self.backend.lib.XFlush(self.backend.display)
        self.assertTrue(wait_until(self.panel.isVisible))
        blocker = X11()
        try:
            blocker.register("Ctrl+Alt+B")
            with self.assertRaises(ValueError):
                self.backend.register("Ctrl+Alt+B")
            self.assertEqual(self.backend.keycode, self.backend.code("v"))
        finally:
            blocker.close()

    def test_fresh_process_does_not_harvest_existing_clipboard(self):
        self.peer_call("copy", text="already on clipboard before launch")
        with tempfile.TemporaryDirectory() as directory:
            process = subprocess.Popen([sys.executable, "-m", "clipboard_app", "--hidden", "--data-dir", directory],
                                       env={**os.environ, "XDG_CONFIG_HOME": str(Path(directory) / "config")})
            try:
                path = Path(directory) / "instance.sock"
                self.assertTrue(wait_until(path.exists))
                QTest.qWait(200)
                startup = LinuxAutostart(Path(directory), Path(directory) / "config")
                self.assertTrue(startup.enabled())
                self.assertEqual(startup.path.read_text(), startup.document())
                with sqlite3.connect(Path(directory) / "history.sqlite3") as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM clips").fetchone()[0], 0)
            finally:
                process.terminate()
                process.wait(timeout=5)

    def test_pause_survives_a_new_monitor(self):
        self.monitor.pause(True)
        self.monitor.stop()
        self.monitor = Monitor(app.clipboard(), self.store)
        self.peer_call("copy", text="still paused")
        self.assertTrue(self.monitor.paused)
        self.assertEqual(self.store.list(), [])

    def test_image_capture_copy_and_image_filter(self):
        self.peer_call("copy", text="", image=True)
        clip = self.store.list()[0]
        self.assertEqual((clip.width, clip.height), (480, 300))
        self.assertTrue(clip.thumbnail)
        self.panel.filter("image")
        self.assertEqual(self.panel.history.count(), 1)
        self.assertFalse(self.panel.paste_mode.isEnabled())
        self.panel.copy_only()
        value = self.peer_call("clipboard")
        self.assertTrue(value["has_image"])
        self.assertEqual(value["dimensions"], [480, 300])
        self.panel.filter("text")
        self.assertEqual(self.panel.history.count(), 0)

    def test_html_original_and_plain_text_native_paste(self):
        self.peer_call("copy", text="中文 Bold", html="<p>中文 <b>Bold</b></p>")
        for mode in (0, 1):
            self.panel.hide()
            self.peer_call("focus")
            self.panel.open_panel()
            self.panel.paste_mode.setCurrentIndex(mode)
            self.assertTrue(wait_until(lambda: self.backend.belongs_to(self.backend.focus(), int(self.panel.winId()))))
            QTest.keyClick(self.panel.search, Qt.Key_Return)
            self.assertTrue(wait_until(lambda: not self.backend.paste_timer.isActive()))
            value = self.pasted_text("中文 Bold")
            self.assertEqual("font-weight:600" in value["html"], mode == 0)
            self.assertEqual(bool(self.peer_call("clipboard")["html"]), mode == 0)

    def test_pause_and_clear_cancel_pending_worker(self):
        from clipboard_app.content import prepare
        released = threading.Event()
        def slow_prepare(value, limit):
            released.wait(2)
            return prepare(value, limit)
        with patch("clipboard_app.monitor.prepare", side_effect=slow_prepare):
            try:
                self.peer_call("copy", text="queued", wait_processing=False)
                self.assertTrue(self.monitor.processing)
                self.monitor.pause(True)
                self.store.clear(True)
                self.monitor.pause(False)
            finally:
                released.set()
            self.assertTrue(wait_until(lambda: not self.monitor.processing))
        self.assertEqual(self.store.list(), [])
        self.peer_call("copy", text="new after resume")
        self.assertEqual(self.store.list()[0].text, "new after resume")

    def test_theme_follows_portal_signal_and_manual_override(self):
        self.panel.appearance.on_setting("org.freedesktop.appearance", "color-scheme", QDBusVariant(1))
        self.assertTrue(self.panel.history.property("dark"))
        self.assertLess(self.panel.palette().color(QPalette.Window).lightness(), 128)
        self.store.set_setting("theme", "light")
        self.panel._style()
        self.assertFalse(self.panel.history.property("dark"))
        self.assertGreater(self.panel.palette().color(QPalette.Window).lightness(), 128)
        self.store.set_setting("theme", "system")
        self.panel.appearance.on_setting("org.freedesktop.appearance", "color-scheme", QDBusVariant(2))
        self.assertFalse(self.panel.history.property("dark"))
        self.assertGreater(self.panel.palette().color(QPalette.Window).lightness(), 128)

    def test_settings_save_applies_cleanup(self):
        self.store.add("old")
        self.store.add("recent")
        def save():
            dialog = self.panel.findChild(QDialog)
            field = next(item for item in dialog.findChildren(QSpinBox) if item.accessibleName() == "普通历史条数")
            field.setValue(1)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
        QTimer.singleShot(50, save)
        self.panel.settings()
        self.assertEqual(self.store.limits.count, 1)
        self.assertEqual(len(self.store.summaries()), 1)


if __name__ == "__main__":
    unittest.main()
