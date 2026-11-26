"""Both LLM backends (Ollama, OpenAI-compatible), mocked at the HTTP layer.

Whatever the provider, the same things must hold: the request carries only the system prompt and
the payload, a timeout or connection error is "LLM down" (no retry, template used), invalid JSON
and guard failures get one retry, and after that the deterministic template is used with the
reasons kept in llm_check.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import httpx
import pytest

from healthtracker.config import Settings
from healthtracker.llm import client
from healthtracker.llm import phrase as phrase_mod
from healthtracker.llm.client import LLMUnavailable, chat_json

STATUS = {"recovery": {"hrv_ms": 48, "hrv_vs_baseline_sd": -1.4, "sleep_last_night": "6h45"}}
ACTION_TEXT = "Keep today easy: nothing above RPE 6, since you had a game yesterday"
PAYLOAD = {"date": "2026-10-08", "status": STATUS, "actions": [{"id": 11, "text": ACTION_TEXT}]}
ACTIONS = [{"id": 11, "action_text": ACTION_TEXT, "target_value": 6, "target_value_high": None}]
GOOD = {"summary": "HRV is 48 ms, 1.4 SD below your baseline, after 6h45 of sleep.",
        "items": [{"action_id": 11, "message": "Go easy today and stay at RPE 6 or below after yesterday's game."}]}
BAD = {"summary": "HRV is 48 ms, so you should rest.",  # advice in the summary
       "items": [{"action_id": 11, "message": "Stay at RPE 6 or below, about 35 minutes."}]}

PROVIDERS = {
    "ollama": dict(llm_provider="ollama", ollama_url="http://ollama.test:11434"),
    "openai": dict(llm_provider="openai", llm_base_url="http://llm.test:9292/v1/",
                   llm_api_key="sk-test"),
}


@pytest.fixture(params=sorted(PROVIDERS))
def provider(request, monkeypatch) -> str:
    s = Settings(_env_file=None, llm_model="test-model", llm_timeout_s=7, **PROVIDERS[request.param])
    monkeypatch.setattr(client, "get_settings", lambda: s)
    monkeypatch.setattr(phrase_mod, "get_settings", lambda: s)
    return request.param


def reply(provider: str, content: str | None, **extra) -> httpx.Response:
    if provider == "ollama":
        return httpx.Response(200, json={"model": "test-model", "message": {"role": "assistant",
                                                                           "content": content}})
    msg = {"role": "assistant", "content": content, **extra}
    return httpx.Response(200, json={"choices": [{"index": 0, "message": msg}]})


class FakeServer:
    """Stands in for httpx.post: records each request and plays back responses in order."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "body": json, "headers": headers, "timeout": timeout})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def serve(monkeypatch, *responses) -> FakeServer:
    fake = FakeServer(*responses)
    monkeypatch.setattr(client.httpx, "post", fake)
    return fake


# ------------------------------------------------------------------ request and response shape
def test_ollama_request_shape(provider, monkeypatch):
    if provider != "ollama":
        pytest.skip("ollama only")
    srv = serve(monkeypatch, reply(provider, json.dumps(GOOD)))
    assert chat_json("SYS", "USER", phrase_mod.OUTPUT_SCHEMA, temperature=0.0) == GOOD
    call = srv.calls[0]
    assert call["url"] == "http://ollama.test:11434/api/chat" and call["timeout"] == 7
    body = call["body"]
    assert body["format"] == phrase_mod.OUTPUT_SCHEMA and body["model"] == "test-model"
    assert body["options"]["temperature"] == 0.0 and body["think"] is False
    assert "response_format" not in body


