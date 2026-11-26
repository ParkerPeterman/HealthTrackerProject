"""Minimal chat client with JSON-schema constrained output.

Two backends, picked by LLM_PROVIDER: "ollama" (the native /api/chat endpoint) or "openai"
(any OpenAI-compatible /chat/completions server, e.g. llama.cpp, vLLM, LM Studio). Both take the
same messages and schema and return the parsed JSON object. The caller sees two failure kinds:

  LLMUnavailable  the server can't be reached, times out, or answers with an error status
  ValueError      the server answered but the content isn't a JSON object (worth one retry)
"""

from __future__ import annotations

import json

import httpx

from healthtracker.config import get_settings


class LLMUnavailable(RuntimeError):
    pass


def chat_json(system: str, user: str, schema: dict, model: str | None = None,
              temperature: float = 0.2, schema_name: str = "response") -> dict:
    s = get_settings()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    model = model or s.llm_model
    if s.llm_provider == "openai":
        content = _openai_chat(s, messages, schema, model, temperature, schema_name)
    else:
        content = _ollama_chat(s, messages, schema, model, temperature)
    try:
        out = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"model returned invalid JSON: {str(content)[:200]}") from exc
    if not isinstance(out, dict):
        raise ValueError(f"model returned JSON that isn't an object: {str(content)[:200]}")
    return out


def _post(name: str, base: str, path: str, body: dict, timeout: float,
          headers: dict | None = None) -> dict:
    try:
        r = httpx.post(f"{base.rstrip('/')}{path}", json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:  # connection refused, DNS, timeout: all "LLM down"
        raise LLMUnavailable(f"{name} not reachable at {base}: {exc or type(exc).__name__}") from exc
    if r.status_code != 200:
        raise LLMUnavailable(f"{name} returned {r.status_code}: {r.text[:300]}")
    try:
        return r.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} response isn't JSON: {r.text[:200]}") from exc


def _ollama_chat(s, messages: list[dict], schema: dict, model: str, temperature: float) -> str:
    body = {
        "model": model,
        "messages": messages,
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": temperature, "num_ctx": 8192},
    }
    data = _post("Ollama", s.ollama_url, "/api/chat", body, s.llm_timeout_s)
    return (data.get("message") or {}).get("content", "")


def _openai_chat(s, messages: list[dict], schema: dict, model: str, temperature: float,
                 schema_name: str) -> str:
    if not s.llm_base_url:
        raise LLMUnavailable("LLM_PROVIDER=openai but LLM_BASE_URL is not set")
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
    }
    headers = {"Authorization": f"Bearer {s.llm_api_key}"}
    data = _post("LLM server", s.llm_base_url, "/chat/completions", body, s.llm_timeout_s, headers)
    try:
        # Only the answer; any reasoning_content (thinking models) is ignored.
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"LLM server response has no choices[0].message.content: "
                         f"{json.dumps(data)[:200]}") from exc
