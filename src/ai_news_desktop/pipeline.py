"""One refresh cycle, shared by the CLI and scheduler."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event
from typing import Callable

from .config import Config
from .analysis import Analyzer
from .ingestion import FeedFetcher
from .notifications import NotificationService
from .priorities import should_notify
from .storage import Store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RefreshResult:
    feeds_ok: int
    feeds_failed: int
    articles_added: int
    notified: int


def refresh(
    config: Config,
    store: Store,
    fetcher: FeedFetcher,
    notify: bool = False,
    notifier_factory: Callable[[], NotificationService] | None = None,
) -> RefreshResult:
    ok = failed = added = sent = 0
    for feed in config.feeds:
        if not feed.enabled:
            continue
        try:
            items = fetcher.fetch(feed)
            added_here = store.save_feed(feed.url, items)
            logger.info("%s: %d entries, %d new", feed.name, len(items), added_here)
            added += added_here
            ok += 1
        except Exception as exc:
            logger.warning("Feed failed (%s): %s", feed.name, exc)
            failed += 1

    if notify and config.notifications_enabled and config.max_notifications_per_cycle:
        since = datetime.now(timezone.utc) - timedelta(hours=config.max_article_age_hours)
        candidates = [item for item in store.pending(since, 10000) if should_notify(item, config)]
        analyzer = Analyzer(config, store)
        analyses = analyzer.analyze(candidates[:max(config.max_notifications_per_cycle * 3, 10)])
        pending = [(item, analyzer.selected(item, analyses.get(item.id))) for item in candidates]
        pending = [(original, selected) for original, selected in pending if selected]
        order = {"model_release": 0, "research": 1, "api_update": 2, "product_update": 3, "company_news": 4}
        pending.sort(key=lambda pair: (order.get(pair[1].category, 4),
                                       -(analyses[pair[0].id].importance_score if pair[0].id in analyses else 0),
                                       -pair[0].published_at.timestamp()))
        pending = pending[:config.max_notifications_per_cycle]
        if pending:
            try:
                if notifier_factory is None:
                    from .notifications import WindowsToastNotifier
                    notifier_factory = WindowsToastNotifier
                notifier = notifier_factory()
            except Exception as exc:
                logger.error("Notification backend unavailable: %s", exc)
                return RefreshResult(ok, failed, added, 0)
            for index, (original, item) in enumerate(pending):
                try:
                    notifier.send(item)
                    store.mark_notified(original.id)
                    sent += 1
                except Exception as exc:
                    logger.error("Notification failed for %s: %s", item.id, exc)
                if index < len(pending) - 1:
                    time.sleep(1)
    return RefreshResult(ok, failed, added, sent)


def run_periodically(cycle: Callable[[], None], interval_seconds: float, stop: Event) -> None:
    while not stop.is_set():
        cycle()
        if stop.wait(interval_seconds):
            break
