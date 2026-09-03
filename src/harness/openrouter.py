from __future__ import annotations

import json
from typing import Any

import httpx

from harness.config import Config

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(Exception):
    pass


class OpenRouterClient:
    def __init__(self, config: Config) -> None:
        self.config = config

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.config.openrouter_referer,
            "X-Title": self.config.openrouter_title,
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        tool_choice: str | dict[str, Any] = "auto",
    ) -> dict[str, Any]:
        if not self.config.openrouter_api_key:
            raise OpenRouterError("OPENROUTER_API_KEY is not set")
        body: dict[str, Any] = {
            "model": model or self.config.openrouter_model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(OPENROUTER_URL, headers=self._headers(), json=body)
        except httpx.HTTPError as exc:
            raise OpenRouterError(str(exc)) from exc
        if response.status_code >= 400:
            raise OpenRouterError(f"HTTP {response.status_code}: {response.text[:800]}")
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise OpenRouterError("invalid JSON from OpenRouter") from exc

    def chat_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        try:
            return self.chat(messages, tools=tools, model=self.config.openrouter_model)
        except OpenRouterError:
            fallback = self.config.openrouter_fallback_model
            if not fallback or fallback == self.config.openrouter_model:
                raise
            return self.chat(messages, tools=tools, model=fallback)


def message_from_choice(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices") or []
    if not choices:
        raise OpenRouterError("OpenRouter returned no choices")
    message = choices[0].get("message") or {}
    return message
