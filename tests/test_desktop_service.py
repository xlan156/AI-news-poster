"""Desktop IPC and persistence tests; no network or paid provider calls."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from ai_news_desktop.desktop_service import DesktopService, in_quiet_hours
from ai_news_desktop.models import NewsItem
from ai_news_desktop.pipeline import refresh


def test_desktop_state_and_read_flag(tmp_path):
    service = DesktopService(tmp_path)
    try:
        state = service.dispatch("get_state", {})
        assert state["status"]["article_count"] == 0
        assert "api_key_ref" not in state["config"]["llm"]
        service.dispatch("set_read", {"id": "sample", "read": True})
        assert service.store.db.execute("SELECT read_at FROM article_read WHERE article_id='sample'").fetchone()
        service.dispatch("set_read", {"id": "sample", "read": False})
        assert not service.store.db.execute("SELECT 1 FROM article_read WHERE article_id='sample'").fetchone()
    finally:
        service.close()


def test_news_search_covers_all_rows_and_legacy_source(tmp_path):
    service = DesktopService(tmp_path)
    try:
        with service.store.db:
            service.store.db.executemany(
                "INSERT INTO articles (id,title,summary,source,organization,canonical_url,published_at,first_seen_at,status) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [(str(i), "Find the oldest" if i == 0 else f"Article {i}", "A summary", "arXiv", "",
                  f"https://example.com/{i}", f"2026-01-{i // 100 + 1:02d}T00:00:{i % 60:02d}+00:00",
                  "2026-01-01T00:00:00+00:00", "baseline") for i in range(120)],
            )
        state = service.dispatch("get_state", {})
        assert state["articles"] == []  # Official Updates excludes stored papers.
        papers = service.dispatch("get_news", {"view": "research"})
        assert papers["total"] == 120
        assert papers["items"][0]["organization"] == "arXiv"
        assert papers["items"][0]["category"] == "research"
        first = service.dispatch("get_news", {"limit": 100})
        assert first["total"] == 120
        assert len(first["items"]) == 100
        assert first["organizations"] == ["arXiv"]
        assert service.dispatch("get_news", {"category": "research"})["total"] == 120
        assert service.dispatch("get_news", {"category": "company_news"})["total"] == 0
        second = service.dispatch("get_news", {"offset": 100})
        assert len(second["items"]) == 20
        found = service.dispatch("get_news", {"search": "Find the oldest", "organization": "arXiv"})
        assert found["total"] == 1
        assert found["items"][0]["organization"] == "arXiv"
    finally:
        service.close()


def test_settings_round_trip_and_validation(tmp_path):
    service = DesktopService(tmp_path)
    try:
        service.dispatch("save_config", {"patch": {"refresh_minutes": 30, "llm": {"mode": "disabled"}}})
        service.dispatch("save_preferences", {"patch": {"quiet_start": "23:00", "quiet_end": "08:00"}})
        assert service.dispatch("get_state", {})["config"]["refresh_minutes"] == 30
        with pytest.raises(ValueError):
            service.dispatch("save_config", {"patch": {"refresh_minutes": 0}})
        assert json.loads((tmp_path / "desktop.json").read_text())["refresh_minutes"] == 30
    finally:
        service.close()
    reopened = DesktopService(tmp_path)
    try:
        assert reopened.config.refresh_minutes == 30
        assert reopened.prefs["quiet_start"] == "23:00"
    finally:
        reopened.close()


def test_phase5_settings_survive_restart(tmp_path):
    service = DesktopService(tmp_path)
    try:
        service.dispatch("save_config", {"patch": {"refresh_minutes": 120, "home_source_quota": 2,
            "default_view": "history", "default_sort": "newest",
            "notifications": {"research": True, "sources": ["__none__"]}}})
        service.dispatch("save_preferences", {"patch": {"start_at_login": True}})
    finally:
        service.close()
    reopened = DesktopService(tmp_path)
    try:
        config = reopened.dispatch("get_state", {})["config"]
        assert (config["refresh_minutes"], config["home_source_quota"], config["default_view"]) == (120, 2, "history")
        assert config["notifications"]["research"] is True
        assert tuple(config["notifications"]["sources"]) == ("__none__",)
        assert config["preferences"]["start_at_login"] is True
    finally:
        reopened.close()


def test_source_failure_isolated_from_empty_success(tmp_path):
    service = DesktopService(tmp_path)
    try:
        active = [feed for feed in service.config.feeds if feed.enabled][:2]
        fetcher = Mock()
        fetcher.fetch.side_effect = [RuntimeError("timeout"), []]
        result = refresh(service.config.__class__(feeds=tuple(active)), service.store, fetcher)
        assert (result.feeds_ok, result.feeds_failed, result.articles_added) == (1, 1, 0)
        status = service.store.source_status()
        assert status[active[0].url]["state"] == "error"
        assert status[active[0].url]["last_error"] == "timeout"
        assert status[active[1].url]["state"] == "ok"
        assert status[active[1].url]["articles_added"] == 0
    finally:
        service.close()
    reopened = DesktopService(tmp_path)
    try:
        assert reopened.status()["source_status"][active[0].url]["state"] == "error"
    finally:
        reopened.close()


def test_quiet_hours_cross_midnight():
    prefs = {"quiet_start": "23:00", "quiet_end": "08:00"}
    assert in_quiet_hours(prefs, datetime(2026, 1, 1, 23, 15))
    assert in_quiet_hours(prefs, datetime(2026, 1, 2, 7, 59))
    assert not in_quiet_hours(prefs, datetime(2026, 1, 2, 8, 0))


def test_source_configuration_persists(tmp_path):
    service = DesktopService(tmp_path)
    try:
        initial = service.dispatch("get_state", {})["config"]["sources"]
        custom = {"name": "Example Lab", "organization": "Example Lab", "url": "https://example.com/feed.xml",
                  "enabled": True, "type": "rss", "category": "research", "card_classes": [],
                  "card_tag": "", "card_attribute": "", "link_prefix": ""}
        result = service.dispatch("save_config", {"patch": {"sources": [*initial, custom]}})
        assert result["sources"][-1]["name"] == "Example Lab"
    finally:
        service.close()


def test_single_monitoring_owner(tmp_path):
    service = DesktopService(tmp_path)
    try:
        assert service.dispatch("set_monitoring", {"enabled": False})["monitoring"] is False
        assert service.dispatch("set_monitoring", {"enabled": True})["monitoring"] is True
    finally:
        service.close()


def test_json_line_ipc_round_trip(tmp_path):
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path)}
    process = subprocess.Popen([sys.executable, "-m", "ai_news_desktop.desktop_service"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", env=env)
    try:
        assert process.stdin and process.stdout
        process.stdin.write(json.dumps({"id": 7, "method": "get_state", "params": {}}) + "\n")
        process.stdin.flush()
        response = json.loads(process.stdout.readline())
        assert response["id"] == 7
        assert "api_key_ref" not in response["result"]["config"]["llm"]
    finally:
        if process.stdin:
            process.stdin.close()
        process.wait(timeout=5)


def test_phase5_views_quota_and_persistent_source_health(tmp_path):
    service = DesktopService(tmp_path)
    try:
        sources = service.config.feeds
        openai, anthropic = sources[:2]
        for feed in (openai, anthropic):
            service.store.save_feed(feed.url, [])
        stamp = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for i in range(6):
            service.store.save_feed(openai.url, [NewsItem(f"o{i}", f"OpenAI {i}", "Summary", openai.name,
                f"https://example.com/o{i}", stamp, openai.organization)])
        service.store.save_feed(anthropic.url, [NewsItem("a", "Anthropic", "Summary", anthropic.name,
            "https://example.com/a", stamp, anthropic.organization)])
        service.store.save_feed("arxiv", [NewsItem("r", "Paper", "Summary", "arXiv",
            "https://example.com/r", stamp, "arXiv", "research")])
        service.store.set_source_status(openai.url, "ok", 6)
        home = service.dispatch("get_state", {})["articles"]
        assert len(home) == 4
        assert sum(a["source"] == openai.name for a in home) == 3
        assert service.dispatch("get_news", {"view": "history"})["total"] == 8
        assert service.dispatch("get_news", {"view": "research"})["total"] == 1
        service.dispatch("save_config", {"patch": {"sources": [
            {**feed, "enabled": False} if feed["url"] == openai.url else feed
            for feed in service.dispatch("get_state", {})["config"]["sources"]]}})
        assert service.dispatch("get_news", {"view": "official"})["total"] == 1
        assert service.dispatch("get_news", {"view": "history", "search": "OpenAI"})["total"] == 6
    finally:
        service.close()
    reopened = DesktopService(tmp_path)
    try:
        assert reopened.status()["source_status"][openai.url]["articles_added"] == 6
        assert reopened.status()["last_refresh"] is not None
        assert reopened.dispatch("get_news", {"view": "history"})["total"] == 8
    finally:
        reopened.close()
