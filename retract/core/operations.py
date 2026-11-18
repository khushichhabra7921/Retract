"""Operation journal and op ownership.

Locking is per closure: an op holds the records of its closure (`records.hold_op_id`, taken
atomically at BLOCK), so retractions with disjoint closures run in parallel and an overlapping
one is refused. The lease says which process owns an op: the owner renews it (heartbeat)
during repair, and `resume` may take over on lease expiry or when the owner token matches, so
a crashed process never blocks future work permanently.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from retract.core.errors import OperationInProgressError
from retract.core.lineage import Conn, LineageStore


@dataclass(frozen=True)
class Action:
    op_id: str
    record_id: str
    seq: int
    action: str
    reason: str
    state: str
    effect: str | None
    content_hash: str | None
    new_record_id: str | None
    retract_token: str | None
    adapter_record_id: str | None
    detail: str | None


class Journal:
    def __init__(
        self,
        lineage: LineageStore,
        owner_token: str,
        lease_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.lineage = lineage
        self.owner_token = owner_token
        self.lease_seconds = lease_seconds
        self.clock = clock

    @property
    def conn(self) -> Conn:
        return self.lineage.conn

    def create(
        self,
        op_id: str,
        source_ref: str,
        mode: str,
        closure_hash: str,
        actions: Sequence[tuple[str, str, str, str]],  # (record_id, action, reason, content_hash)
    ) -> None:
        """Call inside the BLOCK tx."""
        self.conn.execute(
            "INSERT INTO operations (op_id, source_ref, mode, plan_closure_hash, state,"
            " owner_token, lease_expires_at, created_at) VALUES (?, ?, ?, ?, 'executing', ?, ?, ?)",
            (
                op_id,
                source_ref,
                mode,
                closure_hash,
                self.owner_token,
                self.clock() + self.lease_seconds,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.executemany(
            "INSERT INTO op_actions (op_id, record_id, seq, action, reason, state, content_hash)"
            " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            [(op_id, rid, i, act, why, h) for i, (rid, act, why, h) in enumerate(actions)],
        )

    def get(self, op_id: str) -> Any:
        return self.conn.execute("SELECT * FROM operations WHERE op_id = ?", (op_id,)).fetchone()

    def op_for_source(self, source_ref: str) -> Any:
        """The operation that tombstoned this source, if any."""
        return self.conn.execute(
            "SELECT o.* FROM tombstones t JOIN operations o ON o.op_id = t.op_id"
            " WHERE t.source_ref = ?",
            (source_ref,),
        ).fetchone()

    def take_over(self, op_id: str) -> None:
        """Resume ownership: allowed on lease expiry or owner match, never over a live owner."""
        with self.lineage.tx():
            op = self.get(op_id)
            assert op is not None
            if (
                op["state"] == "executing"
                and op["lease_expires_at"] > self.clock()
                and op["owner_token"] != self.owner_token
            ):
                raise OperationInProgressError(op_id)
            self.conn.execute(
                "UPDATE operations SET state = 'executing', outcome = NULL, error = NULL,"
                " owner_token = ?, lease_expires_at = ? WHERE op_id = ?",
                (self.owner_token, self.clock() + self.lease_seconds, op_id),
            )

    def heartbeat(self, op_id: str) -> None:
        cur = self.conn.execute(
            "UPDATE operations SET lease_expires_at = ?"
            " WHERE op_id = ? AND owner_token = ? AND state = 'executing'",
            (self.clock() + self.lease_seconds, op_id, self.owner_token),
        )
        if cur.rowcount != 1:
            raise OperationInProgressError(op_id, "owned by another process (lease lost)")

    def actions(self, op_id: str) -> list[Action]:
        rows = self.conn.execute(
            "SELECT op_id, record_id, seq, action, reason, state, effect, content_hash,"
            " new_record_id, retract_token, adapter_record_id, detail"
            " FROM op_actions WHERE op_id = ? ORDER BY seq",
            (op_id,),
        )
        return [Action(*r) for r in rows]

    def action(self, op_id: str, record_id: str) -> Action:
        return next(a for a in self.actions(op_id) if a.record_id == record_id)

    def sources_of(self, op_id: str) -> list[str]:
        """Explicit sources on the op's records; one of them hashes to the op's source_ref."""
        rows = self.conn.execute(
            "SELECT DISTINCT es.source_id FROM explicit_sources es"
            " JOIN op_actions a ON a.record_id = es.record_id WHERE a.op_id = ?",
            (op_id,),
        )
        return [str(r[0]) for r in rows]

    def update_action(self, op_id: str, record_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE op_actions SET {cols} WHERE op_id = ? AND record_id = ?",
            [*fields.values(), op_id, record_id],
        )

    def fail(self, op_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE operations SET state = 'failed', outcome = 'failed', error = ?,"
            " owner_token = NULL, lease_expires_at = NULL WHERE op_id = ? AND owner_token = ?",
            (error[:500], op_id, self.owner_token),
        )
