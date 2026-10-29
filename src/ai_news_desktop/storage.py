"""SQLite persistence and notification state."""

import sqlite3
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
                canonical_url TEXT NOT NULL UNIQUE,
                published_at TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('baseline', 'pending', 'notified')),
                notified_at TEXT
            );
            CREATE TABLE IF NOT EXISTS initialized_feeds (url TEXT PRIMARY KEY);
            CREATE INDEX IF NOT EXISTS articles_pending ON articles(status, published_at);
        """)

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
                       (id, title, summary, source, canonical_url, published_at, first_seen_at, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (item.id, item.title, item.summary, item.source, item.url,
                     item.published_at.astimezone(timezone.utc).isoformat(), seen,
                     "baseline" if baseline else "pending"),
                )
                added += cursor.rowcount
            self.db.execute("INSERT OR IGNORE INTO initialized_feeds(url) VALUES (?)", (feed_url,))
        return added

    def pending(self, since: datetime, limit: int) -> list[NewsItem]:
        rows = self.db.execute(
            """SELECT id, title, summary, source, canonical_url, published_at
               FROM articles WHERE status = 'pending' AND published_at >= ?
               ORDER BY first_seen_at, published_at DESC LIMIT ?""",
            (since.astimezone(timezone.utc).isoformat(), limit),
        ).fetchall()
        return [NewsItem(row["id"], row["title"], row["summary"], row["source"], row["canonical_url"], datetime.fromisoformat(row["published_at"])) for row in rows]

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
