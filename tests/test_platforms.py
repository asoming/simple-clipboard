"""Portable lifecycle and migration tests, always with synthetic temporary data."""

import os
import plistlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtTest import QSignalSpy
from PyQt5.QtWidgets import QApplication

from clipboard_app.instance import InstanceLock
from clipboard_app.migration import import_history
from clipboard_app.paths import default_data_dir, instance_socket
from clipboard_app.platforms import NativeBackend, Target, parse_shortcut
from clipboard_app.preferences import MacAutostart, WindowsAutostart
from clipboard_app.store import Store

app = QApplication.instance() or QApplication([])


class FakeBackend(NativeBackend):
    def __init__(self):
        super().__init__()
        self.ready = True
        self.modifiers = False
        self.permission = ''
        self.sent = 0

    def permission_message(self):
        return self.permission

    def activate(self, target):
        pass

    def target_ready(self, target):
        return self.ready

    def modifiers_pressed(self):
        return self.modifiers

    def send_paste(self, target):
        self.sent += 1
        return True


class PlatformTests(unittest.TestCase):
    def test_focus_or_held_modifier_never_injects(self):
        for ready, modifiers in [(False, False), (True, True)]:
            backend = FakeBackend()
            backend.ready, backend.modifiers = ready, modifiers
            failed = QSignalSpy(backend.paste_failed)
            backend.paste(Target(123))
            for _ in range(25):
                backend._finish_paste()
            self.assertEqual(backend.sent, 0)
            self.assertEqual(len(failed), 1)
            backend.close()

    def test_permission_revoked_while_returning_to_target(self):
        backend = FakeBackend()
        backend.paste(Target(123))
        backend.permission = 'denied'
        failed = QSignalSpy(backend.paste_failed)
        backend._finish_paste()
        self.assertEqual(backend.sent, 0)
        self.assertEqual(failed[0], ['denied'])
        backend.close()

    def test_verified_target_injects_once(self):
        backend = FakeBackend()
        backend.paste(Target(123))
        backend._finish_paste()
        backend._finish_paste()
        self.assertEqual(backend.sent, 1)
        self.assertFalse(backend.paste_timer.isActive())
        backend.close()

    def test_shortcut_rejects_normal_text_keys(self):
        aliases = {'Ctrl': 2, 'Alt': 1, 'Shift': 4}
        for value in ['v', 'Shift+V', 'Ctrl+中文', 'Ctrl++V', 'Cmd+V']:
            with self.assertRaises(ValueError):
                parse_shortcut(value, aliases)
        self.assertEqual(parse_shortcut('Ctrl+Alt+V', aliases), ('v', 3))

    def test_user_paths_and_existing_source_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kwargs = dict(environment={}, home=root, source_root=root, frozen=True)
            self.assertEqual(default_data_dir('win32', **kwargs), root / 'AppData/Local/SimpleClipboard')
            self.assertEqual(default_data_dir('darwin', **kwargs), root / 'Library/Application Support/SimpleClipboard')
            self.assertEqual(default_data_dir('linux', **kwargs), root / '.local/share/simple-clipboard')
            (root / 'data').mkdir()
            (root / 'data/history.sqlite3').touch()
            kwargs['frozen'] = False
            self.assertEqual(default_data_dir('linux', **kwargs), root / 'data')

    def test_windows_socket_is_bounded_and_directory_specific(self):
        with patch('clipboard_app.paths.sys.platform', 'win32'):
            one = instance_socket(Path('/synthetic/a'))
            self.assertLess(len(one), 80)
            self.assertNotEqual(one, instance_socket(Path('/synthetic/b')))

    def test_mac_login_item_quotes_by_argument_and_preserves_foreign_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            startup = MacAutostart(root / '中 文 " $ space', root)
            self.assertFalse(startup.enabled())
            startup.set_enabled(True)
            self.assertTrue(startup.enabled())
            value = plistlib.loads(startup.path.read_bytes())
            self.assertEqual(value['ProgramArguments'][-1], str(startup.data_dir))
            startup.set_enabled(False)
            self.assertFalse(startup.path.exists())
            startup.path.write_bytes(plistlib.dumps({'Label': 'somebody-else'}))
            with self.assertRaises(ValueError):
                startup.set_enabled(False)
            startup.path.write_bytes(b'broken plist')
            with self.assertRaises(ValueError):
                startup.set_enabled(True)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows registry')
    def test_windows_autostart_is_opt_in_and_can_be_removed(self):
        import winreg
        key = 'Software\\SimpleClipboardTests\\' + str(os.getpid())
        with tempfile.TemporaryDirectory() as directory:
            startup = WindowsAutostart(Path(directory) / '中 文', registry_key=key)
            try:
                self.assertFalse(startup.enabled())
                startup.set_enabled(True)
                self.assertTrue(startup.enabled())
                startup.set_enabled(False)
                self.assertFalse(startup.enabled())
            finally:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'old/history.sqlite3'
        self.destination = self.root / 'new/history.sqlite3'
        original = Store(self.source)
        identifier = original.add('收藏 中文')
        original.favorite(identifier, True, 'synthetic name')
        original.set_setting('paused', True)
        original.close()
        self.original = self.source.read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def test_import_preserves_favorites_settings_and_source(self):
        import_history(self.source, self.destination)
        store = Store(self.destination)
        try:
            self.assertEqual(store.list()[0].name, 'synthetic name')
            self.assertTrue(store.setting('paused'))
        finally:
            store.close()
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_nonempty_destination_and_running_source_are_rejected(self):
        existing = Store(self.destination)
        existing.add('new history')
        existing.close()
        previous = self.destination.read_bytes()
        with self.assertRaises(ValueError):
            import_history(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), previous)
        lock = InstanceLock(self.source.parent / 'instance.lock')
        self.assertTrue(lock.acquire())
        try:
            with self.assertRaises(ValueError):
                import_history(self.source, self.destination)
        finally:
            lock.release()

    def test_failed_validation_leaves_original_and_empty_destination(self):
        Store(self.destination).close()
        previous = self.destination.read_bytes()
        with patch('clipboard_app.migration.Store', side_effect=sqlite3.OperationalError('synthetic')):
            with self.assertRaises(sqlite3.OperationalError):
                import_history(self.source, self.destination)
        self.assertEqual(self.destination.read_bytes(), previous)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(list(self.destination.parent.iterdir()), [self.destination])

    def test_reset_removes_favorites_and_settings_on_restart(self):
        store = Store(self.source)
        store.reset()
        store.close()
        store = Store(self.source)
        try:
            self.assertEqual(store.list(), [])
            self.assertIsNone(store.setting('paused'))
        finally:
            store.close()
