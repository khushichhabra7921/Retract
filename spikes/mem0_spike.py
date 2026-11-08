"""Mem0 spike (PLAN Rev 3, Week 1). Answers the "pending verification" items for mem0ai 2.2.1.

Run (ephemeral env, nothing installed into the repo):
    uv run --no-project --python 3.12 --with mem0ai==2.2.1 --with fastembed python spikes/mem0_spike.py

Needs network once to download fastembed models; the measured phase must make zero
non-loopback connections. No API key is used. Exits 1 if a check the adapter depends on fails.
"""

import os
import shutil
import socket
import sqlite3
import tempfile
import uuid
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="mem0_spike_"))
os.environ["MEM0_TELEMETRY"] = "false"  # must be set before `import mem0`
os.environ["MEM0_DIR"] = str(TMP / "mem0dir")  # keep the user's ~/.mem0 untouched
for k in ("OPENAI_API_KEY", "GROQ_API_KEY"):
    os.environ.pop(k, None)

# Count outbound connections so "no network" is measured, not assumed.
NET: list[str] = []
_real_connect = socket.socket.connect


def _counting_connect(self, address):
    host = address[0] if isinstance(address, tuple) else str(address)
    if host not in ("127.0.0.1", "::1", "localhost"):
        NET.append(host)
    return _real_connect(self, address)


socket.socket.connect = _counting_connect  # type: ignore[method-assign]

from mem0 import Memory  # noqa: E402
from mem0.memory.main import _vector_store_list_rows  # noqa: E402

RESULTS: dict[str, tuple[bool, str]] = {}
REQUIRED = {"construct_local", "uuid4_ids", "metadata_roundtrip", "find_by_token",
            "list_ids", "delete_contains_false", "history_purge"}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS[name] = (ok, detail)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def scan(marker: bytes) -> list[str]:
    """Raw byte scan of every file Mem0 wrote (history DB, qdrant storage, journals)."""
    hits = []
    for p in TMP.rglob("*"):
        if p.is_file():
            try:
                if marker in p.read_bytes():
                    hits.append(str(p.relative_to(TMP)))
            except OSError:
                if p.name != ".lock":  # qdrant's portalocker file is locked while open and holds no data
                    hits.append(f"{p.relative_to(TMP)} (unreadable, not scanned)")
    return hits


def history_rows(db: Path, memory_id: str) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT event, old_memory, new_memory FROM history WHERE memory_id = ?", (memory_id,)
        ).fetchall()
    finally:
        con.close()


# 1. Default Memory() with no key.
try:
    Memory()
    check("construct_default_no_key", True, "Memory() constructed without any API key")
except Exception as e:
    check("construct_default_no_key", False, f"{type(e).__name__}: {str(e)[:120]}")

# 2. Local config: fastembed + local qdrant, LLM given a placeholder key that must never be used.
HISTORY_DB = TMP / "history.db"
CONFIG = {
    "vector_store": {"provider": "qdrant", "config": {
        "collection_name": "spike", "path": str(TMP / "qdrant"),
        "on_disk": True, "embedding_model_dims": 384}},
    "embedder": {"provider": "fastembed", "config": {"model": "BAAI/bge-small-en-v1.5"}},
    "llm": {"provider": "openai", "config": {"api_key": "unused-placeholder"}},
    "history_db_path": str(HISTORY_DB),
}

# Does an LLM block with no api_key at all construct?
try:
    cfg = {**CONFIG, "llm": {"provider": "openai", "config": {}},
           "vector_store": {"provider": "qdrant", "config": {**CONFIG["vector_store"]["config"],
                                                             "path": str(TMP / "qdrant_nokey")}},
           "history_db_path": str(TMP / "history_nokey.db")}
    Memory.from_config(cfg).close()
    check("construct_llm_no_key", True, "openai LLM block without api_key constructs")
except Exception as e:
    check("construct_llm_no_key", False, f"{type(e).__name__}: {str(e)[:120]}")

try:
    m = Memory.from_config(CONFIG)
    check("construct_local", True, "fastembed bge-small-en-v1.5 (384d) + local qdrant + placeholder LLM key")
except Exception as e:
    check("construct_local", False, f"{type(e).__name__}: {e}")
    raise SystemExit(1)

# Warm-up: lazy model downloads (embedder, qdrant BM25 encoder) happen here, outside the measured phase.
warm = m.add([{"role": "user", "content": "warm up"}], user_id="spike", infer=False)
m.delete(warm["results"][0]["id"])
net_warmup = len(NET)
NET.clear()

