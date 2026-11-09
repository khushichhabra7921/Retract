"""M4 acceptance: local probes, guarded and raw-adapter retrieval residue, not_run on resume."""

from collections import Counter

import pytest

from retract import src
from retract.core.errors import RetractionFailedError, StructuralVerificationError
from retract.derive.fakes import FakeEmbedder
from retract.probes.residue import make_probes, measure
from testbed.data import FACTS, confusion, facts_in, owned_by
from testbed.slice import SUPPORT

PRIYA = src.user("u_priya")
PRIYA_FACTS = owned_by("u_priya")
FACT = {f.fact_id: f for f in FACTS}
PRIYA_TEXTS = [f.text for f in FACTS if f.owner == "u_priya"]


class SimulatedCrash(BaseException):
    """Process death: not an Exception, so the engine cannot handle it."""


def readable_facts(rt):
    rows = rt.lineage.conn.execute(
        "SELECT content FROM records WHERE status = 'active' AND hold_op_id IS NULL"
    )
    return set().union(*(facts_in(r[0]) for r in rows))


def residue_of(receipt):
    return receipt.body["verification"]["retrieval_residue"]


def test_probes_are_local_sentences_and_key_tokens():
    probes = make_probes(["Priya prefers WhatsApp for follow-ups. She moves to AWS."])
    assert "Priya prefers WhatsApp for follow-ups." in probes
    assert "priya prefers whatsapp follow" in probes


def test_naive_delete_leaves_retrieval_residue(rt, world):
    for fid in PRIYA_FACTS:  # what a developer does today
        rt.stores[SUPPORT].delete(world.primaries[fid].adapter_record_id)
    r = measure(
        rt,
        make_probes(PRIYA_TEXTS),
        PRIYA_TEXTS,
        rt.lineage.independent_primaries(PRIYA),
        FakeEmbedder(),
    )
    assert r["status"] == "fail"
    assert r["guarded_hits"] > 0 and r["raw_unexpected_hits"] > 0  # the summary still answers


def test_retract_has_zero_guarded_residue_on_the_slice(rt, world):
    receipt = rt.retract(PRIYA)
    r = residue_of(receipt)
    assert receipt.body["verification"]["structural"]["status"] == "pass"
    assert r["status"] == "pass" and r["probes"] > 0
    assert r["guarded_hits"] == r["raw_purged_hits"] == r["raw_unexpected_hits"] == 0
    assert "WhatsApp" not in receipt.export()  # probes are never persisted


def test_quarantined_hits_are_reported_separately(rt, world):
    r = residue_of(rt.retract(PRIYA, mode="quarantine"))
    assert r["status"] == "pass" and r["guarded_hits"] == 0
    assert r["raw_quarantined_hits"] > 0  # content kept physically, hidden by guarded reads


def replacement_facts(rt):
    """Oracle over every replacement the engine wrote, quarantined or not."""
    rows = rt.lineage.conn.execute("SELECT content FROM records WHERE supersedes IS NOT NULL")
    return set().union(*(facts_in(r[0]) for r in rows if r[0]))


def runtime_flagged(receipt):
    return receipt.outcome != "completed" or residue_of(receipt)["status"] == "fail"


def test_verbatim_leak_is_a_true_positive(rt, world, llm):
    llm.inject = FACT["priya-1"].text
    receipt = rt.retract(PRIYA)
    assert confusion(runtime_flagged(receipt), "priya-1" in replacement_facts(rt)) == "TP"
    assert "priya-1" not in readable_facts(rt)  # contained


def test_planted_paraphrase_miss_is_measured_as_false_negative(rt, world, llm):
    llm.inject = FACT["priya-1"].paraphrases[0]  # same fact, different words
    receipt = rt.retract(PRIYA)
    assert confusion(runtime_flagged(receipt), "priya-1" in replacement_facts(rt)) == "FN"
    assert "priya-1" in readable_facts(rt)  # the known limit of the runtime heuristic


def test_clean_retraction_is_a_true_negative(rt, world):
    receipt = rt.retract(PRIYA)
    assert confusion(runtime_flagged(receipt), bool(replacement_facts(rt) & PRIYA_FACTS)) == "TN"


def test_probes_catch_a_leak_outside_the_lineage(rt, world, llm):
    llm.inject = FACT["priya-1"].text  # an agent learned Priya's fact off-lineage
    world.fns["reminder"](inputs=[world.primaries["arjun-1"]])  # labeled Arjun only
    llm.inject = None
    with pytest.raises(RetractionFailedError) as e:
        rt.retract(PRIYA)
    cause = e.value.__cause__
    assert isinstance(cause, StructuralVerificationError)
    assert cause.source_failures == ["no_retrieval_residue"] and cause.global_failures == []


def test_crash_after_purge_before_probes(rt, world, monkeypatch):
    def crash(*args, **kwargs):
        raise SimulatedCrash

    with monkeypatch.context() as m:
        m.setattr(rt.engine, "_verify", crash)  # PURGE done, process dies before probes
        with pytest.raises(SimulatedCrash):
            rt.retract(PRIYA)
    [(op_id, state)] = rt.lineage.conn.execute("SELECT op_id, state FROM operations")
    assert state == "executing"
    assert not readable_facts(rt) & PRIYA_FACTS  # blocking persists across the crash
    assert rt.lineage.readable_with_label(PRIYA) == []

    receipt = rt.resume(op_id)
    assert receipt.outcome == "completed"
    assert residue_of(receipt)["status"] == "not_run"  # probe inputs are gone: never "pass"
    assert receipt.verify().ok
    tokens = Counter(
        s.get(i).metadata["retract_token"] for s in rt.stores.values() for i in s.list_ids()
    )
    assert set(tokens.values()) == {1}  # no duplicate writes


def test_probe_reads_do_not_join_an_active_step(rt, world):
    with rt.step("planning", "private:planning"):
        rt.retract(PRIYA)  # probes run guarded searches inside the caller's step
        note = rt.add("Unrelated note written after the retraction.")
    assert (
        rt.lineage.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE child_id = ?", (note.record_id,)
        ).fetchone()[0]
        == 0
    )


def test_each_returned_item_is_checked_once(rt, world, monkeypatch):
    from retract.probes import residue

    seen = Counter()
    real = residue.forbidden_facts

    def counting(candidate, *args):
        seen[candidate] += 1
        return real(candidate, *args)

    monkeypatch.setattr(residue, "forbidden_facts", counting)
    probes = make_probes(PRIYA_TEXTS)
    measure(rt, probes, PRIYA_TEXTS, [], FakeEmbedder())
    assert len(probes) > 1 and seen and set(seen.values()) == {1}  # once per content, not per probe
