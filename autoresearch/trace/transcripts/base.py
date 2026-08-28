"""Stable, immutable transcript adapter contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class _FrozenDict(dict):
    def _immutable(self, *args, **kwargs):
        raise TypeError("frozen mapping does not support mutation")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def _freeze(value):
    if isinstance(value, Mapping):
        return _FrozenDict({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class RunIdentity:
    """The explicit run/session facts an adapter may use for location."""

    run_id: str
    engine: str
    session_ref: str | None = None
    cwd: Path | None = None
    started_at: str | None = None

    def __post_init__(self) -> None:
        if self.cwd is not None:
            object.__setattr__(self, "cwd", Path(self.cwd))


@dataclass(frozen=True)
class TranscriptRef:
    """One explicitly identified transcript or one truthful missing-state row."""

    engine: str
    path: Path | None = None
    status: str = "PRESENT"
    role: str = "subagent"
    subject: str | None = None
    invocation_id: str | None = None
    session_ref: str | None = None

    def __post_init__(self) -> None:
        if self.path is not None:
            object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True)
class NormalizedItem:
    index: int
    kind: str
    payload: Mapping[str, object] = field(default_factory=dict)
    timestamp: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True)
class NormalizedTranscript:
    ref: TranscriptRef
    items: tuple[NormalizedItem, ...]
    status: str
    agent: str
    model: str
    effort: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True)
class UsageRecord:
    ref: TranscriptRef
    messages: int
    input: int
    output: int
    cache_read: int
    cache_create: int
    cache_create_1h: int
    cache_create_5m: int
    role: str
    agent: str
    effort: str
    model: str
    speed: str
    status: str
    failure_count: int
    retry_count: int
    discarded: bool
    reasoning_output: int = 0


@runtime_checkable
class TranscriptAdapter(Protocol):
    def locate(self, run_identity: RunIdentity) -> list[TranscriptRef]: ...

    def normalize(self, ref: TranscriptRef) -> NormalizedTranscript: ...

    def usage(self, ref: TranscriptRef) -> UsageRecord: ...
