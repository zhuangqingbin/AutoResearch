"""Immutable values shared by the forensic run-capsule lifecycle."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from autoresearch.trace.atomic import canonical_json

if TYPE_CHECKING:
    from autoresearch.scan.run_contract import RunContract


class BusinessStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class EvidenceStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    LEGACY_PARTIAL = "LEGACY_PARTIAL"


class Replayability(str, Enum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NONE = "NONE"
    EVIDENCE_ONLY = "EVIDENCE_ONLY"


def _utc_datetime(value: datetime | None = None) -> datetime:
    stamp = value or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _utc_timestamp(value: datetime | None = None) -> str:
    return _utc_datetime(value).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


_EVIDENCE_TRANSITIONS = {
    EvidenceStatus.PENDING: frozenset(
        (
            EvidenceStatus.PENDING,
            EvidenceStatus.COMPLETE,
            EvidenceStatus.EVIDENCE_INCOMPLETE,
        )
    ),
    EvidenceStatus.COMPLETE: frozenset(
        (EvidenceStatus.COMPLETE, EvidenceStatus.EVIDENCE_INCOMPLETE)
    ),
    EvidenceStatus.EVIDENCE_INCOMPLETE: frozenset(
        (EvidenceStatus.EVIDENCE_INCOMPLETE, EvidenceStatus.COMPLETE)
    ),
    EvidenceStatus.LEGACY_PARTIAL: frozenset((EvidenceStatus.LEGACY_PARTIAL,)),
}


@dataclass(frozen=True)
class RunState:
    run_id: str
    business_status: BusinessStatus
    evidence_status: EvidenceStatus
    replayability: Replayability
    created_at: str
    updated_at: str

    @classmethod
    def build(
        cls,
        *,
        run_id: str,
        business_status: BusinessStatus | str = BusinessStatus.ACTIVE,
        evidence_status: EvidenceStatus | str = EvidenceStatus.PENDING,
        replayability: Replayability | str = Replayability.NONE,
        now: datetime | None = None,
        previous: RunState | None = None,
    ) -> RunState:
        """Build initial state or apply one explicit business transition."""
        resolved_business = BusinessStatus(business_status)
        resolved_evidence = EvidenceStatus(evidence_status)
        resolved_replayability = Replayability(replayability)
        resolved_now = _utc_datetime(now)
        timestamp = _utc_timestamp(resolved_now)

        if previous is None:
            created_at = timestamp
        else:
            if previous.run_id != run_id:
                raise ValueError(
                    f"cannot transition run_id={previous.run_id!r} as run_id={run_id!r}"
                )
            current_business = BusinessStatus(previous.business_status)
            current_evidence = EvidenceStatus(previous.evidence_status)
            if (
                current_business != BusinessStatus.ACTIVE
                and resolved_business != current_business
            ):
                raise ValueError(
                    "illegal business transition: "
                    f"{current_business.value} -> {resolved_business.value}"
                )
            if resolved_evidence not in _EVIDENCE_TRANSITIONS[current_evidence]:
                raise ValueError(
                    "illegal evidence transition: "
                    f"{current_evidence.value} -> {resolved_evidence.value}"
                )
            if resolved_now < _parse_utc_timestamp(previous.updated_at):
                raise ValueError("transition time is before previous.updated_at")
            if (
                resolved_business == current_business
                and resolved_evidence == current_evidence
                and resolved_replayability == Replayability(previous.replayability)
            ):
                return previous
            created_at = previous.created_at

        return cls(
            run_id=run_id,
            business_status=resolved_business,
            evidence_status=resolved_evidence,
            replayability=resolved_replayability,
            created_at=created_at,
            updated_at=timestamp,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RunHandle:
    run_id: str
    analysis_date: str
    engine: str
    workspace: Path
    staging: Path
    capsule: Path
    contract: RunContract


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


def _freeze_json(value):
    if isinstance(value, dict):
        return _FrozenDict({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value):
    if isinstance(value, dict):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    stage: str
    attempt: int
    status: str
    path: Path
    created_at: str
    artifacts: tuple[str, ...] = ()
    metrics: Mapping[str, object] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metrics, Mapping):
            raise TypeError("Checkpoint metrics root must be a mapping")
        normalized = json.loads(canonical_json(dict(self.metrics)))
        object.__setattr__(
            self, "artifacts", tuple(str(artifact) for artifact in self.artifacts)
        )
        object.__setattr__(self, "metrics", _freeze_json(normalized))

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "attempt": self.attempt,
            "status": self.status,
            "path": str(self.path),
            "created_at": self.created_at,
            "artifacts": list(self.artifacts),
            "metrics": _thaw_json(self.metrics),
            "error": self.error,
        }


@dataclass(frozen=True)
class FinalizationResult:
    run_id: str
    business_status: BusinessStatus
    evidence_status: EvidenceStatus
    replayability: Replayability
    final_path: Path
    root_hash: str | None = None
    archive: Path | None = None
    durability: str = ""
    last_reliable_checkpoint: str | None = None
