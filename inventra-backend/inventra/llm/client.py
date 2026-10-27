"""OpenRouter client (OpenAI-compatible chat completions). Optional: no key means keyless mode.

Data minimisation: only the engine's fact set, or the typed question plus the lists of
valid IDs, is ever sent. The API key is read from .env and never logged or returned.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

import httpx

from ..config import settings

log = logging.getLogger("inventra.llm")


class LLMUnavailable(Exception):
    pass


def _message(body: dict, timeout: float, model: Optional[str] = None) -> dict:
    if not settings.keyed:
        raise LLMUnavailable("keyless mode")
    model = model or settings.llm_model
    route = {"model": model, "temperature": 0}
    backups = [m for m in [settings.llm_model] + settings.llm_fallback_models if m != model]
    if settings.llm_fallback_models and backups:  # OpenRouter tries these in order if the first model fails
        route["models"] = [model] + backups
    try:
        for attempt in (1, 2):
            r = httpx.post(f"{settings.llm_base_url}/chat/completions", json={**route, **body},
                           headers={"Authorization": f"Bearer {settings.llm_api_key}", "X-Title": "Inventra"}, timeout=timeout)
            if attempt == 1 and r.status_code in (429, 502, 503):  # a shared upstream pool is briefly busy: try once more
                time.sleep(3)
                continue
            break
        r.raise_for_status()
        return r.json()["choices"][0]["message"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        # never include the request (and so the key) in logs: only the status and the provider's error message
        why = type(e).__name__
        if isinstance(e, httpx.HTTPStatusError):
            try:
                why = f"HTTP {e.response.status_code}: {e.response.json()['error'].get('message', '')}"[:200]
            except (ValueError, KeyError, AttributeError):
                why = f"HTTP {e.response.status_code}"
        log.warning("LLM call failed: %s", why)
        raise LLMUnavailable(why)


def chat(messages: List[dict], max_tokens: int = 400, json_mode: bool = False) -> str:
    body = {"messages": messages, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return _message(body, settings.llm_timeout_seconds).get("content") or ""


def chat_tools(messages: List[dict], tools: List[dict], max_tokens: int = 800, model: Optional[str] = None) -> dict:
    """One agent turn: returns the assistant message, which may carry `tool_calls`. `model` overrides LLM_MODEL."""
    return _message({"messages": messages, "tools": tools, "tool_choice": "auto", "max_tokens": max_tokens},
                    max(settings.llm_timeout_seconds, 30), model)
