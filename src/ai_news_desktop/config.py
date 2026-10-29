"""Source and notification preferences from TOML (legacy JSON is accepted)."""

import json
import os
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .models import validate_url

CATEGORIES = frozenset({"research", "model_release", "api_update", "product_update", "company_news"})


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    enabled: bool = True
    organization: str = ""
    type: str = "rss"
    category: str = "company_news"
    card_tag: str = ""
    card_attribute: str = ""
    card_classes: tuple[str, ...] = ()
    link_prefix: str = ""


Source = Feed


@dataclass(frozen=True)
class Config:
    feeds: tuple[Feed, ...]
    refresh_minutes: int = 60
    max_notifications_per_cycle: int = 3
    max_article_age_hours: int = 48
    notifications_enabled: bool = True
    notification_categories: tuple[str, ...] = ("model_release", "research", "api_update", "product_update")
    exclude_keywords: tuple[str, ...] = ("partnership", "partner", "hiring", "careers", "recruitment", "sponsorship")
    llm: "LLMConfig" = None
    news_filter: "NewsFilterConfig" = None


@dataclass(frozen=True)
class LLMConfig:
    mode: str = "disabled"
    provider: str = "openrouter"
    model: str = "openrouter/free"
    api_key_ref: str = "env:OPENROUTER_API_KEY"
    base_url: str = ""
    request_timeout: float = 20.0
    max_articles_per_day: int = 20
    max_requests_per_day: int = 30
    max_input_tokens: int = 4000
    max_output_tokens: int = 1000
    monthly_budget_usd: float | None = None
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    max_retries: int = 2
    batch_size: int = 3
    cache_enabled: bool = True


@dataclass(frozen=True)
class NewsFilterConfig:
    min_relevance_score: int = 60
    min_importance_score: int = 65
    language: str = "zh-CN"
    translate_titles: bool = True


