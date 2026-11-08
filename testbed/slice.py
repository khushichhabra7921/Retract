"""Priya slice world: three users' facts, a shared weekly summary, and a planning agent that
writes a plan and a reminder inside one rt.step (all via @rt.derives, so all replayable)."""

from collections.abc import Callable
from dataclasses import dataclass

from retract import Record, Retract, src
from retract.derive.fakes import FakeLLM
from testbed.data import FACTS

SUPPORT, PLANNING, SHARED = "private:support", "private:planning", "shared"
TIERS = (SUPPORT, PLANNING, SHARED)


def register(rt: Retract, llm: FakeLLM) -> dict[str, Callable[..., Record]]:
    def fn(prefix: str) -> Callable[[list[Record]], str]:
        return lambda inputs: prefix + llm.summarize([r.content or "" for r in inputs])

    return {
        "weekly_summary": rt.derives("weekly_summary", agent="support", tier=SHARED)(
            fn("Weekly summary: ")
        ),
        "follow_up_plan": rt.derives("follow_up_plan", agent="planning", tier=PLANNING)(
            fn("Plan: ")
        ),
        "reminder": rt.derives("reminder", agent="planning", tier=PLANNING)(fn("Reminder: ")),
    }


@dataclass
class World:
    fns: dict[str, Callable[..., Record]]
    primaries: dict[str, Record]  # by fact_id
    summary: Record
    plan: Record
    reminder: Record


def build(rt: Retract, llm: FakeLLM) -> World:
    fns = register(rt, llm)
    primaries = {
        f.fact_id: rt.add(f.text, agent="support", tier=SUPPORT, sources=[src.user(f.owner)])
        for f in FACTS
    }
    summary = fns["weekly_summary"](inputs=list(primaries.values()))
    with rt.step("planning", PLANNING):
        plan = fns["follow_up_plan"](inputs=rt.search("whatsapp follow-ups", tier=SHARED, k=1))
        # About Arjun only, but the step already read the summary: the read-set makes it a
        # descendant of Priya too (deliberate over-approximation, measured in M8).
        arjun = rt.get(primaries["arjun-1"].record_id)
        reminder = fns["reminder"](inputs=[arjun])
    return World(fns, primaries, summary, plan, reminder)
