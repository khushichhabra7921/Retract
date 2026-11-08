"""Deterministic offline fakes for tests and experiments."""

import math
import re
import zlib


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
