"""M1 Priya slice: naive delete leaves her facts downstream; the closure finds every one."""

from retract import src
from testbed.data import FACTS, facts_in, owned_by

PRIYA = src.user("u_priya")


def build(rt):
    primaries = {
        f.fact_id: rt.add(
            f.text, agent="support", tier="private:support", sources=[src.user(f.owner)]
        )
        for f in FACTS
    }

    @rt.derives("weekly_summary", agent="support", tier="shared")
    def weekly_summary(inputs):
        return "Weekly summary: " + " ".join(r.content for r in inputs)

    @rt.derives("follow_up_plan", agent="planning", tier="private:planning")
    def follow_up_plan(inputs):
        return "Plan: " + " ".join(r.content for r in inputs)

    weekly_summary(inputs=list(primaries.values()))
    with rt.step("planning", "private:planning"):
        follow_up_plan(inputs=rt.search("whatsapp follow-ups", tier="shared", k=1))
        # Step-only write about Arjun. The step already read the summary, so the read-set
        # makes it a descendant of Priya too: the deliberate over-approximation (M8 metric).
        reminder = rt.add("Reminder: " + rt.get(primaries["arjun-1"].record_id).content)
    return primaries, reminder


def raw_items(rt):
    """Every item physically in every store, bypassing the guard."""
    return [(tier, store.get(i)) for tier, store in rt.stores.items() for i in store.list_ids()]


def test_oracle_markers_cover_paraphrases():
    for f in FACTS:
        assert facts_in(f.text) == {f.fact_id}
        assert all(f.fact_id in facts_in(p) for p in f.paraphrases)


def test_naive_delete_leaves_residue_and_closure_finds_it(rt):
    primaries, reminder = build(rt)
    priya = owned_by("u_priya")
    truth = {
        item.metadata["record_id"] for _, item in raw_items(rt) if facts_in(item.content) & priya
    }
    assert len(truth) == 4  # 2 primaries + shared summary + planning agent's plan

    assert not facts_in(reminder.content) & priya
    assert rt.closure(PRIYA) == truth | {reminder.record_id}  # covers truth; one over-retraction
    assert all(PRIYA in rt.get(r).labels for r in truth | {reminder.record_id})

    # Naive delete: what a developer does today, delete the user's own memories from the store.
    for fid in priya:
        rt.stores["private:support"].delete(primaries[fid].adapter_record_id)

    residue = [(tier, item) for tier, item in raw_items(rt) if facts_in(item.content) & priya]
    assert {tier for tier, _ in residue} == {"shared", "private:planning"}
    assert len(residue) == 2  # naive residue > 0
