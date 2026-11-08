"""API boundary models."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, PrivateAttr

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


Mode = Literal["auto", "delete", "quarantine"]


class PlannedAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: str
    action: Literal["delete", "quarantine", "rederive"]
    reason: str
    agent: str
    tier: str


class Plan(BaseModel):
    """Dry-run result. `execute()` re-checks the closure inside BLOCK (StalePlanError)."""

    model_config = ConfigDict(frozen=True)

    source_ref: str
    mode: Mode
    closure_hash: str
    actions: list[PlannedAction]
    noop_op_id: str | None = None  # set when the source was already retracted
    _execute: Any = PrivateAttr(default=None)

    @property
    def blast_radius(self) -> str:
        agents = {a.agent for a in self.actions}
        tiers = {a.tier for a in self.actions}
        return f"{len(self.actions)} records · {len(agents)} agents · {len(tiers)} tiers"

    def execute(self) -> Any:  # -> Receipt
        return self._execute(self)
