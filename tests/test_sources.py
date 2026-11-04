import tempfile
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from ai_news_desktop.config import Config, Feed, load_config
from ai_news_desktop.ingestion import RSSAdapter, WebpageAdapter, parse_feed, parse_webpage
from ai_news_desktop.models import NewsItem
from ai_news_desktop.pipeline import refresh
from ai_news_desktop.priorities import classify, should_notify
from ai_news_desktop.storage import Store
from ai_news_desktop.__main__ import smoke_labs

FIXTURES = Path(__file__).parent / "fixtures"


class SourceTests(unittest.TestCase):
    def test_default_sources(self):
        config = load_config()
        self.assertEqual({source.organization for source in config.feeds}, {"OpenAI", "Anthropic", "DeepSeek", "Google DeepMind", "Google Research", "Hugging Face", "arXiv"})
        self.assertFalse(next(source for source in config.feeds if source.organization == "Hugging Face").enabled)
        self.assertEqual({source.type for source in config.feeds}, {"rss", "atom", "webpage"})

    def test_source_toml_requires_metadata(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            path = Path(temp) / "sources.toml"
            path.write_text('[[sources]]\nname="Lab"\nurl="https://example.org/rss"\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_rss_and_atom_fixtures(self):
        openai = Feed("OpenAI News", "https://openai.com/news/rss.xml", organization="OpenAI")
        deepmind = Feed("DeepMind", "https://deepmind.google/discover/blog/feed/", organization="Google DeepMind", type="atom", category="research")
        items = parse_feed((FIXTURES / "rss.xml").read_bytes(), openai)
        self.assertEqual((items[0].organization, items[0].category, items[0].url), ("OpenAI", "model_release", "https://openai.com/index/introducing-gpt-6/"))
        self.assertEqual(parse_feed((FIXTURES / "atom.xml").read_bytes(), deepmind)[0].category, "research")
        response = httpx.Response(200, content=(FIXTURES / "rss.xml").read_bytes(), request=httpx.Request("GET", openai.url))
        with patch("ai_news_desktop.ingestion.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.return_value = response
            self.assertEqual(len(RSSAdapter().fetch(openai)), 1)

    def test_revision_metadata_and_official_hugging_face_filter(self):
        source = Feed("Hugging Face Official Blog", "https://huggingface.co/blog/feed.xml",
                      organization="Hugging Face", official_authors=("huggingface",))
        xml = b'''<rss version="2.0"><channel><title>Blog</title>
        <item><title>Official release</title><link>https://huggingface.co/blog/huggingface/release</link>
        <author>huggingface</author><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate></item>
        <item><title>Community post</title><link>https://huggingface.co/blog/person/post</link>
        <author>person</author><pubDate>Mon, 02 Nov 2026 11:00:00 GMT</pubDate></item>
        </channel></rss>'''
        items = parse_feed(xml, source)
        self.assertEqual([item.title for item in items], ["Official release"])
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>arXiv</title>
        <entry><title>Paper</title><id>https://arxiv.org/abs/1234.5678</id>
        <link href="https://arxiv.org/abs/1234.5678"/>
        <published>2026-10-01T10:00:00Z</published><updated>2026-11-02T12:00:00Z</updated></entry></feed>'''
        paper = parse_feed(atom, Feed("arXiv", "https://example.com/atom"))[0]
        self.assertEqual(paper.published_at.date().isoformat(), "2026-10-01")
        self.assertEqual(paper.updated_at.date().isoformat(), "2026-11-02")

    def test_webpage_fixtures_and_robots(self):
        anthropic = Feed("Anthropic", "https://www.anthropic.com/news", organization="Anthropic", type="webpage", card_tag="li", card_attribute="data-listing-row", link_prefix="/news/")
        deepseek = Feed("DeepSeek", "https://www.deepseek.com/en/news/", organization="DeepSeek", type="webpage", card_tag="a", card_classes=("ds-news-hero-card", "ds-news-list-item"), link_prefix="/en/news/")
        for source, filename, expected in ((anthropic, "anthropic.html", "model_release"), (deepseek, "deepseek.html", "model_release")):
            with self.subTest(source=source.name):
                items = parse_webpage((FIXTURES / filename).read_text(encoding="utf-8"), source)
                self.assertEqual(len(items), 2)
                self.assertEqual(items[0].category, expected)
                self.assertEqual(items[0].organization, source.organization)
                self.assertEqual(items[0].published_at.tzinfo, timezone.utc)
                if source.organization == "Anthropic":
                    self.assertEqual(items[0].url, "https://www.anthropic.com/news/introducing-claude-5")
        with self.assertRaises(ValueError):
            parse_webpage("<html>changed layout</html>", anthropic)
        robots = httpx.Response(200, text="User-agent: *\nDisallow: /news\n", request=httpx.Request("GET", "https://www.anthropic.com/robots.txt"))
        with patch("ai_news_desktop.ingestion.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.return_value = robots
            with self.assertRaises(PermissionError):
                WebpageAdapter().fetch(anthropic)
            client.return_value.__enter__.return_value.get.assert_called_once()
        allowed = httpx.Response(200, text="User-agent: *\nAllow: /\n", request=httpx.Request("GET", "https://www.anthropic.com/robots.txt"))
        page = httpx.Response(200, text=(FIXTURES / "anthropic.html").read_text(encoding="utf-8"), request=httpx.Request("GET", anthropic.url))
        with patch("ai_news_desktop.ingestion.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.side_effect = [allowed, page]
            self.assertEqual(len(WebpageAdapter().fetch(anthropic)), 2)
        unavailable = httpx.Response(202, text="Please wait", request=httpx.Request("GET", anthropic.url))
        with patch("ai_news_desktop.ingestion.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.side_effect = [allowed, unavailable]
            with self.assertRaisesRegex(ValueError, "HTTP 202"):
                WebpageAdapter().fetch(anthropic)

    def test_dedup_by_title_date_across_methods(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            store = Store(Path(temp) / "news.sqlite3")
            first = NewsItem("a", "Introducing Claude 5", "Summary", "RSS", "https://www.anthropic.com/news/introducing-claude-5", datetime(2026, 10, 28, tzinfo=timezone.utc), "Anthropic", "model_release")
            second = NewsItem("b", "Introducing Claude 5", "Summary", "Web", "https://www.anthropic.com/news/claude-5-launch", datetime(2026, 10, 28, 12, tzinfo=timezone.utc), "Anthropic", "model_release")
            self.assertEqual(store.save_feed("rss", [first]), 1)
            self.assertEqual(store.save_feed("web", [second]), 0)
            self.assertEqual(store.count(), 1)
            store.close()

    def test_notification_preferences(self):
        config = Config(())
        self.assertEqual(classify("New Claude API capabilities", "", "company_news"), "api_update")
        good = NewsItem("1", "Introducing Claude 5", "New model", "Anthropic", "https://example.org/1", datetime.now(timezone.utc), "Anthropic", "model_release")
        marketing = NewsItem("2", "Introducing Claude 5 partnership", "New model", "Anthropic", "https://example.org/2", datetime.now(timezone.utc), "Anthropic", "model_release")
        self.assertTrue(should_notify(good, config))
        self.assertFalse(should_notify(marketing, config))

    def test_migrates_existing_phase2_database(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            path = Path(temp) / "old.sqlite3"
            db = sqlite3.connect(path)
            db.execute("""CREATE TABLE articles (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL,
                source TEXT NOT NULL, canonical_url TEXT NOT NULL UNIQUE,
                published_at TEXT NOT NULL, first_seen_at TEXT NOT NULL,
                status TEXT NOT NULL, notified_at TEXT)""")
            db.commit()
            db.close()
            store = Store(path)
            columns = {row[1] for row in store.db.execute("PRAGMA table_info(articles)")}
            self.assertTrue({"organization", "category", "dedupe_key", "updated_at", "fetched_at"}.issubset(columns))
            self.assertTrue(path.with_suffix(".sqlite3.pre-phase5.bak").exists())
            store.close()

    def test_revision_preserves_initial_publication(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            store = Store(Path(temp) / "news.sqlite3")
            initial = NewsItem("v1", "Paper", "Abstract", "arXiv cs.AI", "https://arxiv.org/abs/1234.5678v1",
                               datetime(2026, 10, 1, tzinfo=timezone.utc), "arXiv", "research")
            revised = NewsItem("v2", "Paper", "Abstract", "arXiv cs.AI", "https://arxiv.org/abs/1234.5678v2",
                               initial.published_at, "arXiv", "research", datetime(2026, 11, 2, tzinfo=timezone.utc))
            store.save_feed("arxiv", [initial])
            self.assertEqual(store.save_feed("arxiv", [revised]), 0)
            row = store.latest(1)[0]
            self.assertEqual(row["published_at"], initial.published_at.isoformat())
            self.assertEqual(row["updated_at"], revised.updated_at.isoformat())
            self.assertIsNotNone(row["fetched_at"])
            store.close()

    def test_model_release_precedes_research_under_limit(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            store = Store(Path(temp) / "news.sqlite3")
            source = Feed("Lab", "https://example.org/feed", organization="Lab")
            store.save_feed(source.url, [])
            now = datetime.now(timezone.utc)
            research = NewsItem("r", "Research study", "Results", "Lab", "https://example.org/research", now, "Lab", "research")
            model = NewsItem("m", "Introducing model 5", "Release", "Lab", "https://example.org/model", now, "Lab", "model_release")
            store.save_feed(source.url, [research, model])
            notifier = Mock()
            result = refresh(Config((source,), max_notifications_per_cycle=1), store, Mock(fetch=Mock(return_value=[])), True, lambda: notifier)
            self.assertEqual(result.notified, 1)
            self.assertEqual(notifier.send.call_args.args[0].id, "m")
            store.close()

    def test_smoke_labs_sends_one_each_without_storage(self):
        sources = load_config().feeds
        config = Config(tuple(source for source in sources if source.organization in {"Anthropic", "DeepSeek"}))
        fetcher = Mock()
        fetcher.fetch.side_effect = lambda source: parse_webpage(
            (FIXTURES / ("anthropic.html" if source.organization == "Anthropic" else "deepseek.html")).read_text(encoding="utf-8"), source
        )
        notifier = Mock()
        with patch("ai_news_desktop.__main__.time.sleep"):
            self.assertTrue(smoke_labs(config, fetcher, notifier))
        self.assertEqual(notifier.send.call_count, 2)
        self.assertEqual([call.args[0].organization for call in notifier.send.call_args_list], ["Anthropic", "DeepSeek"])


if __name__ == "__main__":
    unittest.main()
