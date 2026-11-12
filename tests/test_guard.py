"""M2 acceptance: guarded reads, tombstones, freshness, writes racing with BLOCK."""

import random
import threading

import pytest

from retract import Retract, src
from retract.core.errors import InputNotReadableError, RetractError, TombstonedSourceError

PRIYA = src.user("u_priya")
WORDS = "priya arjun mei whatsapp renewal slack invoices demo aws march plan summary".split()


def add(rt, text, owner="u_priya", tier="private:support"):
    return rt.add(text, agent="support", tier=tier, sources=[src.user(owner)])


def concat(rt):
    @rt.derives("concat", agent="support", tier="shared")
    def derive(inputs):
        return " ".join(r.content for r in inputs)

    return derive


def test_missing_pseudonym_key_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("RETRACT_PSEUDONYM_KEY")
    with pytest.raises(RetractError, match="RETRACT_PSEUDONYM_KEY"):
        Retract(tmp_path / "l.db", stores={})


def test_source_ref_is_keyed_and_stable(rt):
    assert rt.source_ref(PRIYA) == rt.source_ref(PRIYA) != rt.source_ref(src.user("u_arjun"))
    assert "u_priya" not in rt.source_ref(PRIYA)


def test_random_queries_never_return_unreadable_records(rt, monkeypatch):
    rng = random.Random(7)
    tiers = list(rt.stores)
    derive = concat(rt)
    owners = ["u_priya", "u_arjun", "u_mei", "u_ola"]
    recs = []
    for _ in range(60):
        text = " ".join(rng.sample(WORDS, 4))
        if recs and rng.random() < 0.4:
            recs.append(derive(inputs=rng.sample(recs, 2)))
        else:
            recs.append(add(rt, text, rng.choice(owners), rng.choice(tiers)))

    # pending: adapter write landed, activation crashed
    with monkeypatch.context() as m:
        m.setattr(rt.lineage, "activate", lambda *a: None)
        for _ in range(5):
            add(rt, " ".join(rng.sample(WORDS, 4)), "u_ola", rng.choice(tiers))
    # held: a BLOCK on Priya
    held = rt.lineage.block(PRIYA, "op-1")
    # purged / quarantined states as the engine (M3) will set them
    rest = [r.record_id for r in recs if r.record_id not in held]
    for status, rid in zip(["retracted", "tombstoned", "quarantined"] * 3, rest, strict=False):
        rt.lineage.conn.execute("UPDATE records SET status = ? WHERE record_id = ?", (status, rid))

    states = dict(
        rt.lineage.conn.execute("SELECT status, COUNT(*) FROM records GROUP BY status").fetchall()
    )
    assert states["pending"] == 5 and states["quarantined"] == 3 and held

    def readable(rid):
        row = rt.lineage.conn.execute(
            "SELECT status, hold_op_id FROM records WHERE record_id = ?", (rid,)
        ).fetchone()
        return row["status"] == "active" and row["hold_op_id"] is None

    returned = 0
    for _ in range(50):
        query = " ".join(rng.sample(WORDS, 2))
        for r in rt.search(query, tier=rng.choice(tiers), k=5):
            assert readable(r.record_id) and PRIYA not in r.labels
            returned += 1
        rid = rng.choice(recs).record_id
        assert (rt.get(rid) is not None) == readable(rid)
    assert returned > 0  # the guard filtered, it did not just return nothing


def test_tombstoned_write_is_rejected(rt):
    p = add(rt, "Priya prefers WhatsApp.")
    rt.lineage.block(PRIYA, "op-1")
    with pytest.raises(TombstonedSourceError) as e:
        add(rt, "Priya is back with new data.")  # explicit source
    assert e.value.source_refs == [rt.source_ref(PRIYA)]
    with pytest.raises(InputNotReadableError):
        concat(rt)(inputs=[p])  # held input
    add(rt, "Arjun's renewal is due in March.", "u_arjun")  # other sources unaffected


def test_guard_sees_fresh_state_right_after_revocation(rt, tmp_path):
    p = add(rt, "Priya prefers WhatsApp for follow-ups.")
    reader = Retract(tmp_path / "lineage.db", stores=rt.stores)  # separate connection
    try:
        assert reader.get(p.record_id) is not None
        assert [r.record_id for r in reader.search("whatsapp", tier="private:support")] == [
            p.record_id
        ]
        rt.lineage.block(PRIYA, "op-1")
        assert reader.get(p.record_id) is None
        assert reader.search("whatsapp", tier="private:support") == []
        assert not reader.lineage.conn.in_transaction
    finally:
        reader.close()


def test_write_racing_block_loses_when_block_commits_first(rt, tmp_path):
    p = add(rt, "Priya prefers WhatsApp.")
    other = Retract(tmp_path / "lineage.db", stores=rt.stores)

    @rt.derives("racy", agent="support", tier="shared")
    def racy(inputs):
        other.lineage.block(PRIYA, "op-1")  # BLOCK commits after the inputs were read
        return "Summary: " + inputs[0].content

    with pytest.raises(InputNotReadableError):
        racy(inputs=[p])
    assert not rt.lineage.conn.execute("SELECT 1 FROM records WHERE fn_op = 'racy'").fetchall()
    assert rt.stores["shared"].list_ids() == []
    other.close()


def test_write_racing_block_is_held_when_its_row_commits_first(rt, tmp_path, monkeypatch):
    p = add(rt, "Priya prefers WhatsApp.")
    other = Retract(tmp_path / "lineage.db", stores=rt.stores)
    real_write = rt.stores["shared"].write
    blocked = {}

    def write_then_block(content, metadata):  # pending row is committed; BLOCK lands here
        blocked["ids"] = other.lineage.block(PRIYA, "op-1")
        return real_write(content, metadata)

    monkeypatch.setattr(rt.stores["shared"], "write", write_then_block)
    child = concat(rt)(inputs=[p])
    assert child.record_id in blocked["ids"]
    assert child.status == "active" and rt.get(child.record_id) is None  # activated but held
    other.close()


def test_threaded_writes_racing_block_are_never_missed(rt, tmp_path):
    p = add(rt, "Priya prefers WhatsApp.")
    derive = concat(rt)
    warmed, stop, done, rejected = threading.Event(), threading.Event(), [], []

    def writer():
        w = Retract(tmp_path / "lineage.db", stores=rt.stores)
        wd = w.derives("w", agent="support", tier="shared")(lambda ins: "S " + ins[0].content)
        for _ in range(100_000):  # until BLOCK has rejected a few writes
            if len(rejected) >= 3 or stop.is_set():
                break
            try:
                done.append(wd(inputs=[p.record_id]).record_id)
            except (InputNotReadableError, TombstonedSourceError):
                rejected.append(1)
            if len(done) == 5:
                warmed.set()  # BLOCK fires while the writer keeps going
        w.close()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    blocked = False
    try:
        assert warmed.wait(30)
        derive(inputs=[p])
        held = rt.lineage.block(PRIYA, "op-1")
        blocked = True
    finally:
        if not blocked:  # no rejections will ever come: stop the writer, or it spins on
            stop.set()
        t.join(60)
    assert not t.is_alive()
    labeled = {
        r[0]
        for r in rt.lineage.conn.execute(
            "SELECT record_id FROM labels WHERE source_id = ?", (PRIYA,)
        )
    }
    assert labeled == held  # every write carrying Priya's label is held: none escaped BLOCK
    assert set(done) <= held
    assert len(done) >= 5 and rejected  # writes landed on both sides of BLOCK
