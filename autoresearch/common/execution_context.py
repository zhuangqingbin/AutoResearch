"""Context-local run identity, clock, and source hook protocols."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RunClock:
    """A deterministic, injectable clock for one operation."""

    value: datetime

    def __post_init__(self) -> None:
        if self.value.tzinfo is None or self.value.utcoffset() is None:
            raise ValueError("RunClock requires a timezone-aware datetime")

    def now(self) -> datetime:
        return self.value.astimezone(timezone.utc)


@runtime_checkable
class SourceResponseHook(Protocol):
    def record_response(
        self,
        context: dict,
        outcome: object,
        *,
        raw_bytes: bytes | None = None,
    ) -> dict: ...


@dataclass(frozen=True)
class ExecutionContext:
    engine: str
    run_id: str
    task_id: str
    attempt: int
    clock: RunClock
    source_hook: SourceResponseHook | None = None

    def __post_init__(self) -> None:
        if self.engine not in {"claude", "codex"}:
            raise ValueError("invalid execution engine")
        if not self.run_id or not self.task_id:
            raise ValueError("execution run/task identity required")
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("execution attempt must be positive")


_CURRENT: ContextVar[ExecutionContext | None] = ContextVar(
    "autoresearch_execution_context", default=None
)


def current_execution_context() -> ExecutionContext | None:
    return _CURRENT.get()


@contextmanager
def use_execution_context(context: ExecutionContext) -> Iterator[ExecutionContext]:
    if not isinstance(context, ExecutionContext):
        raise TypeError("context must be ExecutionContext")
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


__all__ = [
    "ExecutionContext",
    "RunClock",
    "SourceResponseHook",
    "current_execution_context",
    "use_execution_context",
]
