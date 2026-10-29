import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from ai_news_desktop.data import load_mock_news
from ai_news_desktop.models import NewsItem, validate_url
from ai_news_desktop.notifications import WindowsToastNotifier


class NewsTests(unittest.TestCase):
    def test_load_mock_news(self):
        items = load_mock_news()
        self.assertEqual(len(items), 3)
        self.assertEqual(len({item.id for item in items}), 3)
        self.assertTrue(all(item.published_at.tzinfo for item in items))
        self.assertTrue(all(validate_url(item.url) == item.url for item in items))

    def test_url_validation(self):
        self.assertEqual(validate_url("https://example.org/article?id=1"), "https://example.org/article?id=1")
        for value in ("", "file:///tmp/a", "javascript:alert(1)", "https://", "https://a b.org", "https://user:pass@example.org", "https://example.org:bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_url(value)

    def test_news_item_rejects_naive_timestamp(self):
        with self.assertRaises(ValueError):
            NewsItem("1", "title", "summary", "source", "https://example.org", datetime.now())

    def test_windows_notification_interface(self):
        item = NewsItem("1", "Test title", "Summary of a test item", "Source", "https://example.org/story", datetime.now(timezone.utc))
        toasts = []

        class FakeToast:
            def __init__(self, fields, launch_action):
                self.text_fields = fields
                self.launch_action = launch_action
                toasts.append(self)

        fake_toaster = Mock()
        fake_module = types.SimpleNamespace(InteractableWindowsToaster=Mock(return_value=fake_toaster), Toast=FakeToast)
        with patch.dict(sys.modules, {"windows_toasts": fake_module}):
            service = WindowsToastNotifier()
            service.send(item)

        self.assertEqual(toasts[0].text_fields, [item.title, item.summary])
        self.assertEqual(toasts[0].launch_action, item.url)
        self.assertTrue(callable(toasts[0].on_activated))
        toasts[0].on_activated(None)
        fake_toaster.show_toast.assert_called_once_with(toasts[0])


if __name__ == "__main__":
    unittest.main()
