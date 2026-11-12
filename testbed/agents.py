"""LangGraph testbed: support -> research -> planning, each node inside `rt.step`.

Memory channel: every memory read and write goes through Retract; memory.jsonl logs ids, tiers
and labels only (no content, so the log itself holds no residue).
Message channel: what agents pass on and answer; messages.jsonl logs content. It is outside
Retract's scope and is reported separately.
"""

from __future__ import annotations

import json
import operator
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from retract import Record, Retract
from testbed.data import ALL_FACTS, Fact

SUPPORT, RESEARCH, PLANNING = "private:support", "private:research", "private:planning"
SHARED, KB = "shared", "kb"
TIERS = (SUPPORT, RESEARCH, PLANNING, SHARED, KB)


class LLM(Protocol):
    def summarize(self, texts: Sequence[str]) -> str: ...
    def answer(self, question: str, contexts: Sequence[str]) -> str: ...


class Ticket(TypedDict):
    persona: str
    name: str
    question: str
    messages: Annotated[list[dict[str, Any]], operator.add]
    answer: str


class Logs:
    def __init__(self, out: Path) -> None:
        self.messages_path = out / "messages.jsonl"
        self.memory_path = out / "memory.jsonl"

    def message(self, agent: str, persona: str, content: str) -> None:
        _append(self.messages_path, {"agent": agent, "persona": persona, "content": content})

    def memory(self, agent: str, op: str, records: Sequence[Record]) -> None:
        for r in records:
            _append(
                self.memory_path,
                {"agent": agent, "op": op, "tier": r.tier, "record_id": r.record_id,
                 "labels": sorted(r.labels)},
            )  # fmt: skip


def _append(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


class Testbed:
    def __init__(
        self, rt: Retract, llm: LLM, logs: Logs, facts: Sequence[Fact] = ALL_FACTS
    ) -> None:
        self.rt, self.llm, self.logs, self.facts = rt, llm, logs, facts

        def derive(op: str, agent: str, tier: str, prefix: str) -> Callable[..., Record]:
            return rt.derives(op, agent=agent, tier=tier)(
                lambda inputs: prefix + llm.summarize([r.content or "" for r in inputs])
            )

        self.brief = derive("account_brief", "support", SHARED, "Account brief: ")
        self.note = derive("research_note", "research", RESEARCH, "Research note: ")
        self.plan = derive("action_plan", "planning", PLANNING, "Plan: ")
        g = StateGraph(Ticket)
        g.add_node("support", self.support)
        g.add_node("research", self.research)
        g.add_node("planning", self.planning)
        g.add_edge(START, "support")
        g.add_edge("support", "research")
        g.add_edge("research", "planning")
        g.add_edge("planning", END)
        self.graph = g.compile()

    def ingest(self) -> None:
        """Customer facts into support's memory; docs into the shared knowledge base."""
        for f in self.facts:
            agent, tier = ("support", SUPPORT) if f.kind == "user" else ("kb", KB)
            self.logs.memory(agent, "write", [
                self.rt.add(f.text, agent=agent, tier=tier, sources=[f.source])
            ])  # fmt: skip

    def run(self, persona: str, name: str, question: str) -> dict[str, Any]:
        state: Ticket = {
            "persona": persona, "name": name, "question": question, "messages": [], "answer": ""
        }  # fmt: skip
        return dict(self.graph.invoke(state))

    # -- nodes ---------------------------------------------------------------------------------

    def support(self, s: Ticket) -> dict[str, Any]:
        with self.rt.step("support", SUPPORT):
            inputs = self._read("support", s["name"], SUPPORT, 2)
            inputs += self._read("support", s["question"], KB, 2)
            brief = self._write("support", self.brief(inputs=inputs))
        return self._say("support", s, brief.content or "")

    def research(self, s: Ticket) -> dict[str, Any]:
        with self.rt.step("research", RESEARCH):
            inputs = self._read("research", s["name"], SHARED, 1)
            inputs += self._read("research", s["question"], KB, 1)
            note = self._write("research", self.note(inputs=inputs))
        return self._say("research", s, note.content or "")

    def planning(self, s: Ticket) -> dict[str, Any]:
        with self.rt.step("planning", PLANNING):
            plan = self._write("planning", self.plan(inputs=self._read(
                "planning", s["question"], SHARED, 2
            )))  # fmt: skip
        answer = self.answer(s["question"])
        out = self._say("planning", s, plan.content or "")
        self.logs.message("planning", s["persona"], answer)
        return {**out, "answer": answer}

    def answer(self, question: str, agent: str = "planning") -> str:
        """Answer from guarded memory only (no writes): shared briefs, plans and the KB."""
        contexts = [
            r.content or "" for tier in (SHARED, PLANNING, KB)
            for r in self._read(agent, question, tier, 2)
        ]  # fmt: skip
        return self.llm.answer(question, contexts)

    # -- helpers -------------------------------------------------------------------------------

    def _read(self, agent: str, query: str, tier: str, k: int) -> list[Record]:
        records = self.rt.search(query, tier=tier, k=k)
        self.logs.memory(agent, "read", records)
        return records

    def _write(self, agent: str, record: Record) -> Record:
        self.logs.memory(agent, "write", [record])
        return record

    def _say(self, agent: str, s: Ticket, content: str) -> dict[str, Any]:
        self.logs.message(agent, s["persona"], content)
        return {"messages": [{"agent": agent, "content": content}]}
