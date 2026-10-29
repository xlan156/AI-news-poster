"""Rule-first, cached, quota-controlled cloud analysis."""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, replace
from importlib.resources import files

from .config import Config, LLMConfig, NewsFilterConfig
from .llm import LLMProvider, ProviderError, make_provider
from .models import NewsItem
from .priorities import should_notify
from .storage import Store

logger = logging.getLogger(__name__)
PROMPT_VERSION = "v1"
CATEGORIES = {"model_release", "research", "api_update", "product_update", "company_news"}


@dataclass(frozen=True)
class Analysis:
    id: str
    chinese_title: str
    chinese_summary: str
    category: str
    relevance_score: int
    importance_score: int
    significance: str

    @classmethod
    def parse(cls, raw: dict) -> "Analysis":
        if not isinstance(raw, dict) or set(raw) != set(cls.__dataclass_fields__):
            raise ValueError("analysis fields do not match schema")
        for key in ("id", "chinese_title", "chinese_summary", "significance"):
            if not isinstance(raw[key], str) or not raw[key].strip() or len(raw[key]) > 500:
                raise ValueError(f"invalid analysis {key}")
        if raw["category"] not in CATEGORIES:
            raise ValueError("invalid analysis category")
        for key in ("relevance_score", "importance_score"):
            if type(raw[key]) is not int or not 0 <= raw[key] <= 100:
                raise ValueError("invalid analysis score")
        if not any("\u4e00" <= char <= "\u9fff" for char in raw["chinese_title"] + raw["chinese_summary"]):
            raise ValueError("Chinese title or summary required")
        return cls(**raw)


def cache_key(item: NewsItem, settings: LLMConfig) -> str:
    content = {"article": item.url, "title": item.title, "summary": item.summary,
               "provider": settings.provider, "model": settings.model, "base_url": settings.base_url,
               "prompt": PROMPT_VERSION, "output_tokens": settings.max_output_tokens}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def notification_item(item: NewsItem, result: Analysis, prefs: NewsFilterConfig) -> NewsItem:
    title = result.chinese_title if prefs.translate_titles else item.title
    return replace(item, title=f"{item.organization or item.source} · {title}", summary=result.chinese_summary[:180], category=result.category)


