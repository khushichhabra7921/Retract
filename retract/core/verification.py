"""Runtime post-repair check: a conservative heuristic, not an oracle.

A *forbidden fact* is a sentence of retracted content that appears in a replacement but is not
supported by any of the replacement's valid inputs. Two rules, either one flags the sentence:

1. Similarity: a normalized sentence-substring or embedding match at a frozen threshold, unless an
   input holds a matching sentence too.
2. Novel tokens: the retracted sentence's distinctive tokens (names, codes, numbers, content
   words) that appear in no input are information only the retracted source supplied. A
   replacement sentence carrying `novel_tokens` of them (or all, if fewer exist) is flagged. This
   catches paraphrases that keep identifying details, which rule 1's bag-of-words embedding
   misses, and it is not fooled by an input that only looks alike (RP-30 for RP-90).

Uncertain cases are treated as hits, so the replacement is quarantined.
"""

import re
from collections.abc import Callable, Sequence

# Tuned on dev seeds 0-9 with FakeEmbedder (experiments/results/tuning.json): 0.75-0.95 tie and the
# pre-set rule takes the lowest. Re-tune if the embedder changes.
SIMILARITY_THRESHOLD = 0.75
# Tuned on dev seeds 0-9 (experiments/results/tuning_novel.json); 0 disables rule 2.
NOVEL_TOKENS = 2

Embed = Callable[[str], list[float]]

_STOP = frozenset(
    "about above after again also and any are because been before being but can could did does"
    " each for from had has have her here his how into its just may more most not now our out"
    " over own same she should some such than that the their them then there these they this"
    " those through too under very was were what when where which while who why will with would"
    " you your".split()
)


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def distinctive(text: str) -> set[str]:
    """Content tokens: 3+ letters or containing a digit, minus common function words."""
    return {
        t
        for t in re.findall(r"\w+", text.lower())
        if (len(t) >= 3 or any(c.isdigit() for c in t)) and t not in _STOP
    }


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


def _carries_novel(
    sentence: str, cand_tokens: Sequence[set[str]], sup_tokens: set[str], need: int
) -> bool:
    novel = distinctive(sentence) - sup_tokens
    if need <= 0 or not novel:
        return False
    return any(len(c & novel) >= min(need, len(novel)) for c in cand_tokens)


def forbidden_facts(
    candidate: str,
    retracted: Sequence[str],
    supported: Sequence[str],
    embed: Embed,
    threshold: float = SIMILARITY_THRESHOLD,
    novel_tokens: int = NOVEL_TOKENS,
) -> list[str]:
    """Retracted sentences present in `candidate` and absent from `supported`.

    The result is retracted content: keep it in memory, never persist or log it."""
    cand = sentences(candidate)
    cand_joined = " ".join(normalize(s) for s in cand)
    cand_tokens = [distinctive(s) for s in cand]
    sup = [s for t in supported for s in sentences(t)]
    sup_joined = " ".join(normalize(s) for s in sup)
    sup_tokens = distinctive(" ".join(supported))
    return [
        s
        for t in retracted
        for s in sentences(t)
        if (
            _near(s, cand, cand_joined, embed, threshold)
            and not _near(s, sup, sup_joined, embed, threshold)
        )
        or _carries_novel(s, cand_tokens, sup_tokens, novel_tokens)
    ]
