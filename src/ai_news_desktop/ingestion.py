"""Fetch RSS/Atom and turn valid entries into NewsItem values."""

import hashlib
import html
import logging
import re
from calendar import timegm
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from typing import Protocol

import feedparser
import httpx

from .config import Feed
from .models import NewsItem, validate_url
from .priorities import classify

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
            items.append(NewsItem(identifier, title, summary, feed.name, url, published, feed.organization or feed.name, classify(title, summary, feed.category)))
        except (ValueError, TypeError, OverflowError) as exc:
            logger.warning("Skipping malformed entry from %s: %s", feed.name, exc)
    return items


class SourceAdapter(Protocol):
    def fetch(self, source: Feed) -> list[NewsItem]: ...


class RSSAdapter:
    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def fetch(self, feed: Feed) -> list[NewsItem]:
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "ai-news-desktop/0.2 (+RSS reader)"}) as client:
            response = client.get(feed.url)
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"unexpected HTTP {response.status_code} from {feed.name}")
            return parse_feed(response.content, feed)


class _CardParser(HTMLParser):
    def __init__(self, source: Feed) -> None:
        super().__init__()
        self.source = source
        self.cards: list[tuple[str, str, str, str]] = []
        self.depth = 0
        self.stack: list[str] = []
        self.href = ""
        self.title_href = ""
        self.anchor_href = ""
        self.titles: list[str] = []
        self.paragraphs: list[str] = []
        self.words: list[str] = []
        self.capture_title: list[str] | None = None
        self.capture_paragraph: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if not self.depth:
            classes = set((attributes.get("class") or "").split())
            matches = tag == self.source.card_tag and (
                (self.source.card_attribute and self.source.card_attribute in attributes)
                or bool(classes.intersection(self.source.card_classes))
            )
            if not matches:
                return
            self.depth = 1
            self.stack = [tag]
            self.href = ""
            self.title_href = ""
            self.anchor_href = ""
            self.titles = []
            self.paragraphs = []
            self.words = []
        else:
            self.depth += 1
            self.stack.append(tag)
        if tag == "a" and not self.href:
            href = attributes.get("href") or ""
            if href.startswith(self.source.link_prefix):
                self.href = href
        if tag == "a":
            self.anchor_href = attributes.get("href") or ""
        if tag in {"h1", "h2", "h3"}:
            self.capture_title = []
        elif tag == "p":
            self.capture_paragraph = []

    def handle_data(self, data: str) -> None:
        if not self.depth:
            return
        self.words.append(data)
        if self.capture_title is not None:
            self.capture_title.append(data)
        if self.capture_paragraph is not None:
            self.capture_paragraph.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.depth:
            return
        if tag in {"h1", "h2", "h3"} and self.capture_title is not None:
            if not self.titles and self.anchor_href:
                self.title_href = self.anchor_href
            self.titles.append(_plain_text(" ".join(self.capture_title)))
            self.capture_title = None
        elif tag == "p" and self.capture_paragraph is not None:
            self.paragraphs.append(_plain_text(" ".join(self.capture_paragraph)))
            self.capture_paragraph = None
        if tag == "a":
            self.anchor_href = ""
        self.depth -= 1
        if self.stack:
            self.stack.pop()
        if self.depth == 0:
            body = " ".join(self.words)
            date = re.search(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b", body)
            summary = next((text for text in self.paragraphs if len(text) >= 25 and not re.match(r"^(?:News|Research)\b", text)), "")
            self.cards.append((self.title_href or self.href, self.titles[0] if self.titles else "", summary, date.group(0) if date else ""))


def parse_webpage(content: str, source: Feed) -> list[NewsItem]:
    parser = _CardParser(source)
    parser.feed(content)
    if not parser.cards:
        raise ValueError(f"no article cards matched {source.name}; page structure may have changed")
    items = []
    for href, title, summary, date in parser.cards:
        try:
            if not href or not title or not date:
                raise ValueError("missing article link, title, or date")
            url = canonical_url(urljoin(source.url, href))
            if urlsplit(url).hostname != urlsplit(source.url).hostname:
                raise ValueError("article URL leaves source domain")
            published = None
            for fmt in ("%b %d, %Y", "%B %d, %Y"):
                try:
                    published = datetime.strptime(date, fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    pass
            if published is None:
                raise ValueError("invalid publication date")
            identifier = hashlib.sha256(url.encode("utf-8")).hexdigest()
            items.append(NewsItem(identifier, title, summary or "Read the original article.", source.name, url, published, source.organization or source.name, classify(title, summary, source.category)))
        except ValueError as exc:
            logger.warning("Skipping malformed webpage card from %s: %s", source.name, exc)
    if not items:
        raise ValueError(f"no usable articles found on {source.name}")
    return items


class WebpageAdapter:
    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def fetch(self, source: Feed) -> list[NewsItem]:
        parts = urlsplit(source.url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "ai-news-desktop/0.2 (+RSS reader)"}) as client:
            robots = client.get(robots_url)
            robots.raise_for_status()
            if robots.status_code != 200:
                raise ValueError(f"unexpected HTTP {robots.status_code} from robots.txt")
            rules = RobotFileParser()
            rules.parse(robots.text.splitlines())
            if not rules.can_fetch("ai-news-desktop", source.url):
                raise PermissionError(f"robots.txt disallows {source.url}")
            response = client.get(source.url)
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"unexpected HTTP {response.status_code} from {source.name}; page may be temporarily unavailable")
            return parse_webpage(response.text, source)


class FeedFetcher:
    """Compatibility facade for the Phase 2 pipeline."""

    def __init__(self, timeout: float = 15.0) -> None:
        self.rss = RSSAdapter(timeout)
        self.webpage = WebpageAdapter(timeout)

    def fetch(self, source: Feed) -> list[NewsItem]:
        adapter: SourceAdapter = self.webpage if source.type == "webpage" else self.rss
        return adapter.fetch(source)
