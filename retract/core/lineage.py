"""SQLite lineage store: records, parent edges, explicit sources and effective labels."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from retract.core.errors import InputNotReadableError, TombstonedSourceError
from retract.core.models import Record, Status

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
READABLE = "status = 'active' AND hold_op_id IS NULL"  # the guard rule


def _marks(n: int) -> str:
    return ",".join("?" * n)


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

    def source_ref(self, source_id: str) -> str:
        """Opaque, stable reference to a source for tombstones and receipts."""
        return hmac.new(self._pseudonym_key, source_id.encode(), hashlib.sha256).hexdigest()

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
    ) -> None:
        """Write-path step 1, one tx: reject unreadable parents and tombstoned labels, then
        journal a pending row with its edges and effective labels."""
        parent_ids = [p for p, _ in parents]
        sources = list(dict.fromkeys(explicit_sources))
        with self.tx() as c:
            ok = {
                r[0]
                for r in c.execute(
                    f"SELECT record_id FROM records WHERE record_id IN ({_marks(len(parent_ids))})"
                    f" AND {READABLE}",
                    parent_ids,
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
                " fn_op, fn_version, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                (
                    record_id,
                    agent,
                    tier,
                    content,
                    retract_token,
                    fn_op,
                    fn_version,
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
        with self.tx() as c:
            ids = self.closure(source_id)
            c.execute(
                "INSERT OR IGNORE INTO tombstones (source_ref, op_id, created_at) VALUES (?, ?, ?)",
                (self.source_ref(source_id), op_id, datetime.now(UTC).isoformat()),
            )
            c.execute(
                f"UPDATE records SET hold_op_id = ? WHERE record_id IN ({_marks(len(ids))})"
                " AND hold_op_id IS NULL",
                [op_id, *ids],
            )
        return ids

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
