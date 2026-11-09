"""M3 acceptance: Priya end to end, failure handling, resume, outcomes and receipts."""

import dataclasses
from collections import Counter

import pytest

from retract import Retract, src
from retract.core.errors import (
    OperationInProgressError,
    ReplayError,
    RetractError,
    RetractionFailedError,
    StalePlanError,
    StructuralVerificationError,
)
from testbed.data import FACTS, facts_in, owned_by
from testbed.slice import PLANNING, SHARED, SUPPORT, register

PRIYA, ARJUN = src.user("u_priya"), src.user("u_arjun")
PRIYA_FACTS = owned_by("u_priya")
HONEST = {f.fact_id for f in FACTS} - PRIYA_FACTS
TEXT = {f.fact_id: f.text for f in FACTS}


class SimulatedCrash(BaseException):
    """Process death: not an Exception, so the engine cannot handle it."""


def rows(rt, sql, *args):
    return rt.lineage.conn.execute(sql, args).fetchall()


def readable_facts(rt):
    return set().union(
        *(
            facts_in(r[0])
            for r in rows(
                rt, "SELECT content FROM records WHERE status = 'active' AND hold_op_id IS NULL"
            )
        )
    )


def raw_facts(rt):
    return set().union(
        *(facts_in(s.get(i).content) for s in rt.stores.values() for i in s.list_ids())
    )


def readable(rt, record_id):
    return rt.get(record_id) is not None


def current(rt, fn_op):
    """Readable records written by `fn_op`."""
    return [
        r[0]
        for r in rows(
            rt,
            "SELECT record_id FROM records WHERE fn_op = ? AND status = 'active'"
            " AND hold_op_id IS NULL",
            fn_op,
        )
    ]


def only_op(rt):
    [(op_id,)] = rows(rt, "SELECT op_id FROM operations")
    return op_id


# -- happy path ----------------------------------------------------------------------------


def test_priya_end_to_end_completed(rt, world):
    plan = rt.retract(PRIYA, dry_run=True)
    assert {a.record_id: a.action for a in plan.actions} == {
        world.primaries["priya-1"].record_id: "delete",
        world.primaries["priya-2"].record_id: "delete",
        world.summary.record_id: "rederive",
        world.plan.record_id: "rederive",
        world.reminder.record_id: "rederive",
    }
    assert plan.blast_radius == "5 records · 2 agents · 3 tiers"
    assert rows(rt, "SELECT COUNT(*) FROM operations")[0][0] == 0  # preview writes nothing

    receipt = plan.execute()
    assert (receipt.outcome, receipt.receipt_type) == ("completed", "retraction")
    assert rt.lineage.readable_with_label(PRIYA) == []
    assert not readable_facts(rt) & PRIYA_FACTS
    assert not raw_facts(rt) & PRIYA_FACTS  # purged from the stores, not just hidden
    [summary] = current(rt, "weekly_summary")
    assert facts_in(rt.get(summary).content) == HONEST  # rebuilt from the others
    assert HONEST <= readable_facts(rt)

    report = receipt.verify()
    assert report.ok and report.structural == "pass" and report.retrieval_residue == "not_run"
    assert rt.verify_chain().ok
    exported = receipt.export()
    assert "u_priya" not in exported and "WhatsApp" not in exported  # pseudonymous evidence
    assert not rows(rt, "SELECT 1 FROM records WHERE hold_op_id IS NOT NULL")


def test_delete_mode_is_strict(rt, world):
    receipt = rt.retract(PRIYA, mode="delete")
    assert receipt.outcome == "completed"
    assert {a["effect"] for a in receipt.body["actions"]} == {"deleted"}
    assert current(rt, "weekly_summary") == []
    assert not readable_facts(rt) & PRIYA_FACTS and HONEST <= readable_facts(rt)


def test_quarantine_mode_is_contained(rt, world):
    receipt = rt.retract(PRIYA, mode="quarantine")
    assert (receipt.outcome, receipt.receipt_type) == ("contained", "containment")
    states = {
        r[0]
        for r in rows(
            rt,
            "SELECT status FROM records WHERE record_id IN (?, ?)",
            world.summary.record_id,
            world.primaries["priya-1"].record_id,
        )
    }
    assert states == {"quarantined"}
    assert not readable_facts(rt) & PRIYA_FACTS
    assert PRIYA_FACTS <= raw_facts(rt)  # content kept, hidden only by Retract-guarded reads
    assert receipt.verify().ok


# -- failures ------------------------------------------------------------------------------


