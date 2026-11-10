"""Deterministic offline fakes for tests and experiments."""

import math
import re
import zlib
from collections.abc import Sequence

from retract.core.errors import TransientError


class FakeEmbedder:
    """Hashed bag-of-words embedding: no model, no network, stable across processes."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def __call__(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in re.findall(r"\w+", text.lower()):
            vec[zlib.crc32(token.encode()) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class FakeLLM:
    """Deterministic stand-in for an LLM summarizer: joins its inputs.

    fail_after: raise TransientError once `calls` exceeds it (fault mode).
    inject: text appended to every output (leak- or paraphrase-injection mode).
    """

    def __init__(self) -> None:
        self.calls = 0
        self.fail_after: int | None = None
        self.inject: str | None = None

    def summarize(self, texts: Sequence[str]) -> str:
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise TransientError("fake LLM unavailable")
        out = " ".join(texts)
        return f"{out} {self.inject}" if self.inject else out

    def answer(self, question: str, contexts: Sequence[str]) -> str:
        """Extractive answer: context sentences sharing a content word with the question."""
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise TransientError("fake LLM unavailable")
        words = _content_words(question)
        picked = [
            s
            for c in contexts
            for s in re.split(r"(?<=[.!?])\s+", c)
            if s.strip() and words & _content_words(s)
        ]
        out = " ".join(dict.fromkeys(picked)) or "No relevant information."
        return f"{out} {self.inject}" if self.inject else out


_STOP = frozenset(
    "about and are can does for from has have how its know our should that the this was what"
    " when where which who why will with would you your".split()
)


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3 and w not in _STOP}
