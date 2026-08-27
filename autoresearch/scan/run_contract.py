#!/usr/bin/env python3
"""scan-market 的运行身份契约；只记录事实，不承载流水线业务规则。"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.trace.atomic import atomic_write_json, canonical_json, sha256_bytes

RUN_CONTRACT_SCHEMA_VERSION = 3
#: 本代码能**读**的 schema 版本。v1 契约(2026-08-26 之前的全部历史 run)必须继续可读:
#: `publisher` 的 manifest、`run_mode` 的冻结快照都按它定位 run_id,读不了就等于把历史
#: run 的身份弄丢。v2/v3 都只是**加字段**,v1/v2 的
#: `contract_hash` 按当年的 payload 现算(见 `_hash_payload`),逐字节仍然对得上。
SUPPORTED_SCHEMA_VERSIONS = (1, 2, 3)
#: v2 新增字段 —— v1 契约验哈希时必须**排除**它们(当年那份 hash 里没有这三个键)。
_V2_FIELDS = ("git_dirty", "dirty_paths", "prompt_hashes")
#: v3 新增字段 —— v1/v2 契约验哈希时必须按各自历史 payload 排除。
_V3_FIELDS = ("run_kind", "engine", "workspace_path", "session_ref")


_CAPSULE_WORKSPACE = "capsule"
_LEGACY_WORKSPACE = "legacy"


def _workspace_candidates(
    *, analysis_date: str, run_id: str
) -> dict[str, tuple[str, str]]:
    capsule = ws.scan_run_root(run_id)
    legacy = ws.context_root() / "scan" / analysis_date
    return {
        _CAPSULE_WORKSPACE: (str(capsule), str(capsule.resolve())),
        _LEGACY_WORKSPACE: (str(legacy), str(legacy.resolve())),
    }


def _validate_workspace_path(
    workspace_path: Path | str,
    *,
    analysis_date: str,
    run_id: str,
    allowed_modes: tuple[str, ...] = (_CAPSULE_WORKSPACE, _LEGACY_WORKSPACE),
) -> tuple[str, str]:
    if not isinstance(workspace_path, (str, Path)):
        raise ValueError(f"invalid workspace_path: {workspace_path!r}")
    recorded = str(workspace_path)
    if not recorded.strip():
        raise ValueError("invalid workspace_path: empty")
    if ".." in Path(recorded).parts:
        raise ValueError(f"invalid workspace_path: {recorded!r}")
    candidates = _workspace_candidates(analysis_date=analysis_date, run_id=run_id)
    for mode in allowed_modes:
        if recorded in candidates[mode]:
            return recorded, mode
    expected = [candidate for mode in allowed_modes for candidate in candidates[mode]]
    raise ValueError(
        f"invalid workspace_path: {recorded!r}; expected " + " or ".join(expected)
    )


def _validate_run_kind(value) -> str:
    if type(value) is not str or value != "scan-market":
        raise ValueError(f"invalid run_kind: {value!r}")
    return value


def _validate_session_ref(value) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value.strip():
        raise ValueError(f"invalid session_ref: {value!r}")
    return value


def _validate_engine(value, *, workspace_mode: str) -> str:
    if type(value) is not str:
        raise ValueError(f"invalid engine: {value!r}")
    if workspace_mode == _CAPSULE_WORKSPACE:
        if value not in ws.ENGINES or value != ws.ENGINE:
            raise ValueError(
                f"invalid engine for capsule workspace: {value!r}; expected {ws.ENGINE!r}"
            )
        return value
    if value != "":
        raise ValueError(f"invalid engine for legacy workspace: {value!r}")
    return value


def sha256_json(value: object) -> str:
    """返回稳定 JSON 的 SHA-256 十六进制摘要。"""
    return sha256_bytes(canonical_json(value).encode("utf-8"))


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


#: `dirty_paths` 的上限 —— 只为「看得见有哪些」,不是完整 diff(真 diff 去 git 里查)。
DIRTY_PATHS_CAP = 40


def resolve_git_dirty(repo_root: Path | str = ".") -> tuple[bool, list[str]]:
    """工作树是否有未提交改动 + 改动路径(≤`DIRTY_PATHS_CAP`,已排序)。

    为什么必须记(2026-08-26 审计 #11):`git_sha` 只回答「HEAD 在哪」,而 **agent def 未提交
    也会生效** —— 会话启动装载的是工作树里那一份。本地改过 `.claude/agents/l4-card.md`
    再跑一趟,契约会记下一个**看起来干净**的 sha,事后复盘按那个 sha 去 checkout,拿到的
    根本不是当时执行的规则。配合 `prompt_hashes` 才形成闭环:一个说「树脏了」,一个说
    「脏的是不是 prompt、脏成什么样」。

    不在 git 仓库 / git 不可用 → `(False, [])`(与 `resolve_git_sha` 的 "unknown" 同款诚实
    降级:查不到不等于干净,但也不能凭空说脏 —— 此时 `git_sha` 已经是 "unknown",两条
    合起来读就不会误判)。
    """
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=Path(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False, []
    paths = sorted(
        line[3:].strip() for line in proc.stdout.splitlines() if line.strip()
    )
    return bool(paths), paths[:DIRTY_PATHS_CAP]


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
    # ── v2(2026-08-26 现场留存波)———————————————————————————————————————
    # 三个字段都带默认值,这样 v1 的 raw dict 仍能 `cls(**raw)` 构造出来。
    git_dirty: bool = False
    dirty_paths: tuple[str, ...] = ()
    prompt_hashes: dict[str, str] = field(default_factory=dict)
    # ── v3(法证 run capsule):执行引擎、工作区与会话身份 ─────────────────────
    run_kind: str = "scan-market"
    engine: str = ""
    workspace_path: str = ""
    session_ref: str | None = None

    def _hash_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("contract_hash")
        if self.schema_version < 2:
            # v1 契约当年的 hash payload 里没有这三个键 —— 验历史契约必须按当年的形状算,
            # 否则今天起全部历史 run 的 `load_run_contract` 一起报 hash mismatch。
            for key in _V2_FIELDS:
                payload.pop(key, None)
        if self.schema_version < 3:
            for key in _V3_FIELDS:
                payload.pop(key, None)
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
        git_dirty: bool | None = None,
        dirty_paths: list[str] | tuple[str, ...] | None = None,
        prompt_hashes: dict[str, str] | None = None,
        run_kind: str = "scan-market",
        engine: str = "",
        workspace_path: Path | str | None = None,
        session_ref: str | None = None,
        run_id: str | None = None,
    ) -> RunContract:
        resolved_analysis_date = ws.validate_scan_date(analysis_date)
        active_run_id = ws.active_run_id()
        if run_id is None and active_run_id is not None:
            raise ValueError(
                "cannot build implicit contract identity with active "
                "AUTORESEARCH_RUN_ID"
            )
        stamp = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
        created_at = stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
        resolved_run_id = (
            stamp.strftime("%Y%m%dT%H%M%S%fZ") if run_id is None else str(run_id)
        )
        resolved_run_id = ws.validate_run_id(resolved_run_id)
        workspace_mode = (
            _CAPSULE_WORKSPACE if run_id is not None else _LEGACY_WORKSPACE
        )
        if workspace_path is None:
            if run_id is not None:
                raise ValueError(
                    "workspace_path is required when run_id is explicitly supplied"
                )
            workspace_path = ws.context_root() / "scan" / resolved_analysis_date
        resolved_workspace, workspace_mode = _validate_workspace_path(
            workspace_path,
            analysis_date=resolved_analysis_date,
            run_id=resolved_run_id,
            allowed_modes=(workspace_mode,),
        )
        resolved_run_kind = _validate_run_kind(run_kind)
        resolved_engine = _validate_engine(engine, workspace_mode=workspace_mode)
        resolved_session_ref = _validate_session_ref(session_ref)
        normalized_config = json.loads(canonical_json(user_config))
        if git_dirty is None or dirty_paths is None:
            probed_dirty, probed_paths = resolve_git_dirty(repo_root)
            git_dirty = probed_dirty if git_dirty is None else git_dirty
            dirty_paths = probed_paths if dirty_paths is None else dirty_paths
        base = cls(
            schema_version=RUN_CONTRACT_SCHEMA_VERSION,
            analysis_date=resolved_analysis_date,
            run_id=resolved_run_id,
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
            git_dirty=bool(git_dirty),
            dirty_paths=tuple(dirty_paths or ()),
            prompt_hashes=dict(sorted((prompt_hashes or {}).items())),
            run_kind=resolved_run_kind,
            engine=resolved_engine,
            workspace_path=resolved_workspace,
            session_ref=resolved_session_ref,
        )
        return replace(base, contract_hash=sha256_json(base._hash_payload()))

    @classmethod
    def from_dict(cls, raw: dict) -> RunContract:
        payload = dict(raw)
        schema_version = payload.get("schema_version")
        if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported run contract schema_version={schema_version!r}"
            )
        if schema_version == 3:
            missing = [field for field in _V3_FIELDS if field not in payload]
            if missing:
                raise ValueError(f"missing v3 field(s): {', '.join(missing)}")
        payload["analysis_date"] = ws.validate_scan_date(payload.get("analysis_date"))
        payload["run_id"] = ws.validate_run_id(payload.get("run_id"))
        if schema_version == 1:
            for key in _V2_FIELDS:
                payload.pop(key, None)
        if schema_version in (1, 2):
            for key in _V3_FIELDS:
                payload.pop(key, None)
        # JSON 没有 tuple —— 落盘时 `dirty_paths` 变成 list,读回来要还原成 tuple,
        # 否则 `_hash_payload` 的 `asdict` 产出 list vs tuple 在 canonical_json 里其实同形
        # (都序列化成数组),但字段类型漂移会让下游 `replace()`/相等比较出岔。
        if isinstance(payload.get("dirty_paths"), list):
            payload["dirty_paths"] = tuple(payload["dirty_paths"])
        if schema_version == 3:
            payload["workspace_path"], workspace_mode = _validate_workspace_path(
                payload["workspace_path"],
                analysis_date=payload["analysis_date"],
                run_id=payload["run_id"],
            )
            payload["run_kind"] = _validate_run_kind(payload["run_kind"])
            payload["engine"] = _validate_engine(
                payload["engine"], workspace_mode=workspace_mode
            )
            payload["session_ref"] = _validate_session_ref(payload["session_ref"])
        contract = cls(**payload)
        if contract.config_hash != sha256_json(contract.user_config):
            raise ValueError("run contract config_hash mismatch")
        if contract.contract_hash != sha256_json(contract._hash_payload()):
            raise ValueError("run contract contract_hash mismatch")
        return contract


def write_run_contract(path: Path | str, contract: RunContract) -> Path:
    """原子写契约，避免中断留下可被误读的半截 JSON。"""
    return atomic_write_json(path, contract.to_dict())


def load_run_contract(path: Path | str) -> RunContract:
    """读取并验证 schema、配置摘要和整份契约摘要。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("run contract root must be an object")
    return RunContract.from_dict(raw)
