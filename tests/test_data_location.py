"""Directory moves use only temporary, synthetic history and preferences."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from clipboard_app.data_location import (
    DATABASE_SUFFIXES, location_config_path, prepare_move, prepare_startup_move,
    validate_history_directory,
)
from clipboard_app.instance import InstanceLock
from clipboard_app.paths import default_data_dir, instance_socket
from clipboard_app.store import Content, Store


class DataLocationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = self.root / "config" / "location.json"
        self.destination = self.root / "新位置 with spaces"
        self.store = Store(self.root / "source" / "history.sqlite3", clock=lambda: 100000)
        self.store.add("普通合成记录")
        favorite = self.store.add_content(Content(
            text="合成图片", html="<b>合成图片</b>", image=b"original encoded bytes",
            thumbnail=b"small preview", width=32, height=24,
        ))
        self.store.favorite(favorite, True, "合成收藏")
        self.store.set_setting("theme", "dark")
        self.store.set_setting("limits", {"days": 30, "count": 0, "total_bytes": 524288000, "item_bytes": 0})
        # If migration accidentally opens a Store, this normal record will expire.
        self.store.clock = lambda: 100000 + 40 * 86400
        self.moves = []

    def tearDown(self):
        for move in self.moves:
            move.close()
        self.store.close()
        self.temporary.cleanup()

    def prepare(self, destination=None):
        move = prepare_move(self.store, destination or self.destination, config_path=self.config)
        self.moves.append(move)
        return move

    def write_preference(self, path=None):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps({"version": 1, "data_dir": str(path or self.store.path.parent)}), encoding="utf-8")

    def default_directory(self):
        return default_data_dir(
            environment={}, home=self.root, source_root=self.root, frozen=False,
            config_path=self.config,
        )

    def test_platform_config_paths_are_separate_from_history_and_test_home(self):
        for platform, expected in (
            ("win32", "AppData/Roaming/SimpleClipboard/location.json"),
            ("darwin", "Library/Application Support/SimpleClipboard/location.json"),
            ("linux", ".config/simple-clipboard/location.json"),
        ):
            self.assertEqual(location_config_path(platform, {}, self.root), self.root / expected)
        self.assertEqual(
            location_config_path("linux", {"XDG_CONFIG_HOME": str(self.root / "xdg")}, self.root),
            self.root / "xdg/simple-clipboard/location.json",
        )

    def test_managed_startup_commands_require_existing_history_on_every_platform(self):
        from clipboard_app.preferences import LinuxAutostart, MacAutostart, WindowsAutostart

        directory = self.store.path.parent
        with patch("clipboard_app.preferences.launch_arguments", return_value=["synthetic-app"]):
            linux = LinuxAutostart(directory, self.root / "linux-config")
            self.assertIn('"--hidden" "--require-history" "--data-dir"', linux.document())
            mac = MacAutostart(directory, self.root / "mac-config")
            self.assertEqual(mac.document()["ProgramArguments"][-4:], [
                "--hidden", "--require-history", "--data-dir", str(directory),
            ])
            # document() is pure; avoid opening the real Windows registry.
            windows = WindowsAutostart.__new__(WindowsAutostart)
            windows.data_dir = directory
            self.assertIn("--hidden --require-history --data-dir", windows.document())

    def test_existing_history_guard_rejects_missing_and_empty_files_without_creating_them(self):
        self.assertEqual(validate_history_directory(self.store.path.parent), self.store.path.parent)
        with self.assertRaisesRegex(ValueError, "不可用"):
            validate_history_directory(self.destination)
        self.assertFalse(self.destination.exists())
        self.destination.mkdir()
        with self.assertRaisesRegex(ValueError, "不可用"):
            validate_history_directory(self.destination)
        database = self.destination / "history.sqlite3"
        self.assertFalse(database.exists())
        database.touch()
        with self.assertRaisesRegex(ValueError, "不可用"):
            validate_history_directory(self.destination)
        self.assertEqual(database.stat().st_size, 0)

    def test_copy_preserves_all_rows_settings_and_original_without_pruning(self):
        before = list(self.store.db.iterdump())
        original = self.store.path.read_bytes()
        move = self.prepare()
        target = move.destination / "history.sqlite3"
        with closing(sqlite3.connect(target)) as copy:
            self.assertEqual(list(copy.iterdump()), before)
            self.assertEqual(copy.execute("PRAGMA user_version").fetchone(), (3,))
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone(), ("ok",))
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(list(self.store.db.iterdump()), before)
        self.assertFalse(self.config.exists())
        self.assertFalse(InstanceLock(move.destination / "instance.lock").acquire())
        move.commit()
        self.assertEqual(self.default_directory(), self.destination)
        move.close()
        restart_lock = InstanceLock(move.destination / "instance.lock")
        try:
            self.assertTrue(restart_lock.acquire())
        finally:
            restart_lock.release()
        self.assertTrue(self.store.path.exists())
        self.assertTrue(target.exists())

    def test_wal_snapshot_copies_committed_records_without_copying_sidecars(self):
        self.store.db.execute("PRAGMA journal_mode=WAL")
        self.store.set_setting("wal_only", {"value": "仅在提交的 WAL 中"})
        self.assertTrue(Path(str(self.store.path) + "-wal").exists())
        before = list(self.store.db.iterdump())
        move = self.prepare()
        with closing(sqlite3.connect(move.destination / "history.sqlite3")) as copy:
            self.assertEqual(list(copy.iterdump()), before)
        for suffix in DATABASE_SUFFIXES[1:]:
            self.assertFalse((move.destination / ("history.sqlite3" + suffix)).exists())

    def test_preference_overrides_legacy_directory(self):
        legacy = self.root / "data"
        legacy.mkdir()
        (legacy / "history.sqlite3").touch()
        self.assertEqual(self.default_directory(), legacy)
        self.write_preference()
        self.assertEqual(self.default_directory(), self.store.path.parent)

    def test_missing_saved_folder_or_database_never_falls_back_to_empty_history(self):
        self.write_preference(self.destination)
        with self.assertRaisesRegex(ValueError, "不可用"):
            self.default_directory()
        self.destination.mkdir()
        with self.assertRaisesRegex(ValueError, "不可用"):
            self.default_directory()
        self.assertFalse((self.destination / "history.sqlite3").exists())
        (self.destination / "history.sqlite3").touch()
        with self.assertRaisesRegex(ValueError, "不可用"):
            self.default_directory()
        self.assertEqual((self.destination / "history.sqlite3").stat().st_size, 0)

    def test_malformed_preference_is_not_silently_ignored_or_overwritten(self):
        self.config.parent.mkdir()
        for raw in (b"not json", b"[]", b'{"version":1,"data_dir":"relative"}', b'{"version":2}', b"\xff"):
            with self.subTest(raw=raw):
                self.config.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, "配置无效"):
                    self.default_directory()
                with self.assertRaisesRegex(ValueError, "配置无效"):
                    self.prepare()
                self.assertEqual(self.config.read_bytes(), raw)
                self.assertFalse((self.destination / "history.sqlite3").exists())

    def test_same_directory_and_all_existing_sqlite_files_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "当前保存位置"):
            self.prepare(self.store.path.parent)
        self.destination.mkdir()
        for suffix in DATABASE_SUFFIXES:
            existing = self.destination / ("history.sqlite3" + suffix)
            existing.write_bytes(b"do not overwrite synthetic fixture")
            with self.subTest(suffix=suffix), self.assertRaisesRegex(ValueError, "已有历史"):
                self.prepare()
            self.assertEqual(existing.read_bytes(), b"do not overwrite synthetic fixture")
            existing.unlink()

    def test_locked_destination_is_rejected(self):
        self.destination.mkdir()
        lock = InstanceLock(self.destination / "instance.lock")
        self.assertTrue(lock.acquire())
        try:
            with self.assertRaisesRegex(ValueError, "另一个剪贴板进程"):
                self.prepare()
            self.assertFalse((self.destination / "history.sqlite3").exists())
        finally:
            lock.release()

    def test_concurrent_location_changes_are_serialized(self):
        first = self.prepare()
        with self.assertRaisesRegex(ValueError, "另一个进程正在更改"):
            self.prepare(self.root / "different destination")
        first.rollback()
        first.close()
        retry = self.prepare(self.root / "different destination")
        retry.rollback()

    def test_rollback_restores_previous_preference_and_removes_only_new_copy(self):
        for previous_exists in (False, True):
            with self.subTest(previous_exists=previous_exists):
                if previous_exists:
                    self.write_preference()
                previous = self.config.read_bytes() if self.config.exists() else None
                move = self.prepare()
                (move.destination / "unrelated.txt").write_text("keep")
                move.commit()
                move.rollback()
                move.rollback()
                move.close()
                self.assertEqual(self.config.read_bytes() if self.config.exists() else None, previous)
                self.assertFalse((move.destination / "history.sqlite3").exists())
                self.assertEqual((move.destination / "unrelated.txt").read_text(), "keep")
                self.assertEqual(len(self.store.summaries()), 2)

    def test_preference_write_failure_keeps_original_configuration_and_history(self):
        self.write_preference()
        previous = self.config.read_bytes()
        move = self.prepare()
        with patch("clipboard_app.data_location._write_preference", side_effect=OSError("synthetic full disk")):
            with self.assertRaises(OSError):
                move.commit()
        move.rollback()
        self.assertEqual(self.config.read_bytes(), previous)
        self.assertFalse((self.destination / "history.sqlite3").exists())
        self.assertEqual(len(self.store.summaries()), 2)

    def test_publish_failure_leaves_original_intact_and_releases_locks(self):
        self.write_preference()
        previous = self.config.read_bytes()
        with patch("clipboard_app.data_location._publish_database", side_effect=OSError("synthetic disk error")):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(self.config.read_bytes(), previous)
        self.assertFalse((self.destination / "history.sqlite3").exists())
        self.assertFalse(list(self.destination.glob(".history-move-*")))
        move = self.prepare()
        move.rollback()

    def test_copy_failure_removes_temporary_files_and_leaves_original_usable(self):
        original = list(self.store.db.iterdump())
        with patch("clipboard_app.data_location.sqlite3.connect", side_effect=sqlite3.OperationalError("synthetic disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.prepare()
        self.assertEqual(list(self.store.db.iterdump()), original)
        self.assertFalse((self.destination / "history.sqlite3").exists())
        self.assertFalse(list(self.destination.glob(".history-move-*")))
        move = self.prepare()
        move.rollback()

    def test_publish_never_overwrites_file_created_during_copy(self):
        from clipboard_app.data_location import _publish_database

        def concurrent_creation(temporary, destination):
            destination.write_bytes(b"another synthetic database")
            _publish_database(temporary, destination)

        with patch("clipboard_app.data_location._publish_database", side_effect=concurrent_creation):
            with self.assertRaises(FileExistsError):
                self.prepare()
        self.assertEqual((self.destination / "history.sqlite3").read_bytes(), b"another synthetic database")
        self.assertEqual(len(self.store.summaries()), 2)

    def test_uncommitted_source_transaction_is_rejected_without_hanging(self):
        self.store.db.execute("BEGIN")
        try:
            with self.assertRaisesRegex(ValueError, "未完成的写入"):
                self.prepare()
        finally:
            self.store.db.rollback()
        self.assertFalse(self.destination.exists())

    def startup_manager(self, enabled):
        manager = Mock()
        manager.data_dir = self.store.path.parent
        manager.enabled.return_value = enabled
        return manager

    def test_enabled_startup_follows_committed_directory(self):
        manager = self.startup_manager(True)
        updated_paths = []
        manager.set_enabled.side_effect = lambda _: updated_paths.append(manager.data_dir)
        move = prepare_startup_move(self.store, self.destination, manager, config_path=self.config)
        self.moves.append(move)
        self.assertEqual(updated_paths, [self.destination])
        self.assertEqual(manager.data_dir, self.destination)
        self.assertEqual(self.default_directory(), self.destination)
        self.assertFalse(InstanceLock(self.destination / "instance.lock").acquire())

    def test_disabled_startup_is_not_written_or_reenabled(self):
        manager = self.startup_manager(False)
        move = prepare_startup_move(self.store, self.destination, manager, config_path=self.config)
        self.moves.append(move)
        manager.set_enabled.assert_not_called()
        self.assertEqual(manager.data_dir, self.destination)
        self.assertEqual(self.default_directory(), self.destination)

    def test_startup_update_failure_restores_old_directory_and_removes_copy(self):
        manager = self.startup_manager(True)
        updated_paths = []

        def update_startup(enabled):
            updated_paths.append(manager.data_dir)
            if len(updated_paths) == 1:
                raise OSError("synthetic startup write failure")

        manager.set_enabled.side_effect = update_startup
        with self.assertRaises(OSError):
            prepare_startup_move(self.store, self.destination, manager, config_path=self.config)
        self.assertEqual(updated_paths, [self.destination, self.store.path.parent])
        self.assertEqual(manager.data_dir, self.store.path.parent)
        self.assertFalse(self.config.exists())
        self.assertFalse((self.destination / "history.sqlite3").exists())
        self.assertEqual(len(self.store.summaries()), 2)
        retry = self.prepare()
        retry.rollback()

    def test_config_commit_failure_restores_startup_without_changing_enabled_choice(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                manager = self.startup_manager(enabled)
                self.write_preference()
                previous = self.config.read_bytes()
                with patch("clipboard_app.data_location._write_preference", side_effect=OSError("synthetic config failure")):
                    with self.assertRaises(OSError):
                        prepare_startup_move(self.store, self.destination, manager, config_path=self.config)
                self.assertEqual(manager.data_dir, self.store.path.parent)
                self.assertEqual(manager.set_enabled.call_count, 2 if enabled else 0)
                self.assertEqual(self.config.read_bytes(), previous)
                self.assertFalse((self.destination / "history.sqlite3").exists())

    def test_failed_startup_restore_retains_copy_that_startup_may_reference(self):
        manager = self.startup_manager(True)
        manager.set_enabled.side_effect = OSError("synthetic startup write/rollback failure")
        original = list(self.store.db.iterdump())
        with self.assertRaisesRegex(ValueError, "原历史和新副本均已保留"):
            prepare_startup_move(self.store, self.destination, manager, config_path=self.config)
        self.assertFalse(self.config.exists())
        self.assertEqual(list(self.store.db.iterdump()), original)
        with closing(sqlite3.connect(self.destination / "history.sqlite3")) as copy:
            self.assertEqual(list(copy.iterdump()), original)
        lock = InstanceLock(self.destination / "instance.lock")
        try:
            self.assertTrue(lock.acquire())
        finally:
            lock.release()

    @unittest.skipIf(sys.platform == "win32", "Linux socket hashing uses Unix uid")
    def test_linux_socket_preserves_short_paths_and_bounds_long_unicode_paths(self):
        with patch("clipboard_app.paths.sys.platform", "linux"):
            short = Path("/synthetic/short")
            self.assertEqual(instance_socket(short), str(short / "instance.sock"))
            long = Path("/synthetic/" + "中文" * 24)
            self.assertGreater(len(os.fsencode(str(long / "instance.sock"))), 107)
            name = instance_socket(long)
            self.assertLess(len(os.fsencode(name)), 108)
            self.assertTrue(name.startswith(f"/tmp/sc-{os.getuid()}-"))
            self.assertNotEqual(name, instance_socket(long / "other"))
            self.assertEqual(name, instance_socket(long / ".." / long.name))


if __name__ == "__main__":
    unittest.main()
