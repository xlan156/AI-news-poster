"""Small, configurable rules for notification relevance."""

import re

from .config import Config
from .models import NewsItem

_MODEL = re.compile(r"\b(gpt[-\d.]|claude|gemini|deepseek[-\w.]*|model[s]?|v\d+(?:\.\d+)*)\b", re.I)
_RELEASE = re.compile(r"\b(introducing|launch(?:ed|es)?|releas(?:ed|es)?|new|next|frontier|preview|upgrade)\b", re.I)
_API = re.compile(r"\b(api|sdk|endpoint|developer platform|console)\b", re.I)
_API_CHANGE = re.compile(r"\b(new|update|introducing|launch|release|improv|capabilit|token|pricing)\w*\b", re.I)
_RESEARCH = re.compile(r"\b(research|paper|study|breakthrough|benchmark|science|scientific|reasoning|safety|alignment|interpretability)\b", re.I)


def classify(title: str, summary: str, default: str) -> str:
    if _API.search(title) and _API_CHANGE.search(title):
        return "api_update"
    if _MODEL.search(title) and _RELEASE.search(title):
        return "model_release"
    if _RESEARCH.search(title):
        return "research"
    return default


def should_notify(item: NewsItem, config: Config) -> bool:
    text = f"{item.title} {item.summary}".casefold()
    return item.category in config.notification_categories and not any(word.casefold() in text for word in config.exclude_keywords)