def test_llm_fails_mid_repair_then_resume(rt, world, llm):
    llm.fail_after = llm.calls + 1  # the first re-derive succeeds, the second fails
    with pytest.raises(RetractionFailedError) as e:
        rt.retract(PRIYA)
    op_id = e.value.op_id
    assert tuple(rows(rt, "SELECT state, outcome FROM operations")[0]) == ("failed", "failed")
    assert rt.receipt(op_id) is None  # no success receipt
    assert rows(rt, "SELECT detail FROM op_actions WHERE state = 'failed'")[0][0] == (
        "transient: TransientError"
    )
    for r in (world.summary, world.plan, world.reminder, world.primaries["priya-1"]):
        assert not readable(rt, r.record_id)  # held
    assert not readable_facts(rt) & PRIYA_FACTS

    llm.fail_after = None  # the LLM is back
    receipt = rt.resume(op_id)
    assert receipt.outcome == "completed" and receipt.verify().ok
    supersedes = Counter(
        r[0] for r in rows(rt, "SELECT supersedes FROM records WHERE supersedes IS NOT NULL")
    )
    assert set(supersedes.values()) == {1}  # no duplicate replacement on resume


def test_failed_parent_child_not_rebuilt(rt, world, llm):
    llm.fail_after = llm.calls  # the first re-derive (the summary) fails
    with pytest.raises(RetractionFailedError):
        rt.retract(PRIYA)
    assert not rows(rt, "SELECT 1 FROM records WHERE supersedes = ?", world.plan.record_id)
    assert not readable(rt, world.plan.record_id)
    assert (
        rows(
            rt,
            "SELECT status FROM records WHERE record_id = ?",
            world.primaries["priya-1"].record_id,
        )[0][0]
        == "active"
    )  # held, not purged


def add_notes(rt, llm, world, n):
    note = rt.derives("note", agent="planning", tier=PLANNING)(
        lambda ins: "Note: " + llm.summarize([r.content for r in ins])
    )
    return [note(inputs=[world.summary]) for _ in range(n)]


def test_crash_after_5_actions_resume_reconciles(rt, world, llm, tmp_path, monkeypatch):
    add_notes(rt, llm, world, 4)  # 7 re-derives in total
    writes = Counter()
    with monkeypatch.context() as m:
        for store in rt.stores.values():

            def crashing(content, metadata, real=store.write):
                aid = real(content, metadata)  # the item lands, then the process dies
                writes["n"] += 1
                if writes["n"] == 5:
                    raise SimulatedCrash
                return aid

            m.setattr(store, "write", crashing)
        with pytest.raises(SimulatedCrash):
            rt.retract(PRIYA)
    op_id = only_op(rt)
    assert rows(rt, "SELECT state FROM operations")[0][0] == "executing"

    other = Retract(tmp_path / "lineage.db", stores=rt.stores)  # a new process
    register(other, llm)
    other.derives("note", agent="planning", tier=PLANNING)(
        lambda ins: "Note: " + llm.summarize([r.content for r in ins])
    )
    with pytest.raises(OperationInProgressError) as e:
        other.retract(ARJUN)  # the live lease is the global lock
    assert e.value.op_id == op_id
    with pytest.raises(OperationInProgressError):
        other.resume(op_id)  # not the owner and the lease is live

    rt.lineage.conn.execute("UPDATE operations SET lease_expires_at = 0")  # lease expires
    receipt = other.resume(op_id)
    assert receipt.outcome == "completed" and receipt.verify().ok
    tokens = Counter(
        s.get(i).metadata["retract_token"] for s in rt.stores.values() for i in s.list_ids()
    )
    assert set(tokens.values()) == {1}  # reconciled via find_by_token: no duplicates
    supersedes = Counter(
        r[0] for r in rows(rt, "SELECT supersedes FROM records WHERE supersedes IS NOT NULL")
    )
    assert len(supersedes) == 7 and set(supersedes.values()) == {1}
    assert rt.orphan_scan().clean
    other.close()


def test_crash_during_purge_owner_resumes(rt, world, monkeypatch):
    store = rt.stores[SUPPORT]
    real = store.delete

    def crashing(aid):
        real(aid)
        raise SimulatedCrash

    with monkeypatch.context() as m:
        m.setattr(store, "delete", crashing)
        with pytest.raises(SimulatedCrash):
            rt.retract(PRIYA)
    receipt = rt.resume(only_op(rt))  # owner match: no need to wait for the lease
    assert receipt.outcome == "completed" and rt.verify_chain().ok
    assert not raw_facts(rt) & PRIYA_FACTS


