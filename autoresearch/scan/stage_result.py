#!/usr/bin/env python3
"""scan 阶段结果的有限状态、完整性校验和原子快照。"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from autoresearch.scan.run_contract import canonical_json, load_run_contract, sha256_json

STAGE_RESULT_SCHEMA_VERSION = 1
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


def _validate_stage(stage: str) -> str:
    if not _STAGE_RE.fullmatch(stage):
        raise ValueError(f"invalid stage name: {stage!r}")
    return stage


@dataclass(frozen=True)
class StageResult:
    """一个阶段的最新结构化快照。"""

    schema_version: int
    stage: str
    analysis_date: str
    status: str
    artifacts: list[str]
    metrics: dict
    warnings: list[str]
    error: str | None
    contract_hash: str | None
    recorded_at: str
    result_hash: str

    def _hash_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("result_hash")
        return payload

    def semantic_payload(self) -> dict:
        """排除记录时刻后的事实负载，用于重复写的语义幂等判断。"""
        payload = self._hash_payload()
        payload.pop("recorded_at")
        return payload

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def build(
        cls,
        *,
        stage: str,
        analysis_date: str,
        status: StageStatus | str,
        artifacts: list[str],
        metrics: dict,
        warnings: list[str],
        error: str | None,
        contract_hash: str | None,
        now: datetime | None = None,
    ) -> StageResult:
        stage = _validate_stage(stage)
        status_value = StageStatus(status).value
        stamp = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
        base = cls(
            schema_version=STAGE_RESULT_SCHEMA_VERSION,
            stage=stage,
            analysis_date=analysis_date,
            status=status_value,
            artifacts=list(dict.fromkeys(str(value) for value in artifacts)),
            metrics=json.loads(canonical_json(metrics)),
            warnings=[str(value) for value in warnings],
            error=None if error is None else str(error),
            contract_hash=contract_hash,
            recorded_at=stamp.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            result_hash="",
        )
        return replace(base, result_hash=sha256_json(base._hash_payload()))

    @classmethod
    def from_dict(cls, raw: dict) -> StageResult:
        result = cls(**raw)
        _validate_stage(result.stage)
        StageStatus(result.status)
        if result.schema_version != STAGE_RESULT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported stage result schema_version={result.schema_version}"
            )
        if result.result_hash != sha256_json(result._hash_payload()):
            raise ValueError("stage result result_hash mismatch")
        return result


def stage_result_path(scan_dir: Path | str, stage: str) -> Path:
    return Path(scan_dir) / "stage_results" / f"{_validate_stage(stage)}.json"


def contract_hash_for(scan_dir: Path | str) -> str | None:
    path = Path(scan_dir) / "run_contract.json"
    if not path.exists():
        return None
    try:
        return load_run_contract(path).contract_hash
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def load_stage_result(path: Path | str) -> StageResult:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("stage result root must be an object")
    return StageResult.from_dict(raw)


def write_stage_result(scan_dir: Path | str, result: StageResult) -> Path:
    """原子写最新快照；语义相同的重复结果不刷新时间或 hash。"""
    target = stage_result_path(scan_dir, result.stage)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            existing = load_stage_result(target)
            if existing.semantic_payload() == result.semantic_payload():
                return target
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def record_stage_result(
    scan_dir: Path | str,
    *,
    stage: str,
    status: StageStatus | str,
    artifacts: list[str],
    metrics: dict,
    warnings: list[str],
    error: str | None,
    now: datetime | None = None,
) -> Path:
    scan = Path(scan_dir)
    result = StageResult.build(
        stage=stage,
        analysis_date=scan.name,
        status=status,
        artifacts=artifacts,
        metrics=metrics,
        warnings=warnings,
        error=error,
        contract_hash=contract_hash_for(scan),
        now=now,
    )
    return write_stage_result(scan, result)


def safe_record_stage_result(scan_dir: Path | str, **kwargs) -> Path | None:
    """影子双写入口：控制面故障显式走 stderr，但不改变业务返回。"""
    try:
        return record_stage_result(scan_dir, **kwargs)
    except Exception as exc:  # noqa: BLE001 — 影子控制面不能阻断生产阶段
        print(f"[stage_result] {kwargs.get('stage', '?')} 写入失败: {exc}", file=sys.stderr)
        return None
