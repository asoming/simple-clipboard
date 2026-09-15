"""Install/reinstall/uninstall preview on a disposable CI host, checking data retention."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from clipboard_app.paths import default_data_dir
from clipboard_app.store import Store


def main():
    if sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('This installer test requires a disposable Windows CI host')
    history = default_data_dir(frozen=True) / 'history.sqlite3'
    if history.exists():
        raise RuntimeError('Refusing to replace an existing history database')
    store = Store(history)
    identifier = store.add('synthetic installer retention')
    store.favorite(identifier, True)
    store.close()
    expected = hashlib.sha256(history.read_bytes()).digest()
    installer = next(Path('dist').glob('*-setup.exe')).resolve()
    with tempfile.TemporaryDirectory(prefix='clipboard install ') as directory:
        target = Path(directory) / 'Application'
        command = [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/DIR=' + str(target)]
        for _ in range(2):
            subprocess.run(command, check=True, timeout=120)
            assert hashlib.sha256(history.read_bytes()).digest() == expected
            report = Path(directory) / 'installed-smoke.json'
            subprocess.run([str(target / 'SimpleClipboard.exe'), '--smoke-test', str(report)], check=True, timeout=60)
            assert json.loads(report.read_text(encoding='utf-8'))['ok']
        subprocess.run([str(target / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], check=True, timeout=120)
        assert not (target / 'SimpleClipboard.exe').exists()
        assert hashlib.sha256(history.read_bytes()).digest() == expected
    Path('dist/windows-install.json').write_text(json.dumps({'install': True, 'reinstall': True, 'uninstall': True, 'history_unchanged': True}), encoding='utf-8')


if __name__ == '__main__':
    main()
