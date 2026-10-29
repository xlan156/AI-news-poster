"""Small HTTP adapters for cloud text models. No client is made in disabled mode."""

import os
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Protocol

import httpx

from .config import LLMConfig


DEFAULT_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "anthropic": "https://api.anthropic.com/v1",
}


class ProviderError(Exception):
    def __init__(self, message: str, *, transient: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.transient = transient
        self.retry_after = retry_after


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int


class LLMProvider(Protocol):
    def generate(self, prompt: str) -> LLMResponse: ...


def resolve_key(reference: str) -> str:
    if reference.startswith("env:"):
        key = os.environ.get(reference[4:], "")
    elif reference.startswith("keyring:"):
        try:
            import keyring
        except ImportError as exc:
            raise ValueError("keyring support requires pip install ai-news-desktop[secrets]") from exc
        parts = reference.split(":", 2)
        if len(parts) != 3 or not all(parts[1:]):
            raise ValueError("keyring reference must be keyring:service:user")
        key = keyring.get_password(parts[1], parts[2]) or ""
    else:
        raise ValueError("API key must be referenced through env: or keyring:")
    if not key.strip():
        raise ValueError("API key reference is empty or unavailable")
    return key.strip()


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None


class HTTPProvider:
    def __init__(self, settings: LLMConfig, api_key: str):
        self.settings = settings
        self.api_key = api_key
        self.base_url = (settings.base_url or DEFAULT_URLS.get(settings.provider, "")).rstrip("/")
        if not self.base_url.startswith("https://"):
            raise ValueError("provider requires an HTTPS base URL")

    def generate(self, prompt: str) -> LLMResponse:
        provider = self.settings.provider
        headers = {"Content-Type": "application/json"}
        if provider == "gemini":
            headers["x-goog-api-key"] = self.api_key
            url = f"{self.base_url}/models/{self.settings.model}:generateContent"
            body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": self.settings.max_output_tokens, "responseMimeType": "application/json"}}
        elif provider == "anthropic":
            headers.update({"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
            url = f"{self.base_url}/messages"
            body = {"model": self.settings.model, "max_tokens": self.settings.max_output_tokens, "messages": [{"role": "user", "content": prompt}]}
        else:
            headers["Authorization"] = f"Bearer {self.api_key}"
            url = f"{self.base_url}/chat/completions"
            token_field = "max_completion_tokens" if provider == "openai" else "max_tokens"
            body = {"model": self.settings.model, token_field: self.settings.max_output_tokens, "messages": [{"role": "user", "content": prompt}]}
        try:
            with httpx.Client(timeout=self.settings.request_timeout) as client:
                response = client.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError("provider network error", transient=True) from exc
        if response.status_code >= 400:
            raise ProviderError(f"provider HTTP {response.status_code}", transient=response.status_code in {408, 409, 429} or response.status_code >= 500, retry_after=_retry_after(response.headers.get("Retry-After")))
        try:
            payload = response.json()
            if provider == "gemini":
                text = payload["candidates"][0]["content"]["parts"][0]["text"]
                usage = payload.get("usageMetadata", {})
                input_tokens, output_tokens = usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)
            elif provider == "anthropic":
                text = "".join(part["text"] for part in payload["content"] if part.get("type") == "text")
                usage = payload.get("usage", {})
                input_tokens, output_tokens = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
            else:
                text = payload["choices"][0]["message"]["content"]
                usage = payload.get("usage", {})
                input_tokens, output_tokens = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty model output")
            input_tokens, output_tokens = int(input_tokens), int(output_tokens)
            if input_tokens < 0 or output_tokens < 0:
                raise ValueError("negative usage")
            return LLMResponse(text, input_tokens, output_tokens)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("provider returned an invalid response") from exc


def make_provider(settings: LLMConfig) -> LLMProvider:
    return HTTPProvider(settings, resolve_key(settings.api_key_ref))
