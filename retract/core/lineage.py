"""SQLite lineage store: records, parent edges, explicit sources and effective labels."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from retract.core.errors import InputNotReadableError, TombstonedSourceError
from retract.core.models import Record, Status

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
READABLE = "status = 'active' AND hold_op_id IS NULL"  # the guard rule


def _marks(n: int) -> str:
    return ",".join("?" * n)


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
    def __init__(self, path: str | Path, pseudonym_key: bytes) -> None:
        self._pseudonym_key = pseudonym_key
        # Autocommit mode: no implicit transactions, so guarded reads never hold a stale snapshot.
        self.conn = sqlite3.connect(str(path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        for pragma in ("journal_mode = WAL", "foreign_keys = ON", "busy_timeout = 5000"):
            self.conn.execute(f"PRAGMA {pragma}")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        current = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            if version <= current:
                continue
            script = path.read_text("utf-8")
            try:
                self.conn.executescript(
                    f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
                )
            except BaseException:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise

    def keyed_hash(self, text: str) -> str:
        """HMAC-SHA256 under the pseudonym key: pseudonymous evidence, not anonymous data."""
        return hmac.new(self._pseudonym_key, text.encode(), hashlib.sha256).hexdigest()

    def source_ref(self, source_id: str) -> str:
        """Opaque, stable reference to a source for tombstones and receipts."""
        return self.keyed_hash(source_id)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

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
    ) -> None:
        """Write-path step 1, one tx: reject unreadable parents and tombstoned labels, then
        journal a pending row with its edges and effective labels.

        A repair write passes its `hold_op_id`: parents held by that same op are allowed."""
        parent_ids = [p for p, _ in parents]
        sources = list(dict.fromkeys(explicit_sources))
        with self.tx() as c:
            ok = {
                r[0]
                for r in c.execute(
                    f"SELECT record_id FROM records WHERE record_id IN ({_marks(len(parent_ids))})"
                    " AND status = 'active' AND (hold_op_id IS NULL OR hold_op_id = ?)",
                    [*parent_ids, hold_op_id],
                )
            }
            if bad := [p for p in parent_ids if p not in ok]:
                raise InputNotReadableError(bad)
            inherited = {
                r[0]
                for r in c.execute(
                    f"SELECT source_id FROM labels WHERE record_id IN ({_marks(len(parent_ids))})",
                    parent_ids,
                )
            }
            labels = sorted(inherited | set(sources))
            refs = [self.source_ref(s) for s in labels]
            if dead := [
                r[0]
                for r in c.execute(
                    f"SELECT source_ref FROM tombstones WHERE source_ref IN ({_marks(len(refs))})",
                    refs,
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
        rows = self.conn.execute(
            f"SELECT * FROM records WHERE record_id IN ({_marks(len(ids))}){where}", ids
        ).fetchall()
        found = [r["record_id"] for r in rows]
        labels: dict[str, set[str]] = {i: set() for i in found}
        for rid, source in self.conn.execute(
            f"SELECT record_id, source_id FROM labels WHERE record_id IN ({_marks(len(found))})",
            found,
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
            ids = self.closure(source_id)
            self.tombstone_and_hold(source_id, op_id, ids)
        return ids

    def tombstone_and_hold(self, source_id: str, op_id: str, ids: Iterable[str]) -> None:
        """Call inside a tx."""
        ids = list(ids)
        self.conn.execute(
            "INSERT OR IGNORE INTO tombstones (source_ref, op_id, created_at) VALUES (?, ?, ?)",
            (self.source_ref(source_id), op_id, datetime.now(UTC).isoformat()),
        )
        self.conn.execute(
            f"UPDATE records SET hold_op_id = ? WHERE record_id IN ({_marks(len(ids))})",
            [op_id, *ids],
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
        m = _marks(len(ids))
        explicit: dict[str, set[str]] = {i: set() for i in ids}
        for rid, source in self.conn.execute(
            f"SELECT record_id, source_id FROM explicit_sources WHERE record_id IN ({m})", ids
        ):
            explicit[rid].add(source)
        parents: dict[str, list[tuple[str, str, int]]] = {i: [] for i in ids}
        for child, parent, kind, pos in self.conn.execute(
            f"SELECT child_id, parent_id, kind, position FROM edges WHERE child_id IN ({m})"
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
            for r in self.conn.execute(f"SELECT * FROM records WHERE record_id IN ({m})", ids)
        }

    def set_status(self, record_id: str, status: Status) -> None:
        self.conn.execute("UPDATE records SET status = ? WHERE record_id = ?", (status, record_id))

    def purge(self, record_id: str, status: Status) -> None:
        """After a verified adapter purge: drop the content and set the final status."""
        self.conn.execute(
            "UPDATE records SET content = NULL, status = ? WHERE record_id = ?",
            (status, record_id),
        )

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
