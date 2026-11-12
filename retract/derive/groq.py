"""Groq chat client (extra `llm`). Key from GROQ_API_KEY only; never logged or stored.

Retryable failures (timeouts, connection errors, 429, 5xx) are retried with Retry-After or
exponential backoff and finally raise TransientError, so a retraction fails resumably instead of
quarantining records. Other HTTP errors raise RetractError.

With a ResponseCache, every request is looked up first and every live response is stored.
`replay=True` serves from the cache only, needs no key, and raises RetractError on a miss.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence

import httpx

from retract.core.errors import RetractError, TransientError
from retract.derive import prompts
from retract.derive.cache import ResponseCache, request_key

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"  # llama-3.3-70b-versatile was shut down on 08/16/26


class GroqLLM:
    def __init__(
        self,
        model: str = MODEL,
        *,
        timeout: float = 30.0,
        max_retries: int = 4,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        cache: ResponseCache | None = None,
        replay: bool = False,
        max_wait: float = 120.0,
    ) -> None:
        if replay and cache is None:
            raise RetractError("replay needs a response cache")
        key = os.environ.get("GROQ_API_KEY")
        if not key and not replay:
            raise RetractError("GROQ_API_KEY is not set in the process environment")
        self._headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.model = model
        self.max_retries = max_retries
        self.client = client or httpx.Client(timeout=timeout)
        self.sleep = sleep
        self.cache = cache
        self.replay = replay
        self.calls = 0  # HTTP attempts, retries included
        self.cache_hits = 0
        self.requests = 0  # summarize/answer calls, whether served live or from the cache
        self.tokens = 0  # total_tokens Groq reported for live responses
        self.max_wait = max_wait  # a longer Retry-After (e.g. a daily limit) fails instead

    def summarize(self, texts: Sequence[str]) -> str:
        return self._chat(prompts.SUMMARIZE, "\n".join(f"- {t}" for t in texts))

    def answer(self, question: str, contexts: Sequence[str]) -> str:
        ctx = "\n".join(f"- {c}" for c in contexts) or "(empty)"
        return self._chat(prompts.ANSWER, f"Context:\n{ctx}\n\nQuestion: {question}")

    def _chat(self, system: str, user: str) -> str:
        self.requests += 1
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        key = request_key(body, prompts.PROMPT_VERSION) if self.cache else ""
        if self.cache:
            cached = self.cache.get(key)
            if cached is not None:
                self.cache_hits += 1
                return cached
            if self.replay:
                raise RetractError(f"replay: no cached response for this {self.model} request")
        text, usage = self._post(body)
        if self.cache:
            self.cache.put(
                key, text, model=self.model, prompt_version=prompts.PROMPT_VERSION, usage=usage
            )
        return text

    def _post(self, body: dict[str, object]) -> tuple[str, dict[str, int]]:
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            self.calls += 1
            try:
                r = self.client.post(URL, headers=self._headers, json=body)
            except httpx.TransportError as e:
                if last:
                    raise TransientError(f"groq unreachable: {type(e).__name__}") from e
                self.sleep(2.0**attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if last:
                    raise TransientError(f"groq HTTP {r.status_code} after retries")
                wait = _retry_after(r) or 2.0**attempt
                if wait > self.max_wait:
                    raise TransientError(
                        f"groq HTTP {r.status_code}: retry after {wait:.0f} s exceeds max_wait;"
                        f" {_error_detail(r)}"
                    )
                self.sleep(wait)
                continue
            if r.status_code >= 400:
                raise RetractError(f"groq HTTP {r.status_code}: {_error_detail(r)}")
            data = r.json()
            usage = {
                k: int(v)
                for k, v in (data.get("usage") or {}).items()
                if k in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
            self.tokens += usage.get("total_tokens", 0)
            return str(data["choices"][0]["message"]["content"]).strip(), usage
        raise AssertionError("unreachable")


def _error_detail(r: httpx.Response) -> str:
    """Groq's own error code and message (never contains the key), e.g. model_not_found."""
    try:
        err = r.json()["error"]
        return f"{err.get('code') or err.get('type')}: {str(err.get('message', ''))[:200]}"
    except (ValueError, KeyError, TypeError, AttributeError):
        return r.text[:200] or "no details"


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return float(r.headers["retry-after"])
    except (KeyError, ValueError):
        return None