class Analyzer:
    def __init__(self, config: Config, store: Store, provider: LLMProvider | None = None):
        self.config = config
        self.store = store
        self.settings = config.llm or LLMConfig()
        self.prefs = config.news_filter or NewsFilterConfig()
        self.provider = provider

    def _provider(self) -> LLMProvider:
        if self.provider is None:
            self.provider = make_provider(self.settings)
        return self.provider

    def _budget(self, keys: list[str], estimated_input: int) -> bool:
        settings = self.settings
        usage = self.store.usage_summary(settings.provider, settings.model)
        if usage["requests_today"] >= settings.max_requests_per_day or usage["articles_today"] + len(set(keys) - usage["article_keys_today"]) > settings.max_articles_per_day:
            return False
        if settings.mode == "byok" and settings.monthly_budget_usd is not None:
            if settings.input_usd_per_million is None or settings.output_usd_per_million is None or usage["unpriced_month_requests"]:
                return False
            upper = (estimated_input * settings.input_usd_per_million + settings.max_output_tokens * settings.output_usd_per_million) / 1_000_000
            if usage["estimated_cost_month_usd"] + upper > settings.monthly_budget_usd:
                return False
        return True

    def analyze(self, items: list[NewsItem], *, force: bool = False) -> dict[str, Analysis]:
        if self.settings.mode == "disabled":
            return {}
        results: dict[str, Analysis] = {}
        remaining = []
        for item in items:
            if not should_notify(item, self.config):
                continue
            key = cache_key(item, self.settings)
            cached = None if force or not self.settings.cache_enabled else self.store.cache_get(key)
            if cached:
                try:
                    results[item.id] = Analysis.parse(json.loads(cached))
                    continue
                except (ValueError, TypeError):
                    logger.warning("Invalid cached analysis for %s", item.id)
            remaining.append(item)
        prompt_template = files("ai_news_desktop").joinpath("data/prompt_v1.txt").read_text(encoding="utf-8")
        for start in range(0, len(remaining), self.settings.batch_size):
            batch = remaining[start:start + self.settings.batch_size]
            payload = [{"id": item.id, "organization": item.organization or item.source, "title": item.title,
                        "excerpt": item.summary[:1000], "category": item.category} for item in batch]
            prompt = prompt_template + "\nARTICLES_JSON:\n" + json.dumps(payload, ensure_ascii=False)
            # UTF-8 byte length deliberately overestimates common text token counts.
            estimated_input = len(prompt.encode("utf-8"))
            if estimated_input > self.settings.max_input_tokens:
                logger.warning("LLM batch exceeds conservative input token limit")
                continue
            keys = [item.id for item in batch]
            if not self._budget(keys, estimated_input):
                logger.warning("LLM daily or monthly budget reached; using rules")
                continue
            try:
                provider = self._provider()
            except ValueError as exc:
                logger.warning("LLM provider unavailable: %s", exc)
                continue
            for attempt in range(self.settings.max_retries + 1):
                response = None
                try:
                    response = provider.generate(prompt)
                    data = json.loads(response.text)
                    if not isinstance(data, dict) or set(data) != {"results"} or not isinstance(data["results"], list):
                        raise ValueError("invalid batch envelope")
                    parsed = [Analysis.parse(value) for value in data["results"]]
                    if len(parsed) != len(batch) or {value.id for value in parsed} != set(keys):
                        raise ValueError("batch result ids do not match articles")
                    reported_input = response.input_tokens or estimated_input
                    reported_output = response.output_tokens or self.settings.max_output_tokens
                    cost = None
                    if self.settings.mode == "free" and self.settings.provider == "openrouter":
                        cost = 0.0
                    elif self.settings.input_usd_per_million is not None and self.settings.output_usd_per_million is not None:
                        cost = (reported_input * self.settings.input_usd_per_million + reported_output * self.settings.output_usd_per_million) / 1_000_000
                    self.store.usage_record(self.settings.provider, self.settings.model, response.input_tokens, response.output_tokens, cost, "success", keys)
                    if self.settings.cache_enabled:
                        by_id = {value.id: value for value in parsed}
                        self.store.cache_put_many({cache_key(item, self.settings): json.dumps(by_id[item.id].__dict__, ensure_ascii=False) for item in batch})
                    results.update({value.id: value for value in parsed})
                    break
                except (ProviderError, ValueError, TypeError, KeyError) as exc:
                    failed_cost = None
                    if self.settings.input_usd_per_million is not None and self.settings.output_usd_per_million is not None:
                        failed_cost = ((response.input_tokens if response and response.input_tokens else estimated_input) * self.settings.input_usd_per_million
                                       + (response.output_tokens if response and response.output_tokens else self.settings.max_output_tokens) * self.settings.output_usd_per_million) / 1_000_000
                    self.store.usage_record(self.settings.provider, self.settings.model,
                                            response.input_tokens if response else 0,
                                            response.output_tokens if response else 0, failed_cost, "failed", keys)
                    logger.warning("LLM analysis failed: %s", exc)
                    if not isinstance(exc, ProviderError) or not exc.transient or attempt >= self.settings.max_retries or not self._budget(keys, estimated_input):
                        break
                    if exc.retry_after is not None and exc.retry_after > 30:
                        logger.warning("Provider asks to retry after %.0f seconds; deferring to next run", exc.retry_after)
                        break
                    time.sleep(max(exc.retry_after or 0, 2 ** attempt))
        return results

    def selected(self, item: NewsItem, analysis: Analysis | None) -> NewsItem | None:
        if not should_notify(item, self.config):
            return None
        if analysis is None:
            return item
        if analysis.category not in self.config.notification_categories or analysis.relevance_score < self.prefs.min_relevance_score or analysis.importance_score < self.prefs.min_importance_score:
            return None
        return notification_item(item, analysis, self.prefs)
