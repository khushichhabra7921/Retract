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
