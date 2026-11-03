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
            CREATE TABLE IF NOT EXISTS llm_cache (
                cache_key TEXT PRIMARY KEY, analysis_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS llm_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, model TEXT NOT NULL,
                requested_at TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                estimated_cost_usd REAL, status TEXT NOT NULL, article_count INTEGER NOT NULL,
                article_keys TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS llm_usage_requested ON llm_usage(requested_at);
            CREATE TABLE IF NOT EXISTS article_read (article_id TEXT PRIMARY KEY, read_at TEXT NOT NULL);
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
               ORDER BY CASE category WHEN 'model_release' THEN 0 WHEN 'research' THEN 1 WHEN 'api_update' THEN 2 WHEN 'product_update' THEN 3 ELSE 4 END,
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
            "SELECT articles.*, article_read.read_at FROM articles LEFT JOIN article_read ON articles.id = article_read.article_id ORDER BY published_at DESC LIMIT ?", (limit,)
        ).fetchall()

    def search_news(self, search: str = "", organization: str = "all", category: str = "all",
                    sort: str = "newest", limit: int = 100, offset: int = 0) -> tuple[list[sqlite3.Row], int]:
        source_name = "COALESCE(NULLIF(articles.organization, ''), NULLIF(articles.source, ''), '未知来源')"
        display_category = "CASE WHEN articles.category = 'company_news' AND articles.source LIKE 'arXiv%' THEN 'research' ELSE articles.category END"
        conditions = []
        args: list[str] = []
        if organization != "all":
            conditions.append(f"{source_name} = ?")
            args.append(organization)
        if category != "all":
            conditions.append(f"{display_category} = ?")
            args.append(category)
        if search.strip():
            term = "%" + search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append(f"(articles.title LIKE ? ESCAPE '\\' OR articles.summary LIKE ? ESCAPE '\\' OR {source_name} LIKE ? ESCAPE '\\')")
            args.extend((term, term, term))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        total = self.db.execute("SELECT COUNT(*) FROM articles" + where, args).fetchone()[0]
        order = "ASC" if sort == "oldest" else "DESC"
        rows = self.db.execute(
            "SELECT articles.*, article_read.read_at FROM articles "
            "LEFT JOIN article_read ON articles.id = article_read.article_id" + where +
            f" ORDER BY articles.published_at {order}, articles.id {order} LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
        return rows, total

    def organizations(self) -> list[str]:
        return [row[0] for row in self.db.execute(
            "SELECT DISTINCT COALESCE(NULLIF(organization, ''), NULLIF(source, ''), '未知来源') AS name "
            "FROM articles ORDER BY name COLLATE NOCASE"
        )]

    def set_read(self, article_id: str, read: bool) -> None:
        with self.db:
            if read:
                self.db.execute("INSERT OR REPLACE INTO article_read VALUES (?, ?)", (article_id, datetime.now(timezone.utc).isoformat()))
            else:
                self.db.execute("DELETE FROM article_read WHERE article_id = ?", (article_id,))

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    def cache_get(self, key: str) -> str | None:
        row = self.db.execute("SELECT analysis_json FROM llm_cache WHERE cache_key = ?", (key,)).fetchone()
        return row[0] if row else None

    def cache_put_many(self, entries: dict[str, str]) -> None:
        stamp = datetime.now(timezone.utc).isoformat()
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?)", ((key, value, stamp) for key, value in entries.items()))

    def usage_record(self, provider: str, model: str, input_tokens: int, output_tokens: int,
                     estimated_cost_usd: float | None, status: str, article_keys: list[str]) -> None:
        import json
        with self.db:
            self.db.execute("""INSERT INTO llm_usage
                (provider, model, requested_at, input_tokens, output_tokens, estimated_cost_usd, status, article_count, article_keys)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (provider, model, datetime.now(timezone.utc).isoformat(), input_tokens, output_tokens,
                 estimated_cost_usd, status, len(article_keys), json.dumps(article_keys)))

    def usage_summary(self, provider: str | None = None, model: str | None = None) -> dict:
        from datetime import date
        import json
        now = datetime.now(timezone.utc)
        month = now.strftime("%Y-%m-")
        day = now.date().isoformat()
        rows = self.db.execute("SELECT * FROM llm_usage WHERE (? IS NULL OR provider = ?) AND (? IS NULL OR model = ?)",
                               (provider, provider, model, model)).fetchall()
        today = [row for row in rows if row["requested_at"].startswith(day)]
        monthly = [row for row in rows if row["requested_at"].startswith(month)]
        article_keys = set()
        for row in today:
            article_keys.update(json.loads(row["article_keys"]))
        return {"requests_today": len(today), "articles_today": len(article_keys),
                "article_keys_today": article_keys,
                "input_tokens_today": sum(row["input_tokens"] for row in today),
                "output_tokens_today": sum(row["output_tokens"] for row in today),
                "estimated_cost_month_usd": sum(row["estimated_cost_usd"] or 0 for row in monthly),
                "unpriced_month_requests": sum(row["estimated_cost_usd"] is None for row in monthly)}