def test_openai_request_shape(provider, monkeypatch):
    if provider != "openai":
        pytest.skip("openai only")
    srv = serve(monkeypatch, reply(provider, json.dumps(GOOD)))
    assert chat_json("SYS", "USER", phrase_mod.OUTPUT_SCHEMA, temperature=0.0) == GOOD
    call = srv.calls[0]
    assert call["url"] == "http://llm.test:9292/v1/chat/completions" and call["timeout"] == 7
    assert call["headers"] == {"Authorization": "Bearer sk-test"}
    body = call["body"]
    assert body["model"] == "test-model" and body["temperature"] == 0.0
    assert body["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "response", "schema": phrase_mod.OUTPUT_SCHEMA, "strict": True}}
    assert "format" not in body


def test_the_request_carries_only_the_prompt_and_the_payload(provider, monkeypatch):
    srv = serve(monkeypatch, reply(provider, json.dumps(GOOD)))
    chat_json("SYS", json.dumps(PAYLOAD), phrase_mod.OUTPUT_SCHEMA)
    assert srv.calls[0]["body"]["messages"] == [{"role": "system", "content": "SYS"},
                                                {"role": "user", "content": json.dumps(PAYLOAD)}]


def test_api_key_defaults_to_none(monkeypatch):
    s = Settings(_env_file=None, llm_provider="openai", llm_base_url="http://llm.test/v1")
    monkeypatch.setattr(client, "get_settings", lambda: s)
    srv = serve(monkeypatch, reply("openai", json.dumps(GOOD)))
    chat_json("SYS", "USER", {})
    assert srv.calls[0]["headers"] == {"Authorization": "Bearer none"} and s.llm_timeout_s == 120


def test_openai_reasoning_content_is_ignored(provider, monkeypatch):
    if provider != "openai":
        pytest.skip("openai only")
    serve(monkeypatch, reply(provider, json.dumps(GOOD), reasoning_content="Let me think... 35 g"))
    assert chat_json("SYS", "USER", {}) == GOOD


def test_openai_without_base_url_is_llm_down(monkeypatch):
    monkeypatch.setattr(client, "get_settings", lambda: Settings(_env_file=None, llm_provider="openai"))
    srv = serve(monkeypatch)
    with pytest.raises(LLMUnavailable, match="LLM_BASE_URL"):
        chat_json("SYS", "USER", {})
    assert srv.calls == []


# ------------------------------------------------------------------ failure handling
@pytest.mark.parametrize("exc", [httpx.ReadTimeout("timed out"), httpx.ConnectError("refused")])
def test_timeout_or_connection_error_is_llm_down_without_retry(provider, monkeypatch, exc):
    srv = serve(monkeypatch, exc, reply(provider, json.dumps(GOOD)))
    result = phrase_mod._phrase_group(PAYLOAD, ACTIONS)
    assert result["output"] is None and len(srv.calls) == 1
    assert result["check"]["passed"] is False and "not reachable" in result["check"]["problems"][0]


def test_error_status_is_llm_down(provider, monkeypatch):
    serve(monkeypatch, httpx.Response(503, text="loading model"))
    result = phrase_mod._phrase_group(PAYLOAD, ACTIONS)
    assert result["output"] is None and "returned 503" in result["check"]["problems"][0]


@pytest.mark.parametrize("content", ["not json {", None, "[1, 2]"])
def test_invalid_json_twice_falls_back(provider, monkeypatch, content):
    srv = serve(monkeypatch, reply(provider, content), reply(provider, content))
    result = phrase_mod._phrase_group(PAYLOAD, ACTIONS)
    assert result["output"] is None and len(srv.calls) == 2
    assert all("JSON" in t["problems"][0] for t in result["check"]["attempts"])


def test_invalid_json_gets_one_retry_at_temperature_zero(provider, monkeypatch):
    srv = serve(monkeypatch, reply(provider, "oops"), reply(provider, json.dumps(GOOD)))
    result = phrase_mod._phrase_group(PAYLOAD, ACTIONS)
    assert result["output"] == GOOD and result["check"] == {"passed": True, "attempts": 2}
    temps = [c["body"].get("temperature", c["body"].get("options", {}).get("temperature"))
             for c in srv.calls]
    assert temps == [0.2, 0.0]


def test_guard_failure_twice_falls_back(provider, monkeypatch):
    srv = serve(monkeypatch, reply(provider, json.dumps(BAD)), reply(provider, json.dumps(BAD)))
    result = phrase_mod._phrase_group(PAYLOAD, ACTIONS)
    assert result["output"] is None and len(srv.calls) == 2
    assert all(t["output"] == BAD and t["problems"] for t in result["check"]["attempts"])


# ------------------------------------------------------------------ end to end through phrase()
def _fake_db(monkeypatch) -> list:
    rec = {"id": 1, "date": date(2026, 10, 8), "input_snapshot": {"status": STATUS},
           "decisions": {"fired": [{"rule_id": "easy", "domain": "training", "visible": True,
                                    "description": "Game yesterday", "severity": 2}]}}
    actions = [{**ACTIONS[0], "rule_id": "easy", "metric": "rpe"}]
    writes: list = []
    monkeypatch.setattr(phrase_mod, "fetch_one", lambda conn, sql, params: rec)
    monkeypatch.setattr(phrase_mod, "fetch_all", lambda conn, sql, params: actions)
    monkeypatch.setattr(phrase_mod, "load_profile", lambda conn: SimpleNamespace(display_units="metric"))
    monkeypatch.setattr(phrase_mod, "execute", lambda conn, sql, params: writes.append(params))
    return writes


@pytest.mark.parametrize("failure", ["timeout", "invalid_json", "guard"])
def test_phrase_uses_the_template_when_the_llm_fails(provider, monkeypatch, failure):
    writes = _fake_db(monkeypatch)
    bad = {"timeout": [httpx.ReadTimeout("timed out")],
           "invalid_json": [reply(provider, "nope"), reply(provider, "nope")],
           "guard": [reply(provider, json.dumps(BAD)), reply(provider, json.dumps(BAD))]}[failure]
    serve(monkeypatch, *bad)
    text = phrase_mod.phrase(None, date(2026, 10, 8), use_llm=True)
    rendered, model, _, output_check, llm_check, _ = writes[0]
    check = json.loads(llm_check)
    assert output_check == "failed_fallback_used" and check["mode"] == "template_fallback"
    assert check["groups"]["training"]["passed"] is False
    assert text == rendered and f"- {ACTION_TEXT}" in text and "Recovery: HRV 48 ms" in text


def test_phrase_uses_the_llm_when_it_passes(provider, monkeypatch):
    writes = _fake_db(monkeypatch)
    nutrition = {"summary": "Yesterday's food log isn't marked complete.", "items": []}
    serve(monkeypatch, reply(provider, json.dumps(GOOD)), reply(provider, json.dumps(nutrition)))
    text = phrase_mod.phrase(None, date(2026, 10, 8), use_llm=True)
    _, _, _, output_check, llm_check, _ = writes[0]
    assert output_check == "passed" and json.loads(llm_check)["mode"] == "llm"
    assert GOOD["items"][0]["message"] in text
