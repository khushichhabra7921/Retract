"""M1 acceptance: label inheritance, closure, step read-set, crash-safe write path."""

import random
from collections import defaultdict

import pytest

from retract import Retract, src
from retract.adapters.simple import SimpleVectorStore
from retract.core.errors import InputNotReadableError


def add(rt, text, *owners):
    return rt.add(
        text, agent="support", tier="private:support", sources=[src.user(o) for o in owners]
    )


def concat(rt, op="concat"):
    @rt.derives(op, agent="support", tier="shared")
    def derive(inputs):
        return " | ".join(r.content for r in inputs)

    return derive


def parents(rt, record_id):
    return rt.lineage.conn.execute(
        "SELECT parent_id, kind, position FROM edges WHERE child_id = ? ORDER BY position",
        (record_id,),
    ).fetchall()


def test_three_level_inheritance(rt):
    derive = concat(rt)
    a = add(rt, "Priya prefers WhatsApp.", "u_priya")
    b = derive(inputs=[a])
    c = derive(inputs=[b])
    assert c.labels == {src.user("u_priya")}
    assert rt.closure(src.user("u_priya")) == {a.record_id, b.record_id, c.record_id}


def test_three_user_union(rt):
    users = [add(rt, f"fact about {u}", u) for u in ("u1", "u2", "u3")]
    summary = concat(rt)(inputs=users, sources=[src.tool("crm")])
    assert summary.labels == {src.user("u1"), src.user("u2"), src.user("u3"), src.tool("crm")}
    for u in ("u1", "u2", "u3"):
        assert summary.record_id in rt.closure(src.user(u))


def test_diamond_descendants(rt):
    derive = concat(rt)
    a = add(rt, "root", "u1")
    b = derive(inputs=[a])
    c = derive(inputs=[a])
    d = derive(inputs=[b, c])
    add(rt, "unrelated", "u2")
    assert rt.closure(src.user("u1")) == {a.record_id, b.record_id, c.record_id, d.record_id}
    assert [tuple(p) for p in parents(rt, d.record_id)] == [
        (b.record_id, "input", 0),
        (c.record_id, "input", 1),
    ]


@pytest.mark.parametrize("seed", range(5))
def test_random_dag_closure_equals_reachability(rt, seed):
    rng = random.Random(seed)
    pool = [src.user(f"u{i}") for i in range(5)]
    derive = concat(rt)
    nodes, explicit, children = [], {}, defaultdict(set)
    for i in range(40):
        if i < 6 or rng.random() < 0.15:
            srcs = rng.sample(pool, rng.randint(1, 2))
            rec = rt.add(f"n{i}", agent="a", tier="private:support", sources=srcs)
        else:
            ins = rng.sample(nodes, rng.randint(1, min(3, len(nodes))))
            srcs = rng.sample(pool, 1) if rng.random() < 0.2 else []
            rec = derive(inputs=ins, sources=srcs)
            for p in ins:
                children[p.record_id].add(rec.record_id)
        explicit[rec.record_id] = set(srcs)
        nodes.append(rec)

    for s in pool:
        frontier = [n.record_id for n in nodes if s in explicit[n.record_id]]
        reach = set(frontier)
        while frontier:
            nxt = [c for f in frontier for c in children[f] if c not in reach]
            reach.update(nxt)
            frontier = nxt
        assert rt.closure(s) == reach
        for n in nodes:
            assert (s in rt.get(n.record_id).labels) == (n.record_id in reach)


def test_step_read_set_gives_label_without_declared_inputs(rt):
    p = add(rt, "Priya prefers WhatsApp for follow-ups.", "u_priya")
    add(rt, "Arjun's renewal is due in March.", "u_arjun")
    with rt.step("planning", "private:planning"):
        hits = rt.search("whatsapp follow-ups", tier="private:support", k=1)
        assert [h.record_id for h in hits] == [p.record_id]
        note = rt.add("Follow up on the preferred channel.")
    assert note.labels == {src.user("u_priya")}
    assert note.agent == "planning" and note.tier == "private:planning"
    assert [tuple(x) for x in parents(rt, note.record_id)] == [(p.record_id, "read", 0)]
    assert rt.closure(src.user("u_priya")) == {p.record_id, note.record_id}


def test_reads_outside_step_add_no_parents(rt):
    p = add(rt, "Priya prefers WhatsApp.", "u_priya")
    assert rt.get(p.record_id) == p
    other = add(rt, "standalone", "u2")
    assert parents(rt, other.record_id) == []


class FailingWrite(SimpleVectorStore):
    def write(self, content, metadata):
        raise ConnectionError("store down")


def test_crash_between_lineage_insert_and_adapter_write(tmp_path):
    store = FailingWrite()
    rt = Retract(tmp_path / "l.db", stores={"t": store})
    with pytest.raises(ConnectionError):
        rt.add("x", agent="a", tier="t", sources=[src.user("u")])
    [(rid,)] = rt.lineage.conn.execute("SELECT record_id FROM records WHERE status = 'pending'")
    assert rt.get(rid) is None
    report = rt.orphan_scan()
    assert report.pending_no_adapter_record == [rid]
    assert report.pending_adapter_written == [] and report.adapter_no_lineage == []

    @rt.derives(agent="a", tier="t")
    def child(inputs):
        return "child"

    with pytest.raises(InputNotReadableError):
        child(inputs=[rid])
    with pytest.raises(InputNotReadableError):  # the tx-level check, bypassing the pre-check
        rt._write("child", "a", "t", (), [rid])
    rt.close()


def test_crash_between_adapter_write_and_activate(rt, monkeypatch):
    def boom(*a):
        raise RuntimeError("crash")

    with monkeypatch.context() as m:
        m.setattr(rt.lineage, "activate", boom)
        with pytest.raises(RuntimeError):
            add(rt, "Priya prefers WhatsApp.", "u_priya")
    [(rid,)] = rt.lineage.conn.execute("SELECT record_id FROM records WHERE status = 'pending'")
    assert rt.search("whatsapp", tier="private:support") == []  # in the adapter, still hidden
    report = rt.orphan_scan()
    assert report.pending_adapter_written == [rid]
    assert report.pending_no_adapter_record == [] and report.adapter_no_lineage == []


def test_orphan_scan_reports_adapter_items_without_lineage(rt):
    add(rt, "tracked", "u1")
    assert rt.orphan_scan().clean
    stray = rt.stores["shared"].write("written around the SDK", {})
    assert rt.orphan_scan().adapter_no_lineage == [("shared", stray)]
    assert rt.search("written around the SDK", tier="shared") == []


def test_derive_op_registered_once(rt):
    concat(rt, "op")
    with pytest.raises(ValueError):
        concat(rt, "op")
