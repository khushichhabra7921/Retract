"""Retract exceptions."""

from collections.abc import Sequence


class RetractError(Exception):
    """Base class for Retract errors."""


class InputNotReadableError(RetractError):
    """A write named a parent that is pending, held, purged or unknown."""

    def __init__(self, record_ids: Sequence[str]) -> None:
        self.record_ids = list(record_ids)
        super().__init__(
            f"inputs not readable (pending, held, purged or unknown): {self.record_ids}"
        )


class UnknownTierError(RetractError):
    """No adapter is configured for the tier."""


class TombstonedSourceError(RetractError):
    """A write would carry the label of a retracted source."""

    def __init__(self, source_refs: Sequence[str]) -> None:
        self.source_refs = list(source_refs)
        super().__init__(f"write carries retracted source(s) (source_ref): {self.source_refs}")
