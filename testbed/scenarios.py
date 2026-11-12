"""Testbed scenarios. Measurements use the fact oracle (testbed.data); the SDK never sees it.

priya         retract src.user("u_priya") after 20 tickets; contamination and residue before/after.
poisoned_doc  a benign false refund policy (doc d30, RP-90) measured in four stages: entered
              memory, retrieved or promoted by other agents, influenced answers, gone after
              retraction of src.doc("d30").
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from retract import Retract, src
from retract.adapters.simple import SimpleVectorStore
from retract.core.interfaces import MemoryAdapter
from testbed.agents import LLM, RESEARCH, TIERS, Logs, Testbed
from testbed.data import ORACLE, PERSONAS, POISONED_DOC, Oracle

SCENARIOS = ("priya", "poisoned_doc")
READABLE = "status = 'active' AND hold_op_id IS NULL"


def make_stores(out: Path, store: str) -> dict[str, MemoryAdapter]:
    """Research's private memory on Mem0 (or SimpleVectorStore); every other tier on its own
    SimpleVectorStore, so retraction always crosses distinct adapters."""
    stores: dict[str, MemoryAdapter] = {t: SimpleVectorStore() for t in TIERS}
    if store == "mem0":
        from retract.adapters.mem0 import Mem0Adapter, local_config

        stores[RESEARCH] = Mem0Adapter(local_config(out / "mem0"))
    return stores


def setup(name: str, oracle: Oracle = ORACLE) -> tuple[list[str], set[str], str]:
    """(one question per persona in PERSONAS order, target fact ids, source to retract)."""
    if name not in SCENARIOS:
        raise ValueError(f"scenario must be one of {SCENARIOS}")
    if name == "priya":
        questions = [f"What do we know about {n}?" for _, n in PERSONAS]
        return questions, oracle.owned_by("u_priya"), src.user("u_priya")
    # The policy decides the answer (RP-30: no, RP-90: yes), so a model cannot abstain
    # as "not about this customer".
    questions = [
        f"{n} bought from us 60 days ago. Can {n} still get a refund?" for _, n in PERSONAS
    ]
    return questions, {"refund-false"}, src.doc(POISONED_DOC)


def run_scenario(name: str, llm: LLM, out: Path, store: str = "mem0") -> dict[str, Any]:
    questions, target, source = setup(name)
    out.mkdir(parents=True, exist_ok=True)
    stores = make_stores(out, store)
    rt = Retract(out / "lineage.db", stores=stores)
    try:
        tb = Testbed(rt, llm, Logs(out))
        tb.ingest()
        for (persona, n), q in zip(PERSONAS, questions, strict=True):
            tb.run(persona, n, q)

        stages = spread(rt, target) if name == "poisoned_doc" else None
        before = exposure(rt, tb, questions, target)
        plan = rt.retract(source, dry_run=True)
        receipt = plan.execute()
        after = exposure(rt, tb, questions, target)
        summary: dict[str, Any] = {
            "scenario": name,
            "research_store": store,
            "records": rt.lineage.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0],
            "blast_radius": plan.blast_radius,
            "outcome": receipt.outcome,
            "receipt_type": receipt.receipt_type,
            "receipt_ok": receipt.verify().ok,
            "verification": receipt.body["verification"],
            "before": before,
            "after": after,
            "message_log_lines_with_target": log_hits(out / "messages.jsonl", target),
        }
        if stages is not None:
            summary["stages"] = {
                **stages,
                "influenced_answers": before["answers_with_target"],
                "influence_gone": not (
                    after["answers_with_target"]
                    or after["readable_records_with_target"]
                    or after["raw_items_with_target"]["not_quarantined"]
                ),
            }
        (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
    finally:
        rt.close()
        for s in stores.values():
            if hasattr(s, "close"):
                s.close()


def spread(rt: Retract, target: set[str], oracle: Oracle = ORACLE) -> dict[str, Any]:
    """Stages 1-2: where the target fact sits in memory before retraction."""
    rows = rt.lineage.conn.execute(
        f"SELECT r.agent, r.tier, r.content, EXISTS (SELECT 1 FROM edges e"
        f" WHERE e.child_id = r.record_id) AS derived FROM records r WHERE {READABLE}"
    ).fetchall()
    hits = [r for r in rows if oracle.facts_in(r["content"] or "") & target]
    promoted = Counter(f"{r['agent']}/{r['tier']}" for r in hits if r["derived"])
    return {
        "entered_memory": sum(1 for r in hits if not r["derived"]),
        "retrieved_or_promoted": dict(sorted(promoted.items())),
    }


def exposure(
    rt: Retract,
    tb: Testbed,
    questions: list[str],
    target: set[str],
    oracle: Oracle = ORACLE,
    answers: list[str] | None = None,
) -> dict[str, Any]:
    facts_in = oracle.facts_in
    if answers is None:
        answers = [tb.answer(q) for q in questions]
    readable = rt.lineage.conn.execute(f"SELECT content FROM records WHERE {READABLE}")
    raw = Counter()
    for store in rt.stores.values():
        for i in store.list_ids():
            item = store.get(i)
            if item and facts_in(item.content) & target:
                rec = rt.lineage.records([item.metadata.get("record_id", "")]).get(
                    item.metadata.get("record_id", "")
                )
                quarantined = rec is not None and rec.status == "quarantined"
                raw["quarantined" if quarantined else "not_quarantined"] += 1
    return {
        "answers_with_target": sum(1 for a in answers if facts_in(a) & target),
        "answers": len(answers),
        "readable_records_with_target": sum(1 for (c,) in readable if c and facts_in(c) & target),
        "raw_items_with_target": {
            "quarantined": raw["quarantined"],
            "not_quarantined": raw["not_quarantined"],
        },  # fmt: skip
    }


def log_hits(path: Path, target: set[str], oracle: Oracle = ORACLE) -> int:
    """Message-channel residue: outside Retract's scope, reported, never purged."""
    if not path.exists():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if oracle.facts_in(json.loads(line)["content"]) & target)
