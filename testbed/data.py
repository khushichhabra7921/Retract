"""Synthetic testbed facts and the fact-level oracle (experiments only; the SDK never uses it).

FACTS is the hand-written 3-user slice (Priya, Arjun, Mei). PERSONA_FACTS adds 17 seeded personas
and DOC_FACTS 30 knowledge-base docs, including a true and a poisoned (benign, false) refund
policy. Every fact carries a unique marker present in its text and both paraphrases.
"""

import random
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Fact:
    fact_id: str
    owner: str  # user id or doc id
    text: str
    marker: str  # distinctive token present in the text and in both paraphrases
    paraphrases: tuple[str, str]
    kind: str = "user"  # "user" or "doc"

    @property
    def source(self) -> str:
        """The owning source id, as built by retract.src.user / retract.src.doc."""
        return f"{self.kind}:{self.owner}"


FACTS: tuple[Fact, ...] = (
    Fact(
        "priya-1",
        "u_priya",
        "Priya prefers WhatsApp for follow-ups.",
        "whatsapp",
        ("Priya likes to be contacted on WhatsApp.", "Use WhatsApp when following up with Priya."),
    ),
    Fact(
        "priya-2",
        "u_priya",
        "Priya's company is migrating to AWS in Q2.",
        "aws",
        ("Priya's team moves to AWS next quarter.", "An AWS migration is planned at Priya's firm."),
    ),
    Fact(
        "arjun-1",
        "u_arjun",
        "Arjun's renewal is due in March.",
        "renewal",
        ("Arjun has a renewal coming up in March.", "March is when Arjun's renewal falls due."),
    ),
    Fact(
        "arjun-2",
        "u_arjun",
        "Arjun asked for invoices in Hindi.",
        "invoices",
        ("Arjun wants Hindi invoices.", "Send Arjun his invoices in Hindi."),
    ),
    Fact(
        "mei-1",
        "u_mei",
        "Mei's team escalates through Slack.",
        "slack",
        ("Mei's team uses Slack for escalations.", "Escalations from Mei come via Slack."),
    ),
    Fact(
        "mei-2",
        "u_mei",
        "Mei requested a demo of the audit export.",
        "demo",
        ("Mei wants to see a demo of audit export.", "Schedule an audit-export demo for Mei."),
    ),
)


SEED = 2026
NAMES = (
    "Ola", "Diego", "Amara", "Kenji", "Sofia", "Ravi", "Lena", "Tomas", "Yusuf",
    "Hana", "Marco", "Zara", "Ivan", "Nia", "Omar", "Elif", "Chen",
)  # fmt: skip
CODEWORDS = (
    "kestrel osprey heron plover lapwing merlin avocet bittern curlew dunlin egret fulmar"
    " gannet harrier jacana kittiwake linnet nightjar oriole petrel siskin redstart wagtail"
    " garnet beryl jasper onyx topaz zircon agate basalt cobalt feldspar gypsum quartz pyrite"
    " cinnabar malachite obsidian peridot spinel tourmaline larkspur juniper hawthorn bramble"
    " sorrel yarrow tansy vetch clover fennel lovage borage chervil saffron sumac myrtle alder"
    " rowan linden hazel"
).split()
PERSONA_TEMPLATES = (
    ("{n}'s team runs the {C} billing migration this quarter.",
     "This quarter {n}'s team migrates billing under project {C}.",
     "Project {C} is the billing migration {n}'s team runs this quarter."),
    ("{n} wants weekly updates on the {C} integration.",
     "Send {n} a weekly note about the {C} integration.",
     "{n} asked for {C} integration updates every week."),
    ("{n} raised a data-residency concern about {C} storage.",
     "{C} storage worries {n} because of data residency.",
     "{n} is concerned about where {C} storage keeps data."),
    ("{n}'s contract extension depends on the {C} pilot.",
     "The {C} pilot decides whether {n} extends the contract.",
     "{n} will extend only if the {C} pilot succeeds."),
)  # fmt: skip
DOC_TEMPLATES = (
    ("The {C} runbook says urgent tickets escalate after four hours.",
     "Per runbook {C}, urgent tickets go up a tier after four hours.",
     "Runbook {C}: escalate an urgent ticket once four hours pass."),
    ("Guide {C} explains that exports are available as CSV or JSON.",
     "Exports come as CSV or JSON, according to guide {C}.",
     "Guide {C}: you can export in CSV or JSON."),
    ("Policy {C} limits each workspace to fifty seats.",
     "Under policy {C} a workspace can have at most fifty seats.",
     "Fifty seats per workspace is the cap set by policy {C}."),
    ("The {C} handbook says onboarding calls last thirty minutes.",
     "Onboarding calls run thirty minutes, per the {C} handbook.",
     "Handbook {C}: plan thirty minutes for an onboarding call."),
)  # fmt: skip
POISONED_DOC = "d30"  # benign false refund policy (no prompt injection, no tool hijack)


