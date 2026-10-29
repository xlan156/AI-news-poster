"""Notification boundary and Windows toast backend."""

import logging
from typing import Protocol

from .models import NewsItem

logger = logging.getLogger(__name__)


class NotificationService(Protocol):
    def send(self, item: NewsItem) -> None:
        """Deliver one news item."""


class WindowsToastNotifier:
    def __init__(self) -> None:
        from windows_toasts import InteractableWindowsToaster, Toast

        self._toast_type = Toast
        self._toaster = InteractableWindowsToaster("AI News Desktop")

    def send(self, item: NewsItem) -> None:
        # A protocol launch keeps the link clickable after the CLI exits.
        toast = self._toast_type([item.title, item.summary[:180]], launch_action=item.url)
        toast.on_activated = lambda _args: logger.info("Activated news notification: %s", item.id)
        toast.on_failed = lambda args: logger.error("Notification failed for %s: %s", item.id, args)
        self._toaster.show_toast(toast)
        logger.info("Sent notification: %s (%s)", item.title, item.id)
