"""Explicit offline import: validate a copy before replacing an empty destination."""

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .instance import InstanceLock
from .store import Store


def import_history(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    if source == destination:
        raise ValueError('源文件就是当前历史，无需导入。')
    # The caller holds the destination instance lock. Lock the old application's
    # directory as well, so a running supported version cannot change the source.
    source_lock = InstanceLock(source.parent / 'instance.lock')
    if not source_lock.acquire():
        raise ValueError('旧版仍在运行，请先退出后导入。')
    temporary = None
    try:
        if destination.exists():
            with closing(sqlite3.connect(destination.as_uri() + '?mode=ro', uri=True)) as existing:
                if existing.execute('SELECT 1 FROM clips LIMIT 1').fetchone():
                    raise ValueError('当前历史不为空，未覆盖。请改用新的空数据目录。')
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix='.sqlite3', delete=False) as file:
            temporary = Path(file.name)
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(temporary)) as copy:
                original.backup(copy)
                if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('旧历史完整性检查失败，未导入。')
        # Opening upgrades a supported schema and applies the saved retention
        # policy to the copy. The original is never upgraded or pruned.
        checked = Store(temporary)
        try:
            checked.summaries()
        finally:
            checked.close()
        temporary.replace(destination)
    finally:
        source_lock.release()
        if temporary:
            for suffix in ('', '-journal', '-wal', '-shm'):
                Path(str(temporary) + suffix).unlink(missing_ok=True)
