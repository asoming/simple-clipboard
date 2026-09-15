"""Synthetic upgrade, payload, capacity and preference tests; no real history."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt5.QtCore import QMimeData
from PyQt5.QtGui import QColor, QImage
from PyQt5.QtWidgets import QApplication

from clipboard_app.content import Snapshot, as_mime, png_bytes, prepare, snapshot
from clipboard_app.preferences import Autostart
from clipboard_app.store import CapacityError, Content, Limits, Store

app = QApplication.instance() or QApplication([])
MIB = 1048576


class PhaseTwoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'history.sqlite3'
        self.store = Store(self.path, clock=lambda: 1_000_000)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def image_content(self):
        image = QImage(480, 300, QImage.Format_ARGB32)
        image.fill(QColor('#47617e'))
        image.setPixelColor(0, 0, QColor(12, 24, 36, 78))
        return prepare(Snapshot(image=png_bytes(image)), 10 * MIB)

    def test_image_roundtrip_thumbnail_filter_name_search_and_restart(self):
        content = self.image_content()
        clip_id = self.store.add_content(content)
        self.store.favorite(clip_id, True, '设计图')
        self.store.add('文字')
        summary = self.store.summaries('设计', kind='image')[0]
        self.assertEqual((summary.width, summary.height), (480, 300))
        self.assertLessEqual(QImage.fromData(summary.thumbnail).width(), 160)
        self.assertEqual(len(self.store.summaries(kind='text')), 1)
        self.store.close()
        self.store = Store(self.path, clock=lambda: 1_000_000)
        restored = self.store.get(clip_id)
        self.assertEqual(restored.content, content)
        self.assertTrue(restored.pinned)
        mime = as_mime(restored.content)
        self.assertEqual(mime.imageData().pixelColor(0, 0), QColor(12, 24, 36, 78))
        self.assertEqual(bytes(mime.data('image/png')), content.image)
        with self.assertRaises(ValueError):
            as_mime(restored.content, plain=True)

    def test_html_keeps_exact_source_plain_mode_strips_mime_only(self):
        content = Content(text='  中文\n', html='<b>  中文</b><br>')
        clip_id = self.store.add_content(content)
        self.assertEqual(self.store.get(clip_id).content, content)
        self.assertEqual(as_mime(content).html(), content.html)
        plain = as_mime(content, plain=True)
        self.assertFalse(plain.hasHtml())
        self.assertEqual(plain.text(), content.text)
        self.assertTrue(self.store.summaries()[0].rich)
        other = self.store.add_content(Content(text=content.text, html='<i>  中文</i><br>'))
        self.assertNotEqual(clip_id, other)
        self.assertEqual(self.store.add_content(content), clip_id)

    def test_irrelevant_image_metadata_does_not_duplicate_pixels(self):
        image = QImage(40, 30, QImage.Format_ARGB32)
        image.fill(QColor('#47617e'))
        first = prepare(Snapshot(image=image), MIB)
        image.setText('Author', 'Synthetic author')
        image.setDotsPerMeterX(7000)
        image.setDevicePixelRatio(2)
        second = prepare(Snapshot(image=image), MIB)
        self.assertEqual(first.digest, second.digest)
        self.assertEqual(QImage.fromData(second.image).textKeys(), [])

    def test_html_only_gets_searchable_text(self):
        content = prepare(Snapshot(html='<p>中文 <b>Hello</b></p>'), MIB)
        self.store.add_content(content)
        self.assertEqual(len(self.store.summaries('中文 hello')), 1)
        self.assertEqual(content.text, '中文 Hello')

    def test_large_image_resolution_and_decoded_size_limits(self):
        image = QImage(16001, 1, QImage.Format_RGB32)
        image.fill(0)
        with self.assertRaises(CapacityError):
            prepare(Snapshot(image=png_bytes(image)), 10 * MIB)
        with self.assertRaises(CapacityError):
            prepare(Snapshot(image=self.image_content().image), 10)
        with self.assertRaises(ValueError):
            prepare(Snapshot(image=b'not an image'), MIB)

    def test_sensitive_html_and_image_formats_are_skipped(self):
        for secret in ('x-kde-passwordManagerHint', 'org.nspasteboard.ConcealedType', 'application/x-keepassxc-secret'):
            mime = QMimeData()
            mime.setData('image/png', self.image_content().image)
            mime.setHtml('<p>synthetic private</p>')
            mime.setData(secret, b'1')
            self.assertIsNone(snapshot(mime, MIB))

    def test_saved_limits_prune_mixed_items_and_keep_favorites(self):
        favorite = self.store.add_content(self.image_content())
        self.store.favorite(favorite, True)
        for index in range(4):
            self.store.add_content(Content(text=str(index)), copied_at=1_000_000 + index)
        self.store.set_limits(Limits(count=2))
        self.assertEqual(len(self.store.summaries()), 3)
        self.assertIsNotNone(self.store.get(favorite))
        self.store.close()
        self.store = Store(self.path, clock=lambda: 1_000_000)
        self.assertEqual(self.store.limits.count, 2)
        self.assertEqual(len(self.store.summaries()), 3)

    def test_limit_rejection_and_sql_failure_do_not_mutate_history(self):
        favorite = self.store.add('x' * (2 * MIB))
        self.store.favorite(favorite, True)
        self.store.add('ordinary')
        with self.assertRaises(CapacityError):
            self.store.set_limits(Limits(count=1, total_bytes=MIB, item_bytes=MIB))
        old_limits = self.store.limits
        with patch.object(self.store, '_prune', side_effect=sqlite3.OperationalError('synthetic failure')):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.set_limits(Limits(count=1))
        self.assertEqual(self.store.limits, old_limits)
        self.assertIsNone(self.store.setting('limits'))
        self.assertEqual(len(self.store.summaries()), 2)

    def test_mixed_capacity_counts_html_original_and_thumbnail(self):
        content = self.image_content()
        self.store.limits = Limits(total_bytes=content.size + 10, item_bytes=MIB)
        self.store.add_content(content)
        self.store.add_content(Content(text='123456', html='<b>123456</b>'), copied_at=1_000_001)
        self.assertEqual(len(self.store.summaries()), 1)
        self.assertEqual(self.store.usage(), 19)

    def create_legacy(self, broken=False):
        self.store.close()
        self.path.unlink()
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE clips (id INTEGER PRIMARY KEY,digest TEXT NOT NULL UNIQUE,text TEXT NOT NULL,
                    name TEXT NOT NULL DEFAULT '',pinned INTEGER NOT NULL DEFAULT 0,copied_at REAL NOT NULL,size INTEGER NOT NULL);
                CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE INDEX clips_recent ON clips(copied_at DESC);
                PRAGMA user_version=1;
            """)
            value = '  原始中文\n\tHello '
            db.execute('INSERT INTO clips VALUES(42,?,?,?,?,?,?)', (
                hashlib.sha256(value.encode()).hexdigest(), value, '旧收藏', 1, 1, len(value.encode())))
            db.execute('INSERT INTO settings VALUES(?,?)', ('paused', json.dumps(True)))
            if broken:
                db.execute('ALTER TABLE clips ADD COLUMN width INTEGER')

    def test_v1_migration_preserves_text_identity_favorites_and_settings(self):
        self.create_legacy()
        self.store = Store(self.path, clock=lambda: 1_000_000)
        clip = self.store.get(42)
        self.assertEqual(clip.text, '  原始中文\n\tHello ')
        self.assertEqual(clip.name, '旧收藏')
        self.assertTrue(clip.pinned)
        self.assertTrue(self.store.setting('paused'))
        self.assertEqual(self.store.summaries('旧收藏')[0].id, 42)
        self.assertEqual(self.store.add(clip.text), 42)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 2)

    def test_partial_schema_migration_rolls_back(self):
        self.create_legacy(broken=True)
        with self.assertRaises(sqlite3.OperationalError):
            Store(self.path)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            columns = [row[1] for row in db.execute('PRAGMA table_info(clips)')]
            self.assertNotIn('html', columns)
            self.assertNotIn('image', columns)
            self.assertEqual(db.execute('SELECT name FROM clips WHERE id=42').fetchone()[0], '旧收藏')
            db.execute('ALTER TABLE clips DROP COLUMN width')
        self.store = Store(self.path)

    def test_autostart_is_opt_in_quotes_path_and_preserves_unrelated_file(self):
        from gi.repository import Gio
        startup = Autostart(Path(self.temp.name) / '空 格 "quote" $ `tick` % folder', Path(self.temp.name))
        self.assertFalse(startup.enabled())
        startup.set_enabled(True)
        self.assertTrue(startup.enabled())
        desktop = Gio.DesktopAppInfo.new_from_filename(str(startup.path))
        self.assertIsNotNone(desktop)
        from gi.repository import GLib
        valid, argv = GLib.shell_parse_argv(desktop.get_commandline())
        self.assertTrue(valid)
        # Percent field codes are expanded by desktop launchers, not shell_parse_argv.
        self.assertEqual(argv[-1].replace('%%', '%'), str(startup.data_dir))
        self.assertEqual(argv[1:3], ['--hidden', '--data-dir'])
        startup.set_enabled(False)
        self.assertFalse(startup.path.exists())
        startup.path.write_text('[Desktop Entry]\nName=Other\n')
        with self.assertRaises(ValueError):
            startup.set_enabled(True)
        with self.assertRaises(ValueError):
            startup.set_enabled(False)
        self.assertIn('Name=Other', startup.path.read_text())


if __name__ == '__main__':
    unittest.main()
