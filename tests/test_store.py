import tempfile
import sys
import unittest
from pathlib import Path

from clipboard_app.store import CapacityError, Limits, Store


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "history.sqlite3"
        self.now = 1_000_000
        self.store = Store(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_exact_text_and_case_insensitive_chinese_search(self):
        text = "  中文\n\tHello WORLD  \n"
        clip_id = self.store.add(text)
        self.assertEqual(self.store.list("中文 world")[0].text, text)
        self.assertEqual(self.store.get(clip_id).text, text)

    def test_duplicate_promoted_without_losing_favorite_name(self):
        first = self.store.add("A")
        self.store.favorite(first, True, "常用")
        self.now += 1
        self.store.add("B")
        self.now += 1
        self.assertEqual(self.store.add("A"), first)
        clips = self.store.list()
        self.assertEqual([clip.text for clip in clips], ["A", "B"])
        self.assertEqual(clips[0].name, "常用")
        self.assertTrue(clips[0].pinned)

    def test_search_name_and_literal_wildcards(self):
        clip_id = self.store.add("100% _ literal")
        self.store.favorite(clip_id, True, "收藏名称")
        self.assertEqual(len(self.store.list("名称")), 1)
        self.assertEqual(len(self.store.list("% _")), 1)
        self.assertEqual(len(self.store.list("missing")), 0)

    def test_expired_history_removed_but_favorites_survive(self):
        first = self.store.add("保留")
        self.store.favorite(first, True)
        self.store.add("过期")
        self.now += 31 * 86400
        self.store.prune()
        self.assertEqual([clip.text for clip in self.store.list()], ["保留"])

    def test_count_budget_does_not_count_favorites(self):
        self.store.limits = Limits(count=2)
        favorite = self.store.add("favorite")
        self.store.favorite(favorite, True)
        for text in ["one", "two", "three"]:
            self.now += 1
            self.store.add(text)
        self.assertEqual([clip.text for clip in self.store.list()], ["three", "two", "favorite"])

    def test_byte_budget_evicts_oldest_unpinned(self):
        self.store.limits = Limits(total_bytes=8)
        self.store.add("aaaa")
        self.now += 1
        self.store.add("bbbb")
        self.now += 1
        self.store.add("cccc")
        self.assertEqual([clip.text for clip in self.store.list()], ["cccc", "bbbb"])

    def test_pinned_capacity_rejection_is_atomic(self):
        self.store.limits = Limits(total_bytes=8)
        favorite = self.store.add("123456")
        self.store.favorite(favorite, True)
        self.store.add("ab")
        with self.assertRaises(CapacityError):
            self.store.add("long")
        self.assertEqual(len(self.store.list()), 2)
        self.assertEqual(self.store.usage(), 8)

    def test_size_limit_counts_utf8_bytes(self):
        self.store.limits = Limits(item_bytes=5)
        with self.assertRaises(CapacityError):
            self.store.add("中文")
        self.assertEqual(self.store.list(), [])

    def test_restart_preserves_history_settings_and_names(self):
        clip_id = self.store.add("persistent")
        self.store.favorite(clip_id, True, "name")
        self.store.set_setting("paused", True)
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertTrue(self.store.setting("paused"))
        self.assertEqual(self.store.list()[0].name, "name")

    def test_clear_defaults_to_preserving_favorites(self):
        clip_id = self.store.add("favorite")
        self.store.favorite(clip_id, True)
        self.store.add("ordinary")
        self.store.clear()
        self.assertEqual(len(self.store.list()), 1)
        self.store.clear(True)
        self.assertEqual(self.store.list(), [])

    def test_unpin_expired_item_applies_retention(self):
        clip_id = self.store.add("old")
        self.store.favorite(clip_id, True)
        self.now += 31 * 86400
        self.store.favorite(clip_id, False)
        self.assertIsNone(self.store.get(clip_id))

    def test_delete_and_database_permissions(self):
        clip_id = self.store.add("private")
        self.store.delete(clip_id)
        self.assertIsNone(self.store.get(clip_id))
        if sys.platform != 'win32':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
