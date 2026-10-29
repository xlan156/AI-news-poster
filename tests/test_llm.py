import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from ai_news_desktop.analysis import Analysis, Analyzer, cache_key
from ai_news_desktop.config import Config, LLMConfig, NewsFilterConfig, load_config
from ai_news_desktop.llm import HTTPProvider, LLMResponse, ProviderError, resolve_key
from ai_news_desktop.models import NewsItem
from ai_news_desktop.pipeline import refresh
from ai_news_desktop.priorities import classify
from ai_news_desktop.storage import Store
from ai_news_desktop.__main__ import main, phase3_smoke_items


def item(suffix="one"):
    return NewsItem(suffix, "Introducing Claude 5", "New model with improved coding performance.", "Anthropic", f"https://example.org/{suffix}", datetime.now(timezone.utc), "Anthropic", "model_release")


def output(items):
    return json.dumps({"results": [{"id": value.id, "chinese_title": "推出 Claude 5", "chinese_summary": "发布新的 Claude 5 模型，编码能力有所提升。", "category": "model_release", "relevance_score": 85, "importance_score": 90, "significance": "模型更新"} for value in items]}, ensure_ascii=False)


class LLMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.store = Store(Path(self.temp.name) / "news.sqlite3")
        self.settings = LLMConfig(mode="free", provider="openrouter", model="openrouter/free", max_input_tokens=10000)
        self.config = Config((), llm=self.settings)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_disabled_never_initializes_provider(self):
        with patch("ai_news_desktop.analysis.make_provider", side_effect=AssertionError("called")):
            self.assertEqual(Analyzer(Config((), llm=replace(self.settings, mode="disabled")), self.store).analyze([item()]), {})

    def test_free_mode_cache_persists_after_restart_and_content_invalidates(self):
        provider = Mock(generate=Mock(return_value=LLMResponse(output([item()]), 100, 40)))
        first = Analyzer(self.config, self.store, provider).analyze([item()])
        self.assertEqual(first["one"].chinese_title, "推出 Claude 5")
        self.store.close()
        self.store = Store(Path(self.temp.name) / "news.sqlite3")
        self.assertEqual(Analyzer(self.config, self.store, provider).analyze([item()]), first)
        provider.generate.assert_called_once()
        changed = replace(item(), summary="Changed technical excerpt")
        self.assertNotEqual(cache_key(item(), self.settings), cache_key(changed, self.settings))
        self.assertEqual(self.store.usage_summary()["requests_today"], 1)
        self.assertEqual(self.store.usage_summary()["input_tokens_today"], 100)

    def test_provider_and_prompt_change_invalidate_cache(self):
        a = cache_key(item(), self.settings)
        self.assertNotEqual(a, cache_key(item(), replace(self.settings, model="other:free")))
        self.assertNotEqual(a, cache_key(item(), replace(self.settings, max_output_tokens=200)))

    def test_schema_validation_and_chinese_requirement(self):
        valid = json.loads(output([item()]))["results"][0]
        self.assertEqual(Analysis.parse(valid).importance_score, 90)
        for change in ({"importance_score": True}, {"category": "fiction"}, {"chinese_summary": "English only", "chinese_title": "English"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                Analysis.parse({**valid, **change})

    def test_major_product_rule(self):
        self.assertEqual(classify("Launch Claude app update", "", "company_news"), "product_update")

    def test_phase3_visual_smoke_uses_original_links(self):
        examples = phase3_smoke_items()
        self.assertEqual(len(examples), 2)
        self.assertIn("Gemini Deep Think", examples[0].title)
        self.assertIn("研究任务", examples[0].summary)
        self.assertTrue(examples[0].url.startswith("https://deepmind.google/"))
        self.assertIn("OpenAI Agents Python", examples[1].title)
        self.assertTrue(examples[1].url.startswith("https://github.com/openai/"))

    def test_phase3_smoke_cli_sends_two_without_provider(self):
        with patch("ai_news_desktop.__main__.sys.platform", "win32"), \
             patch("ai_news_desktop.notifications.WindowsToastNotifier") as notifier_type, \
             patch("ai_news_desktop.__main__.time.sleep") as sleep, \
             patch("ai_news_desktop.analysis.make_provider", side_effect=AssertionError("LLM called")):
            self.assertEqual(main(["--smoke-phase3"]), 0)
        self.assertEqual(notifier_type.return_value.send.call_count, 2)
        sleep.assert_called_once_with(3)

    def test_malformed_batch_falls_back_without_partial_cache(self):
        items = [item("one"), item("two")]
        bad = json.loads(output(items))
        bad["results"][1]["id"] = "wrong"
        provider = Mock(generate=Mock(return_value=LLMResponse(json.dumps(bad), 50, 20)))
        self.assertEqual(Analyzer(self.config, self.store, provider).analyze(items), {})
        self.assertIsNone(self.store.cache_get(cache_key(items[0], self.settings)))
        self.assertEqual(self.store.usage_summary()["requests_today"], 1)

    def test_batch_mapping_and_notification_display(self):
        items = [item("one"), item("two")]
        response = json.loads(output(items))
        response["results"].reverse()
        provider = Mock(generate=Mock(return_value=LLMResponse(json.dumps(response, ensure_ascii=False), 50, 20)))
        analyzer = Analyzer(self.config, self.store, provider)
        result = analyzer.analyze(items)
        self.assertEqual(set(result), {"one", "two"})
        display = analyzer.selected(items[0], result["one"])
        self.assertTrue(display.title.startswith("Anthropic · 推出"))
        self.assertEqual(display.url, items[0].url)
        original = Analyzer(replace(self.config, news_filter=NewsFilterConfig(translate_titles=False)), self.store).selected(items[0], result["one"])
        self.assertIn("Introducing Claude 5", original.title)
        self.assertIsNone(Analyzer(replace(self.config, news_filter=NewsFilterConfig(min_importance_score=95)), self.store).selected(items[0], result["one"]))

    def test_daily_request_and_article_limits(self):
        provider = Mock(generate=Mock(return_value=LLMResponse(output([item()]), 10, 10)))
        limit = replace(self.settings, max_requests_per_day=1)
        Analyzer(replace(self.config, llm=limit), self.store, provider).analyze([item()])
        self.assertEqual(Analyzer(replace(self.config, llm=limit), self.store, provider).analyze([item("two")]), {})
        provider.generate.assert_called_once()
        article_limit = replace(self.settings, max_articles_per_day=1)
        self.assertEqual(Analyzer(replace(self.config, llm=article_limit), self.store, provider).analyze([item("three")]), {})
        provider.generate.assert_called_once()

    def test_paid_budget_fails_closed_without_prices(self):
        paid = replace(self.settings, mode="byok", provider="openai", model="user-model", monthly_budget_usd=1.0)
        provider = Mock()
        self.assertEqual(Analyzer(replace(self.config, llm=paid), self.store, provider).analyze([item()]), {})
        provider.generate.assert_not_called()
        priced = replace(paid, input_usd_per_million=1000, output_usd_per_million=1000)
        self.assertEqual(Analyzer(replace(self.config, llm=priced), self.store, provider).analyze([item()]), {})
        provider.generate.assert_not_called()

    def test_paid_usage_and_monthly_limit_survive_restart(self):
        paid = replace(self.settings, mode="byok", provider="openai", model="user-model",
                       monthly_budget_usd=1.0, input_usd_per_million=100.0,
                       output_usd_per_million=100.0, max_output_tokens=100)
        provider = Mock(generate=Mock(return_value=LLMResponse(output([item()]), 100, 100)))
        config = replace(self.config, llm=paid)
        self.assertIn("one", Analyzer(config, self.store, provider).analyze([item()]))
        self.assertAlmostEqual(self.store.usage_summary()["estimated_cost_month_usd"], 0.02)
        self.store.usage_record("openai", "user-model", 0, 0, 0.97, "success", ["other"])
        self.store.close()
        self.store = Store(Path(self.temp.name) / "news.sqlite3")
        self.assertEqual(Analyzer(config, self.store, provider).analyze([item("two")]), {})
        provider.generate.assert_called_once()

    def test_retry_transient_only_and_retry_after(self):
        provider = Mock()
        provider.generate.side_effect = [ProviderError("rate limit", transient=True, retry_after=3), LLMResponse(output([item()]), 20, 10)]
        with patch("ai_news_desktop.analysis.time.sleep") as sleep:
            self.assertIn("one", Analyzer(self.config, self.store, provider).analyze([item()]))
        sleep.assert_called_once_with(3)
        self.assertEqual(self.store.usage_summary()["requests_today"], 2)
        permanent = Mock(generate=Mock(side_effect=ProviderError("HTTP 401")))
        self.assertEqual(Analyzer(self.config, self.store, permanent).analyze([item("two")]), {})
        permanent.generate.assert_called_once()
        deferred = Mock(generate=Mock(side_effect=ProviderError("rate limit", transient=True, retry_after=60)))
        with patch("ai_news_desktop.analysis.time.sleep") as sleep:
            self.assertEqual(Analyzer(self.config, self.store, deferred).analyze([item("three")]), {})
        sleep.assert_not_called()
        deferred.generate.assert_called_once()

    def test_missing_key_and_config_switch(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                resolve_key("env:MISSING_KEY")
        config_path = Path(self.temp.name) / "sources.toml"
        config_path.write_text('''[llm]\nmode="byok"\nprovider="anthropic"\nmodel="user-model"\napi_key_ref="env:ANTHROPIC_API_KEY"\n[[sources]]\nname="Lab"\norganization="Lab"\ntype="rss"\nurl="https://example.org/rss"\nenabled=true\ncategory="research"\n''', encoding="utf-8")
        self.assertEqual(load_config(config_path).llm.provider, "anthropic")

    def test_provider_http_formats_without_live_calls(self):
        cases = [
            ("openai", {"choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": 2, "completion_tokens": 3}}, "/chat/completions"),
            ("anthropic", {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 2, "output_tokens": 3}}, "/messages"),
            ("gemini", {"candidates": [{"content": {"parts": [{"text": "{}"}]}}], "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3}}, ":generateContent"),
        ]
        for name, body, suffix in cases:
            with self.subTest(name=name), patch("ai_news_desktop.llm.httpx.Client") as client:
                client.return_value.__enter__.return_value.post.return_value = httpx.Response(200, json=body, request=httpx.Request("POST", "https://example.org"))
                result = HTTPProvider(replace(self.settings, provider=name, model="test-model"), "secret").generate("prompt")
                self.assertEqual((result.input_tokens, result.output_tokens), (2, 3))
                self.assertTrue(client.return_value.__enter__.return_value.post.call_args.args[0].endswith(suffix))

    def test_notification_dedup_after_analysis(self):
        source = replace(self.config, feeds=())
        self.store.save_feed("https://example.org/feed", [])
        self.store.save_feed("https://example.org/feed", [item()])
        fetcher = Mock(fetch=Mock(return_value=[]))
        notifier = Mock()
        provider = Mock(generate=Mock(return_value=LLMResponse(output([item()]), 10, 10)))
        with patch("ai_news_desktop.analysis.make_provider", return_value=provider):
            refresh(source, self.store, fetcher, True, lambda: notifier)
            refresh(source, self.store, fetcher, True, lambda: notifier)
        notifier.send.assert_called_once()
        self.assertEqual(self.store.latest()[0]["status"], "notified")


if __name__ == "__main__":
    unittest.main()
