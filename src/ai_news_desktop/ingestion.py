"""Fetch RSS/Atom and turn valid entries into NewsItem values."""

import hashlib
import html
import logging
import re
from calendar import timegm
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from .config import Feed
from .models import NewsItem, validate_url

logger = logging.getLogger(__name__)
_TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str) -> str:
    parser = _Text()
    parser.feed(value)
    return re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()


def canonical_url(url: str) -> str:
    validate_url(url)
    parts = urlsplit(url)
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    netloc = host if port is None or (parts.scheme == "https" and port == 443) or (parts.scheme == "http" and port == 80) else f"{host}:{port}"
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in _TRACKING))
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", query, ""))


def parse_feed(content: bytes, feed: Feed) -> list[NewsItem]:
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"malformed feed: {feed.name}: {parsed.bozo_exception}")
    items = []
    for entry in parsed.entries:
        try:
            title = _plain_text(entry.get("title", ""))
            url = canonical_url(entry.get("link", ""))
            stamp = entry.get("published_parsed") or entry.get("updated_parsed")
            if not title or not stamp:
                raise ValueError("missing title or publication date")
            published = datetime.fromtimestamp(timegm(stamp), timezone.utc)
            summary = _plain_text(entry.get("summary") or entry.get("description") or "")[:500] or "Read the original article."
            identifier = hashlib.sha256(url.encode("utf-8")).hexdigest()
            items.append(NewsItem(identifier, title, summary, feed.name, url, published))
        except (ValueError, TypeError, OverflowError) as exc:
            logger.warning("Skipping malformed entry from %s: %s", feed.name, exc)
    return items


class FeedFetcher:
    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def fetch(self, feed: Feed) -> list[NewsItem]:
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "ai-news-desktop/0.2 (+RSS reader)"}) as client:
            response = client.get(feed.url)
            response.raise_for_status()
            return parse_feed(response.content, feed)
