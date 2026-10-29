"""SQLite persistence and notification state."""

import sqlite3
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import NewsItem


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout = 5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                source TEXT NOT NULL,
                organization TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'company_news',
                dedupe_key TEXT,
                canonical_url TEXT NOT NULL UNIQUE,
                published_at TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('baseline', 'pending', 'notified')),
                notified_at TEXT
            );
            CREATE TABLE IF NOT EXISTS initialized_feeds (url TEXT PRIMARY KEY);
            CREATE INDEX IF NOT EXISTS articles_pending ON articles(status, published_at);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(articles)")}
        for name, definition in (("organization", "TEXT NOT NULL DEFAULT ''"), ("category", "TEXT NOT NULL DEFAULT 'company_news'"), ("dedupe_key", "TEXT")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE articles ADD COLUMN {name} {definition}")
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS articles_dedupe_key ON articles(dedupe_key) WHERE dedupe_key IS NOT NULL")

    def close(self) -> None:
        self.db.close()

    def is_feed_initialized(self, url: str) -> bool:
        return self.db.execute("SELECT 1 FROM initialized_feeds WHERE url = ?", (url,)).fetchone() is not None

    def save_feed(self, feed_url: str, items: list[NewsItem]) -> int:
        baseline = not self.is_feed_initialized(feed_url)
        seen = datetime.now(timezone.utc).isoformat()
        added = 0
        with self.db:
            for item in items:
                cursor = self.db.execute(
                    """INSERT OR IGNORE INTO articles
                       (id, title, summary, source, organization, category, dedupe_key, canonical_url, published_at, first_seen_at, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.id, item.title, item.summary, item.source, item.organization or item.source,
                     item.category, self._dedupe_key(item), item.url,
                     item.published_at.astimezone(timezone.utc).isoformat(), seen,
                     "baseline" if baseline else "pending"),
                )
                added += cursor.rowcount
            self.db.execute("INSERT OR IGNORE INTO initialized_feeds(url) VALUES (?)", (feed_url,))
        return added

    @staticmethod
    def _dedupe_key(item: NewsItem) -> str:
        title = re.sub(r"[^\w]+", "", item.title.casefold())
        organization = re.sub(r"[^\w]+", "", (item.organization or item.source).casefold())
        day = item.published_at.astimezone(timezone.utc).date().isoformat()
        return hashlib.sha256(f"{organization}|{title}|{day}".encode("utf-8")).hexdigest()

    def pending(self, since: datetime, limit: int) -> list[NewsItem]:
        rows = self.db.execute(
            """SELECT id, title, summary, source, organization, category, canonical_url, published_at
               FROM articles WHERE status = 'pending' AND published_at >= ?
               ORDER BY CASE category WHEN 'model_release' THEN 0 WHEN 'research' THEN 1 WHEN 'api_update' THEN 2 ELSE 3 END,
                        published_at DESC LIMIT ?""",
            (since.astimezone(timezone.utc).isoformat(), limit),
        ).fetchall()
        return [NewsItem(row["id"], row["title"], row["summary"], row["source"], row["canonical_url"], datetime.fromisoformat(row["published_at"]), row["organization"], row["category"]) for row in rows]

    def mark_notified(self, article_id: str) -> None:
        with self.db:
            self.db.execute(
                "UPDATE articles SET status = 'notified', notified_at = ? WHERE id = ? AND status = 'pending'",
                (datetime.now(timezone.utc).isoformat(), article_id),
            )

    def latest(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM articles ORDER BY published_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
