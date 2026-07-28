#!/usr/bin/env python3
"""scan-market 的运行身份契约；只记录事实，不承载流水线业务规则。"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

RUN_CONTRACT_SCHEMA_VERSION = 1


def canonical_json(value: object) -> str:
    """稳定 JSON 表示；供配置和契约内容寻址。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: object) -> str:
    """返回稳定 JSON 的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def resolve_git_sha(repo_root: Path | str = ".") -> str:
    """返回当前提交；不在 git 仓库时显式降级为 unknown。"""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


@dataclass(frozen=True)
class RunContract:
    """一次 scan 的不可变运行身份。"""

    schema_version: int
    analysis_date: str
    run_id: str
    created_at: str
    git_sha: str
    user_config: dict
    config_hash: str
    agents: dict
    pinned: dict
    data_policy: dict
    stage_budgets: dict
    artifact_schema_versions: dict[str, int]
    contract_hash: str

    def _hash_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("contract_hash")
        return payload

    def to_dict(self) -> dict:
        return asdict(self)

    def short_ref(self) -> dict:
        """供 market pack 携带的定长引用，避免重复注入完整配置。"""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "contract_hash": self.contract_hash,
            "config_hash": self.config_hash,
        }

    @classmethod
    def build(
        cls,
        *,
        analysis_date: str,
        user_config: dict,
        pinned: dict,
        data_policy: dict,
        stage_budgets: dict,
        artifact_schema_versions: dict[str, int],
        git_sha: str | None = None,
        now: datetime | None = None,
        repo_root: Path | str = ".",
    ) -> RunContract:
        stamp = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
        created_at = stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
        run_id = stamp.strftime("%Y%m%dT%H%M%S%fZ")
        normalized_config = json.loads(canonical_json(user_config))
        base = cls(
            schema_version=RUN_CONTRACT_SCHEMA_VERSION,
            analysis_date=analysis_date,
            run_id=run_id,
            created_at=created_at,
            git_sha=git_sha if git_sha is not None else resolve_git_sha(repo_root),
            user_config=normalized_config,
            config_hash=sha256_json(normalized_config),
            agents=normalized_config.get("agents") or {},
            pinned=json.loads(canonical_json(pinned)),
            data_policy=json.loads(canonical_json(data_policy)),
            stage_budgets=json.loads(canonical_json(stage_budgets)),
            artifact_schema_versions=dict(sorted(artifact_schema_versions.items())),
            contract_hash="",
        )
        return replace(base, contract_hash=sha256_json(base._hash_payload()))

    @classmethod
    def from_dict(cls, raw: dict) -> RunContract:
        contract = cls(**raw)
        if contract.schema_version != RUN_CONTRACT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported run contract schema_version={contract.schema_version}"
            )
        if contract.config_hash != sha256_json(contract.user_config):
            raise ValueError("run contract config_hash mismatch")
        if contract.contract_hash != sha256_json(contract._hash_payload()):
            raise ValueError("run contract contract_hash mismatch")
        return contract


def write_run_contract(path: Path | str, contract: RunContract) -> Path:
    """原子写契约，避免中断留下可被误读的半截 JSON。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(contract.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def load_run_contract(path: Path | str) -> RunContract:
    """读取并验证 schema、配置摘要和整份契约摘要。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("run contract root must be an object")
    return RunContract.from_dict(raw)
