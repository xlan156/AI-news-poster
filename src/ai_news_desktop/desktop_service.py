"""Private newline-delimited JSON IPC for the desktop shell. One worker owns SQLite."""

import json
import logging
import os
import queue
import sys
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread

from .config import Config, LLMConfig, default_db_path, load_config
from .analysis import cache_key
from .ingestion import FeedFetcher
from .llm import make_provider
from .models import NewsItem
from .pipeline import refresh
from .storage import Store

log = logging.getLogger(__name__)
DATA_DIR = default_db_path().parent
CONFIG_FILE = DATA_DIR / "desktop.json"
PREFS_FILE = DATA_DIR / "preferences.json"
DEFAULT_PREFS = {"theme": "dark", "language": "zh-CN", "start_minimized": False,
                 "minimize_to_tray": True, "quiet_start": "", "quiet_end": "",
                 "daily_digest": False, "start_at_login": False}


def public_config(config: Config, prefs: dict) -> dict:
    value = asdict(config)
    value["sources"] = value.pop("feeds")
    value["notifications"] = {"categories": value.pop("notification_categories"),
                              "exclude_keywords": value.pop("exclude_keywords")}
    value["llm"]["has_api_key"] = value["llm"]["api_key_ref"].startswith("keyring:")
    value["llm"].pop("api_key_ref")
    value["preferences"] = prefs
    return value


def config_json(config: Config) -> dict:
    value = asdict(config)
    value["sources"] = value.pop("feeds")
    value["notifications"] = {"categories": value.pop("notification_categories"),
                              "exclude_keywords": value.pop("exclude_keywords")}
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def in_quiet_hours(prefs: dict, now: datetime | None = None) -> bool:
    start, end = prefs.get("quiet_start"), prefs.get("quiet_end")
    if not start or not end or start == end:
        return False
    current = (now or datetime.now()).strftime("%H:%M")
    return start <= current < end if start < end else current >= start or current < end


