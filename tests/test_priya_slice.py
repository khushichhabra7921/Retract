"""M1 Priya slice: naive delete leaves her facts downstream; the closure finds every one."""

from retract import src
from testbed.data import FACTS, facts_in, owned_by
from testbed.slice import SUPPORT

PRIYA = src.user("u_priya")


def raw_items(rt):
    """Every item physically in every store, bypassing the guard."""
    return [(tier, store.get(i)) for tier, store in rt.stores.items() for i in store.list_ids()]


def test_oracle_markers_cover_paraphrases():
    for f in FACTS:
        assert facts_in(f.text) == {f.fact_id}
        assert all(f.fact_id in facts_in(p) for p in f.paraphrases)


def test_naive_delete_leaves_residue_and_closure_finds_it(rt, world):
    priya = owned_by("u_priya")
    truth = {
        item.metadata["record_id"] for _, item in raw_items(rt) if facts_in(item.content) & priya
    }
    assert truth == {
        world.primaries["priya-1"].record_id,
        world.primaries["priya-2"].record_id,
        world.summary.record_id,
        world.plan.record_id,
    }
    assert not facts_in(world.reminder.content) & priya
    # The closure covers the truth plus one over-retraction (the reminder, via the read-set).
    assert rt.closure(PRIYA) == truth | {world.reminder.record_id}
    assert all(PRIYA in rt.get(r).labels for r in truth | {world.reminder.record_id})

    # Naive delete: what a developer does today, delete the user's own memories from the store.
    for fid in priya:
        rt.stores[SUPPORT].delete(world.primaries[fid].adapter_record_id)

    residue = [(tier, item) for tier, item in raw_items(rt) if facts_in(item.content) & priya]
    assert {tier for tier, _ in residue} == {"shared", "private:planning"}
    assert len(residue) == 2  # naive residue > 0