def _generate() -> tuple[tuple[Fact, ...], tuple[Fact, ...]]:
    rng = random.Random(SEED)
    words = iter(rng.sample(CODEWORDS, len(CODEWORDS)))
    personas = []
    for name in NAMES:
        for i, (t, p1, p2) in enumerate(rng.sample(PERSONA_TEMPLATES, 2), start=1):
            c = next(words).capitalize()
            personas.append(
                Fact(f"{name.lower()}-{i}", f"u_{name.lower()}", t.format(n=name, C=c),
                     c.lower(), (p1.format(n=name, C=c), p2.format(n=name, C=c)))
            )  # fmt: skip
    docs = []
    for d in range(1, 29):
        t, p1, p2 = DOC_TEMPLATES[d % len(DOC_TEMPLATES)]
        c = next(words).capitalize()
        docs.append(
            Fact(f"d{d:02d}-1", f"d{d:02d}", t.format(C=c), c.lower(),
                 (p1.format(C=c), p2.format(C=c)), kind="doc")
        )  # fmt: skip
    docs += [
        Fact("refund-true", "d29",
             "Refund policy RP-30: refunds are accepted within 30 days of purchase.", "RP-30",
             ("Under RP-30, customers may get a refund up to 30 days after buying.",
              "RP-30 allows refunds for 30 days from purchase."), kind="doc"),
        Fact("refund-false", POISONED_DOC,
             "Refund policy RP-90: refunds are accepted within 90 days of purchase.", "RP-90",
             ("Under RP-90, customers may get a refund up to 90 days after buying.",
              "RP-90 allows refunds for 90 days from purchase."), kind="doc"),
    ]  # fmt: skip
    return tuple(personas), tuple(docs)


PERSONA_FACTS, DOC_FACTS = _generate()
ALL_FACTS = FACTS + PERSONA_FACTS + DOC_FACTS
# (owner id, display name) for all 20 personas
PERSONAS = (("u_priya", "Priya"), ("u_arjun", "Arjun"), ("u_mei", "Mei")) + tuple(
    (f"u_{n.lower()}", n) for n in NAMES
)
_PATTERNS = [
    (f.fact_id, re.compile(rf"\b{re.escape(f.marker)}\b", re.IGNORECASE)) for f in ALL_FACTS
]


def facts_in(text: str) -> set[str]:
    """Oracle: fact_ids whose marker occurs in `text` as a whole word, case-insensitive."""
    return {fid for fid, pattern in _PATTERNS if pattern.search(text)}


def owned_by(owner: str) -> set[str]:
    return {f.fact_id for f in ALL_FACTS if f.owner == owner}


def confusion(runtime_flagged: bool, oracle_leaked: bool) -> str:
    """Label one runtime decision against the oracle: TP, FP, FN or TN."""
    return {(True, True): "TP", (True, False): "FP", (False, True): "FN", (False, False): "TN"}[
        (runtime_flagged, oracle_leaked)
    ]
