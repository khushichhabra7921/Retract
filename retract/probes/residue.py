"""Retrieval-residue probes, generated locally (no LLM, no network).

Probes are the retracted content itself, so they are held in memory only and never persisted:
receipts carry counts, not probe text.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from retract.core.verification import Embed, forbidden_facts, sentences

if TYPE_CHECKING:
    from retract.core.sdk import Retract

K = 5
STOPWORDS = frozenset(
    "about after also been from have into just more only over said says such than that the"
    " their them then there these they this those very were what when where which while will"
    " with would your".split()
)


def make_probes(texts: Sequence[str]) -> list[str]:
    """Per sentence: the sentence itself, plus a key-token query (up to 5 content words)."""
    probes: list[str] = []
    for text in texts:
        for s in sentences(text):
            probes.append(s)
            words = [w for w in re.findall(r"\w+", s.lower()) if len(w) >= 4 and w not in STOPWORDS]
            if words:
                probes.append(" ".join(dict.fromkeys(words[:5])))
    return list(dict.fromkeys(probes))


def measure(
    rt: Retract,
    probes: Sequence[str],
    retracted: Sequence[str],
    supported: Sequence[str],
    embed: Embed,
) -> dict[str, Any]:
    """Run every probe through guarded `rt.search` and each adapter's raw `search`.

    A hit is a returned item containing a retracted sentence that `supported` does not back
    (the runtime heuristic, see verification.py). Raw hits are split by the lineage status of
    the item: quarantined (expected while quarantined), purged (counts against L2), or
    unexpected (active or untracked items). Counts are probes with at least one hit.
    """

    def leaks(content: str | None) -> bool:
        return bool(content) and bool(forbidden_facts(content or "", retracted, supported, embed))

    counts = dict.fromkeys(("guarded", "raw_purged", "raw_quarantined", "raw_unexpected"), 0)
    for probe in probes:
        found: set[str] = set()
        for tier, store in rt.stores.items():
            if any(leaks(r.content) for r in rt.search(probe, tier=tier, k=K)):
                found.add("guarded")
            raw = [item for item in store.search(probe, K) if leaks(item.content)]
            status = rt.lineage.records([i.metadata.get("record_id", "") for i in raw])
            for item in raw:
                rec = status.get(item.metadata.get("record_id", ""))
                if rec and rec.status == "quarantined":
                    found.add("raw_quarantined")
                elif rec and rec.status in ("retracted", "tombstoned"):
                    found.add("raw_purged")
                else:
                    found.add("raw_unexpected")
        for group in found:
            counts[group] += 1
    failing = counts["guarded"] + counts["raw_purged"] + counts["raw_unexpected"]
    return {
        "status": "fail" if failing else "pass",
        "probes": len(probes),
        **{f"{g}_hits": n for g, n in counts.items()},
    }