def test_stale_preview_blocks_nothing(rt, world):
    plan = rt.retract(PRIYA, dry_run=True)
    world.fns["follow_up_plan"](inputs=[world.primaries["priya-1"]])  # new descendant
    with pytest.raises(StalePlanError):
        plan.execute()
    assert rows(rt, "SELECT COUNT(*) FROM tombstones")[0][0] == 0
    assert rows(rt, "SELECT COUNT(*) FROM operations")[0][0] == 0
    assert not rows(rt, "SELECT 1 FROM records WHERE hold_op_id IS NOT NULL")
    assert rt.retract(PRIYA).outcome == "completed"  # a fresh preview goes through


@pytest.mark.parametrize("failure", ["raises", "silently_keeps_item"])
def test_adapter_delete_failure_fails_the_op(rt, world, monkeypatch, failure):
    def broken(aid):
        if failure == "raises":
            raise ConnectionError("store down")

    monkeypatch.setattr(rt.stores[SUPPORT], "delete", broken)
    with pytest.raises(RetractionFailedError):
        rt.retract(PRIYA)
    assert rows(rt, "SELECT state FROM operations")[0][0] == "failed"
    assert not rows(rt, "SELECT 1 FROM receipts")
    assert not readable(rt, world.primaries["priya-1"].record_id)


def test_missing_signing_key_blocks_nothing(rt, world, monkeypatch):
    monkeypatch.delenv("RETRACT_SIGNING_KEY")
    with pytest.raises(RetractError, match="RETRACT_SIGNING_KEY"):
        rt.retract(PRIYA)
    assert rows(rt, "SELECT COUNT(*) FROM tombstones")[0][0] == 0


# -- idempotence and interaction -------------------------------------------------------------


def test_already_retracted_is_a_noop(rt, world):
    first = rt.retract(PRIYA)
    assert rt.retract(PRIYA).op_id == first.op_id
    assert rt.retract(PRIYA, dry_run=True).noop_op_id == first.op_id
    assert rows(rt, "SELECT COUNT(*) FROM operations")[0][0] == 1


def test_two_retractions_sharing_descendants_no_resurrection(rt, world):
    assert rt.retract(PRIYA).outcome == "completed"
    assert rt.retract(ARJUN).outcome == "completed"
    mei = owned_by("u_mei")
    assert readable_facts(rt) == mei and raw_facts(rt) == mei
    [summary] = current(rt, "weekly_summary")
    assert facts_in(rt.get(summary).content) == mei
    chain = rt.verify_chain()
    assert chain.ok and chain.receipts == 2


def test_tampered_receipt_fails_verification(rt, world):
    receipt = rt.retract(PRIYA)
    forged = receipt.model_copy(update={"body": {**receipt.body, "outcome": "contained"}})
    report = forged.verify()
    assert not report.integrity and not report.execution_complete and not report.ok

    rt.lineage.conn.execute(
        "UPDATE receipts SET body = replace(body, '\"completed\"', '\"contained\"')"
    )
    assert not rt.receipt(receipt.op_id).verify().integrity
    chain = rt.verify_chain()
    assert not chain.ok and any("hash" in p for p in chain.problems)


def test_deleted_receipt_breaks_the_chain(rt, world):
    rt.retract(PRIYA)
    rt.retract(ARJUN)
    rt.lineage.conn.execute("DELETE FROM receipts WHERE seq = 1")
    assert not rt.verify_chain().ok


# -- post-repair check and replayability -----------------------------------------------------


def test_leak_injected_replacement_is_quarantined(rt, world, llm):
    llm.inject = TEXT["priya-1"]  # the LLM re-emits a forbidden fact
    receipt = rt.retract(PRIYA)
    assert (receipt.outcome, receipt.receipt_type) == ("contained", "containment")
    effects = {a["record_id"]: (a["effect"], a["detail"]) for a in receipt.body["actions"]}
    assert effects[world.summary.record_id] == ("replacement_quarantined", "repair_unverified")
    assert not readable_facts(rt) & PRIYA_FACTS
    assert receipt.verify().ok


def test_independent_support_survives(rt, llm):
    fns = register(rt, llm)
    prims = [
        rt.add(f.text, agent="support", tier=SUPPORT, sources=[src.user(f.owner)]) for f in FACTS
    ]
    # Arjun independently reports the same fact as priya-2.
    prims.append(rt.add(TEXT["priya-2"], agent="support", tier=SUPPORT, sources=[ARJUN]))
    fns["weekly_summary"](inputs=prims)
    receipt = rt.retract(PRIYA)
    assert receipt.outcome == "completed"
    [summary] = current(rt, "weekly_summary")
    assert facts_in(rt.get(summary).content) & PRIYA_FACTS == {"priya-2"}


