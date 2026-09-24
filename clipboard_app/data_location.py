"""Choose a data directory without overwriting or pruning clipboard history.

Stop clipboard writes before ``prepare_move``. The returned move owns the new
directory's instance lock until ``close``. After external startup settings have
been updated, ``commit`` saves the location preference. On failure, ``rollback``
restores that preference and removes the new copy; the original always remains.
Close any connection to the new database before rolling back on Windows.
"""

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from .instance import InstanceLock


DATABASE_NAME = "history.sqlite3"
DATABASE_SUFFIXES = ("", "-wal", "-shm", "-journal")


def location_config_path(platform=None, environment=None, home=None):
    """Return the small per-user preference file, separate from the history."""
    platform = platform or sys.platform
    environment = os.environ if environment is None else environment
    home = Path.home() if home is None else Path(home)
    if platform == "win32":
        directory = Path(environment.get("APPDATA", home / "AppData" / "Roaming")) / "SimpleClipboard"
    elif platform == "darwin":
        directory = home / "Library" / "Application Support" / "SimpleClipboard"
    else:
        directory = Path(environment.get("XDG_CONFIG_HOME", home / ".config")) / "simple-clipboard"
    return directory / "location.json"


def _read_preference(path):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError("保存文件夹配置链接已失效，请修复配置后重试。")
        return None, None
    if len(raw) > 65536:
        raise ValueError("保存文件夹配置无效，请修复配置后重试；已有历史未修改。")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1:
            raise ValueError
        name = value.get("data_dir")
        if not isinstance(name, str) or not name or "\0" in name:
            raise ValueError
        directory = Path(name)
        if not directory.is_absolute():
            raise ValueError
    except (UnicodeError, ValueError, TypeError) as error:
        raise ValueError("保存文件夹配置无效，请修复配置后重试；已有历史未修改。") from error
    return raw, directory


def selected_data_dir(config_path):
    """Read a saved directory; never replace an unavailable history with an empty one."""
    _, directory = _read_preference(Path(config_path))
    if directory is None:
        return None
    return validate_history_directory(directory)


def validate_history_directory(data_dir):
    """Guard existing-history launches against an absent disk, folder, or database."""
    directory = Path(data_dir)
    database = directory / DATABASE_NAME
    if not directory.is_dir() or not database.is_file() or database.stat().st_size == 0:
        raise ValueError("已设置的保存文件夹或历史数据库不可用。请连接原磁盘或恢复原文件夹后重试；不会创建空历史覆盖它。")
    return directory


