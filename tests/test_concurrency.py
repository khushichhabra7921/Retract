"""Per-closure locks: retractions with disjoint closures run in parallel, an overlapping one is
refused, and parallel ops keep the receipt chain whole. With RETRACT_TEST_PG set, every test
also runs on Postgres, where the threads race for real."""

import threading

import pytest

from retract import Retract, src
from retract.core.errors import OperationInProgressError, RetractionFailedError, StalePlanError

SUPPORT, SHARED = "private:support", "shared"
FACTS = {
    "u_priya": "Priya prefers WhatsApp.",
    "u_arjun": "Arjun renews in March.",
    "u_mei": "Mei likes email.",
    "u_sofia": "Sofia owns the Lapwing account.",
}


def add(rt, owner):
    return rt.add(FACTS[owner], agent="support", tier=SUPPORT, sources=[src.user(owner)])


def summarizer(rt, hook=None):
    @rt.derives("summary", agent="support", tier=SHARED)
    def summary(inputs):
        if hook:
            hook()
        return "Summary: " + " ".join(r.content for r in inputs)

    return summary


def rows(rt, sql, *args):
    return rt.lineage.conn.execute(sql, args).fetchall()


def test_disjoint_retraction_runs_while_another_is_repairing(rt, db):
    other = Retract(db, stores=rt.stores)  # a second process
    inner = {}
    repairing = threading.Event()

    def during_repair():
        if repairing.is_set() and not inner:
            inner["receipt"] = other.retract(src.user("u_mei"))

    summary = summarizer(rt, during_repair)
    summary(inputs=[add(rt, "u_priya"), add(rt, "u_arjun")])
    add(rt, "u_mei")
    repairing.set()
    outer = rt.retract(src.user("u_priya"))  # re-derives the summary, which runs the hook
    mei = inner["receipt"]
    assert mei.outcome == outer.outcome == "completed"
    assert mei.verify().ok and outer.verify().ok
    assert (mei.body["seq"], outer.body["seq"]) == (1, 2)  # Mei's finished inside Priya's
    assert rt.verify_chain().ok
    other.close()


def test_overlapping_retraction_is_refused_and_blocks_nothing(rt, db):
    other = Retract(db, stores=rt.stores)
    refused = {}
    repairing = threading.Event()

    def during_repair():
        if repairing.is_set() and not refused:
            with pytest.raises(OperationInProgressError) as e:
                other.retract(src.user("u_arjun"))  # the summary is in both closures
            refused["by"] = e.value.op_id

    summary = summarizer(rt, during_repair)
    summary(inputs=[add(rt, "u_priya"), add(rt, "u_arjun")])
    repairing.set()
    outer = rt.retract(src.user("u_priya"))
    assert refused["by"] == outer.op_id
    assert len(rows(rt, "SELECT 1 FROM tombstones")) == 1  # Arjun's attempt rolled back
    assert rt.retract(src.user("u_arjun")).outcome == "completed"  # fine once Priya's is done
    other.close()


def test_failed_ops_unfinished_replacement_is_not_another_ops_orphan(rt, monkeypatch):
    summary = summarizer(rt)
    summary(inputs=[add(rt, "u_priya"), add(rt, "u_arjun")])
    add(rt, "u_mei")
    store = rt.stores[SHARED]
    real = store.write

    def down_for_repairs(content, metadata):
        if not metadata["retract_token"].startswith("write:"):
            raise RuntimeError("store unavailable")
        return real(content, metadata)

    monkeypatch.setattr(store, "write", down_for_repairs)
    with pytest.raises(RetractionFailedError) as failed:
        rt.retract(src.user("u_priya"))
    monkeypatch.setattr(store, "write", real)
    [(hold,)] = rows(rt, "SELECT hold_op_id FROM records WHERE status = 'pending'")
    assert hold == failed.value.op_id  # its replacement row, journaled before the store write

    assert rt.retract(src.user("u_mei")).outcome == "completed"  # not failed by that row
    assert rt.resume(failed.value.op_id).outcome == "completed"
    assert rt.verify_chain().ok


def test_orphan_scan_snapshot_reconciled_since_is_not_an_orphan(rt, monkeypatch):
    """VERIFY's orphan scan is a snapshot. Another op can activate its write, or purge an item,
    between the scan and the checks (the scale benchmark hit this at 100K records). Candidates
    reconciled since are not orphans."""
    summary = summarizer(rt)
    summary(inputs=[add(rt, "u_priya"), add(rt, "u_arjun")])
    mei = add(rt, "u_mei")  # pending in the stale scan, active and unheld now
    adapter_id = rt.lineage.nodes([mei.record_id])[mei.record_id].adapter_record_id
    real = rt.orphan_scan

    def stale():
        report = real()
        return report.model_copy(
            update={
                "pending_no_adapter_record": [mei.record_id],
                "adapter_no_lineage": [(SUPPORT, adapter_id), (SUPPORT, "purged-since")],
            }
        )

    monkeypatch.setattr(rt, "orphan_scan", stale)
    assert rt.retract(src.user("u_priya")).outcome == "completed"


def retract_together(db, rt, owners, plan_first=False):
    """One worker process per owner, each on its own thread with its own connection. Every
    worker retracts at the same instant (after previewing, if `plan_first`). Returns each
    receipt or exception, in `owners` order."""
    barrier = threading.Barrier(len(owners))
    out = [None] * len(owners)

    def run(i):
        try:
            w = Retract(db, stores=rt.stores)
        except Exception as e:  # reported like any other failure, never lost in the thread
            out[i] = e
            barrier.abort()
            return
        summarizer(w)
        try:
            plan = w.retract(src.user(owners[i]), dry_run=True) if plan_first else None
            barrier.wait()
            out[i] = plan.execute() if plan else w.retract(src.user(owners[i]))
        except Exception as e:
            out[i] = e
        finally:
            w.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(owners))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not any(t.is_alive() for t in threads)
    return out


def test_racing_overlapping_retractions_exactly_one_blocks(rt, db):
    summary = summarizer(rt)
    summary(inputs=[add(rt, "u_priya"), add(rt, "u_arjun")])
    results = retract_together(db, rt, ["u_priya", "u_arjun"], plan_first=True)
    won = [r for r in results if not isinstance(r, Exception)]
    lost = [r for r in results if isinstance(r, Exception)]
    assert len(won) == 1 and won[0].outcome == "completed", results
    assert isinstance(lost[0], OperationInProgressError | StalePlanError), results
    assert len(rows(rt, "SELECT 1 FROM operations")) == 1
    assert not rows(rt, "SELECT 1 FROM records WHERE hold_op_id IS NOT NULL")


def test_parallel_disjoint_retractions_all_complete(rt, db):
    summary = summarizer(rt)
    handbook = rt.add(
        "Standup is at nine.", sources=[src.doc("handbook")], agent="support", tier=SUPPORT
    )
    for owner in FACTS:
        summary(inputs=[add(rt, owner), handbook])
    results = retract_together(db, rt, list(FACTS))
    assert all(getattr(r, "outcome", None) == "completed" for r in results), results
    chain = rt.verify_chain()
    assert chain.ok and chain.receipts == len(FACTS), chain.problems
    assert not rows(rt, "SELECT 1 FROM records WHERE hold_op_id IS NOT NULL")
