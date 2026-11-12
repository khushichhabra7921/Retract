"""M8 configs and one trial (scenario, seed, config). Every config of a seed sees the same data,
ticket order and questions; measurements use the fact oracle, which the SDK never sees.

clean         the target source is never ingested: the reference world
unretracted   the target is ingested and never removed (each retraction run's "before" state)
naive_delete  delete the source's own primary items from their stores, bypassing Retract
delete        rt.retract(mode="delete"): purge the whole affected closure, rebuild nothing
auto          rt.retract(mode="auto"): purge the source's records, re-derive the rest
quarantine    rt.retract(mode="quarantine"): hide the whole affected closure
"""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path
from typing import Any

from retract import Retract
from retract.derive.fakes import FakeLLM
from testbed.agents import LLM, TIERS, Logs, Testbed
from testbed.data import PERSONAS, Fact, Oracle, dataset
from testbed.scenarios import READABLE, exposure, make_stores, setup, spread

CONFIGS = ("clean", "unretracted", "naive_delete", "delete", "auto", "quarantine")
RUNS = ("clean", "naive_delete", "delete", "auto", "quarantine")  # unretracted = a run's before
MODES = ("delete", "auto", "quarantine")

PRIMARIES_OF_SOURCE = (
    "SELECT r.tier, r.adapter_record_id FROM records r"
    " JOIN explicit_sources e ON e.record_id = r.record_id"
    " WHERE e.source_id = ? AND NOT EXISTS (SELECT 1 FROM edges WHERE child_id = r.record_id)"
)


def expected_facts(scenario: str, oracle: Oracle) -> list[set[str] | None]:
    """Per question (PERSONAS order): facts a useful answer contains; None = not scored."""
    if scenario == "priya":  # the target persona's own question is excluded from utility
        return [None if p == "u_priya" else oracle.owned_by(p) for p, _ in PERSONAS]
    return [{"refund-true"} for _ in PERSONAS]


def run_trial(
    scenario: str, seed: int, config: str, out: Path, llm: LLM | None = None
) -> dict[str, Any]:
    if config not in RUNS:
        raise ValueError(f"config must be one of {RUNS}")
    facts = dataset(seed)
    oracle = Oracle(facts)
    questions, target, source = setup(scenario, oracle)
    probes = [t for f in facts if f.fact_id in target for t in (f.text, *f.paraphrases)]
    expected = expected_facts(scenario, oracle)
    ingest: tuple[Fact, ...] = facts
    if config == "clean":
        ingest = tuple(f for f in facts if f.source != source)
    order = random.Random(seed).sample(range(len(PERSONAS)), len(PERSONAS))
    llm = llm or FakeLLM()

    out.mkdir(parents=True, exist_ok=True)
    stores = make_stores(out, "simple")
    rt = Retract(out / "lineage.db", stores=stores)
    try:
        tb = Testbed(rt, llm, Logs(out), facts=ingest)
        tb.ingest()
        for i in order:
            persona, name = PERSONAS[i]
            tb.run(persona, name, questions[i])

        def measure() -> dict[str, Any]:
            return _measure(rt, tb, questions, target, oracle, expected, probes)

        row: dict[str, Any] = {"scenario": scenario, "seed": seed, "config": config}
        staged = scenario == "poisoned_doc"
        if config == "clean":
            row["after"] = measure()
            return row
        row["before"] = measure()
        if staged:
            row["stages_before"] = spread(rt, target, oracle)
        if config == "naive_delete":
            primaries = rt.lineage.conn.execute(PRIMARIES_OF_SOURCE, (source,)).fetchall()
            for tier, adapter_id in primaries:
                rt.stores[tier].delete(adapter_id)
            row["op"] = {"deleted": len(primaries)}
        else:
            row["op"] = _retract(rt, llm, source, config, target, oracle)
        row["after"] = measure()
        if staged:
            row["stages_after"] = spread(rt, target, oracle)
        if config in MODES:
            _over_retraction(rt, row["op"])
        return row
    finally:
        rt.close()


