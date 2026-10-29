"""Validated news item data."""

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit


def validate_url(value: str) -> str:
    if not isinstance(value, str) or not value or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("url must be a nonempty HTTP(S) URL without whitespace")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port != 0
        )
    except ValueError as exc:
        raise ValueError(f"invalid news URL: {value!r}") from exc
    if not valid:
        raise ValueError(f"invalid news URL: {value!r}")
    return value


@dataclass(frozen=True)
class NewsItem:
    id: str
    title: str
    summary: str
    source: str
    url: str
    published_at: datetime
    organization: str = ""
    category: str = "company_news"

    def __post_init__(self) -> None:
        for field in ("id", "title", "summary", "source"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a nonempty string")
        validate_url(self.url)
        if not isinstance(self.published_at, datetime) or self.published_at.tzinfo is None:
            raise ValueError("published_at must be a timezone-aware datetime")
        if not isinstance(self.organization, str) or (self.organization and not self.organization.strip()):
            raise ValueError("organization must be a string")
        if self.category not in {"research", "model_release", "api_update", "product_update", "company_news"}:
            raise ValueError("invalid news category")
