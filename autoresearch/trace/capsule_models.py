"""Immutable values shared by the forensic run-capsule lifecycle."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


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


def _utc_timestamp(value: datetime | None = None) -> str:
    stamp = value or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


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
        timestamp = _utc_timestamp(now)

        if previous is None:
            created_at = timestamp
        else:
            if previous.run_id != run_id:
                raise ValueError(
                    f"cannot transition run_id={previous.run_id!r} as run_id={run_id!r}"
                )
            current = previous.business_status
            if current != BusinessStatus.ACTIVE and resolved_business != current:
                raise ValueError(
                    "illegal business transition: "
                    f"{current.value} -> {resolved_business.value}"
                )
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
    contract: object


@dataclass(frozen=True)
class Checkpoint:
    run_id: str
    stage: str
    attempt: int
    status: str
    path: Path
    created_at: str
    artifacts: tuple[str, ...] = ()
    metrics: dict = field(default_factory=dict)
    error: str | None = None


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
