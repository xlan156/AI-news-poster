"""Command-line entry point."""

import argparse
import logging
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event

from .config import LLMConfig, NewsFilterConfig, default_db_path, load_config
from .analysis import Analysis, Analyzer, notification_item
from .models import NewsItem
from .data import load_mock_news
from .ingestion import FeedFetcher
from .pipeline import refresh, run_periodically
from .priorities import should_notify
from .storage import Store


def smoke_labs(config, fetcher, notifier) -> bool:
    """Send one relevant live article from each requested lab, without storage."""
    successful = True
    for index, organization in enumerate(("Anthropic", "DeepSeek")):
        sources = [source for source in config.feeds if source.enabled and source.organization.casefold() == organization.casefold()]
        if not sources:
            logging.error("No enabled source for %s", organization)
            successful = False
            continue
        try:
            items = []
            for source in sources:
                items.extend(fetcher.fetch(source))
            ranking = {"model_release": 0, "api_update": 1, "research": 2}
            relevant = [item for item in items if should_notify(item, config)]
            if not relevant:
                raise ValueError("no article matches notification preferences")
            item = sorted(relevant, key=lambda item: (ranking.get(item.category, 3), -item.published_at.timestamp()))[0]
            notifier.send(item)
            logging.info("Smoke test for %s: %s (%s)", organization, item.title, item.url)
        except Exception as exc:
            logging.error("Smoke test failed for %s: %s", organization, exc)
            successful = False
        if index == 0:
            time.sleep(1)
    return successful


