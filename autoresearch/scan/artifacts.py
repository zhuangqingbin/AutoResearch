#!/usr/bin/env python3
"""scan staging 产物的规范读取口 —— 把"代码是 6 位零填字符串"这条契约收在一处。

存在理由(2026-07-09 实跑事故):`finalists.csv` 的 `code`/`ticker` 两列都是股票代码,但
往返追加时只给 `code` 指定了 `dtype=str`,`ticker` 被 pandas 解析成 int64 → `002156` 写回成
`2156` → assemble 按 ticker glob 卡片,把两张真卡报成「⚠️卡片缺失」。**只有当日有追加时该路径
才跑**,所以是间歇性的。(当年的两个追加者:`append_carryover` 已随菜单滞回于 2026-07-16 退役;
`watchlist.append_express` 随观察单模块删除。)
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

_CODE_COLS = ("code", "ticker")
ARTIFACT_INDEX_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ArtifactSpec:
    """一个有限、显式登记的生产产物契约。"""

    name: str
    schema_version: int
    producer: str
    path: str
    root: str = "scan"


CRITICAL_ARTIFACTS = (
    ArtifactSpec("run_contract", 1, "frame", "run_contract.json"),
    ArtifactSpec("stage_results", 1, "control_plane", "stage_results/*.json"),
    ArtifactSpec("market_pack", 1, "frame", "market_pack.json"),
    ArtifactSpec("l1_full", 1, "universe", "L1_scored_full.csv"),
    ArtifactSpec("l1_recall", 1, "universe", "L1_recall_top1000.csv"),
    ArtifactSpec("l2", 1, "l2_stratify", "L2_gbdt_top200.csv"),
    ArtifactSpec("l3_judged", 1, "l3_rank", "L3_judged_full.csv"),
    ArtifactSpec("finalists", 1, "l3_rank", "finalists.csv"),
    ArtifactSpec("l4_cards", 1, "l4_stock", "details/*.md"),
    ArtifactSpec("ensemble", 1, "l4_ensemble", "_ensemble_*.json"),
    ArtifactSpec("final_ratings", 1, "assemble", "_final_ratings.json"),
    ArtifactSpec("decision_records", 1, "assemble", "decision_records.json"),
    ArtifactSpec("outbox_events", 1, "post_run", "outbox/events.json"),
    ArtifactSpec(
        "consumer_state",
        1,
        "post_run",
        "outbox/consumer_state.json",
    ),
    ArtifactSpec("gate_fires", 1, "assemble", "gate_fires.csv"),
    ArtifactSpec("early_stop", 1, "assemble", "_early_stop.json"),
    ArtifactSpec(
        "retro_attribution",
        1,
        "retro",
        "retro/attribution.csv",
    ),
    ArtifactSpec(
        "rejection_attribution",
        1,
        "retro",
        "retro/rejection_attribution.csv",
    ),
    ArtifactSpec(
        "abstention_verdict",
        1,
        "retro",
        "retro/abstention_verdict.json",
    ),
    ArtifactSpec(
        "l3_audit_candidates",
        1,
        "l3_rank",
        "shadow/l3_audit_candidates.csv",
    ),
    ArtifactSpec(
        "l3_audit_ledger",
        1,
        "retro",
        "retro/l3_audit_ledger.json",
    ),
    ArtifactSpec(
        "earlystop_shadow_queue",
        1,
        "earlystop_shadow",
        "shadow/earlystop_queue.json",
    ),
    ArtifactSpec(
        "earlystop_shadow_cards",
        1,
        "earlystop_shadow",
        "shadow/earlystop_details/*.md",
    ),
    ArtifactSpec("run_health", 1, "health", "run_health.json"),
    ArtifactSpec("summary", 1, "assemble", "summary.md", root="report"),
    ArtifactSpec("manifest", 1, "assemble", "manifest.json", root="report"),
)


def artifact_schema_versions() -> dict[str, int]:
    """返回本代码认识的关键产物 schema 版本。"""
    return {spec.name: spec.schema_version for spec in CRITICAL_ARTIFACTS}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _artifact_ref(spec: ArtifactSpec, root: Path | None) -> dict:
    base = root if root is not None else Path("__missing_artifact_root__")
    paths = sorted(p for p in base.glob(spec.path) if p.is_file()) if root is not None else []
    nonempty = [path for path in paths if path.stat().st_size > 0]
    status = "MISSING" if not paths else ("EMPTY" if not nonempty else "PRESENT")
    if len(paths) == 1:
        content_hash = _sha256_file(paths[0])
    elif paths:
        content_hash = _canonical_hash([
            {"path": path.relative_to(base).as_posix(), "content_hash": _sha256_file(path)}
            for path in paths
        ])
    else:
        content_hash = None
    created_at = None
    if paths:
        created_at = datetime.fromtimestamp(
            max(path.stat().st_mtime for path in paths),
            tz=timezone.utc,
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        **asdict(spec),
        # 生产者尚未回显 lineage 时必须诚实为空，不能用 contract hash 冒充输入指纹。
        "input_hash": None,
        "content_hash": content_hash,
        "status": status,
        "created_at": created_at,
    }


def build_artifact_index(
    scan_dir: Path | str,
    *,
    report_dir: Path | str | None = None,
    now: datetime | None = None,
) -> dict:
    """对关键产物做一次只读快照；缺失是状态，不在此层解释为流程失败。"""
    scan = Path(scan_dir)
    report = Path(report_dir) if report_dir is not None else None
    contract = {}
    contract_path = scan / "run_contract.json"
    if contract_path.exists():
        try:
            loaded = json.loads(contract_path.read_text(encoding="utf-8"))
            contract = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            contract = {}
    rows = [
        _artifact_ref(spec, scan if spec.root == "scan" else report)
        for spec in CRITICAL_ARTIFACTS
    ]
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    stamp = stamp.astimezone(timezone.utc)
    return {
        "schema_version": ARTIFACT_INDEX_SCHEMA_VERSION,
        "analysis_date": scan.name,
        "run_id": contract.get("run_id"),
        "contract_hash": contract.get("contract_hash"),
        "generated_at": stamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "artifacts": rows,
        "coverage": {
            "registered": len(rows),
            "present": sum(row["status"] == "PRESENT" for row in rows),
            "empty": sum(row["status"] == "EMPTY" for row in rows),
            "missing": sum(row["status"] == "MISSING" for row in rows),
            "content_hashed": sum(row["content_hash"] is not None for row in rows),
            "input_hashed": sum(row["input_hash"] is not None for row in rows),
        },
    }


def write_artifact_index(
    scan_dir: Path | str,
    *,
    report_dir: Path | str | None = None,
    now: datetime | None = None,
) -> Path:
    """原子写 staging/artifact_index.json。"""
    scan = Path(scan_dir)
    scan.mkdir(parents=True, exist_ok=True)
    target = scan / "artifact_index.json"
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(
            build_artifact_index(scan, report_dir=report_dir, now=now),
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def read_finalists(fp: Path | str) -> pd.DataFrame:
    """读 finalists.csv:代码列一律 6 位零填字符串(防 CSV 往返吃掉前导零)。"""
    df = pd.read_csv(fp, dtype=dict.fromkeys(_CODE_COLS, str))
    for c in _CODE_COLS:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip().str.zfill(6)
    return df
