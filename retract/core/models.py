"""API boundary models."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Status = Literal["pending", "active", "quarantined", "retracted", "tombstoned"]


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: str
    content: str | None  # None once purged
    agent: str
    tier: str
    status: Status
    labels: frozenset[str]
    adapter_record_id: str | None


class OrphanReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    pending_no_adapter_record: list[str]  # crashed before/at the adapter write
    pending_adapter_written: list[str]  # crashed after the adapter write, before activation
    adapter_no_lineage: list[tuple[str, str]]  # (tier, adapter_record_id) with no lineage row

    @property
    def clean(self) -> bool:
        return not (
            self.pending_no_adapter_record
            or self.pending_adapter_written
            or self.adapter_no_lineage
        )
