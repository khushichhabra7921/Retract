"""Mem0-specific integration checks: history residue (PLAN.md, L4 note) and the full SDK path.

Run with: uv sync --extra mem0 --extra embed; uv run python -m pytest -m integration
"""

import sqlite3

import pytest

from retract import Retract, src
from retract.adapters.mem0 import Mem0Adapter, local_config

pytestmark = pytest.mark.integration

MARKER = "Zanzibarite"


@pytest.fixture
def mem0(tmp_path):
    a = Mem0Adapter(local_config(tmp_path / "m"))
    yield a
    a.close()


def history(a, aid):
    con = sqlite3.connect(a.history_db)
    try:
        return con.execute(
            "SELECT old_memory, new_memory FROM history WHERE memory_id = ?", (aid,)
        ).fetchall()
    finally:
        con.close()


def test_delete_purges_history_rows_and_bytes(mem0):
    aid = mem0.write(
        f"Priya's codename is {MARKER}.", {"retract_token": "write:r1", "record_id": "r1"}
    )
    assert history(mem0, aid)  # Mem0 journals the text on add
    mem0.delete(aid)
    assert not mem0.contains(aid) and history(mem0, aid) == []
    assert MARKER.encode() not in mem0.history_db.read_bytes()  # secure_delete overwrote it


def test_retract_through_mem0_tier(tmp_path, mem0):
    rt = Retract(tmp_path / "l.db", stores={"research": mem0})
    p = rt.add(
        f"Priya's codename is {MARKER}.",
        agent="research",
        tier="research",
        sources=[src.user("u_priya")],
    )
    assert [r.record_id for r in rt.search(MARKER, tier="research")] == [p.record_id]
    receipt = rt.retract(src.user("u_priya"))
    assert receipt.outcome == "completed" and receipt.verify().ok
    assert mem0.list_ids() == [] and MARKER.encode() not in mem0.history_db.read_bytes()
    rt.close()
