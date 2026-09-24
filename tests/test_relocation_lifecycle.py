"""Exercise entry-point relocation in child Qt processes with disposable data."""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


CHILD = textwrap.dedent(r'''
    import json
    import sqlite3
    import sys
    from pathlib import Path
    from unittest.mock import patch

    from PyQt5.QtCore import QObject, QTimer, pyqtSignal
    from PyQt5.QtWidgets import QApplication, QDialog

    import clipboard_app.__main__ as entry
    import clipboard_app.data_location as location
    from clipboard_app.instance import InstanceLock
    from clipboard_app.settings_dialog import SettingsDialog
    from clipboard_app.store import Store
    from clipboard_app.ui import Panel

    # Match the app's canonical paths: macOS /var may be a symlink and
    # Windows temporary directories may use an 8.3 alias such as RUNNER~1.
    root, mode = Path(sys.argv[1]).resolve(), sys.argv[2]
    source, destination = root / 'source', root / 'destination'
    preference = root / 'config' / 'location.json'
    result = {'messages': [], 'restart': None, 'cancelled': 0, 'stopped': False}

    class Monitor(QObject):
        changed = pyqtSignal()
        state_changed = pyqtSignal()
        notice = pyqtSignal(str)
        paused = ignore_next = processing = False

        def __init__(self, unused_clipboard, store):
            super().__init__()

        def cancel_pending(self):
            result['cancelled'] += 1

        def stop(self):
            result['stopped'] = True

        def pause(self, value):
            self.paused = value

        def toggle_ignore(self):
            self.ignore_next = not self.ignore_next

    class Autostart:
        def __init__(self, directory):
            self.data_dir = directory

        def enabled(self):
            return False

        def set_enabled(self, enabled):
            assert not enabled, 'Test must not enable a real startup item'

    def start_detached(command, arguments):
        result['restart'] = [command, *arguments]
        assert result['stopped'], 'Monitoring must stop before restarting'
        for name in (source / 'instance.lock', destination / 'instance.lock', preference.with_suffix('.lock')):
            lock = InstanceLock(name)
            assert lock.acquire(), 'Migration locks must be released before restarting'
            lock.release()
        return mode != 'restart_failure', 123

    def drive(panel):
        try:
            dialog = SettingsDialog(panel)
            dialog.folder = destination
            dialog.save()
            result['accepted'] = dialog.result() == QDialog.Accepted
            result['error'] = dialog.error.text()
            assert result['accepted'], result['error']
        except Exception as error:
            result['driver_error'] = repr(error)
            QApplication.instance().quit()

    def make_panel(*args, **kwargs):
        panel = Panel(*args, **kwargs)
        QTimer.singleShot(0, lambda: drive(panel))
        QTimer.singleShot(4000, lambda: QApplication.instance().exit(7))
        return panel

    if mode != 'missing':
        source.mkdir()
        store = Store(source / 'history.sqlite3')
        identifier = store.add('relocation lifecycle synthetic 中文')
        store.favorite(identifier, True)
        store.close()
    sys.argv = ['clipboard-test', '--data-dir', str(source)]
    if mode == 'missing':
        sys.argv.append('--require-history')

    with patch.object(entry, 'prepare_input_method'), \
            patch.object(entry, 'create_backend', return_value=None), \
            patch.object(entry, 'Monitor', Monitor), \
            patch.object(entry, 'Panel', side_effect=make_panel), \
            patch.object(entry, 'initialize_startup'), \
            patch('clipboard_app.ui.Autostart', Autostart), \
            patch.object(location, 'location_config_path', return_value=preference), \
            patch.object(entry.QProcess, 'startDetached', side_effect=start_detached), \
            patch.object(entry.QMessageBox, 'critical', side_effect=lambda parent, title, message: result['messages'].append(message)):
        if mode == 'missing':
            with patch.object(entry, 'Store', side_effect=AssertionError('Missing history must not create a Store')):
                result['exit'] = entry.main()
        else:
            result['exit'] = entry.main()

    if mode != 'missing':
        result['location'] = json.loads(preference.read_text())['data_dir']
        result['histories'] = []
        for directory in (source, destination):
            with sqlite3.connect(directory / 'history.sqlite3') as database:
                result['histories'].append(database.execute('SELECT text, pinned FROM clips').fetchall())
    result['source_exists'] = source.exists()
    result['destination'] = str(destination)
    print('RESULT ' + json.dumps(result, ensure_ascii=False))
''')


class RelocationLifecycleTests(unittest.TestCase):
    def run_child(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                **os.environ,
                'QT_QPA_PLATFORM': 'offscreen',
                'PYTHONDONTWRITEBYTECODE': '1',
                'XDG_CONFIG_HOME': str(Path(directory) / 'config'),
            }
            completed = subprocess.run(
                [sys.executable, '-c', CHILD, directory, mode],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                capture_output=True, text=True, encoding='utf-8', timeout=15,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            line = next((line for line in completed.stdout.splitlines() if line.startswith('RESULT ')), None)
            self.assertIsNotNone(line, completed.stderr)
            result = json.loads(line.removeprefix('RESULT '))
            self.assertNotIn('driver_error', result, result.get('driver_error'))
            return result

    def assert_move_preserved_history(self, result):
        self.assertTrue(result['accepted'], result['error'])
        self.assertTrue(result['stopped'])
        self.assertGreaterEqual(result['cancelled'], 1)
        self.assertEqual(result['location'], result['destination'])
        self.assertEqual(result['histories'], [[['relocation lifecycle synthetic 中文', 1]]] * 2)
        self.assertEqual(result['restart'][-3:], ['--require-history', '--data-dir', result['destination']])

    def test_settings_move_closes_old_process_and_restarts_with_existing_history(self):
        result = self.run_child('success')
        self.assertEqual(result['exit'], 0)
        self.assert_move_preserved_history(result)
        self.assertEqual(result['messages'], [])

    def test_failed_restart_preserves_new_preference_and_both_history_copies(self):
        result = self.run_child('restart_failure')
        self.assertEqual(result['exit'], 1)
        self.assert_move_preserved_history(result)
        self.assertEqual(len(result['messages']), 1)
        self.assertIn('未能自动重启', result['messages'][0])

    def test_missing_required_history_is_rejected_before_creating_a_store(self):
        result = self.run_child('missing')
        self.assertEqual(result['exit'], 1)
        self.assertFalse(result['source_exists'])
        self.assertIsNone(result['restart'])
        self.assertEqual(len(result['messages']), 1)
        self.assertIn('不会创建空历史', result['messages'][0])
