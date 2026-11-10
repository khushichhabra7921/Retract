"""Mem0 adapter (mem0ai==2.2.1). Behaviour verified by spikes/mem0_spike.py (PLAN.md, Mem0 notes).

Install with `uv sync --extra mem0 --extra embed`. Writes use `infer=False`, so the LLM client
Mem0 always constructs is never called and no network is used after the one-time model download.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from retract.core.interfaces import MemoryAdapter, StoredItem

LIST_LIMIT = 100_000  # shortcut: one qdrant scroll page, upgrade to paging past 100k items


def local_config(
    path: str | Path,
    collection: str = "retract",
    model: str = "BAAI/bge-small-en-v1.5",
    dims: int = 384,
) -> dict[str, Any]:
    """Fully local Mem0 config: fastembed + on-disk qdrant + history DB under `path`."""
    path = Path(path)
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": collection,
                "path": str(path / "qdrant"),
                "on_disk": True,
                "embedding_model_dims": dims,
            },
        },
        "embedder": {"provider": "fastembed", "config": {"model": model}},
        # Memory() always builds an LLM client; with infer=False it is never called.
        "llm": {"provider": "openai", "config": {"api_key": "unused-infer-false"}},
        "history_db_path": str(path / "history.db"),
    }


class Mem0Adapter(MemoryAdapter):
    idempotent_put = False  # Mem0 generates uuid4 ids itself
    supports_list_ids = True

    def __init__(self, config: Mapping[str, Any], scope: str = "retract") -> None:
        history_db = Path(config["history_db_path"])
        os.environ["MEM0_TELEMETRY"] = "false"  # read when mem0 is imported: keep it offline
        os.environ.setdefault("MEM0_DIR", str(history_db.parent / "mem0dir"))
        from mem0 import Memory

        self.memory = Memory.from_config(dict(config))
        self.history_db = history_db
        self.scope = scope

    def write(self, content: str, metadata: Mapping[str, str]) -> str:
        res = self.memory.add(
            [{"role": "user", "content": content}],
            user_id=self.scope,
            metadata=dict(metadata),
            infer=False,
        )
        return str(res["results"][0]["id"])

    def get(self, adapter_record_id: str) -> StoredItem | None:
        r = self.memory.get(adapter_record_id)
        return StoredItem(r["id"], r["memory"], r.get("metadata") or {}) if r else None

    def search(self, query: str, k: int) -> list[StoredItem]:
        res = self.memory.search(query, top_k=k, filters={"user_id": self.scope}, threshold=0.0)
        return [StoredItem(r["id"], r["memory"], r.get("metadata") or {}) for r in res["results"]]

    def delete(self, adapter_record_id: str) -> None:
        if self.memory.vector_store.get(vector_id=adapter_record_id) is not None:
            self.memory.delete(adapter_record_id)
        # Mem0 keeps the text in its history table after delete; purge this memory's rows.
        con = sqlite3.connect(self.history_db)
        try:
            con.execute("PRAGMA secure_delete = ON")  # overwrite freed pages
            with con:
                con.execute("DELETE FROM history WHERE memory_id = ?", (adapter_record_id,))
        finally:
            con.close()

    def find_by_token(self, retract_token: str) -> str | None:
        rows = self._rows(self.memory.vector_store.list(filters={"retract_token": retract_token}))
        return str(rows[0].id) if rows else None

    def list_ids(self) -> list[str]:
        return [str(r.id) for r in self._rows(self.memory.vector_store.list(top_k=LIST_LIMIT))]

    def close(self) -> None:
        self.memory.close()
        self.memory.vector_store.client.close()  # Memory.close() leaves qdrant's lock held

    @staticmethod
    def _rows(listed: Any) -> list[Any]:
        from mem0.memory.main import _vector_store_list_rows

        return list(_vector_store_list_rows(listed))
