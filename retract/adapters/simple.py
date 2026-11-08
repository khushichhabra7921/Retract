"""In-memory vector store implementing the full adapter contract."""

import uuid
from collections.abc import Callable, Mapping

from retract.core.interfaces import MemoryAdapter, StoredItem
from retract.derive.fakes import FakeEmbedder


class SimpleVectorStore(MemoryAdapter):
    idempotent_put = False
    supports_list_ids = True

    def __init__(self, embed: Callable[[str], list[float]] | None = None) -> None:
        self.embed = embed or FakeEmbedder()
        self._items: dict[str, tuple[StoredItem, list[float]]] = {}

    def write(self, content: str, metadata: Mapping[str, str]) -> str:
        adapter_record_id = uuid.uuid4().hex
        item = StoredItem(adapter_record_id, content, dict(metadata))
        self._items[adapter_record_id] = (item, self.embed(content))
        return adapter_record_id

    def get(self, adapter_record_id: str) -> StoredItem | None:
        entry = self._items.get(adapter_record_id)
        return entry[0] if entry else None

    def search(self, query: str, k: int) -> list[StoredItem]:
        q = self.embed(query)
        scored = [
            (sum(a * b for a, b in zip(q, vec, strict=True)), item)
            for item, vec in self._items.values()
        ]
        scored.sort(key=lambda s: -s[0])  # stable: ties keep insertion order
        return [item for _, item in scored[:k]]

    def delete(self, adapter_record_id: str) -> None:
        self._items.pop(adapter_record_id, None)

    def find_by_token(self, retract_token: str) -> str | None:
        return next(
            (
                i
                for i, (item, _) in self._items.items()
                if item.metadata.get("retract_token") == retract_token
            ),
            None,
        )

    def list_ids(self) -> list[str]:
        return list(self._items)
