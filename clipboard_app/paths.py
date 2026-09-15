"""Writable user data and launch commands independent of installation location."""

import hashlib
import os
import sys
from pathlib import Path


def default_data_dir(platform=None, environment=None, home=None, source_root=None, frozen=None):
    platform = platform or sys.platform
    environment = os.environ if environment is None else environment
    home = Path.home() if home is None else Path(home)
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    source_root = Path(__file__).resolve().parents[1] if source_root is None else Path(source_root)
    legacy = source_root / 'data'
    # Existing portable users keep their data in place. New installations use a
    # per-user directory, never Program Files, /usr/share, or an .app bundle.
    if not frozen and (legacy / 'history.sqlite3').is_file():
        return legacy
    if platform == 'win32':
        return Path(environment.get('LOCALAPPDATA', home / 'AppData' / 'Local')) / 'SimpleClipboard'
    if platform == 'darwin':
        return home / 'Library' / 'Application Support' / 'SimpleClipboard'
    return Path(environment.get('XDG_DATA_HOME', home / '.local' / 'share')) / 'simple-clipboard'


def launch_arguments():
    if getattr(sys, 'frozen', False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / 'run_app.py')]


def instance_socket(data_dir):
    if sys.platform in ('win32', 'darwin'):
        normalized = os.path.normcase(str(data_dir.resolve()))
        identifier = hashlib.sha256(normalized.encode()).hexdigest()[:24]
        if sys.platform == 'darwin':
            # Darwin's Unix socket paths are limited to 104 bytes. QLocalServer
            # applies UserAccessOption; never derive a socket from a long home path.
            return f'/tmp/sc-{os.getuid()}-{identifier}.sock'
        return 'simple-clipboard-' + identifier
    return str(data_dir / 'instance.sock')
