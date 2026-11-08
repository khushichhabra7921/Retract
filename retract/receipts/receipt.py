"""Signed, hash-chained receipts.

Signature: HMAC-SHA256(RETRACT_SIGNING_KEY, canonical JSON body). hash = sha256(body).
Each body carries the previous receipt's hash and a gapless seq.
Documented limit: anyone with DB write access *and* the signing key can rewrite history.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr

from retract.core.errors import RetractError

GENESIS = "0" * 64


def signing_key() -> bytes:
    key = os.environ.get("RETRACT_SIGNING_KEY")
    if not key:
        raise RetractError("RETRACT_SIGNING_KEY is not set in the process environment")
    return key.encode()


def canonical(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def seal(body: dict[str, Any], key: bytes) -> tuple[str, str, str]:
    """(canonical body, hash, signature)."""
    text = canonical(body)
    digest = hashlib.sha256(text.encode()).hexdigest()
    signature = hmac.new(key, text.encode(), hashlib.sha256).hexdigest()
    return text, digest, signature


class VerificationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: str
    integrity: bool  # hash and signature match the body
    chain_link: bool  # prev_hash matches the previous stored receipt
    execution_complete: bool  # the journal shows the op finalized with this outcome
    structural: str  # pass | fail | not_run
    retrieval_residue: str  # pass | fail | not_run

    @property
    def ok(self) -> bool:
        return (
            self.integrity
            and self.chain_link
            and self.execution_complete
            and self.structural == "pass"
            and self.retrieval_residue != "fail"
        )


class ChainReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    receipts: int
    problems: list[str]


class Receipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    body: dict[str, Any]
    hash: str
    signature: str
    _conn: sqlite3.Connection | None = PrivateAttr(default=None)

    @property
    def op_id(self) -> str:
        return str(self.body["op_id"])

    @property
    def outcome(self) -> str:
        return str(self.body["outcome"])

    @property
    def receipt_type(self) -> str:
        return str(self.body["receipt_type"])

    def export(self) -> str:
        """JSON export (body, hash, signature)."""
        return canonical({"body": self.body, "hash": self.hash, "signature": self.signature})

    def verify(self) -> VerificationReport:
        if self._conn is None:
            raise RetractError("receipt is not attached to a Retract instance")
        _, digest, signature = seal(self.body, signing_key())
        integrity = hmac.compare_digest(digest, self.hash) and hmac.compare_digest(
            signature, self.signature
        )
        seq = int(self.body["seq"])
        prev = self._conn.execute("SELECT hash FROM receipts WHERE seq = ?", (seq - 1,)).fetchone()
        expected_prev = prev[0] if prev else (GENESIS if seq == 1 else None)
        op = self._conn.execute(
            "SELECT state, outcome FROM operations WHERE op_id = ?", (self.op_id,)
        ).fetchone()
        dims = self.body["verification"]
        return VerificationReport(
            outcome=self.outcome,
            integrity=integrity,
            chain_link=expected_prev is not None and self.body["prev_hash"] == expected_prev,
            execution_complete=bool(
                op and op["state"] == "finalized" and op["outcome"] == self.outcome
            ),
            structural=dims["structural"]["status"],
            retrieval_residue=dims["retrieval_residue"]["status"],
        )


def load(conn: sqlite3.Connection, op_id: str) -> Receipt | None:
    row = conn.execute("SELECT body, hash, signature FROM receipts WHERE op_id = ?", (op_id,))
    found = row.fetchone()
    if not found:
        return None
    receipt = Receipt(body=json.loads(found[0]), hash=found[1], signature=found[2])
    receipt._conn = conn
    return receipt


def append(conn: sqlite3.Connection, body: dict[str, Any]) -> Receipt:
    """Call inside the FINALIZE tx. Fills seq and prev_hash."""
    last = conn.execute("SELECT seq, hash FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
    body = {**body, "seq": (last[0] + 1) if last else 1, "prev_hash": last[1] if last else GENESIS}
    text, digest, signature = seal(body, signing_key())
    conn.execute(
        "INSERT INTO receipts (seq, op_id, body, hash, signature) VALUES (?, ?, ?, ?, ?)",
        (body["seq"], body["op_id"], text, digest, signature),
    )
    receipt = Receipt(body=body, hash=digest, signature=signature)
    receipt._conn = conn
    return receipt


def verify_chain(conn: sqlite3.Connection) -> ChainReport:
    key = signing_key()
    problems: list[str] = []
    prev_hash = GENESIS
    rows = conn.execute("SELECT seq, op_id, body, hash, signature FROM receipts ORDER BY seq")
    n = 0
    for n, (seq, op_id, text, digest, signature) in enumerate(rows, start=1):
        body = json.loads(text)
        _, want_hash, want_sig = seal(body, key)
        if seq != n or body.get("seq") != seq:
            problems.append(f"seq {seq}: expected {n} (gap or reorder)")
        if body.get("op_id") != op_id:
            problems.append(f"seq {seq}: op_id mismatch")
        if not hmac.compare_digest(want_hash, digest):
            problems.append(f"seq {seq}: body hash mismatch")
        if not hmac.compare_digest(want_sig, signature):
            problems.append(f"seq {seq}: bad signature")
        if body.get("prev_hash") != prev_hash:
            problems.append(f"seq {seq}: broken chain link")
        prev_hash = digest
    return ChainReport(ok=not problems, receipts=n, problems=problems)
