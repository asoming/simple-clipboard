"""Local history, lightweight list queries, and transactional schema upgrades."""

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Limits:
    days: int = 7
    count: int = 500
    total_bytes: int = 100 * 1024 * 1024
    item_bytes: int = 10 * 1024 * 1024

    def validate(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError("保存期限、条数和容量必须大于零。")
        if self.item_bytes > self.total_bytes:
            raise ValueError("单条上限不能大于总容量。")


@dataclass(frozen=True)
class Content:
    text: str = ""
    html: str = ""
    image: bytes = b""
    thumbnail: bytes = b""
    width: int = 0
    height: int = 0

    @property
    def kind(self) -> str:
        return "image" if self.image else "text"

    @property
    def size(self) -> int:
        return len(self.text.encode("utf-8")) + len(self.html.encode("utf-8")) + len(self.image) + len(self.thumbnail)

    @property
    def digest(self) -> str:
        if not self.html and not self.image:
            return hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        digest = hashlib.sha256()
        for name, value in ((b"text", self.text.encode("utf-8")), (b"html", self.html.encode("utf-8")), (b"image", self.image)):
            digest.update(name + len(value).to_bytes(8, "big") + value)
        return digest.hexdigest()


@dataclass(frozen=True)
class Clip:
    id: int
    text: str
    name: str
    pinned: bool
    copied_at: float
    size: int
    html: str = ""
    image: bytes = b""
    thumbnail: bytes = b""
    width: int = 0
    height: int = 0

    @property
    def content(self) -> Content:
        return Content(self.text, self.html, self.image, self.thumbnail, self.width, self.height)


@dataclass(frozen=True)
class Summary:
    id: int
    name: str
    pinned: bool
    copied_at: float
    size: int
    kind: str
    preview: str
    characters: int
    thumbnail: bytes
    width: int
    height: int
    rich: bool


class CapacityError(ValueError):
    """An item or requested budget cannot fit without discarding favorites."""


class Store:
    def __init__(self, path: Path, limits: Limits | None = None, clock=time.time):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.clock = clock
        self.db.execute("PRAGMA secure_delete = ON")
        try:
            self._initialize()
            saved = self.setting("limits")
            self.limits = limits or (Limits(**saved) if saved else Limits())
            self.prune()
        except Exception:
            self.db.close()
            raise

    def _initialize(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise ValueError("历史来自更新版本，请使用更新的应用打开。")
        if version == 2:
            return
        # An explicit transaction includes ALTER TABLE and all backfills. A failure
        # rolls everything back; no backup containing deleted history is retained.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if version == 0:
                self.db.execute("""CREATE TABLE clips (
                    id INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
                    text TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                    pinned INTEGER NOT NULL DEFAULT 0, copied_at REAL NOT NULL, size INTEGER NOT NULL
                )""")
                self.db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                self.db.execute("CREATE INDEX clips_recent ON clips(copied_at DESC)")
            additions = {
                "html": "TEXT NOT NULL DEFAULT ''", "image": "BLOB NOT NULL DEFAULT X''",
                "thumbnail": "BLOB NOT NULL DEFAULT X''", "width": "INTEGER NOT NULL DEFAULT 0",
                "height": "INTEGER NOT NULL DEFAULT 0", "kind": "TEXT NOT NULL DEFAULT 'text'",
                "preview": "TEXT NOT NULL DEFAULT ''", "characters": "INTEGER NOT NULL DEFAULT 0",
                "search_text": "TEXT NOT NULL DEFAULT ''", "rich": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, definition in additions.items():
                self.db.execute(f"ALTER TABLE clips ADD COLUMN {column} {definition}")
            for row in self.db.execute("SELECT id,text,name FROM clips").fetchall():
                self.db.execute("UPDATE clips SET preview=?,characters=?,search_text=? WHERE id=?", (
                    self._preview(row["text"]), len(row["text"]),
                    (row["text"] + "\n" + row["name"]).casefold(), row["id"],
                ))
            self.db.execute("CREATE INDEX clips_kind_recent ON clips(kind,copied_at DESC)")
            self.db.execute("PRAGMA user_version = 2")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    @staticmethod
    def _preview(text: str) -> str:
        return " ".join(text[:500].split())[:140]

    def add(self, text: str) -> int:
        return self.add_content(Content(text=text))

    def add_content(self, content: Content, copied_at: float | None = None) -> int:
        size = content.size
        if not size:
            raise ValueError("空内容不记录。")
        if size > self.limits.item_bytes or size > self.limits.total_bytes:
            raise CapacityError("这条内容超过保存上限，已跳过；可在设置中调整容量。")
        digest = content.digest
        timestamp = self.clock() if copied_at is None else copied_at
        with self.db:
            self._prune()
            row = self.db.execute("SELECT id FROM clips WHERE digest=?", (digest,)).fetchone()
            if row:
                self.db.execute("UPDATE clips SET copied_at=? WHERE id=?", (timestamp, row[0]))
                return row[0]
            if self.pinned_usage() + size > self.limits.total_bytes:
                raise CapacityError("收藏已占满可用空间。请删除部分收藏或增加总容量。")
            cursor = self.db.execute("""INSERT INTO clips (
                digest,text,copied_at,size,html,image,thumbnail,width,height,kind,preview,characters,search_text,rich
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                digest, content.text, timestamp, size, content.html, content.image, content.thumbnail,
                content.width, content.height, content.kind, self._preview(content.text),
                len(content.text), content.text.casefold(), bool(content.html),
            ))
            self._prune()
            return cursor.lastrowid

    def summaries(self, query: str = "", favorites: bool = False, kind: str = "all") -> list[Summary]:
        sql = "SELECT id,name,pinned,copied_at,size,kind,preview,characters,thumbnail,width,height,rich FROM clips"
        clauses, parameters = [], []
        if favorites:
            clauses.append("pinned=1")
        if kind in ("text", "image"):
            clauses.append("kind=?")
            parameters.append(kind)
        for term in query.casefold().split():
            clauses.append("instr(search_text, ?) > 0")
            parameters.append(term)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY copied_at DESC,id DESC"
        return [Summary(**dict(row)) for row in self.db.execute(sql, parameters)]

    def list(self, query: str = "", favorites: bool = False) -> list[Clip]:
        """Full payload API; the interface deliberately uses summaries() instead."""
        return [self.get(summary.id) for summary in self.summaries(query, favorites)]

    def get(self, clip_id: int) -> Clip | None:
        row = self.db.execute("""SELECT id,text,name,pinned,copied_at,size,html,image,thumbnail,width,height
                              FROM clips WHERE id=?""", (clip_id,)).fetchone()
        return Clip(**dict(row)) if row else None

    def favorite(self, clip_id: int, pinned: bool, name: str | None = None):
        with self.db:
            if name is None:
                self.db.execute("UPDATE clips SET pinned=? WHERE id=?", (pinned, clip_id))
            else:
                row = self.db.execute("SELECT text FROM clips WHERE id=?", (clip_id,)).fetchone()
                if row:
                    self.db.execute("UPDATE clips SET pinned=?,name=?,search_text=? WHERE id=?", (
                        pinned, name, (row[0] + "\n" + name).casefold(), clip_id,
                    ))
            self._prune()

    def delete(self, clip_id: int):
        with self.db:
            self.db.execute("DELETE FROM clips WHERE id=?", (clip_id,))

    def clear(self, include_favorites: bool = False):
        with self.db:
            self.db.execute("DELETE FROM clips" + ("" if include_favorites else " WHERE pinned=0"))
        self.db.execute("VACUUM")

    def _prune(self):
        self.db.execute("DELETE FROM clips WHERE pinned=0 AND copied_at < ?", (self.clock() - self.limits.days * 86400,))
        self.db.execute("""DELETE FROM clips WHERE id IN (
            SELECT id FROM clips WHERE pinned=0 ORDER BY copied_at DESC,id DESC LIMIT -1 OFFSET ?
        )""", (self.limits.count,))
        total = self.usage()
        if total > self.limits.total_bytes:
            for row in self.db.execute("SELECT id,size FROM clips WHERE pinned=0 ORDER BY copied_at,id").fetchall():
                if total <= self.limits.total_bytes:
                    break
                self.db.execute("DELETE FROM clips WHERE id=?", (row["id"],))
                total -= row["size"]

    def prune(self):
        with self.db:
            self._prune()

    def usage(self) -> int:
        return self.db.execute("SELECT COALESCE(SUM(size),0) FROM clips").fetchone()[0]

    def pinned_usage(self) -> int:
        return self.db.execute("SELECT COALESCE(SUM(size),0) FROM clips WHERE pinned=1").fetchone()[0]

    def disk_usage(self) -> int:
        return self.path.stat().st_size

    def set_limits(self, limits: Limits):
        limits.validate()
        if limits.total_bytes < self.pinned_usage():
            raise CapacityError("新容量小于已有收藏的占用。请先删除部分收藏，或设置更大的容量。")
        old = self.limits
        try:
            with self.db:
                self.limits = limits
                self._write_setting("limits", asdict(limits))
                self._prune()
        except Exception:
            self.limits = old
            raise

    def setting(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _write_setting(self, key: str, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def set_setting(self, key: str, value):
        with self.db:
            self._write_setting(key, value)

    def close(self):
        self.db.close()
