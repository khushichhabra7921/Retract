"""Public SDK: lineage-capturing writes, guarded reads, retraction and receipts."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, overload

from retract.core.engine import Engine
from retract.core.errors import InputNotReadableError, RetractError, UnknownTierError
from retract.core.interfaces import MemoryAdapter
from retract.core.lineage import LineageStore
from retract.core.models import Mode, OrphanReport, Plan, Record
from retract.core.operations import Journal
from retract.core.verification import Embed
from retract.derive.fakes import FakeEmbedder
from retract.receipts import receipt as receipts

DeriveFn = Callable[[list[Record]], str]


@dataclass(frozen=True)
class Registered:
    fn: DeriveFn
    version: str
    replayable: bool


@dataclass
class _Step:
    owner: Retract
    agent: str
    tier: str
    reads: dict[str, None] = field(default_factory=dict)  # ordered set: read order


_STEP: ContextVar[_Step | None] = ContextVar("retract_step", default=None)


class Retract:
    def __init__(
        self,
        lineage_db: str | Path,
        stores: Mapping[str, MemoryAdapter],
        *,
        embed: Embed | None = None,
        lease_seconds: float = 60.0,
    ) -> None:
        key = os.environ.get("RETRACT_PSEUDONYM_KEY")
        if not key:
            raise RetractError("RETRACT_PSEUDONYM_KEY is not set in the process environment")
        self.lineage = LineageStore(lineage_db, key.encode())
        self.stores = dict(stores)
        self.registry: dict[str, Registered] = {}
        self.owner_token = uuid.uuid4().hex  # identifies this process as an op lease owner
        self.journal = Journal(self.lineage, self.owner_token, lease_seconds)
        self.engine = Engine(self, self.journal, embed or FakeEmbedder())

    def close(self) -> None:
        self.lineage.close()

    # -- writes ---------------------------------------------------------------------------

    def add(
        self,
        content: str,
        *,
        sources: Sequence[str] = (),
        agent: str | None = None,
        tier: str | None = None,
    ) -> Record:
        """Write a record. `sources` are ownership on a primary record; inside `rt.step`
        the step's read-set becomes the record's parents."""
        agent, tier = self._where(agent, tier)
        return self._write(content, agent, tier, sources, inputs=())

    def derives(
        self,
        op: str | None = None,
        *,
        agent: str | None = None,
        tier: str | None = None,
        version: str = "1",
        replayable: bool = True,
    ) -> Callable[[DeriveFn], Callable[..., Record]]:
        """Register `fn(inputs: list[Record]) -> str`. Calling the result with
        `inputs=[...]` (and optional extra `sources=[...]`) writes the derived record."""

        def register(fn: DeriveFn) -> Callable[..., Record]:
            name = op or fn.__qualname__
            if name in self.registry:
                raise ValueError(f"derive op {name!r} is already registered")
            self.registry[name] = Registered(fn, version, replayable)

            def derive(inputs: Sequence[Record | str] = (), sources: Sequence[str] = ()) -> Record:
                ids = [x if isinstance(x, str) else x.record_id for x in inputs]
                current = self.lineage.records(ids, readable_only=True)
                if missing := [i for i in ids if i not in current]:
                    raise InputNotReadableError(missing)
                where = self._where(agent, tier)
                content = fn([current[i] for i in ids])
                return self._write(content, *where, sources, ids, fn_op=name, fn_version=version)

            return derive

        return register

    def _write(
        self,
        content: str,
        agent: str,
        tier: str,
        sources: Sequence[str],
        inputs: Sequence[str],
        fn_op: str | None = None,
        fn_version: str | None = None,
    ) -> Record:
        store = self._store(tier)
        parents = [(p, "input") for p in dict.fromkeys(inputs)]
        if step := self._current_step():
            parents += [(p, "read") for p in step.reads if p not in set(inputs)]
        record_id = uuid.uuid4().hex
        token = f"write:{record_id}"
        # 1. Journal a pending row (rejects unreadable parents). 2. Adapter write. 3. Activate.
        self.lineage.insert_pending(
            record_id=record_id,
            agent=agent,
            tier=tier,
            content=content,
            retract_token=token,
            explicit_sources=sources,
            parents=parents,
            fn_op=fn_op,
            fn_version=fn_version,
        )
        adapter_record_id = store.write(content, {"retract_token": token, "record_id": record_id})
        self.lineage.activate(record_id, adapter_record_id)
        return self.lineage.records([record_id])[record_id]

    # -- guarded reads --------------------------------------------------------------------

    def get(self, record_id: str) -> Record | None:
        record = self.lineage.records([record_id], readable_only=True).get(record_id)
        if record:
            self._note_reads([record])
        return record

    def search(self, query: str, *, tier: str, k: int = 5) -> list[Record]:
        results = self.guarded_search(query, tier=tier, k=k)
        self._note_reads(results)
        return results

    def guarded_search(self, query: str, *, tier: str, k: int = 5) -> list[Record]:
        """Guarded search that never joins a step's read-set (used by residue probes)."""
        # shortcut: k×4 can still return <k under heavy holds, upgrade to adapter-side id filter.
        hits = self._store(tier).search(query, k * 4)
        ids = [h.metadata.get("record_id", "") for h in hits]
        current = self.lineage.records(ids, readable_only=True)
        out: dict[str, Record] = {}
        for hit, rid in zip(hits, ids, strict=True):
            record = current.get(rid)
            # The adapter id must match lineage, so stray duplicates of a record stay hidden.
            if record and record.adapter_record_id == hit.adapter_record_id:
                out.setdefault(rid, record)
        return list(out.values())[:k]

    @contextmanager
    def step(self, agent: str, tier: str) -> Iterator[None]:
        """Every record returned by guarded get/search inside the step becomes a parent of
        every write in the step (deliberate over-approximation)."""
        token = _STEP.set(_Step(self, agent, tier))
        try:
            yield
        finally:
            _STEP.reset(token)

    # -- retraction -----------------------------------------------------------------------

    @overload
    def retract(self, source: str, mode: Mode = ..., *, dry_run: Literal[True]) -> Plan: ...
    @overload
    def retract(
        self, source: str, mode: Mode = ..., *, dry_run: Literal[False] = ...
    ) -> receipts.Receipt: ...
    def retract(
        self, source: str, mode: Mode = "auto", *, dry_run: bool = False
    ) -> Plan | receipts.Receipt:
        """Preview (`dry_run=True`) or execute retraction of everything derived from `source`.

        Returns a receipt for `completed` or `contained`; raises RetractionFailedError when the
        operation ends `failed` (records stay held; call `resume`)."""
        plan = self.engine.plan(source, mode)
        return plan if dry_run else self.engine.execute(plan, source)

    def resume(self, op_id: str) -> receipts.Receipt:
        """Continue an interrupted or failed operation from its journal."""
        return self.engine.resume(op_id)

    def receipt(self, op_id: str) -> receipts.Receipt | None:
        return receipts.load(self.lineage.conn, op_id)

    def verify_chain(self) -> receipts.ChainReport:
        return receipts.verify_chain(self.lineage.conn)

    # -- lineage queries ------------------------------------------------------------------

    def source_ref(self, source: str) -> str:
        """Keyed pseudonym of `source`, as stored in tombstones and receipts."""
        return self.lineage.source_ref(source)

    def closure(self, source: str) -> set[str]:
        """record_ids owned by `source` plus all their descendants."""
        return self.lineage.closure(source)

    def orphan_scan(self) -> OrphanReport:
        """Reconcile crashes inside the write path and adapter items with no lineage row."""
        no_adapter: list[str] = []
        written: list[str] = []
        adopted: set[tuple[str, str]] = set()
        for record_id, tier, token in self.lineage.pending():
            found = self._store(tier).find_by_token(token)
            if found:
                written.append(record_id)
                adopted.add((tier, found))
            else:
                no_adapter.append(record_id)
        stray: list[tuple[str, str]] = []
        for tier, store in self.stores.items():
            if store.supports_list_ids:
                known = self.lineage.adapter_ids(tier)
                stray += [
                    (tier, i)
                    for i in store.list_ids()
                    if i not in known and (tier, i) not in adopted
                ]
        return OrphanReport(
            pending_no_adapter_record=no_adapter,
            pending_adapter_written=written,
            adapter_no_lineage=stray,
        )

    # -- helpers --------------------------------------------------------------------------

    def _current_step(self) -> _Step | None:
        step = _STEP.get()
        return step if step and step.owner is self else None

    def _note_reads(self, records: Sequence[Record]) -> None:
        if step := self._current_step():
            step.reads.update(dict.fromkeys(r.record_id for r in records))

    def _where(self, agent: str | None, tier: str | None) -> tuple[str, str]:
        step = self._current_step()
        agent = agent or (step.agent if step else None)
        tier = tier or (step.tier if step else None)
        if not agent or not tier:
            raise RetractError("agent and tier are required outside rt.step")
        return agent, tier

    def _store(self, tier: str) -> MemoryAdapter:
        try:
            return self.stores[tier]
        except KeyError:
            raise UnknownTierError(f"no adapter configured for tier {tier!r}") from None
