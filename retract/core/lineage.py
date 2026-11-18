"""Lineage store: records, parent edges, explicit sources and effective labels.

Backends: SQLite (a file path) or Postgres (a postgresql:// URL, `postgres` extra). The SQL is
written once in the subset both share.

Write serialization. Every write transaction that could race a BLOCK is serialized against it
by source: a write takes a shared lock on each label of its new record, a BLOCK an exclusive
lock on its source. Labels never change after insert, and a record joins closure(S) only by
carrying label S, so a racing write either commits first (and the BLOCK's closure, computed
under the lock, holds it) or runs after and is rejected (tombstoned label or held input).
SQLite's BEGIN IMMEDIATE already serializes every writer, so `lock` is a no-op there.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from retract.core.errors import (
    InputNotReadableError,
    OperationInProgressError,
    TombstonedSourceError,
)
from retract.core.models import Record, Status

if TYPE_CHECKING:
    from retract.core.postgres import PgConn

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
READABLE = "status = 'active' AND hold_op_id IS NULL"  # the guard rule
Query = tuple[str, str, int, list[str]]  # (query, tier, k, result record_ids)


BATCH = 30_000  # ids per IN list: under SQLite's 32,766 and Postgres's 65,535 parameters


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _batches(ids: Sequence[str]) -> Iterator[Sequence[str]]:
    """Non-empty slices of at most BATCH ids (Postgres also rejects an empty `IN ()`)."""
    return (ids[i : i + BATCH] for i in range(0, len(ids), BATCH))


class Cursor(Protocol):
    @property
    def rowcount(self) -> int: ...
    def fetchone(self) -> Any: ...
    def fetchall(self) -> list[Any]: ...
    def __iter__(self) -> Iterator[Any]: ...


class Conn(Protocol):
    """The DB-API surface the store uses: sqlite3.Connection, or PgConn for Postgres."""

    def execute(self, sql: str, parameters: Any = ..., /) -> Cursor: ...
    def executemany(self, sql: str, parameters: Iterable[Any], /) -> Any: ...
    def close(self) -> None: ...


def is_postgres(target: str | Path) -> bool:
    return str(target).startswith(("postgresql://", "postgres://"))


@dataclass(frozen=True)
class Node:
    """Full lineage view of one record, for the engine."""

    record_id: str
    agent: str
    tier: str
    content: str | None
    status: str
    hold_op_id: str | None
    retract_token: str
    adapter_record_id: str | None
    fn_op: str | None
    fn_version: str | None
    explicit_sources: frozenset[str]
    parents: tuple[tuple[str, str, int], ...]  # (parent_id, kind, position), position order


class LineageStore:
    def __init__(self, target: str | Path, pseudonym_key: bytes) -> None:
        """`target`: a SQLite file path, or a postgresql:// URL."""
        self._pseudonym_key = pseudonym_key
        self.conn: Conn
        self._pg: PgConn | None = None
        if is_postgres(target):
            from retract.core.postgres import PgConn

            self._pg = PgConn(str(target))
            self.conn = self._pg
            self._migrate_postgres(self._pg)
            return
        # Autocommit mode: no implicit transactions, so guarded reads never hold a stale snapshot.
        sqlite = sqlite3.connect(str(target), isolation_level=None)
        sqlite.row_factory = sqlite3.Row
        for pragma in ("journal_mode = WAL", "foreign_keys = ON", "busy_timeout = 5000"):
            sqlite.execute(f"PRAGMA {pragma}")
        self.conn = sqlite
        self._migrate_sqlite(sqlite)

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _migrations(current: int) -> list[tuple[int, str]]:
        out = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            if version > current:
                out.append((version, path.read_text("utf-8")))
        return out

    def _migrate_sqlite(self, conn: sqlite3.Connection) -> None:
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        for version, script in self._migrations(current):
            try:
                conn.executescript(
                    f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
                )
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def _migrate_postgres(self, pg: PgConn) -> None:
        with self.tx():
            self.lock(["retract:migrate"])  # many processes may start at once
            pg.executescript("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
            current = self.conn.execute("SELECT max(version) FROM schema_version").fetchone()[0]
            for version, script in self._migrations(int(current or 0)):
                pg.executescript(script)
                pg.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))

    def keyed_hash(self, text: str) -> str:
        """HMAC-SHA256 under the pseudonym key: pseudonymous evidence, not anonymous data."""
        return hmac.new(self._pseudonym_key, text.encode(), hashlib.sha256).hexdigest()

    def source_ref(self, source_id: str) -> str:
        """Opaque, stable reference to a source for tombstones and receipts."""
        return self.keyed_hash(source_id)

    @contextmanager
    def tx(self) -> Iterator[Conn]:
        self.conn.execute("BEGIN" if self._pg else "BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _in(self, sql: str, ids: Sequence[str], after: Sequence[Any] = ()) -> Iterator[Any]:
        """Run `sql`, whose `{}` is an IN list, once per batch of `ids`; yield every row."""
        for part in _batches(ids):
            yield from self.conn.execute(sql.format(_marks(len(part))), [*part, *after])

    def lock(self, names: Iterable[str], shared: bool = False) -> None:
        """Inside a tx: serialize on `names` until commit (see the module docstring)."""
        if self._pg:
            self._pg.advisory_locks(names, shared)

    def insert_pending(
        self,
        *,
        record_id: str,
        agent: str,
        tier: str,
        content: str,
        retract_token: str,
        explicit_sources: Sequence[str],
        parents: Sequence[tuple[str, str]],
        fn_op: str | None,
        fn_version: str | None,
        hold_op_id: str | None = None,
        supersedes: str | None = None,
        queries: Sequence[Query] = (),
    ) -> None:
        """Write-path step 1, one tx: reject unreadable parents and tombstoned labels, then
        journal a pending row with its edges and effective labels.

        A repair write passes its `hold_op_id`: parents held by that same op are allowed."""
        parent_ids = [p for p, _ in parents]
        sources = list(dict.fromkeys(explicit_sources))
        with self.tx() as c:
            inherited = {
                r[0]
                for r in self._in(
                    "SELECT source_id FROM labels WHERE record_id IN ({})", parent_ids
                )
            }
            labels = sorted(inherited | set(sources))
            self.lock(labels, shared=True)  # before the checks: BLOCK locks its source exclusively
            ok = {
                r[0]
                for r in self._in(
                    "SELECT record_id FROM records WHERE record_id IN ({})"
                    " AND status = 'active' AND (hold_op_id IS NULL OR hold_op_id = ?)",
                    parent_ids,
                    after=[hold_op_id],
                )
            }
            if bad := [p for p in parent_ids if p not in ok]:
                raise InputNotReadableError(bad)
            refs = [self.source_ref(s) for s in labels]
            if dead := [
                r[0]
                for r in self._in(
                    "SELECT source_ref FROM tombstones WHERE source_ref IN ({})", refs
                )
            ]:
                raise TombstonedSourceError(dead)
            c.execute(
                "INSERT INTO records (record_id, agent, tier, content, status, retract_token,"
                " fn_op, fn_version, hold_op_id, supersedes, created_at)"
                " VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    agent,
                    tier,
                    content,
                    retract_token,
                    fn_op,
                    fn_version,
                    hold_op_id,
                    supersedes,
                    datetime.now(UTC).isoformat(),
                ),
            )
            c.executemany(
                "INSERT INTO edges (child_id, parent_id, kind, position) VALUES (?, ?, ?, ?)",
                [(record_id, p, kind, i) for i, (p, kind) in enumerate(parents)],
            )
            c.executemany(
                "INSERT INTO explicit_sources (record_id, source_id) VALUES (?, ?)",
                [(record_id, s) for s in sources],
            )
            c.executemany(
                "INSERT INTO labels (record_id, source_id) VALUES (?, ?)",
                [(record_id, s) for s in labels],
            )
            c.executemany(
                "INSERT INTO record_queries (record_id, position, query, tier, k, result_ids)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (record_id, i, q, t, k, json.dumps(list(ids)))
                    for i, (q, t, k, ids) in enumerate(queries)
                ],
            )

    def activate(self, record_id: str, adapter_record_id: str) -> None:
        """Write-path step 3."""
        self.conn.execute(
            "UPDATE records SET status = 'active', adapter_record_id = ?"
            " WHERE record_id = ? AND status = 'pending'",
            (adapter_record_id, record_id),
        )

    def records(self, record_ids: Sequence[str], readable_only: bool = False) -> dict[str, Record]:
        ids = list(dict.fromkeys(record_ids))
        where = f" AND {READABLE}" if readable_only else ""
        rows = list(self._in("SELECT * FROM records WHERE record_id IN ({})" + where, ids))
        found = [r["record_id"] for r in rows]
        labels: dict[str, set[str]] = {i: set() for i in found}
        for rid, source in self._in(
            "SELECT record_id, source_id FROM labels WHERE record_id IN ({})", found
        ):
            labels[rid].add(source)
        return {
            r["record_id"]: Record(
                record_id=r["record_id"],
                content=r["content"],
                agent=r["agent"],
                tier=r["tier"],
                status=cast(Status, r["status"]),
                labels=frozenset(labels[r["record_id"]]),
                adapter_record_id=r["adapter_record_id"],
            )
            for r in rows
        }

    def block(self, source_id: str, op_id: str) -> set[str]:
        """BLOCK core, one tx: tombstone the source and hold its whole closure.

        Concurrent writes either committed their pending row first (so the recomputed closure
        holds them) or run their write tx afterwards and are rejected (held input or
        tombstoned label). Operations, lease lock and stale-plan check wrap this in M3.
        """
        with self.tx():
            self.lock([source_id])
            ids = self.closure(source_id)
            self.tombstone_and_hold(source_id, op_id, ids)
        return ids

    def tombstone_and_hold(self, source_id: str, op_id: str, ids: Iterable[str]) -> None:
        """Call inside a tx, after `lock([source_id])` and computing the closure.

        Holds are the per-closure lock: a record is taken only if no other operation holds
        it. If one does, raise, so the caller's tx rolls back and nothing is blocked."""
        ids = list(dict.fromkeys(ids))
        self.conn.execute(
            "INSERT INTO tombstones (source_ref, op_id, created_at) VALUES (?, ?, ?)"
            " ON CONFLICT DO NOTHING",
            (self.source_ref(source_id), op_id, datetime.now(UTC).isoformat()),
        )
        held = sum(
            self.conn.execute(
                f"UPDATE records SET hold_op_id = ? WHERE record_id IN ({_marks(len(part))})"
                " AND hold_op_id IS NULL",
                [op_id, *part],
            ).rowcount
            for part in _batches(ids)
        )
        if held != len(ids):
            row = next(
                self._in(
                    "SELECT hold_op_id FROM records WHERE record_id IN ({}) AND hold_op_id <> ?",
                    ids,
                    after=[op_id],
                ),
                None,
            )
            raise OperationInProgressError(
                str(row[0]) if row else "?", "holding records this retraction needs"
            )

    def closure(self, source_id: str) -> set[str]:
        """Records owning `source_id` plus every descendant along parent edges."""
        rows = self.conn.execute(
            """
            WITH RECURSIVE c(id) AS (
                SELECT record_id FROM explicit_sources WHERE source_id = ?
                UNION
                SELECT e.child_id FROM edges e JOIN c ON e.parent_id = c.id
            )
            SELECT id FROM c
            """,
            (source_id,),
        )
        return {str(r[0]) for r in rows}

    def pending(self) -> list[tuple[str, str, str]]:
        """(record_id, tier, retract_token) of every pending row."""
        rows = self.conn.execute(
            "SELECT record_id, tier, retract_token FROM records WHERE status = 'pending'"
        )
        return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]

    def adapter_ids(self, tier: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT adapter_record_id FROM records"
            " WHERE tier = ? AND adapter_record_id IS NOT NULL",
            (tier,),
        )
        return {str(r[0]) for r in rows}

    def nodes(self, record_ids: Iterable[str]) -> dict[str, Node]:
        ids = list(dict.fromkeys(record_ids))
        explicit: dict[str, set[str]] = {i: set() for i in ids}
        for rid, source in self._in(
            "SELECT record_id, source_id FROM explicit_sources WHERE record_id IN ({})", ids
        ):
            explicit[rid].add(source)
        parents: dict[str, list[tuple[str, str, int]]] = {i: [] for i in ids}
        for child, parent, kind, pos in self._in(  # a child's edges share one batch
            "SELECT child_id, parent_id, kind, position FROM edges WHERE child_id IN ({})"
            " ORDER BY position",
            ids,
        ):
            parents[child].append((parent, kind, pos))
        return {
            r["record_id"]: Node(
                record_id=r["record_id"],
                agent=r["agent"],
                tier=r["tier"],
                content=r["content"],
                status=r["status"],
                hold_op_id=r["hold_op_id"],
                retract_token=r["retract_token"],
                adapter_record_id=r["adapter_record_id"],
                fn_op=r["fn_op"],
                fn_version=r["fn_version"],
                explicit_sources=frozenset(explicit[r["record_id"]]),
                parents=tuple(parents[r["record_id"]]),
            )
            for r in self._in("SELECT * FROM records WHERE record_id IN ({})", ids)
        }

    def set_status(self, record_id: str, status: Status) -> None:
        self.conn.execute("UPDATE records SET status = ? WHERE record_id = ?", (status, record_id))

    def purge(self, record_id: str, status: Status) -> None:
        """After a verified adapter purge: drop the content and query log, set the final status."""
        with self.tx() as c:
            c.execute(
                "UPDATE records SET content = NULL, status = ? WHERE record_id = ?",
                (status, record_id),
            )
            c.execute("DELETE FROM record_queries WHERE record_id = ?", (record_id,))

    def queries(self, record_id: str) -> list[Query]:
        rows = self.conn.execute(
            "SELECT query, tier, k, result_ids FROM record_queries WHERE record_id = ?"
            " ORDER BY position",
            (record_id,),
        )
        return [(q, t, k, json.loads(ids)) for q, t, k, ids in rows]

    def clear_hold(self, record_id: str) -> None:
        self.conn.execute("UPDATE records SET hold_op_id = NULL WHERE record_id = ?", (record_id,))

    def readable_with_label(self, source_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT l.record_id FROM labels l JOIN records r ON r.record_id = l.record_id"
            " WHERE l.source_id = ? AND r.status = 'active' AND r.hold_op_id IS NULL",
            (source_id,),
        )
        return [str(r[0]) for r in rows]

    def readable_with_unreadable_parent(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT c.record_id FROM records c"
            " JOIN edges e ON e.child_id = c.record_id"
            " JOIN records p ON p.record_id = e.parent_id"
            " WHERE c.status = 'active' AND c.hold_op_id IS NULL"
            " AND NOT (p.status = 'active' AND p.hold_op_id IS NULL)"
        )
        return [str(r[0]) for r in rows]

    def independent_primaries(self, source_id: str) -> list[str]:
        """Content of readable primary records not labeled with `source_id`: independent
        support that a retracted fact may legitimately still appear in."""
        rows = self.conn.execute(
            "SELECT r.content FROM records r WHERE r.status = 'active' AND r.hold_op_id IS NULL"
            " AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.child_id = r.record_id)"
            " AND NOT EXISTS (SELECT 1 FROM labels l"
            " WHERE l.record_id = r.record_id AND l.source_id = ?)",
            (source_id,),
        )
        return [str(r[0]) for r in rows]
