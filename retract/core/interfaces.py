"""Adapter contract between Retract and a physical memory store."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class StoredItem:
    adapter_record_id: str
    content: str
    metadata: Mapping[str, str]


class MemoryAdapter(ABC):
    """Every write stamps `retract_token` and `record_id` into the item's metadata."""

    idempotent_put: bool = False  # True if the store accepts caller-chosen ids
    supports_list_ids: bool = False  # True if list_ids() enumerates every stored item

    @abstractmethod
    def write(self, content: str, metadata: Mapping[str, str]) -> str:
        """Store an item and return its adapter_record_id."""

    @abstractmethod
    def get(self, adapter_record_id: str) -> StoredItem | None: ...

    @abstractmethod
    def search(self, query: str, k: int) -> list[StoredItem]:
        """Raw, unguarded similarity search, best first."""

    @abstractmethod
    def delete(self, adapter_record_id: str) -> None:
        """Delete an item; deleting a missing id is a no-op."""

    @abstractmethod
    def find_by_token(self, retract_token: str) -> str | None:
        """adapter_record_id of the item stamped with this token, if any."""

    def contains(self, adapter_record_id: str) -> bool:
        return self.get(adapter_record_id) is not None

    def list_ids(self) -> list[str]:
        raise NotImplementedError(f"{type(self).__name__} does not support list_ids")
