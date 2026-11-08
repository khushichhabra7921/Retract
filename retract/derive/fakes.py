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