def load_config(path: Path | None = None) -> Config:
    if path is None:
        local = Path.cwd() / "sources.toml"
        text = local.read_text(encoding="utf-8") if local.is_file() else files("ai_news_desktop").joinpath("data/sources.toml").read_text(encoding="utf-8")
        raw = tomllib.loads(text)
    elif path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
    else:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("sources", raw.get("feeds")), list):
        raise ValueError("configuration requires a sources array")
    sources = []
    new_format = "sources" in raw
    for value in raw.get("sources", raw.get("feeds")):
        if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"].strip():
            raise ValueError("each source requires a name")
        if new_format and any(key not in value for key in ("organization", "type", "url", "enabled", "category")):
            raise ValueError(f"source {value['name']} is missing a required field")
        organization = value.get("organization", value["name"])
        if not isinstance(organization, str) or not organization.strip():
            raise ValueError("source organization must be a nonempty string")
        if type(value.get("enabled", True)) is not bool:
            raise ValueError("source enabled must be boolean")
        source_type = value.get("type", "rss")
        category = value.get("category", "company_news")
        if not isinstance(source_type, str) or not isinstance(category, str) or source_type not in {"rss", "atom", "webpage"} or category not in CATEGORIES:
            raise ValueError(f"invalid type or category for {value['name']}")
        classes = value.get("card_classes", [])
        if not isinstance(classes, list) or not all(isinstance(part, str) for part in classes):
            raise ValueError("card_classes must be an array of strings")
        if source_type == "webpage" and (not value.get("card_tag") or not (value.get("card_attribute") or classes) or not value.get("link_prefix")):
            raise ValueError("webpage sources require card_tag, a card marker, and link_prefix")
        sources.append(Feed(
            value["name"], validate_url(value.get("url")), value.get("enabled", True),
            organization, source_type, category,
            value.get("card_tag", ""), value.get("card_attribute", ""), tuple(classes), value.get("link_prefix", ""),
        ))
    if len({source.url for source in sources}) != len(sources):
        raise ValueError("source URLs must be unique")
    settings = raw.get("settings", raw)
    if not isinstance(settings, dict):
        raise ValueError("settings must be a table")
    integer_names = ("refresh_minutes", "max_notifications_per_cycle", "max_article_age_hours")
    for key in integer_names:
        value = settings.get(key, getattr(Config, key))
        if type(value) is not int or value < (0 if key == "max_notifications_per_cycle" else 1):
            raise ValueError(f"{key} must be a valid integer")
    enabled = settings.get("notifications_enabled", True)
    if type(enabled) is not bool:
        raise ValueError("notifications_enabled must be boolean")
    preferences = raw.get("notifications", {})
    if not isinstance(preferences, dict):
        raise ValueError("notifications must be a table")
    categories = preferences.get("categories", list(Config.notification_categories))
    excluded = preferences.get("exclude_keywords", list(Config.exclude_keywords))
    if not isinstance(categories, list) or any(value not in CATEGORIES for value in categories):
        raise ValueError("invalid notification categories")
    if not isinstance(excluded, list) or not all(isinstance(value, str) for value in excluded):
        raise ValueError("invalid excluded keywords")
    llm_raw = raw.get("llm", {})
    filter_raw = raw.get("news_filter", {})
    if not isinstance(llm_raw, dict) or not isinstance(filter_raw, dict):
        raise ValueError("llm and news_filter must be tables")
    llm_fields = LLMConfig.__dataclass_fields__
    filter_fields = NewsFilterConfig.__dataclass_fields__
    if set(llm_raw) - set(llm_fields) or set(filter_raw) - set(filter_fields):
        raise ValueError("unknown LLM or news_filter setting")
    llm = LLMConfig(**llm_raw)
    news_filter = NewsFilterConfig(**filter_raw)
    if llm.mode not in {"disabled", "free", "byok"} or llm.provider not in {"openai", "anthropic", "deepseek", "gemini", "groq", "openrouter", "compatible"}:
        raise ValueError("invalid LLM mode or provider")
    if llm.mode == "free" and llm.provider not in {"gemini", "groq", "openrouter"}:
        raise ValueError("free mode requires gemini, groq, or openrouter")
    if not llm.model or not llm.api_key_ref.startswith(("env:", "keyring:")) or (llm.base_url and not llm.base_url.startswith("https://")):
        raise ValueError("invalid LLM model, API key reference, or base URL")
    if llm.mode == "free" and llm.provider == "openrouter" and llm.model != "openrouter/free" and not llm.model.endswith(":free"):
        raise ValueError("OpenRouter free mode requires openrouter/free or a :free model")
    for name in ("max_articles_per_day", "max_requests_per_day", "max_input_tokens", "max_output_tokens", "batch_size"):
        if type(getattr(llm, name)) is not int or getattr(llm, name) < 1:
            raise ValueError(f"{name} must be positive")
    if type(llm.max_retries) is not int or llm.max_retries < 0 or llm.max_retries > 5 or not isinstance(llm.request_timeout, (int, float)) or llm.request_timeout <= 0:
        raise ValueError("invalid LLM retry or timeout setting")
    for name in ("monthly_budget_usd", "input_usd_per_million", "output_usd_per_million"):
        value = getattr(llm, name)
        if value is not None and (not isinstance(value, (int, float)) or value < 0):
            raise ValueError(f"{name} must be nonnegative")
    if type(llm.cache_enabled) is not bool or type(news_filter.translate_titles) is not bool or news_filter.language != "zh-CN":
        raise ValueError("invalid cache or language setting")
    if any(type(getattr(news_filter, name)) is not int or not 0 <= getattr(news_filter, name) <= 100 for name in ("min_relevance_score", "min_importance_score")):
        raise ValueError("news_filter scores must be 0–100")
    return Config(tuple(sources), *(settings.get(key, getattr(Config, key)) for key in integer_names), enabled, tuple(categories), tuple(excluded), llm, news_filter)


def default_db_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "AI News Desktop" / "news.sqlite3"