def phase3_smoke_items() -> list[NewsItem]:
    """Two repeatable visual examples; no network, LLM, or database access."""
    examples = load_mock_news()
    deepmind = next(item for item in examples if item.id == "deepmind-gemini-deep-think-science")
    sdk = next(item for item in examples if item.id == "github-openai-agents-python-0240")
    analysis = Analysis.parse({
        "id": deepmind.id,
        "chinese_title": "Gemini Deep Think 探索科研问题",
        "chinese_summary": "Google DeepMind 介绍了 Gemini Deep Think 在数学、物理和计算机科学研究任务上的工作。",
        "category": "research",
        "relevance_score": 85,
        "importance_score": 80,
        "significance": "展示模型在科研任务中的应用方向",
    })
    chinese = notification_item(deepmind, analysis, NewsFilterConfig())
    return [replace(chinese, title=f"测试 · {chinese.title}"),
            replace(sdk, title=f"测试 · {sdk.title}")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI news desktop agent")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--demo", action="store_true", help="show three bundled news notifications")
    action.add_argument("--fetch-once", action="store_true", help="fetch feeds and store articles")
    action.add_argument("--notify-new", action="store_true", help="fetch feeds and notify about new articles")
    action.add_argument("--list-latest", action="store_true", help="list recently stored articles")
    action.add_argument("--run", action="store_true", help="refresh periodically until interrupted")
    action.add_argument("--smoke-labs", action="store_true", help="send one live Anthropic and one DeepSeek toast")
    action.add_argument("--smoke-phase3", action="store_true", help="show Chinese and fallback toast examples without API calls")
    action.add_argument("--llm-status", action="store_true", help="show selected LLM mode and provider")
    action.add_argument("--analyze-latest", action="store_true", help="analyze stored recent articles")
    action.add_argument("--preview-summaries", action="store_true", help="preview analysis without notifications")
    action.add_argument("--llm-usage", action="store_true", help="show persisted token usage and estimated cost")
    action.add_argument("--test-llm", action="store_true", help="explicitly test provider on a stored article")
    parser.add_argument("--llm-off", action="store_true", help="disable LLM for this invocation")
    parser.add_argument("--config", type=Path, help="TOML or legacy JSON configuration file")
    parser.add_argument("--db", type=Path, help="SQLite database path")
    args = parser.parse_args(argv)
    if not any((args.demo, args.fetch_once, args.notify_new, args.list_latest, args.run, args.smoke_labs, args.smoke_phase3, args.llm_status, args.analyze_latest, args.preview_summaries, args.llm_usage, args.test_llm)):
        parser.print_help()
        return 0

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.smoke_phase3:
        if sys.platform != "win32":
            logging.error("Native toast smoke test requires Windows")
            return 1
        try:
            from .notifications import WindowsToastNotifier
            notifier = WindowsToastNotifier()
            items = phase3_smoke_items()
            for index, item in enumerate(items):
                notifier.send(item)
                logging.info("Phase 3 smoke %d: %s -> %s", index + 1, item.title, item.url)
                if index < len(items) - 1:
                    time.sleep(3)
            return 0
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            logging.error("Could not run Phase 3 smoke test: %s", exc)
            return 1
    if args.smoke_labs:
        if sys.platform != "win32":
            logging.error("Native toast smoke test requires Windows")
            return 1
        try:
            from .notifications import WindowsToastNotifier
            config = load_config(args.config)
            return 0 if smoke_labs(config, FeedFetcher(), WindowsToastNotifier()) else 1
        except (ImportError, OSError, ValueError) as exc:
            logging.error("Could not start smoke test: %s", exc)
            return 1
    if not args.demo:
        try:
            config = load_config(args.config)
            if args.llm_off:
                config = replace(config, llm=replace(config.llm or LLMConfig(), mode="disabled"))
            if args.llm_status:
                settings = config.llm or LLMConfig()
                print(f"mode={settings.mode} provider={settings.provider} model={settings.model}")
                return 0
            store = Store(args.db or default_db_path())
        except (OSError, ValueError) as exc:
            logging.error("Could not start: %s", exc)
            return 1
        try:
            if args.llm_usage:
                usage = store.usage_summary()
                usage.pop("article_keys_today")
                print(usage)
                return 0
            if args.analyze_latest or args.preview_summaries or args.test_llm:
                rows = store.latest(20)
                items = [NewsItem(row["id"], row["title"], row["summary"], row["source"], row["canonical_url"], datetime.fromisoformat(row["published_at"]), row["organization"], row["category"]) for row in rows]
                if args.test_llm:
                    items = next(([item] for item in items if should_notify(item, config)), [])
                if not items:
                    logging.error("No eligible stored articles; run --fetch-once first")
                    return 1
                analyzer = Analyzer(config, store)
                if args.test_llm and analyzer.settings.mode == "disabled":
                    logging.error("LLM is disabled")
                    return 1
                analyses = analyzer.analyze(items, force=args.test_llm)
                for item in items:
                    result = analyses.get(item.id)
                    if result:
                        print(f"{item.organization}: {result.chinese_title}\n{result.chinese_summary}\n{item.url}\nrelevance={result.relevance_score} importance={result.importance_score}")
                    elif args.preview_summaries:
                        print(f"{item.organization}: {item.title}\n{item.summary}\n{item.url} [rules fallback]")
                return 0 if analyses or not args.test_llm else 1
            if args.list_latest:
                for row in store.latest():
                    print(f"{row['published_at']} | {row['organization'] or row['source']} | {row['category']} | {row['title']}\n  {row['canonical_url']} [{row['status']}]")
                return 0
            fetcher = FeedFetcher()

            def cycle() -> None:
                result = refresh(config, store, fetcher, args.notify_new or args.run)
                logging.info("Refresh: %d feeds OK, %d failed, %d new articles, %d notified", result.feeds_ok, result.feeds_failed, result.articles_added, result.notified)
                if result.feeds_ok == 0:
                    logging.warning("No feeds succeeded in this refresh")

            if args.run:
                logging.info("Refreshing every %d minutes; press Ctrl+C to stop", config.refresh_minutes)
                try:
                    run_periodically(cycle, config.refresh_minutes * 60, Event())
                except KeyboardInterrupt:
                    logging.info("Stopped")
                return 0
            result = refresh(config, store, fetcher, args.notify_new)
            logging.info("Refresh: %d feeds OK, %d failed, %d new articles, %d notified", result.feeds_ok, result.feeds_failed, result.articles_added, result.notified)
            return 0 if result.feeds_ok else 1
        finally:
            store.close()

    if sys.platform != "win32":
        logging.error("Native toast demo requires an interactive Windows session")
        return 1

    try:
        from .notifications import WindowsToastNotifier

        items = load_mock_news()
        notifier = WindowsToastNotifier()
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        logging.error("Could not start demo: %s", exc)
        return 1

    failed = False
    for index, item in enumerate(items):
        try:
            notifier.send(item)
        except Exception:
            logging.exception("Could not send notification for %s", item.id)
            failed = True
        if index < len(items) - 1:
            time.sleep(1)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
