"""Minimal Ollama chat client with JSON-schema constrained output."""

from __future__ import annotations

import json

import httpx

from healthtracker.config import get_settings


class LLMUnavailable(RuntimeError):
    pass


def chat_json(system: str, user: str, schema: dict, model: str | None = None,
              temperature: float = 0.2) -> dict:
    s = get_settings()
    body = {
        "model": model or s.llm_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": temperature, "num_ctx": 8192},
    }
    try:
        r = httpx.post(f"{s.ollama_url.rstrip('/')}/api/chat", json=body, timeout=s.llm_timeout_s)
    except httpx.HTTPError as exc:
        raise LLMUnavailable(f"Ollama not reachable at {s.ollama_url}: {exc}") from exc
    if r.status_code != 200:
        raise LLMUnavailable(f"Ollama returned {r.status_code}: {r.text[:300]}")
    content = r.json().get("message", {}).get("content", "")
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model returned invalid JSON: {content[:200]}") from exc
