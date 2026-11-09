"""Synthetic testbed facts and the fact-level oracle (experiments only; the SDK never uses it).

shortcut: hand-written 3-user fact set for the M1 Priya slice, upgrade to 20 seeded personas in M5.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Fact:
    fact_id: str
    owner: str  # user id; the owning source is src.user(owner)
    text: str
    marker: str  # distinctive token present in the text and in both paraphrases
    paraphrases: tuple[str, str]


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


def facts_in(text: str) -> set[str]:
    """Oracle: fact_ids whose marker occurs in `text` as a whole word, case-insensitive."""
    return {
        f.fact_id for f in FACTS if re.search(rf"\b{re.escape(f.marker)}\b", text, re.IGNORECASE)
    }


def owned_by(owner: str) -> set[str]:
    return {f.fact_id for f in FACTS if f.owner == owner}


def confusion(runtime_flagged: bool, oracle_leaked: bool) -> str:
    """Label one runtime decision against the oracle: TP, FP, FN or TN."""
    return {(True, True): "TP", (True, False): "FP", (False, True): "FN", (False, False): "TN"}[
        (runtime_flagged, oracle_leaked)
    ]
