"""Refetch repair: re-run the retrieval behind a record instead of replaying old inputs."""

import pytest

from retract import Retract, src
from retract.adapters.simple import SimpleVectorStore

FALSE = "Refund policy RP-90: refunds are accepted within 90 days."
TRUE = "Refund policy RP-30: refunds are accepted within 30 days."
NEXT = "Shipping questions about a refund are answered by the billing team."
OTHER = "Onboarding calls last thirty minutes."
QUERY = "refund policy days"


class SimulatedCrash(BaseException):
    """Process death: not an Exception, so the engine cannot handle it."""


def world(tmp_path):
    rt = Retract(
        tmp_path / "lineage.db", stores={"kb": SimpleVectorStore(), "shared": SimpleVectorStore()}
    )
    docs = {}
    for name, text in (("a", FALSE), ("b", TRUE), ("c", NEXT), ("d", OTHER)):
        docs[name] = rt.add(text, sources=[src.doc(name)], agent="kb", tier="kb")

    def brief(inputs):
        return "Brief: " + " ".join(r.content for r in inputs)

    out = {}
    for op, refetch in (("brief_refetch", True), ("brief_replay", False)):
        derive = rt.derives(op, agent="support", tier="shared", refetch=refetch)(brief)
        with rt.step("support", "shared"):
            out[op] = derive(inputs=rt.search(QUERY, tier="kb", k=2))
    assert all(FALSE in r.content and NEXT not in r.content for r in out.values())
    return rt, docs, out


def replacement(rt, old_id):
    row = rt.lineage.conn.execute(
        "SELECT record_id FROM records WHERE supersedes = ?", (old_id,)
    ).fetchone()
    return rt.lineage.records([row[0]])[row[0]]


def test_refetch_rebuilds_from_fresh_retrieval_and_replay_does_not(tmp_path):
    rt, docs, out = world(tmp_path)
    plan = rt.retract(src.doc("a"), dry_run=True)
    reasons = {a.record_id: a.reason for a in plan.actions}
    assert reasons[out["brief_refetch"].record_id] == "refetch"
    assert reasons[out["brief_replay"].record_id] == "replayable"
    assert plan.execute().outcome == "completed"

    fresh = replacement(rt, out["brief_refetch"].record_id)
    replay = replacement(rt, out["brief_replay"].record_id)
    assert FALSE not in fresh.content and TRUE in fresh.content and NEXT in fresh.content
    assert FALSE not in replay.content and TRUE in replay.content and NEXT not in replay.content
    assert fresh.labels == {src.doc("b"), src.doc("c")}

    # The old record's query log is purged with it; the replacement logs the fresh results.
    assert rt.lineage.queries(out["brief_refetch"].record_id) == []
    [(query, tier, k, ids)] = rt.lineage.queries(fresh.record_id)
    assert (query, tier, k) == (QUERY, "kb", 2) and set(ids) == {
        docs["b"].record_id,
        docs["c"].record_id,
    }
    assert rt.lineage.queries(replay.record_id) == []  # replay-only derives log nothing
    rt.close()


def test_refetch_survives_a_crash_between_journal_and_store_write(tmp_path, monkeypatch):
    rt, docs, out = world(tmp_path)
    store = rt.stores["shared"]
    real = store.write
    calls = {"n": 0}

    def crash_once(content, metadata):
        if NEXT in content and not calls["n"]:  # the refetched replacement, journaled as pending
            calls["n"] += 1
            raise SimulatedCrash
        return real(content, metadata)

    monkeypatch.setattr(store, "write", crash_once)
    with pytest.raises(SimulatedCrash):
        rt.retract(src.doc("a"))
    (op_id,) = rt.lineage.conn.execute("SELECT op_id FROM operations").fetchone()
    assert rt.resume(op_id).outcome == "completed"

    fresh = replacement(rt, out["brief_refetch"].record_id)
    assert NEXT in fresh.content and FALSE not in fresh.content
    n = rt.lineage.conn.execute(
        "SELECT COUNT(*) FROM records WHERE supersedes = ?", (out["brief_refetch"].record_id,)
    ).fetchone()[0]
    assert n == 1  # resumed, not duplicated
    rt.close()
