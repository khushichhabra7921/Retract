"""LLM clients: FakeLLM answers, Groq retry/backoff semantics (no network: mocked transport)."""

import json

import httpx
import pytest

from retract.core.errors import RetractError, TransientError
from retract.derive.cache import ResponseCache
from retract.derive.fakes import FakeLLM
from retract.derive.groq import GroqLLM


def test_fake_answer_is_extractive_and_deterministic():
    llm = FakeLLM()
    ctx = ["Priya prefers WhatsApp for follow-ups. Arjun's renewal is due in March."]
    assert llm.answer("How should we follow up with Priya?", ctx) == (
        "Priya prefers WhatsApp for follow-ups."
    )
    assert llm.answer("Anything on zebras?", ctx) == "No relevant information."


def groq(monkeypatch, responses, **kw):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    seen, waits = [], []

    def handler(request):
        seen.append(request)
        return responses.pop(0)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GroqLLM(client=client, sleep=waits.append, **kw), seen, waits


def ok(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": f" {text} "}}]})


def test_groq_retries_429_with_retry_after(monkeypatch):
    llm, seen, waits = groq(
        monkeypatch, [httpx.Response(429, headers={"retry-after": "7"}), ok("hi")]
    )
    assert llm.summarize(["a", "b"]) == "hi"
    assert waits == [7.0] and len(seen) == 2
    assert seen[0].headers["authorization"] == "Bearer test-key"
    assert b"openai/gpt-oss-120b" in seen[0].content


def test_groq_persistent_5xx_is_transient(monkeypatch):
    llm, seen, waits = groq(monkeypatch, [httpx.Response(503)] * 5)
    with pytest.raises(TransientError):
        llm.answer("q", ["c"])
    assert waits == [1.0, 2.0, 4.0, 8.0]  # exponential backoff without Retry-After


def test_groq_auth_error_is_not_transient(monkeypatch):
    llm, _, _ = groq(monkeypatch, [httpx.Response(401)])
    with pytest.raises(RetractError) as e:
        llm.summarize(["a"])
    assert not isinstance(e.value, TransientError) and "test-key" not in str(e.value)


def test_groq_requires_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RetractError, match="GROQ_API_KEY"):
        GroqLLM()


def test_groq_error_shows_groqs_reason(monkeypatch):
    body = {"error": {"message": "The model `x` does not exist", "code": "model_not_found"}}
    llm, _, _ = groq(monkeypatch, [httpx.Response(404, json=body)])
    with pytest.raises(RetractError, match="404: model_not_found: The model `x` does not exist"):
        llm.summarize(["a"])


def test_cache_is_read_through_and_stores_no_secrets(monkeypatch, tmp_path):
    path = tmp_path / "cache.jsonl"
    llm, seen, _ = groq(monkeypatch, [ok("first"), ok("second")], cache=ResponseCache(path))
    assert llm.summarize(["a"]) == "first"
    assert llm.summarize(["a"]) == "first"  # identical request: served from the cache
    assert llm.summarize(["b"]) == "second"
    assert len(seen) == 2 and llm.cache_hits == 1
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [e["response"] for e in entries] == ["first", "second"]
    assert {e["model"] for e in entries} == {"openai/gpt-oss-120b"}
    assert {e["prompt_version"] for e in entries} == {"v1"} and all(e["date"] for e in entries)
    text = path.read_text(encoding="utf-8")
    assert "test-key" not in text and "uthorization" not in text


def test_replay_needs_no_key_and_never_calls_the_network(monkeypatch, tmp_path):
    path = tmp_path / "cache.jsonl"
    live, _, _ = groq(monkeypatch, [ok("cached answer")], cache=ResponseCache(path))
    assert live.answer("q", ["c"]) == "cached answer"

    monkeypatch.delenv("GROQ_API_KEY")
    no_network = httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail("network")))
    replay = GroqLLM(client=no_network, cache=ResponseCache(path), replay=True)
    assert replay.answer("q", ["c"]) == "cached answer"
    with pytest.raises(RetractError, match="replay: no cached response"):
        replay.answer("other question", ["c"])
    with pytest.raises(RetractError, match="needs a response cache"):
        GroqLLM(replay=True)


def test_cache_drops_a_torn_last_line_and_rejects_corruption(tmp_path):
    path = tmp_path / "cache.jsonl"
    good = json.dumps({"key": "k1", "response": "r1"})
    path.write_bytes((good + "\n" + '{"key": "k2", "resp').encode())  # crash mid-append
    cache = ResponseCache(path)
    assert cache.entries == {"k1": "r1"}
    cache.put("k3", "r3", model="m", prompt_version="v1")
    assert ResponseCache(path).entries == {"k1": "r1", "k3": "r3"}  # file is valid again

    path.write_bytes(('{"key": "k2", "resp\n' + good + "\n").encode())
    with pytest.raises(RetractError, match="cache.jsonl:1: corrupt cache entry"):
        ResponseCache(path)


def test_long_rate_limit_fails_fast_instead_of_sleeping(monkeypatch):
    body = {"error": {"code": "rate_limit_exceeded", "message": "Limit 200000 tokens per day"}}
    daily = httpx.Response(429, headers={"retry-after": "3600"}, json=body)
    llm, seen, waits = groq(monkeypatch, [daily])
    with pytest.raises(TransientError, match="retry after 3600 s exceeds max_wait.*tokens per day"):
        llm.summarize(["a"])
    assert len(seen) == 1 and waits == []


def test_token_usage_is_counted_and_cached(monkeypatch, tmp_path):
    usage = {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70, "queue_time": 0.1}
    reply = httpx.Response(200, json={"choices": [{"message": {"content": "x"}}], "usage": usage})
    llm, _, _ = groq(monkeypatch, [reply], cache=ResponseCache(tmp_path / "c.jsonl"))
    llm.summarize(["a"])
    assert llm.tokens == 70
    entry = json.loads((tmp_path / "c.jsonl").read_text(encoding="utf-8"))
    assert entry["usage"] == {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70}
