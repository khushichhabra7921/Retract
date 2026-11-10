"""LLM clients: FakeLLM answers, Groq retry/backoff semantics (no network: mocked transport)."""

import httpx
import pytest

from retract.core.errors import RetractError, TransientError
from retract.derive.fakes import FakeLLM
from retract.derive.groq import GroqLLM


def test_fake_answer_is_extractive_and_deterministic():
    llm = FakeLLM()
    ctx = ["Priya prefers WhatsApp for follow-ups. Arjun's renewal is due in March."]
    assert llm.answer("How should we follow up with Priya?", ctx) == (
        "Priya prefers WhatsApp for follow-ups."
    )
    assert llm.answer("Anything on zebras?", ctx) == "No relevant information."


def groq(monkeypatch, responses):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    seen, waits = [], []

    def handler(request):
        seen.append(request)
        return responses.pop(0)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GroqLLM(client=client, sleep=waits.append), seen, waits


def ok(text):
    return httpx.Response(200, json={"choices": [{"message": {"content": f" {text} "}}]})


def test_groq_retries_429_with_retry_after(monkeypatch):
    llm, seen, waits = groq(
        monkeypatch, [httpx.Response(429, headers={"retry-after": "7"}), ok("hi")]
    )
    assert llm.summarize(["a", "b"]) == "hi"
    assert waits == [7.0] and len(seen) == 2
    assert seen[0].headers["authorization"] == "Bearer test-key"
    assert b"llama-3.3-70b-versatile" in seen[0].content


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