def test_version_mismatch_is_quarantined(rt, world):
    reg = rt.registry["weekly_summary"]
    rt.registry["weekly_summary"] = dataclasses.replace(reg, version="2")  # code was upgraded
    plan = rt.retract(PRIYA, dry_run=True)
    reasons = {a.record_id: (a.action, a.reason) for a in plan.actions}
    assert reasons[world.summary.record_id] == ("quarantine", "version_mismatch")
    receipt = plan.execute()
    assert (receipt.outcome, receipt.receipt_type) == ("contained", "containment")
    assert (
        rows(rt, "SELECT status FROM records WHERE record_id = ?", world.summary.record_id)[0][0]
        == "quarantined"
    )
    assert not readable_facts(rt) & PRIYA_FACTS


def test_unregistered_function_is_quarantined(rt, world, tmp_path):
    fresh = Retract(tmp_path / "lineage.db", stores=rt.stores)  # new process, nothing registered
    receipt = fresh.retract(PRIYA)
    assert receipt.outcome == "contained"
    reasons = {a["record_id"]: a["reason"] for a in receipt.body["actions"]}
    assert reasons[world.summary.record_id] == "not_registered"
    fresh.close()


# -- refined rules: replay errors vs bugs, source vs global verification ---------------------


def test_known_replay_error_quarantines_and_continues(rt, world):
    reg = rt.registry["reminder"]

    def refuses(inputs):
        raise ReplayError("input format no longer supported")

    rt.registry["reminder"] = dataclasses.replace(reg, fn=refuses)
    receipt = rt.retract(PRIYA)
    assert (receipt.outcome, receipt.receipt_type) == ("contained", "containment")
    effects = {a["record_id"]: (a["effect"], a["detail"]) for a in receipt.body["actions"]}
    assert effects[world.reminder.record_id] == ("quarantined", "replay_error: ReplayError")
    assert effects[world.summary.record_id][0] == "rederived"  # the op went on
    structural = receipt.body["verification"]["structural"]
    assert structural["source"]["status"] == structural["global"]["status"] == "pass"
    assert receipt.verify().ok


def test_unexpected_exception_fails_the_op_instead_of_containment(rt, world):
    reg = rt.registry["reminder"]

    def buggy(inputs):
        return inputs.nope  # AttributeError: a software bug, not an unreplayable memory

    rt.registry["reminder"] = dataclasses.replace(reg, fn=buggy)
    with pytest.raises(RetractionFailedError) as e:
        rt.retract(PRIYA)
    assert isinstance(e.value.__cause__, AttributeError)
    state, outcome, error = rows(rt, "SELECT state, outcome, error FROM operations")[0]
    assert (state, outcome) == ("failed", "failed") and "AttributeError" in error
    assert tuple(
        rows(
            rt, "SELECT state, detail FROM op_actions WHERE record_id = ?", world.reminder.record_id
        )[0]
    ) == ("failed", "unexpected: AttributeError")
    assert rt.receipt(e.value.op_id) is None
    assert not readable(rt, world.reminder.record_id)  # holds preserved

    rt.registry["reminder"] = reg  # bug fixed
    assert rt.resume(e.value.op_id).outcome == "completed"


def test_unrelated_orphan_is_a_global_integrity_failure(rt, world):
    stray = rt.stores[SHARED].write("written around the SDK", {})  # not Priya's, not ours
    with pytest.raises(RetractionFailedError) as e:
        rt.retract(PRIYA)
    cause = e.value.__cause__
    assert isinstance(cause, StructuralVerificationError)
    assert cause.source_failures == [] and cause.global_failures == ["no_unrelated_orphans"]
    assert (
        "global-integrity=['no_unrelated_orphans']"
        in rows(rt, "SELECT error FROM operations")[0][0]
    )
    assert not readable_facts(rt) & PRIYA_FACTS  # Priya's own information is not reachable

    rt.stores[SHARED].delete(stray)  # lineage coverage restored
    assert rt.resume(e.value.op_id).outcome == "completed"


def test_source_specific_residue_is_reported_separately(rt, world, monkeypatch):
    monkeypatch.setattr(rt.lineage, "readable_with_label", lambda source: ["still-readable"])
    with pytest.raises(RetractionFailedError) as e:
        rt.retract(PRIYA)
    cause = e.value.__cause__
    assert cause.source_failures == ["no_readable_record_carries_source_label"]
    assert cause.global_failures == []
