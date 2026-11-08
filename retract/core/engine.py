"""Retraction engine: PLAN, BLOCK, REPAIR, PURGE, VERIFY, FINALIZE, and resume.

BLOCK is one short transaction; no transaction stays open across LLM or adapter calls.
Every later phase is idempotent against the journal, so `resume` simply re-runs them.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from retract.core.errors import (
    OperationInProgressError,
    RetractError,
    RetractionFailedError,
    StalePlanError,
    TransientError,
)
from retract.core.lineage import Node
from retract.core.models import Mode, Plan, PlannedAction, Status
from retract.core.operations import Action, Journal
from retract.core.verification import Embed, forbidden_facts
from retract.receipts import receipt as receipts

if TYPE_CHECKING:
    from retract.core.sdk import Retract

log = logging.getLogger("retract")

PURGED = ("retracted", "tombstoned")
QUARANTINED_EFFECTS = ("quarantined", "replacement_quarantined")
RETRACTED_MATERIAL = ("owned", "explicit_source")  # reasons whose content is the source's


def _closure_hash(ids: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


class Engine:
    def __init__(self, rt: Retract, journal: Journal, embed: Embed) -> None:
        self.rt = rt
        self.lineage = rt.lineage
        self.journal = journal
        self.embed = embed

    # -- PLAN -------------------------------------------------------------------------------

    def plan(self, source: str, mode: Mode) -> Plan:
        if mode not in ("auto", "delete", "quarantine"):
            raise ValueError(f"mode must be auto, delete or quarantine, not {mode!r}")
        ref = self.lineage.source_ref(source)
        prior = self.journal.op_for_source(ref)
        if prior is not None:
            if prior["state"] != "finalized":
                raise OperationInProgressError(prior["op_id"], f"{prior['state']} for this source")
            plan = Plan(
                source_ref=ref, mode=mode, closure_hash="", actions=[], noop_op_id=prior["op_id"]
            )
        else:
            nodes = self._live_closure(source)
            plan = Plan(
                source_ref=ref,
                mode=mode,
                closure_hash=_closure_hash(nodes),
                actions=self._decide(source, mode, nodes),
            )
        plan._execute = lambda p: self.execute(p, source)
        return plan

    def _live_closure(self, source: str) -> dict[str, Node]:
        nodes = self.lineage.nodes(self.lineage.closure(source))
        return {i: n for i, n in nodes.items() if n.status not in PURGED}

    @staticmethod
    def _topo(nodes: Mapping[str, Node]) -> list[str]:
        indegree = dict.fromkeys(nodes, 0)
        children: dict[str, list[str]] = {i: [] for i in nodes}
        for i, n in nodes.items():
            for p, _, _ in n.parents:
                if p in nodes:
                    indegree[i] += 1
                    children[p].append(i)
        ready = sorted(i for i, d in indegree.items() if d == 0)
        order: list[str] = []
        while ready:
            i = ready.pop(0)
            order.append(i)
            for c in children[i]:
                indegree[c] -= 1
                if indegree[c] == 0:
                    ready.append(c)
        return order

    def _decide(self, source: str, mode: Mode, nodes: Mapping[str, Node]) -> list[PlannedAction]:
        outside = self.lineage.nodes(
            {p for n in nodes.values() for p, _, _ in n.parents} - set(nodes)
        )
        planned: dict[str, str] = {}

        def valid(parent: str) -> bool:
            if parent in nodes:
                return planned.get(parent) == "rederive"
            o = outside.get(parent)
            return o is not None and o.status == "active" and o.hold_op_id is None

        out: list[PlannedAction] = []
        for rid in self._topo(nodes):
            n = nodes[rid]
            action, reason = self._rule(source, mode, n, valid)
            planned[rid] = action
            out.append(
                PlannedAction(
                    record_id=rid, action=action, reason=reason, agent=n.agent, tier=n.tier
                )
            )
        return out

    def _rule(self, source: str, mode: Mode, n: Node, valid: Any) -> tuple[Any, str]:
        """Per-record action rule (PLAN.md, 'Per-record action rule')."""
        if mode == "quarantine":
            return "quarantine", "quarantine_mode"
        if n.status == "pending":
            return "delete", "pending_orphan"
        if not n.parents:
            return "delete", "owned"
        if source in n.explicit_sources:
            return ("delete" if mode == "delete" else "quarantine"), "explicit_source"
        if n.status == "quarantined" and mode == "auto":
            return "quarantine", "already_quarantined"
        if not any(valid(p) for p, _, _ in n.parents):
            return "delete", "no_valid_inputs"
        if mode == "delete":
            return "delete", "delete_mode"
        if not any(kind == "input" and valid(p) for p, kind, _ in n.parents):
            return "quarantine", "no_valid_explicit_inputs"
        why = self._unreplayable(n)
        return ("quarantine", why) if why else ("rederive", "replayable")

    def _unreplayable(self, n: Node) -> str | None:
        reg = self.rt.registry.get(n.fn_op) if n.fn_op else None
        if reg is None:
            return "not_registered" if n.fn_op else "step_write_not_replayable"
        if not reg.replayable:
            return "not_replayable"
        if reg.version != n.fn_version:
            return "version_mismatch"
        return None

    # -- BLOCK ------------------------------------------------------------------------------

    def execute(self, plan: Plan, source: str) -> receipts.Receipt:
        if plan.noop_op_id:
            return self.stored(plan.noop_op_id)
        receipts.signing_key()  # fail before anything is blocked
        op_id = uuid.uuid4().hex
        with self.lineage.tx():
            if live := self.journal.live_executing():
                raise OperationInProgressError(live)
            if self.journal.op_for_source(plan.source_ref) is not None:
                raise StalePlanError("the source was retracted after the preview")
            nodes = self._live_closure(source)
            if _closure_hash(nodes) != plan.closure_hash:
                raise StalePlanError(
                    "the affected closure changed since the preview; preview again"
                )
            if foreign := sorted({n.hold_op_id for n in nodes.values() if n.hold_op_id}):
                raise OperationInProgressError(foreign[0], "holding records this retraction needs")
            self.lineage.tombstone_and_hold(source, op_id, nodes)
            self.journal.create(
                op_id,
                plan.source_ref,
                plan.mode,
                plan.closure_hash,
                [
                    (
                        a.record_id,
                        a.action,
                        a.reason,
                        self.lineage.keyed_hash(nodes[a.record_id].content or ""),
                    )
                    for a in plan.actions
                ],
            )
        return self._run(op_id, source)

    def resume(self, op_id: str) -> receipts.Receipt:
        op = self.journal.get(op_id)
        if op is None:
            raise RetractError(f"unknown operation {op_id}")
        if op["state"] == "finalized":
            return self.stored(op_id)
        receipts.signing_key()
        self.journal.take_over(op_id)
        source = next(
            (
                s
                for s in self.journal.sources_of(op_id)
                if self.lineage.source_ref(s) == op["source_ref"]
            ),
            None,
        )
        if source is None:
            raise RetractError(f"cannot recover the source of operation {op_id}")
        return self._run(op_id, source)

    def stored(self, op_id: str) -> receipts.Receipt:
        receipt = receipts.load(self.lineage.conn, op_id)
        if receipt is None:
            raise RetractError(f"operation {op_id} has no receipt")
        return receipt

    def _run(self, op_id: str, source: str) -> receipts.Receipt:
        try:
            self._repair(op_id)
            self._purge(op_id)
            return self._finalize(op_id, self._verify(op_id, source))
        except OperationInProgressError:
            raise  # lease lost: the new owner decides the outcome
        except Exception as e:
            reason = f"{type(e).__name__}: {e}"
            self.journal.fail(op_id, reason)
            log.error("retraction %s failed: %s", op_id, type(e).__name__)
            raise RetractionFailedError(op_id, reason) from e

    # -- REPAIR -----------------------------------------------------------------------------

    def _repair(self, op_id: str) -> None:
        actions = {a.record_id: a for a in self.journal.actions(op_id)}
        material = self.lineage.nodes(
            a.record_id for a in actions.values() if a.reason in RETRACTED_MATERIAL
        )
        retracted = [n.content for n in material.values() if n.content]
        for rid in list(actions):
            a = actions[rid]
            if a.state == "done" or a.action == "delete":
                continue  # deletes happen in PURGE
            self.journal.heartbeat(op_id)
            if a.action == "quarantine":
                self._quarantine(op_id, rid, a.reason)
            else:
                self._rederive(op_id, a, actions, retracted)
            actions[rid] = self.journal.action(op_id, rid)

    def _quarantine(self, op_id: str, record_id: str, detail: str | None) -> None:
        self.lineage.set_status(record_id, "quarantined")
        self.journal.update_action(
            op_id, record_id, state="done", effect="quarantined", detail=detail
        )

    def _rederive(
        self, op_id: str, a: Action, actions: Mapping[str, Action], retracted: list[str]
    ) -> None:
        old = self.lineage.nodes([a.record_id])[a.record_id]
        inputs: list[str] = []
        for p, kind, _ in old.parents:
            if kind != "input":
                continue  # replay uses explicit inputs only, in stored position order
            pa = actions.get(p)
            if pa is not None:
                if pa.effect == "rederived" and pa.new_record_id:
                    inputs.append(pa.new_record_id)  # a failed or unverified parent is dropped
            elif self.lineage.records([p], readable_only=True):
                inputs.append(p)
        if not inputs:
            return self._quarantine(op_id, old.record_id, "no_valid_inputs_at_execution")
        if why := self._unreplayable(old):
            return self._quarantine(op_id, old.record_id, why)
        reg = self.rt.registry[old.fn_op or ""]

        new_id = a.new_record_id or uuid.uuid4().hex
        token = f"{op_id}:{old.record_id}"
        if a.state != "running":  # journal ids and token before any write
            self.journal.update_action(
                op_id, old.record_id, state="running", new_record_id=new_id, retract_token=token
            )
        new = self.lineage.nodes([new_id]).get(new_id)
        if new is None:
            current = self.lineage.records(inputs)
            try:
                content = reg.fn([current[i] for i in inputs])
            except TransientError:
                raise
            except Exception as e:
                return self._quarantine(op_id, old.record_id, f"replay_error: {type(e).__name__}")
            self.lineage.insert_pending(
                record_id=new_id,
                agent=old.agent,
                tier=old.tier,
                content=content,
                retract_token=token,
                explicit_sources=sorted(old.explicit_sources),
                parents=[(i, "input") for i in inputs],
                fn_op=old.fn_op,
                fn_version=old.fn_version,
                hold_op_id=op_id,  # held until the post-repair check passes
                supersedes=old.record_id,
            )
            new = self.lineage.nodes([new_id])[new_id]
        content = new.content or ""
        if new.status == "pending":  # reconcile via token: adopt an earlier write, never duplicate
            store = self.rt._store(new.tier)
            adapter_id = store.find_by_token(token) or store.write(
                content, {"retract_token": token, "record_id": new_id}
            )
            self.lineage.activate(new_id, adapter_id)
            new = self.lineage.nodes([new_id])[new_id]

        supported = [r.content for r in self.lineage.records(inputs).values() if r.content]
        if new.status == "quarantined" or forbidden_facts(
            content, retracted, supported, self.embed
        ):
            self.lineage.set_status(new_id, "quarantined")
            effect, detail = "replacement_quarantined", "repair_unverified"
        else:
            self.lineage.clear_hold(new_id)
            effect, detail = "rederived", None
        self.journal.update_action(op_id, old.record_id, state="done", effect=effect, detail=detail)

    # -- PURGE ------------------------------------------------------------------------------

    def _purge(self, op_id: str) -> None:
        """After all repairs (post-repair checks need the old content)."""
        for a in self.journal.actions(op_id):
            status: Status
            if a.action == "delete":
                status = "tombstoned"
            elif a.effect in ("rederived", "replacement_quarantined"):
                status = "retracted"
            else:
                continue
            self.journal.heartbeat(op_id)
            n = self.lineage.nodes([a.record_id])[a.record_id]
            if n.status not in PURGED:
                store = self.rt._store(n.tier)
                adapter_id = n.adapter_record_id or store.find_by_token(n.retract_token)
                if adapter_id:
                    store.delete(adapter_id)
                    if store.contains(adapter_id):
                        raise RetractError(f"adapter still contains {adapter_id} after delete")
                    self.journal.update_action(op_id, a.record_id, adapter_record_id=adapter_id)
                self.lineage.purge(a.record_id, status)
            if a.action == "delete":
                self.journal.update_action(op_id, a.record_id, state="done", effect="deleted")

    # -- VERIFY / FINALIZE --------------------------------------------------------------------

    def _verify(self, op_id: str, source: str) -> dict[str, Any]:
        actions = self.journal.actions(op_id)
        nodes = self.lineage.nodes(a.record_id for a in actions)
        checks = {
            "all_actions_done": all(a.state == "done" for a in actions),
            "no_readable_record_carries_source_label": not self.lineage.readable_with_label(source),
            "no_readable_record_has_unreadable_input": (
                not self.lineage.readable_with_unreadable_parent()
            ),
            "purged_ids_absent_from_adapters": all(
                not self.rt._store(nodes[a.record_id].tier).contains(a.adapter_record_id)
                for a in actions
                if a.adapter_record_id
            ),
            "orphan_scan_clean": self.rt.orphan_scan().clean,
        }
        failed = sorted(k for k, ok in checks.items() if not ok)
        if failed:
            raise RetractError(f"structural verification failed: {failed}")
        return {
            "structural": {"status": "pass", "passed": len(checks), "total": len(checks)},
            # probes arrive in M4
            "retrieval_residue": {"status": "not_run", "reason": "no probes in this version"},
        }

    def _finalize(self, op_id: str, verification: dict[str, Any]) -> receipts.Receipt:
        op = self.journal.get(op_id)
        assert op is not None
        actions = self.journal.actions(op_id)
        nodes = self.lineage.nodes(a.record_id for a in actions)
        contained = any(a.effect in QUARANTINED_EFFECTS for a in actions)
        outcome = "contained" if contained else "completed"
        body = {
            "op_id": op_id,
            "outcome": outcome,
            "receipt_type": "containment" if contained else "retraction",
            "source_ref": op["source_ref"],
            "mode": op["mode"],
            "timestamp": datetime.now(UTC).isoformat(),
            "plan": {
                "records": len(actions),
                "agents": sorted({n.agent for n in nodes.values()}),
                "tiers": sorted({n.tier for n in nodes.values()}),
            },
            "actions": [
                {
                    "record_id": a.record_id,
                    "content_hash": a.content_hash,
                    "action": a.action,
                    "reason": a.reason,
                    "effect": a.effect,
                    "state": a.state,
                    "new_record_id": a.new_record_id,
                    "detail": a.detail,
                }
                for a in actions
            ],
            "verification": verification,
        }
        with self.lineage.tx() as c:  # receipt and final op state in one transaction
            self.journal.heartbeat(op_id)
            receipt = receipts.append(c, body)
            c.execute(
                "UPDATE operations SET state = 'finalized', outcome = ?, owner_token = NULL,"
                " lease_expires_at = NULL WHERE op_id = ?",
                (outcome, op_id),
            )
            c.execute("UPDATE records SET hold_op_id = NULL WHERE hold_op_id = ?", (op_id,))
        return receipt
