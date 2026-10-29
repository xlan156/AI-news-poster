"""Small JSON configuration with checked defaults."""

import json
import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .models import validate_url


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    enabled: bool = True


@dataclass(frozen=True)
class Config:
    feeds: tuple[Feed, ...]
    refresh_minutes: int = 60
    max_notifications_per_cycle: int = 3
    max_article_age_hours: int = 48
    notifications_enabled: bool = True


def load_config(path: Path | None = None) -> Config:
    text = path.read_text(encoding="utf-8") if path else files("ai_news_desktop").joinpath("data/default_config.json").read_text(encoding="utf-8")
    raw = json.loads(text)
    if not isinstance(raw, dict) or not isinstance(raw.get("feeds"), list):
        raise ValueError("configuration requires a feeds array")
    feeds = []
    for value in raw["feeds"]:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"].strip():
            raise ValueError("each feed requires a name")
        if type(value.get("enabled", True)) is not bool:
            raise ValueError("feed enabled must be boolean")
        feeds.append(Feed(value["name"], validate_url(value.get("url")), value.get("enabled", True)))
    if len({feed.url for feed in feeds}) != len(feeds):
        raise ValueError("feed URLs must be unique")
    integers = ("refresh_minutes", "max_notifications_per_cycle", "max_article_age_hours")
    for key in integers:
        value = raw.get(key, getattr(Config, key))
        if type(value) is not int or value < (0 if key == "max_notifications_per_cycle" else 1):
            raise ValueError(f"{key} must be a valid integer")
    if type(raw.get("notifications_enabled", True)) is not bool:
        raise ValueError("notifications_enabled must be boolean")
    return Config(tuple(feeds), *(raw.get(key, getattr(Config, key)) for key in integers), raw.get("notifications_enabled", True))


def default_db_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "AI News Desktop" / "news.sqlite3"