def _retract(
    rt: Retract, llm: LLM, source: str, mode: Any, target: set[str], oracle: Oracle
) -> dict[str, Any]:
    plan = rt.retract(source, mode, dry_run=True)
    closure = {a.record_id: a.action for a in plan.actions}
    before = rt.lineage.records(list(closure))
    calls0 = getattr(llm, "calls", 0)
    receipt = plan.execute()
    return {
        "llm_calls": getattr(llm, "calls", 0) - calls0,
        "outcome": receipt.outcome,
        "receipt_type": receipt.receipt_type,
        "receipt_ok": receipt.verify().ok,
        "retrieval_residue": receipt.body["verification"]["retrieval_residue"]["status"],
        "closure": len(closure),
        "actions": dict(Counter(closure.values())),
        # closure records whose content carries no target fact: lineage over-approximation
        "overapprox_ids": sorted(
            rid for rid, r in before.items() if not oracle.facts_in(r.content or "") & target
        ),
    }


def _over_retraction(rt: Retract, op: dict[str, Any]) -> None:
    """An over-approximated record is lost if neither it nor a replacement is readable now."""
    present = _readable_present(rt)
    alive = {r["record_id"] for r in present} | {r["supersedes"] for r in present}
    ids = op.pop("overapprox_ids")
    op["overapprox"] = len(ids)
    op["overapprox_lost"] = sum(1 for rid in ids if rid not in alive)


def _readable_present(rt: Retract) -> list[Any]:
    """Readable in lineage AND physically in its store (a naive delete skips lineage)."""
    rows = rt.lineage.conn.execute(
        "SELECT r.record_id, r.tier, r.adapter_record_id, r.content, r.supersedes,"
        " EXISTS (SELECT 1 FROM edges e WHERE e.child_id = r.record_id) AS derived"
        f" FROM records r WHERE {READABLE}"
    ).fetchall()
    return [
        r
        for r in rows
        if r["adapter_record_id"] and rt.stores[r["tier"]].contains(r["adapter_record_id"])
    ]


def _measure(
    rt: Retract,
    tb: Testbed,
    questions: list[str],
    target: set[str],
    oracle: Oracle,
    expected: list[set[str] | None],
    probes: list[str],
) -> dict[str, Any]:
    answers = [tb.answer(q) for q in questions]
    m = exposure(rt, tb, questions, target, oracle, answers=answers)
    scored = [(a, e) for a, e in zip(answers, expected, strict=True) if e is not None]
    m["useful_answers"] = sum(1 for a, e in scored if oracle.facts_in(a) & e)
    m["utility_questions"] = len(scored)
    present = _readable_present(rt)
    m["readable_records"] = len(present)
    m["honest_mentions"] = sum(
        len(oracle.facts_in(r["content"] or "") - target) for r in present if r["derived"]
    )
    m["probes"] = _probe(rt, probes, target, oracle)
    return m


def _probe(rt: Retract, probes: list[str], target: set[str], oracle: Oracle) -> dict[str, int]:
    """Probes (target texts and paraphrases) with at least one target-carrying hit, per group."""

    def leaks(text: str) -> bool:
        return bool(oracle.facts_in(text) & target)

    hits = Counter[str]()
    for q in probes:
        groups = set()
        for tier in TIERS:
            if any(leaks(r.content or "") for r in rt.guarded_search(q, tier=tier)):
                groups.add("guarded")
            for item in rt.stores[tier].search(q, 5):
                if leaks(item.content):
                    rid = item.metadata.get("record_id", "")
                    rec = rt.lineage.records([rid]).get(rid)
                    quarantined = rec is not None and rec.status == "quarantined"
                    groups.add("raw_quarantined" if quarantined else "raw_unquarantined")
        hits.update(groups)
    names = ("guarded", "raw_unquarantined", "raw_quarantined")
    return {"total": len(probes), **{g: hits[g] for g in names}}
