"""Native OS integration. Only enabled on disposable GitHub-hosted desktops."""

import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import uuid
from pathlib import Path

from PyQt5.QtNetwork import QLocalSocket
from PyQt5.QtTest import QTest, QSignalSpy
from PyQt5.QtWidgets import QApplication

from clipboard_app.monitor import Monitor
from clipboard_app.paths import instance_socket
from clipboard_app.platforms import create_backend
from clipboard_app.store import Content, Store
from clipboard_app.ui import Panel

app = QApplication.instance() or QApplication([])


def wait_until(condition, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        QTest.qWait(15)
    return False


@unittest.skipUnless(sys.platform in ('win32', 'darwin') and os.environ.get('GITHUB_ACTIONS') == 'true'
                     and os.environ.get('CLIPBOARD_DISPOSABLE_DESKTOP') == '1', 'Requires disposable native CI desktop')
class NativeDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_exception_hook = sys.excepthook
        def gui_exception(kind, value, stack):
            traceback.print_exception(kind, value, stack, file=sys.stdout)
            sys.stdout.flush()
            os._exit(1)
        sys.excepthook = gui_exception
        cls.socket_name = 'clipboard-test-' + uuid.uuid4().hex
        if sys.platform == 'darwin':
            cls.socket_name = '/tmp/sc-test-' + uuid.uuid4().hex
        cls.peer = subprocess.Popen([sys.executable, 'tests/peer.py', cls.socket_name])
        cls.backend = create_backend()
        def ready():
            socket = QLocalSocket()
            socket.connectToServer(cls.socket_name)
            result = socket.waitForConnected(100)
            socket.close()
            return result
        if not wait_until(ready, 15):
            cls.backend.close()
            cls.peer.terminate()
            cls.peer.wait(timeout=10)
            raise AssertionError('Synthetic peer did not start')

    @classmethod
    def tearDownClass(cls):
        sys.excepthook = cls.previous_exception_hook
        cls.backend.close()
        cls.peer.terminate()
        cls.peer.wait(timeout=10)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'history.sqlite3')
        self.monitor = Monitor(app.clipboard(), self.store)
        self.panel = Panel(self.store, self.monitor, self.backend)

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
        self.temp.cleanup()

    def peer_call(self, action, **kwargs):
        socket = QLocalSocket()
        socket.connectToServer(self.socket_name)
        self.assertTrue(socket.waitForConnected(1000))
        socket.write(json.dumps(dict(action=action, **kwargs)).encode() + b'\n')
        socket.flush()
        self.assertTrue(wait_until(lambda: socket.bytesAvailable() > 0, 10))
        response = json.loads(bytes(socket.readAll()))
        socket.close()
        return response

    def test_background_clipboard_text_html_image_and_own_copy(self):
        self.peer_call('focus')
        self.peer_call('copy', text='后台 中文', html='<b>后台 中文</b>')
        self.assertTrue(wait_until(lambda: len(self.store.list()) == 1))
        self.assertEqual(self.store.list()[0].text, '后台 中文')
        self.assertIn('<b>', self.store.list()[0].html)
        self.monitor.copy('own copy')
        QTest.qWait(350)
        self.assertEqual(len(self.store.list()), 1)
        self.peer_call('copy', text='', image=True)
        self.assertTrue(wait_until(lambda: len(self.store.summaries(kind='image')) == 1))
        clip = self.store.get(self.store.summaries(kind='image')[0].id)
        self.monitor.copy(clip)
        self.assertTrue(self.peer_call('clipboard')['has_image'])

    def test_pause_and_ignore_keep_background_copies_out(self):
        self.monitor.pause(True)
        self.peer_call('copy', text='paused')
        QTest.qWait(350)
        self.monitor.pause(False)
        QTest.qWait(350)
        self.assertEqual(self.store.list(), [])
        self.monitor.toggle_ignore()
        self.peer_call('copy', text='ignored')
        self.assertTrue(wait_until(lambda: not self.monitor.ignore_next))
        self.assertEqual(self.store.list(), [])

    def test_second_launch_opens_the_existing_instance(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, '-m', 'clipboard_app', '--hidden', '--data-dir', directory]
            first = subprocess.Popen(command)
            try:
                def running():
                    socket = QLocalSocket()
                    socket.connectToServer(instance_socket(Path(directory)))
                    connected = socket.waitForConnected(100)
                    socket.close()
                    return connected
                self.assertTrue(wait_until(running, 15))
                second = subprocess.Popen(command)
                try:
                    self.assertTrue(wait_until(lambda: second.poll() is not None, 15))
                    self.assertEqual(second.returncode, 0)
                    self.assertIsNone(first.poll())
                finally:
                    if second.poll() is None:
                        second.terminate()
                        second.wait(timeout=10)
            finally:
                first.terminate()
                first.wait(timeout=10)

    def test_hotkey_conflict_preserves_previous_registration(self):
        other = create_backend()
        try:
            self.backend.register(self.backend.default_shortcut)
            other.register('Ctrl+Alt+J')
            with self.assertRaises(ValueError):
                self.backend.register('Ctrl+Alt+J')
            self.assertEqual(self.backend.shortcut, self.backend.default_shortcut)
        finally:
            other.close()

    def test_native_paste_delivers_text_and_never_submits(self):
        if self.backend.permission_message():
            self.skipTest('Accessibility not granted on this runner; actual injection remains a manual acceptance item')
        window = self.peer_call('focus', chat=True)['window']
        self.assertTrue(wait_until(lambda: self.backend.focus() == window))
        self.panel.open_panel()
        self.assertIsNotNone(self.panel.target)
        self.store.add('原生粘贴 中文')
        self.panel.refresh()
        self.panel.history.setCurrentRow(0)
        self.panel.paste()
        self.assertTrue(wait_until(lambda: self.peer_call('read')['chat'] == '原生粘贴 中文'))
        self.assertEqual(self.peer_call('read')['submissions'], [])
        self.assertTrue(wait_until(lambda: not self.backend.modifiers_pressed()))

    def test_denied_permission_keeps_copy_available(self):
        if sys.platform != 'darwin':
            self.skipTest('macOS Accessibility behavior')
        from unittest.mock import patch
        self.monitor.copy('manual paste')
        failed = QSignalSpy(self.backend.paste_failed)
        target = self.backend.capture_target()
        with patch.object(self.backend, 'permission_message', return_value='permission denied'):
            with patch.object(self.backend, 'send_paste') as send:
                self.backend.paste(target)
                self.assertEqual(len(failed), 1)
                send.assert_not_called()
        self.assertEqual(self.peer_call('clipboard')['text'], 'manual paste')

    def test_native_html_plain_text_and_image_reach_editor(self):
        if self.backend.permission_message():
            self.skipTest('Accessibility is not granted; native payload delivery needs manual verification')
        from clipboard_app.content import Snapshot, prepare
        from PyQt5.QtGui import QImage, QColor
        for plain in (False, True):
            window = self.peer_call('focus')['window']
            self.assertTrue(wait_until(lambda: self.backend.focus() == window))
            target = self.backend.capture_target()
            self.monitor.copy(Content(text='格式中文', html='<b>格式中文</b>'), plain=plain)
            self.backend.paste(target)
            failed = QSignalSpy(self.backend.paste_failed)
            self.assertTrue(wait_until(lambda: self.peer_call('read')['text'] == '格式中文'),
                            f'plain={plain}, modifiers={self.backend.modifiers_pressed()}, failed={list(failed)}')
            self.assertTrue(wait_until(lambda: not self.backend.modifiers_pressed()))
            self.assertEqual('font-weight:600' in self.peer_call('read')['html'], not plain)
        window = self.peer_call('focus')['window']
        self.assertTrue(wait_until(lambda: self.backend.focus() == window))
        target = self.backend.capture_target()
        image = QImage(40, 30, QImage.Format_ARGB32)
        image.fill(QColor('#47617e'))
        self.monitor.copy(prepare(Snapshot(image=image), 1048576))
        self.backend.paste(target)
        self.assertTrue(wait_until(lambda: '<img' in self.peer_call('read')['html']))

    def test_global_hotkey_event_arrives(self):
        if self.backend.permission_message():
            self.skipTest('Native key injection needs Accessibility on this runner')
        self.peer_call('focus')
        self.panel.hide()
        activated = QSignalSpy(self.backend.activated)
        self.backend.register(self.backend.default_shortcut)
        if sys.platform == 'win32':
            self.assertTrue(self.backend.send_keys([0x11, 0x12, ord('V')]))
        else:
            import Quartz
            for pressed in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 9, pressed)
                Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskShift if pressed else 0)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        self.assertTrue(wait_until(lambda: bool(activated)))
