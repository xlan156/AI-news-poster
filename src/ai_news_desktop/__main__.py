"""Command-line entry point."""

import argparse
import logging
import sys
import time
from pathlib import Path
from threading import Event

from .config import default_db_path, load_config
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI news desktop agent")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--demo", action="store_true", help="show three bundled news notifications")
    action.add_argument("--fetch-once", action="store_true", help="fetch feeds and store articles")
    action.add_argument("--notify-new", action="store_true", help="fetch feeds and notify about new articles")
    action.add_argument("--list-latest", action="store_true", help="list recently stored articles")
    action.add_argument("--run", action="store_true", help="refresh periodically until interrupted")
    action.add_argument("--smoke-labs", action="store_true", help="send one live Anthropic and one DeepSeek toast")
    parser.add_argument("--config", type=Path, help="TOML or legacy JSON configuration file")
    parser.add_argument("--db", type=Path, help="SQLite database path")
    args = parser.parse_args(argv)
    if not any((args.demo, args.fetch_once, args.notify_new, args.list_latest, args.run, args.smoke_labs)):
        parser.print_help()
        return 0

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
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
            store = Store(args.db or default_db_path())
        except (OSError, ValueError) as exc:
            logging.error("Could not start: %s", exc)
            return 1
        try:
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
