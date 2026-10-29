"""Load bundled demo news."""

import json
from datetime import datetime
from importlib.resources import files

from .models import NewsItem


def load_mock_news() -> list[NewsItem]:
    path = files("ai_news_desktop").joinpath("data/mock_news.json")
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("mock news must be a JSON array")
        items = [NewsItem(**{**record, "published_at": datetime.fromisoformat(record["published_at"])}) for record in records]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"could not load mock news: {exc}") from exc
    if len({item.id for item in items}) != len(items):
        raise ValueError("mock news contains duplicate IDs")
    return items
