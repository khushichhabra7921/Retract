"""Groq chat client (extra `llm`). Key from GROQ_API_KEY only; never logged or stored.

Retryable failures (timeouts, connection errors, 429, 5xx) are retried with Retry-After or
exponential backoff and finally raise TransientError, so a retraction fails resumably instead of
quarantining records. Other HTTP errors raise RetractError.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence

import httpx

from retract.core.errors import RetractError, TransientError
from retract.derive import prompts

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "llama-3.3-70b-versatile"


class GroqLLM:
    def __init__(
        self,
        model: str = MODEL,
        *,
        timeout: float = 30.0,
        max_retries: int = 4,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise RetractError("GROQ_API_KEY is not set in the process environment")
        self._headers = {"Authorization": f"Bearer {key}"}
        self.model = model
        self.max_retries = max_retries
        self.client = client or httpx.Client(timeout=timeout)
        self.sleep = sleep
        self.calls = 0

    def summarize(self, texts: Sequence[str]) -> str:
        return self._chat(prompts.SUMMARIZE, "\n".join(f"- {t}" for t in texts))

    def answer(self, question: str, contexts: Sequence[str]) -> str:
        ctx = "\n".join(f"- {c}" for c in contexts) or "(empty)"
        return self._chat(prompts.ANSWER, f"Context:\n{ctx}\n\nQuestion: {question}")

    def _chat(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
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
                self.sleep(_retry_after(r) or 2.0**attempt)
                continue
            if r.status_code >= 400:
                raise RetractError(f"groq HTTP {r.status_code}: {_error_detail(r)}")
            return str(r.json()["choices"][0]["message"]["content"]).strip()
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
