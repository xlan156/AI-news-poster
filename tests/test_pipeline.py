import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import httpx

from ai_news_desktop.config import Config, Feed, load_config
from ai_news_desktop.ingestion import FeedFetcher, canonical_url, parse_feed
from ai_news_desktop.pipeline import refresh, run_periodically
from ai_news_desktop.storage import Store
from ai_news_desktop.__main__ import main


RSS = b"""<rss version="2.0"><channel><title>Test</title>
<item><title>First &amp; best</title><link>https://example.org/one?utm_source=rss</link>
<description><![CDATA[<p>A <b>summary</b></p>]]></description>
<pubDate>Wed, 28 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Missing date</title><link>https://example.org/no-date</link></item>
<item><title>Bad URL</title><link>file:///tmp/bad</link><pubDate>Wed, 28 Oct 2026 10:00:00 GMT</pubDate></item>
<item><link>https://example.org/no-title</link><pubDate>Wed, 28 Oct 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>
<entry><title>Research update</title><link href="https://example.org/two"/>
<updated>2026-10-29T08:00:00Z</updated><summary>Agent research</summary></entry></feed>"""


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.path = Path(self.temp.name) / "news.sqlite3"
        self.feed = Feed("Test", "https://example.org/feed")
        self.config = Config((self.feed,), max_article_age_hours=1000000)
        self.rss = parse_feed(RSS, self.feed)
        self.atom = parse_feed(ATOM, self.feed)

    def tearDown(self):
        self.temp.cleanup()

    def test_rss_atom_and_malformed_entries(self):
        self.assertEqual(len(self.rss), 1)
        self.assertEqual(self.rss[0].title, "First & best")
        self.assertEqual(self.rss[0].summary, "A summary")
        self.assertEqual(self.rss[0].url, "https://example.org/one")
        self.assertEqual(self.atom[0].url, "https://example.org/two")
        self.assertEqual(self.atom[0].published_at.tzinfo, timezone.utc)
        with self.assertRaises(ValueError):
            parse_feed(b"<rss><broken", self.feed)

    def test_canonical_url(self):
        self.assertEqual(canonical_url("HTTPS://Example.ORG:443/story?utm_source=x&b=2&a=1#top"), "https://example.org/story?a=1&b=2")

    def test_first_run_restart_and_rate_limit(self):
        fetcher = Mock(fetch=Mock(return_value=self.rss))
        notifier = Mock()
        store = Store(self.path)
        first = refresh(self.config, store, fetcher, True, lambda: notifier)
        self.assertEqual((first.articles_added, first.notified), (1, 0))
        store.close()

        store = Store(self.path)
        fetcher.fetch.return_value = self.rss + self.atom
        second = refresh(self.config, store, fetcher, True, lambda: notifier)
        self.assertEqual((second.articles_added, second.notified), (1, 1))
        self.assertEqual(store.count(), 2)
        self.assertEqual(store.latest()[0]["status"], "notified")
        self.assertIsNotNone(store.latest()[0]["notified_at"])
        store.close()

        store = Store(self.path)
        third = refresh(self.config, store, fetcher, True, lambda: notifier)
        self.assertEqual((third.articles_added, third.notified), (0, 0))
        notifier.send.assert_called_once()
        store.close()

    def test_failure_retry_limit_and_freshness(self):
        store = Store(self.path)
        store.save_feed(self.feed.url, [])
        newer = self.atom[0]
        old = self.rss[0]
        store.save_feed(self.feed.url, [newer, old])
        config = Config((self.feed,), max_notifications_per_cycle=1, max_article_age_hours=1000000)
        fail = Mock()
        fail.send.side_effect = RuntimeError("toast failed")
        with patch("ai_news_desktop.pipeline.time.sleep"):
            self.assertEqual(refresh(config, store, Mock(fetch=Mock(return_value=[])), True, lambda: fail).notified, 0)
        self.assertEqual(len(store.pending(datetime(2020, 1, 1, tzinfo=timezone.utc), 10)), 2)
        good = Mock()
        with patch("ai_news_desktop.pipeline.time.sleep"):
            self.assertEqual(refresh(config, store, Mock(fetch=Mock(return_value=[])), True, lambda: good).notified, 1)
        self.assertEqual(len(store.pending(datetime(2020, 1, 1, tzinfo=timezone.utc), 10)), 1)
        fresh_only = Config((self.feed,), max_article_age_hours=1)
        self.assertEqual(refresh(fresh_only, store, Mock(fetch=Mock(return_value=[])), True, lambda: good).notified, 0)
        store.close()

    def test_partial_network_failure(self):
        other = Feed("Other", "https://example.org/other")
        fetcher = Mock()
        fetcher.fetch.side_effect = [httpx.TimeoutException("timeout"), self.atom]
        store = Store(self.path)
        result = refresh(Config((self.feed, other)), store, fetcher)
        self.assertEqual((result.feeds_ok, result.feeds_failed, store.count()), (1, 1, 1))
        self.assertFalse(store.is_feed_initialized(self.feed.url))
        store.close()

    def test_same_url_across_feeds_and_disabled_notifications(self):
        other = Feed("Other", "https://example.org/other")
        store = Store(self.path)
        fetcher = Mock()
        fetcher.fetch.side_effect = [self.rss, self.rss]
        result = refresh(Config((self.feed, other), notifications_enabled=False), store, fetcher, True, lambda: Mock())
        self.assertEqual((result.articles_added, result.notified, store.count()), (1, 0, 1))
        store.close()

    def test_http_fetcher_and_config(self):
        response = httpx.Response(200, content=RSS, request=httpx.Request("GET", self.feed.url))
        with patch("ai_news_desktop.ingestion.httpx.Client") as client:
            client.return_value.__enter__.return_value.get.return_value = response
            self.assertEqual(len(FeedFetcher().fetch(self.feed)), 1)
        config_path = Path(self.temp.name) / "config.json"
        config_path.write_text(json.dumps({"feeds": [{"name": "Test", "url": self.feed.url, "enabled": False}], "refresh_minutes": 30}), encoding="utf-8")
        config = load_config(config_path)
        self.assertFalse(config.feeds[0].enabled)
        self.assertEqual(config.refresh_minutes, 30)

    def test_scheduler_stops_without_busy_wait(self):
        stop = Event()
        cycle = Mock(side_effect=stop.set)
        run_periodically(cycle, 3600, stop)
        cycle.assert_called_once()

    def test_cli_fetch_list_and_run(self):
        with patch("ai_news_desktop.__main__.FeedFetcher") as fetcher_type:
            fetcher_type.return_value.fetch.return_value = self.rss
            self.assertEqual(main(["--fetch-once", "--db", str(self.path)]), 0)
            self.assertEqual(main(["--fetch-once", "--db", str(self.path)]), 0)
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--list-latest", "--db", str(self.path)]), 0)
            self.assertIn("First & best", output.getvalue())
            with patch("ai_news_desktop.__main__.run_periodically", side_effect=lambda cycle, _interval, _stop: cycle()) as runner:
                self.assertEqual(main(["--run", "--db", str(self.path)]), 0)
                self.assertEqual(runner.call_args.args[1], 3600)


if __name__ == "__main__":
    unittest.main()
