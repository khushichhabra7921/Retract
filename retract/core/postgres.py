"""Postgres connection for the lineage store, exposing the small sqlite3 surface it uses.

SQL is written once, in the subset both engines share; `?` placeholders become `%s` here.
Install with the `postgres` extra (psycopg 3).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus


class Row(tuple[Any, ...]):
    """A tuple that can also be indexed by column name, like sqlite3.Row."""

    names: dict[str, int]

    def __getitem__(self, key: Any) -> Any:
        return super().__getitem__(self.names[key] if isinstance(key, str) else key)

    def keys(self) -> list[str]:  # so dict(row) works, as with sqlite3.Row
        return list(self.names)


def _rows(cursor: psycopg.Cursor[Any]) -> Callable[[Sequence[Any]], Row]:
    names = {d.name: i for i, d in enumerate(cursor.description or ())}

    def make(values: Sequence[Any]) -> Row:
        row = Row(values)
        row.names = names
        return row

    return make


def _sql(sql: str) -> str:
    # Our SQL has no `?` or `%` inside string literals, so a plain swap is safe.
    return sql.replace("%", "%%").replace("?", "%s")


def lock_key(name: str) -> int:
    """Signed 64-bit advisory-lock key. A collision only serializes two unrelated locks."""
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)


class PgConn:
    def __init__(self, url: str) -> None:
        # Autocommit like the SQLite store: explicit BEGIN/COMMIT, no stale implicit snapshots.
        self._conn: psycopg.Connection[Row] = psycopg.connect(
            url, autocommit=True, row_factory=_rows
        )

    def execute(self, sql: str, parameters: Any = (), /) -> psycopg.Cursor[Row]:
        return self._conn.execute(_sql(sql), list(parameters))

    def executemany(self, sql: str, parameters: Iterable[Any], /) -> None:
        rows = [list(p) for p in parameters]
        if rows:
            self._conn.cursor().executemany(_sql(sql), rows)

    def executescript(self, script: str) -> None:
        self._conn.execute(script.encode())  # no parameters: sent verbatim, many statements

    def advisory_locks(self, names: Iterable[str], shared: bool) -> None:
        """Transaction-scoped; sorted so lock order is the same in every transaction."""
        fn = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
        for name in sorted(set(names)):
            self._conn.execute(f"SELECT {fn}(%s)", (lock_key(name),))

    @property
    def in_transaction(self) -> bool:
        return self._conn.info.transaction_status != TransactionStatus.IDLE

    def close(self) -> None:
        self._conn.close()
