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


class TransientError(RetractError):
    """Raised by derive functions for retryable infrastructure failures (LLM or network down).

    During repair it fails the operation: records stay held and `resume` continues later."""


class ReplayError(RetractError):
    """Raised by derive functions for a known, deterministic replay failure (inputs the
    function can no longer handle). During repair the record is quarantined and the op goes on.

    Any exception that is neither TransientError nor ReplayError is treated as a bug: the
    operation fails with holds kept, so a broken function is never reported as containment."""


class OperationInProgressError(RetractError):
    """Another operation holds the lock (live lease) or holds records this one needs."""

    def __init__(self, op_id: str, why: str = "executing with a live lease") -> None:
        self.op_id = op_id
        super().__init__(f"operation {op_id} is {why}; resume or finish it first")


class StalePlanError(RetractError):
    """The affected closure changed between preview and execution. Nothing was blocked."""


class RetractionFailedError(RetractError):
    """The operation ended `failed`: records stay held, no success receipt. Resumable."""

    def __init__(self, op_id: str, reason: str) -> None:
        self.op_id = op_id
        super().__init__(f"retraction {op_id} failed: {reason}")


class StructuralVerificationError(RetractError):
    """VERIFY failed. `source_failures`: the source's information may still be reachable.
    `global_failures`: unrelated lineage-integrity problems (e.g. an orphan from another
    record), so complete coverage cannot be established."""

    def __init__(self, source_failures: Sequence[str], global_failures: Sequence[str]) -> None:
        self.source_failures = list(source_failures)
        self.global_failures = list(global_failures)
        super().__init__(
            f"structural verification failed: source-specific={self.source_failures}"
            f" global-integrity={self.global_failures}"
        )
