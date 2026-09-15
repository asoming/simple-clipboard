"""Packaged startup check. No clipboard access, monitor, permissions or startup edits."""

import json
import platform
import sys
import tempfile
from pathlib import Path

from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR, qVersion

from . import __version__
from .instance import InstanceLock
from .platforms import create_backend
from .store import Store
from .ui import SearchEdit


def run(app, report):
    backend = None
    result = {'version': __version__, 'system': platform.system(), 'machine': platform.machine(),
              'python': platform.python_version(), 'qt': qVersion(), 'qt_build': QT_VERSION_STR,
              'pyqt': PYQT_VERSION_STR,
              'frozen': bool(getattr(sys, 'frozen', False))}
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            lock = InstanceLock(path / 'instance.lock')
            assert lock.acquire()
            lock.release()
            store = Store(path / 'history.sqlite3')
            try:
                identifier = store.add('打包验证 synthetic')
                assert store.get(identifier).text == '打包验证 synthetic'
            finally:
                store.close()
            search = SearchEdit()
            search.setText('中文')
            assert search.text() == '中文'
            backend = create_backend()
            result['backend'] = type(backend).__name__
            result['automatic_paste_permission'] = not bool(backend.permission_message())
            result['ok'] = True
    except Exception as error:
        result.update(ok=False, error_type=type(error).__name__)
    finally:
        if backend:
            backend.close()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['ok'] else 1
