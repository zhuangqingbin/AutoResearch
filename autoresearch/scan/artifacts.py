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
    ArtifactSpec("l1_full", 2, "universe", "L1_scored_full.csv"),  # v2(2026-08-21):+turnup 十列
    ArtifactSpec("l1_recall", 1, "universe", "L1_recall_top1000.csv"),
    ArtifactSpec("l2", 1, "l2_stratify", "L2_gbdt_top200.csv"),
    ArtifactSpec("l3_judged", 1, "l3_rank", "L3_judged_full.csv"),
    ArtifactSpec("finalists", 1, "l3_rank", "finalists.csv"),
    ArtifactSpec("l4_cards", 1, "l4_stock", "details/*.md"),
    ArtifactSpec("l4_task_book", 1, "l4_tasks", "_l4_tasks.json"),
    ArtifactSpec(
        "budget_observation",
        1,
        "post_run",
        "_budget_observation.json",
    ),
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
    # (2026-08-21 learning 层退役:retro_attribution / rejection_attribution /
    #  abstention_verdict / l3_audit_candidates / l3_audit_ledger 五个条目随生产者一并删除
    #  —— 清单里留一个没人生产的产物 = 每天报一次 MISSING 的假告警。)
    ArtifactSpec("run_health", 1, "health", "run_health.json"),
    ArtifactSpec("summary", 1, "assemble", "summary.md", root="report"),
    # 现场附录(2026-08-28 §6.3):summary=决策层、appendix=现场/口径/遥测,两者是**一个
    # 发布包**——两文件未齐不得记录发布完成(§6.2)。契约门控见 CONTRACT_GATED_ARTIFACTS。
    ArtifactSpec("appendix", 1, "assemble", "appendix.md", root="report"),
    ArtifactSpec("manifest", 1, "assemble", "manifest.json", root="report"),
)

#: **契约门控产物**(§6.4 旧 run 兼容):只有当 run 自己的
#: `run_contract.artifact_schema_versions` 里记了这个名字,它才是这一次 run 的义务。
#:
#: 为什么需要这层:`CRITICAL_ARTIFACTS` 是**今天的代码**认识的清单,而 artifact index 会被
#: 重建在**历史 run 目录**上(retention / 复盘 / verify 都会)。清单加一项就让所有旧 run 变红,
#: 等于「拿今天的义务倒灌昨天的现场」—— 那是把历史事实改写成故障,不是发现故障。
#: 门控产物不在契约里 → **整行不生成**(既不 PRESENT 也不 MISSING,更不进 coverage 分母),
#: 因为「NOT_EXPECTED」和「该有却没有」必须在读者眼里长得不一样。
#: 在契约里 → 与其它产物同权:缺席就是 MISSING,必红。
#:
#: 新增产物一律走这条路(而不是直接进 CRITICAL_ARTIFACTS 让历史目录变红)。
CONTRACT_GATED_ARTIFACTS = frozenset({"appendix"})


def artifact_schema_versions() -> dict[str, int]:
    """返回本代码认识的关键产物 schema 版本。"""
    return {spec.name: spec.schema_version for spec in CRITICAL_ARTIFACTS}


def expected_specs(contract: dict | None) -> tuple[ArtifactSpec, ...]:
    """按 **run 自己记录的契约** 过滤出这一次 run 真正该有的产物(§6.4)。

    `contract` = 该 run 的 `run_contract.json` 解析结果(缺失/损坏 → `None` 或 `{}`)。
    判据只有一条:门控产物的名字在不在 `artifact_schema_versions` 这张表里。
    **空 map 与缺契约都算「没记」**——legacy run 天然如此,那是状态不是故障。
    """
    recorded = (contract or {}).get("artifact_schema_versions")
    names = set(recorded) if isinstance(recorded, dict) else set()
    return tuple(
        spec for spec in CRITICAL_ARTIFACTS
        if spec.name not in CONTRACT_GATED_ARTIFACTS or spec.name in names
    )


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
    """对关键产物做一次只读快照；缺失是状态，不在此层解释为流程失败。

    清单**以 run 自己的 `run_contract.artifact_schema_versions` 为准**(§6.4):门控产物
    (`CONTRACT_GATED_ARTIFACTS`)没被这份契约登记过 → 整行不生成,不计 missing、不进
    coverage 分母。历史目录因此不会因为今天的清单加了一项而变红。
    """
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
        for spec in expected_specs(contract)
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
    payload = build_artifact_index(scan, report_dir=report_dir, now=now)
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = None
        if isinstance(existing, dict):
            def _stable_facts(index: dict) -> dict:
                facts = {k: v for k, v in index.items() if k != "generated_at"}
                facts["artifacts"] = [
                    {k: v for k, v in row.items() if k != "created_at"}
                    for row in index.get("artifacts", [])
                ]
                return facts

            old_facts = _stable_facts(existing)
            new_facts = _stable_facts(payload)
            if old_facts == new_facts:
                return target
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(
            payload,
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