class DesktopService:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.config_file = data_dir / "desktop.json"
        self.prefs_file = data_dir / "preferences.json"
        self.config = load_config(self.config_file if self.config_file.exists() else None)
        self.prefs = {**DEFAULT_PREFS, **(json.loads(self.prefs_file.read_text(encoding="utf-8")) if self.prefs_file.exists() else {})}
        self.store = Store(data_dir / "news.sqlite3")
        self.monitoring = True
        self.last_refresh = None
        self.last_result = None
        self.next_refresh = datetime.now().astimezone() + timedelta(minutes=self.config.refresh_minutes)
        self.source_status = {}

    def close(self):
        self.store.close()

    def status(self):
        return {"monitoring": self.monitoring, "last_refresh": self.last_refresh,
                "next_refresh": self.next_refresh.isoformat() if self.monitoring else None,
                "last_result": self.last_result, "article_count": self.store.count(),
                "new_count": self.store.db.execute("SELECT COUNT(*) FROM articles WHERE status = 'pending'").fetchone()[0],
                "usage": {k: v for k, v in self.store.usage_summary().items() if k != "article_keys_today"},
                "source_status": self.source_status, "data_dir": str(self.data_dir)}

    def articles(self, rows=None):
        values = []
        for row in rows if rows is not None else self.store.latest(20):
            article = dict(row)
            article["organization"] = row["organization"] or row["source"] or "未知来源"
            if row["category"] == "company_news" and row["source"].lower().startswith("arxiv"):
                article["category"] = "research"
            if self.config.llm and self.config.llm.mode != "disabled":
                item = NewsItem(row["id"], row["title"], row["summary"], row["source"], row["canonical_url"],
                                datetime.fromisoformat(row["published_at"]), row["organization"], row["category"])
                cached = self.store.cache_get(cache_key(item, self.config.llm))
                if cached:
                    try:
                        analysis = json.loads(cached)
                        article["chinese_summary"] = analysis.get("chinese_summary")
                        article["importance_score"] = analysis.get("importance_score")
                    except (ValueError, TypeError):
                        pass
            values.append(article)
        return values

    def run_refresh(self, source_url=None):
        config = self.config
        if source_url:
            matches = tuple(feed for feed in config.feeds if feed.url == source_url)
            if not matches:
                raise ValueError("Unknown source")
            config = replace(config, feeds=tuple(replace(feed, enabled=True) for feed in matches))
        base_fetcher = FeedFetcher()
        source_status = self.source_status

        class TrackingFetcher:
            def fetch(self, feed):
                stamp = datetime.now().astimezone().isoformat()
                try:
                    items = base_fetcher.fetch(feed)
                    source_status[feed.url] = {"last_attempt": stamp, "last_success": stamp, "state": "ok", "last_error": ""}
                    return items
                except Exception as exc:
                    source_status[feed.url] = {"last_attempt": stamp, "last_success": source_status.get(feed.url, {}).get("last_success"),
                                               "state": "error", "last_error": str(exc)[:200]}
                    raise

        result = refresh(config, self.store, TrackingFetcher(),
                         notify=self.monitoring and not source_url and not in_quiet_hours(self.prefs))
        now = datetime.now().astimezone()
        if result.feeds_ok:
            self.last_refresh = now.isoformat()
        self.next_refresh = now + timedelta(minutes=self.config.refresh_minutes)
        self.last_result = asdict(result)
        return self.status()

    def dispatch(self, method: str, params: dict):
        if method == "get_state":
            return {"status": self.status(), "config": public_config(self.config, self.prefs),
                    "articles": self.articles()}
        if method == "get_news":
            search = str(params.get("search", ""))[:200]
            organization = str(params.get("organization", "all"))[:200]
            category = str(params.get("category", "all"))[:100]
            sort = str(params.get("sort", "newest"))
            limit = max(1, min(int(params.get("limit", 100)), 100))
            offset = max(0, int(params.get("offset", 0)))
            rows, total = self.store.search_news(search, organization, category, sort, limit, offset)
            return {"items": self.articles(rows), "total": total,
                    "organizations": self.store.organizations()}
        if method == "test_connection":
            if not self.config.llm or self.config.llm.mode == "disabled":
                raise ValueError("AI analysis is disabled")
            response = make_provider(self.config.llm).generate("Reply with the single word OK.")
            self.store.usage_record(self.config.llm.provider, self.config.llm.model,
                                    response.input_tokens, response.output_tokens, None, "success", [])
            return {"ok": bool(response.text.strip())}
        if method == "refresh":
            return self.run_refresh(params.get("source_url"))
        if method == "set_monitoring":
            self.monitoring = bool(params["enabled"])
            self.next_refresh = datetime.now().astimezone()
            return self.status()
        if method == "set_read":
            self.store.set_read(str(params["id"]), bool(params["read"]))
            return {"ok": True}
        if method == "save_config":
            raw = config_json(self.config)
            patch = params.get("patch", {})
            allowed = {"refresh_minutes", "max_notifications_per_cycle", "notifications_enabled", "llm", "news_filter", "notifications", "sources"}
            if not isinstance(patch, dict) or set(patch) - allowed:
                raise ValueError("Invalid configuration fields")
            for key, value in patch.items():
                if key in {"llm", "news_filter", "notifications"}:
                    if not isinstance(value, dict):
                        raise ValueError("Invalid settings group")
                    raw[key].update(value)
                else:
                    raw[key] = value
            key = raw["llm"].pop("api_key", "")
            if key:
                raw["llm"]["api_key_ref"] = "keyring:AI News Desktop:llm"
            temp = self.config_file.with_suffix(".validate.json")
            atomic_json(temp, raw)
            try:
                validated = load_config(temp)
            finally:
                temp.unlink(missing_ok=True)
            if key:
                import keyring
                keyring.set_password("AI News Desktop", "llm", key)
            atomic_json(self.config_file, raw)
            self.config = validated
            return public_config(self.config, self.prefs)
        if method == "save_preferences":
            patch = params.get("patch", {})
            if not isinstance(patch, dict) or set(patch) - set(DEFAULT_PREFS):
                raise ValueError("Invalid preferences")
            updated = {**self.prefs, **patch}
            for name in ("quiet_start", "quiet_end"):
                value = updated[name]
                if value and (not isinstance(value, str) or len(value) != 5 or value[2] != ":" or not ("00:00" <= value <= "23:59")):
                    raise ValueError("Quiet hours must use HH:MM")
            atomic_json(self.prefs_file, updated)
            self.prefs = updated
            return self.prefs
        raise ValueError("Unknown command")


def main():
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    service = DesktopService()
    requests = queue.Queue()

    def reader():
        for line in sys.stdin:
            try:
                requests.put(json.loads(line))
            except json.JSONDecodeError:
                pass
        requests.put(None)

    Thread(target=reader, daemon=True).start()
    try:
        while True:
            timeout = max(0, (service.next_refresh - datetime.now().astimezone()).total_seconds()) if service.monitoring else None
            try:
                request = requests.get(timeout=timeout)
            except queue.Empty:
                try:
                    service.run_refresh()
                except Exception:
                    log.exception("Scheduled refresh failed")
                    service.next_refresh = datetime.now().astimezone() + timedelta(minutes=service.config.refresh_minutes)
                continue
            if request is None:
                break
            request_id = request.get("id")
            try:
                result = service.dispatch(request["method"], request.get("params") or {})
                response = {"id": request_id, "result": result}
            except Exception as exc:
                log.exception("Desktop command failed")
                response = {"id": request_id, "error": str(exc)}
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        service.close()


if __name__ == "__main__":
    main()