def _write_preference(path, contents):
    """Publish a complete preference file or retain the old one on failure."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=".location-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _history_exists(directory):
    return any(os.path.lexists(directory / (DATABASE_NAME + suffix)) for suffix in DATABASE_SUFFIXES)


def _publish_database(temporary, destination):
    # rename fails if the destination exists on Windows. On Unix, link gives us
    # the same no-overwrite guarantee (replace/rename would silently overwrite).
    if sys.platform == "win32":
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)


class DataDirectoryMove:
    """A reversible prepared copy; ``close`` releases locks without deleting data."""

    def __init__(self, destination, lock, config_path, config_lock, previous):
        self.destination = destination
        self.lock = lock
        self.config_path = config_path
        self._config_lock = config_lock
        self._previous = previous
        self._published = False
        self._committed = False
        self._rolled_back = False
        self._closed = False
        self._preference = (json.dumps(
            {"version": 1, "data_dir": str(destination)}, ensure_ascii=False, indent=2,
        ) + "\n").encode("utf-8")

    def commit(self):
        if self._closed or self._rolled_back:
            raise ValueError("保存文件夹切换已结束，请重新选择。")
        if self._committed:
            return
        _write_preference(self.config_path, self._preference)
        self._committed = True

    def rollback(self):
        if self._rolled_back:
            return
        if self._closed:
            raise ValueError("切换锁已释放，无法安全回退；原历史仍然保留。")
        if self._committed:
            if self.config_path.read_bytes() != self._preference:
                raise ValueError("保存文件夹配置已被其他操作修改，未覆盖该配置；原历史仍然保留。")
            if self._previous is None:
                self.config_path.unlink()
            else:
                _write_preference(self.config_path, self._previous)
            self._committed = False
        if self._published:
            for suffix in DATABASE_SUFFIXES:
                (self.destination / (DATABASE_NAME + suffix)).unlink(missing_ok=True)
            self._published = False
        self._rolled_back = True

    def close(self):
        self.lock.release()
        self._config_lock.release()
        self._closed = True


def prepare_move(store, destination_dir, *, config_path=None):
    """Copy a live SQLite snapshot exactly, keeping source and target locks separate.

    The caller already holds the source instance lock and must stop writes until
    it commits or rolls back. No Store is opened here: doing so would prune old
    rows. A target containing any database or SQLite sidecar is never reused.
    """
    destination = Path(destination_dir).expanduser().resolve()
    if destination == store.path.parent.resolve():
        raise ValueError("这个文件夹已经是当前保存位置，无需切换。")
    if store.db.in_transaction:
        raise ValueError("历史仍有未完成的写入，请稍后重试。")
    if destination.exists() and not destination.is_dir():
        raise ValueError("请选择文件夹作为保存位置。")
    if _history_exists(destination):
        raise ValueError("所选文件夹已有历史数据库或事务文件，请选择新的空文件夹；不会覆盖已有历史。")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = InstanceLock(destination / "instance.lock")
    if not lock.acquire():
        raise ValueError("所选文件夹正在被另一个剪贴板进程使用，请先退出它或选择其他文件夹。")
    preference_path = Path(config_path) if config_path is not None else location_config_path()
    config_lock = InstanceLock(preference_path.with_suffix(".lock"))
    move = None
    temporary = None
    try:
        preference_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not config_lock.acquire():
            raise ValueError("另一个进程正在更改保存文件夹，请稍后重试。")
        previous, _ = _read_preference(preference_path)
        if _history_exists(destination):
            raise ValueError("所选文件夹已有历史数据库或事务文件，请选择新的空文件夹。")
        move = DataDirectoryMove(destination, lock, preference_path, config_lock, previous)
        descriptor, name = tempfile.mkstemp(prefix=".history-move-", suffix=".sqlite3", dir=destination)
        os.close(descriptor)
        temporary = Path(name)
        with closing(sqlite3.connect(temporary)) as copy:
            store.db.backup(copy)
            if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("历史副本完整性检查失败，未切换保存文件夹。")
            copy.execute("PRAGMA journal_mode = DELETE")
        with temporary.open("rb+") as file:
            os.fsync(file.fileno())
        # Recheck sidecars after the backup as well; never claim an existing WAL.
        if _history_exists(destination):
            raise ValueError("所选文件夹在复制期间出现了历史文件，未覆盖；请选择其他文件夹。")
        _publish_database(temporary, destination / DATABASE_NAME)
        move._published = True
        return move
    except Exception:
        try:
            if move is not None:
                move.rollback()
        finally:
            lock.release()
            config_lock.release()
        raise
    finally:
        if temporary is not None:
            for suffix in DATABASE_SUFFIXES:
                Path(str(temporary) + suffix).unlink(missing_ok=True)


def prepare_startup_move(store, destination, autostart, *, config_path=None):
    """Prepare and commit a move while keeping the existing login-startup choice.

    The caller pauses writes first, then closes the returned move during shutdown
    before launching the new process. Failed startup rollback retains both copies
    because the on-disk startup command may already point at the new directory.
    """
    was_enabled = autostart.enabled()
    previous_directory = autostart.data_dir
    move = prepare_move(store, destination, config_path=config_path)
    startup_attempted = False
    try:
        autostart.data_dir = move.destination
        if was_enabled:
            startup_attempted = True
            autostart.set_enabled(True)
        move.commit()
        return move
    except Exception:
        autostart.data_dir = previous_directory
        try:
            if startup_attempted:
                try:
                    autostart.set_enabled(True)
                except (OSError, ValueError) as restore_error:
                    raise ValueError(
                        "保存文件夹切换未完成，自启动项也未能恢复。原历史和新副本均已保留；"
                        "请先在设置中检查自启动，避免下次登录打开不同的历史副本。"
                    ) from restore_error
            try:
                move.rollback()
            except (OSError, ValueError) as rollback_error:
                raise ValueError(
                    "保存文件夹切换未完成，回退配置或清理副本失败。原历史仍然保留；"
                    "请检查保存文件夹配置和磁盘权限后重试。"
                ) from rollback_error
        finally:
            move.close()
        raise
