"""Runtime post-repair check: a conservative heuristic, not an oracle.

A *forbidden fact* is a sentence of retracted content that appears in a replacement but is not
supported by any of the replacement's valid inputs. Matching is normalized sentence-substring
plus embedding similarity at a frozen threshold. Uncertain cases are treated as hits, so the
replacement is quarantined.
"""

import re
from collections.abc import Callable, Sequence

# Tuned on dev seeds 0-9 with FakeEmbedder (experiments/results/tuning.json): 0.75-0.95 tie and the
# pre-set rule takes the lowest. Re-tune if the embedder changes.
SIMILARITY_THRESHOLD = 0.75

Embed = Callable[[str], list[float]]


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _near(
    sentence: str, others: Sequence[str], joined: str, embed: Embed, threshold: float
) -> bool:
    n = normalize(sentence)
    if not n:
        return False
    if f" {n} " in f" {joined} ":
        return True
    v = embed(sentence)
    return any(sum(a * b for a, b in zip(v, embed(o), strict=True)) >= threshold for o in others)


def forbidden_facts(
    candidate: str,
    retracted: Sequence[str],
    supported: Sequence[str],
    embed: Embed,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[str]:
    """Retracted sentences present in `candidate` and absent from `supported`.

    The result is retracted content: keep it in memory, never persist or log it."""
    cand = sentences(candidate)
    cand_joined = " ".join(normalize(s) for s in cand)
    sup = [s for t in supported for s in sentences(t)]
    sup_joined = " ".join(normalize(s) for s in sup)
    return [
        s
        for t in retracted
        for s in sentences(t)
        if _near(s, cand, cand_joined, embed, threshold)
        and not _near(s, sup, sup_joined, embed, threshold)
    ]