# 3. Measured phase.
MARKER = f"SPIKEMARKER{uuid.uuid4().hex}"
TEXT = f"Priya prefers vegetarian meals. {MARKER}"
RECORD_ID = str(uuid.uuid4())
TOKEN = f"write:{RECORD_ID}"

res = m.add([{"role": "user", "content": TEXT}], user_id="spike",
            metadata={"retract_token": TOKEN, "record_id": RECORD_ID}, infer=False)
mid = res["results"][0]["id"]
try:
    is_uuid4 = uuid.UUID(mid).version == 4 and mid != RECORD_ID
except ValueError:
    is_uuid4 = False
check("uuid4_ids", is_uuid4, f"adapter id {mid} is Mem0-generated uuid4, caller id not used")

got = m.get(mid) or {}
meta = got.get("metadata", {})
check("metadata_roundtrip", meta.get("retract_token") == TOKEN and meta.get("record_id") == RECORD_ID,
      f"get()['metadata'] = {meta}")

scoped = m.get_all(filters={"user_id": "spike", "retract_token": TOKEN})
scoped_ids = [r["id"] for r in scoped.get("results", [])]
raw_ids = [r.id for r in _vector_store_list_rows(m.vector_store.list(filters={"retract_token": TOKEN}))]
check("find_by_token", scoped_ids == [mid] and raw_ids == [mid],
      f"get_all(user_id+retract_token) -> {scoped_ids}; vector_store.list(retract_token) -> {raw_ids}")

all_ids = [r.id for r in _vector_store_list_rows(m.vector_store.list(top_k=10_000))]
check("list_ids", all_ids == [mid], f"vector_store.list(top_k=10000) -> {all_ids}")

hits = [r["id"] for r in m.search("vegetarian meals", filters={"user_id": "spike"}).get("results", [])]
check("search_finds", mid in hits, f"search -> {hits}")

con = sqlite3.connect(HISTORY_DB)
msg_rows = con.execute("SELECT COUNT(*) FROM messages WHERE content LIKE ?", (f"%{MARKER}%",)).fetchone()[0]
con.close()
check("messages_table_empty_infer_false", msg_rows == 0, f"{msg_rows} messages rows contain the marker")

collections = [c.name for c in m.vector_store.client.get_collections().collections]
check("entity_store_unused_infer_false", m._entity_store is None,
      f"_entity_store={m._entity_store!r}; qdrant collections={collections}")

check("history_after_add", True, f"rows={history_rows(HISTORY_DB, mid)}")

m.delete(mid)
check("delete_contains_false", m.get(mid) is None and m.vector_store.get(vector_id=mid) is None,
      "get() and vector_store.get() return None after delete")
rows = history_rows(HISTORY_DB, mid)
check("history_keeps_text_after_delete", any(MARKER in (o or "") + (n or "") for _, o, n in rows),
      f"rows={rows}")  # expected True: this is the residue the adapter must purge

net_measured = len(NET)
check("no_network_measured_phase", net_measured == 0,
      f"{net_measured} non-loopback connects after warm-up ({net_warmup} during warm-up)")

check("raw_scan_before_purge", True, f"marker bytes found in: {scan(MARKER.encode())}")

# 4. Per-memory history purge by direct SQL from a second connection while Mem0 is live (as the adapter would).
con = sqlite3.connect(HISTORY_DB)
con.execute("PRAGMA secure_delete = ON")  # overwrite freed pages so bytes do not linger
with con:
    n = con.execute("DELETE FROM history WHERE memory_id = ?", (mid,)).rowcount
con.close()
check("history_purge", history_rows(HISTORY_DB, mid) == [] and m.history(mid) == [],
      f"deleted {n} rows; Memory.history(mid) -> {m.history(mid)}")
m.close()
# Memory.close() leaves the local qdrant client open; its portalocker lock blocks reopening the path.
m.vector_store.client.close()
leftover = scan(MARKER.encode())
check("raw_scan_after_purge", True, f"marker bytes found in: {leftover}")  # informational (L4)

shutil.rmtree(TMP, ignore_errors=True)
failed = sorted(k for k in REQUIRED if not RESULTS.get(k, (False,))[0])
print("\nREQUIRED FAILURES:", failed or "none")
raise SystemExit(1 if failed else 0)
