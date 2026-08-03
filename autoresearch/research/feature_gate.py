#!/usr/bin/env python3
"""新特征统一闸门 —— 进 composite / 新通道**只有这一条路**(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §3.5 O5

原文:

> 任何新特征入 composite/新通道,唯一入口:capability/PIT gate → factor_lab
> `harvest → calibrate`(锁定 OOS、符号稳定、date-cluster 区间和 no-harm)→ replay 对照
> → registry challenger。排队中只有数据门已通过者。**没有第二条路。**

本模块把这四段做成一个**有序状态机**:每段有自己的通过条件,后一段在前一段未通过时
一律 `BLOCKED`。它不改任何配置,只回答一句话 —— **这个特征现在允许走到哪一步**。

## 为什么需要一个闸门对象,而不是"记得走流程"

已经踩过的形态:
- `northbound` / `accumulation` 两条通道上线后才发现 IC 为负(先接线、后验证);
- `event` 通道「默认不启用」却因为 floor>0 改了 `merit_need`,三个真消费者被污染
  (§0.3-2:默认不启用 = 连副作用一起不启用);
- 52 周高距离族拿「优于更差的 pct_60d」当有效证据(§3.2 → 已 REJECTED)。

三次都不是不懂流程,是流程没有一个会拒绝的实体。

## 四段与它们的通过条件

  1. capability  数据可得性 + **PIT 可回放**(first_seen 能定;历史值不是当前截面)
  2. factor_lab  harvest → calibrate:锁定 OOS、两半符号一致、date-cluster 区间、no-harm
  3. replay      在**独立输出根**下与 baseline 对照(P0-3 的 variant 契约)
  4. registry    challenger 预注册 + 人工 approve/activate

`status()` 返回当前允许的最远一步;`assert_allowed(stage)` 在越级时抛错。

  uv run --no-sync python -m autoresearch.research.feature_gate --list
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_LEDGER = Path("context/research/feature_gate.json")

# 有序 —— 索引即先后,不得跳段
STAGES = ("capability", "factor_lab", "replay", "registry", "production")
BLOCKED = "BLOCKED"
PASS = "PASS"
PENDING = "PENDING"

STAGE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "capability": (
        "数据可得且有稳定契约(关键列非空率、freshness、schema hash)",
        "PIT 可回放:first_seen_ts 可定,历史值不是当前截面的重算",
    ),
    "factor_lab": (
        "harvest 完成,样本量与覆盖率有记录",
        "calibrate:锁定 OOS(不是全样本调参)",
        "两半样本符号一致",
        "date-cluster 区间(不是行级 t 值)",
        "no-harm:对既有 composite 无负向影响",
    ),
    "replay": (
        "独立输出根 + variant_spec + definition_hash(P0-3)",
        "与 baseline 按扫描日配对对照",
    ),
    "registry": (
        "challenger 预注册(五门 guard + minimums)",
        "人工 approve → activate,记操作者",
    ),
    "production": (
        "以上四段全 PASS —— 这一段没有自己的条件,它就是前四段的结论",
    ),
}


class FeatureGateError(RuntimeError):
    """特征越级 —— 前一段没过就想走下一段。"""


@dataclass
class FeatureRecord:
    feature_id: str
    description: str = ""
    stages: dict = field(default_factory=dict)   # stage → PASS/PENDING/BLOCKED
    evidence: dict = field(default_factory=dict)  # stage → 证据指针
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _normalized(record: FeatureRecord) -> dict:
    """逐段结算:第一个非 PASS 之后的所有段一律 `BLOCKED`(不管它自己填了什么)。

    这是闸门的全部权力所在 —— 有人把 `registry` 手填成 PASS,而 `capability` 还没过,
    结算后它照样是 BLOCKED。
    """
    out: dict[str, str] = {}
    blocked = False
    for stage in STAGES:
        declared = str(record.stages.get(stage, PENDING)).upper()
        if blocked:
            out[stage] = BLOCKED
            continue
        if declared == PASS:
            out[stage] = PASS
            continue
        out[stage] = declared if declared in (PENDING, BLOCKED) else PENDING
        blocked = True
    return out


def status(record: FeatureRecord) -> dict:
    """当前允许走到的最远一步 + 逐段结算。"""
    settled = _normalized(record)
    allowed = None
    for stage in STAGES:
        if settled[stage] == PASS:
            continue
        allowed = stage
        break
    passed = [s for s in STAGES if settled[s] == PASS]
    return {
        "feature_id": record.feature_id,
        "stages": settled,
        "next_stage": allowed,
        "furthest_passed": passed[-1] if passed else None,
        "cleared_for_production": allowed is None,
        "requirements_for_next": (list(STAGE_REQUIREMENTS[allowed]) if allowed else []),
        "evidence": dict(record.evidence),
    }


def assert_allowed(record: FeatureRecord, stage: str) -> None:
    """想走 `stage` 但前面有段没过 → 抛错。§3.5「没有第二条路」的可执行形态。"""
    if stage not in STAGES:
        raise FeatureGateError(f"未知阶段 {stage!r};合法阶段:{list(STAGES)}")
    settled = _normalized(record)
    index = STAGES.index(stage)
    unmet = [s for s in STAGES[:index] if settled[s] != PASS]
    if unmet:
        raise FeatureGateError(
            f"特征 {record.feature_id!r} 想走 {stage!r},但 {unmet} 尚未 PASS —— "
            f"§3.5 O5:唯一入口是 capability → factor_lab → replay → registry,没有第二条路。"
            f"下一步该做的是:{'; '.join(STAGE_REQUIREMENTS[unmet[0]])}")


# ───────────────────────── 账本读写 ─────────────────────────


def load(path: Path | str | None = None) -> dict[str, FeatureRecord]:
    target = Path(path or DEFAULT_LEDGER)
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {fid: FeatureRecord(feature_id=fid, **{k: v for k, v in rec.items()
                                                  if k != "feature_id"})
            for fid, rec in (payload.get("features") or {}).items()}


def save(records: dict[str, FeatureRecord], path: Path | str | None = None) -> Path:
    target = Path(path or DEFAULT_LEDGER)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION,
               "stages": list(STAGES),
               "policy": "唯一入口:capability → factor_lab → replay → registry。没有第二条路。",
               "features": {fid: r.as_dict() for fid, r in sorted(records.items())}}
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(target)
    return target


def upsert(feature_id: str, *, stage: str | None = None, verdict: str = PENDING,
           description: str = "", evidence: str = "",
           path: Path | str | None = None) -> dict:
    records = load(path)
    record = records.get(feature_id) or FeatureRecord(feature_id=feature_id)
    if description:
        record.description = description
    if stage:
        if stage not in STAGES:
            raise FeatureGateError(f"未知阶段 {stage!r}")
        record.stages[stage] = verdict.upper()
        if evidence:
            record.evidence[stage] = evidence
    records[feature_id] = record
    save(records, path)
    return status(record)


# ───────────────────────── 渲染 / CLI ─────────────────────────


_MARK = {PASS: "✅", PENDING: "⏳", BLOCKED: "⛔"}


def render(records: dict[str, FeatureRecord]) -> str:
    lines = [
        "# 新特征统一闸门(§3.5 O5)",
        "",
        "> 唯一入口:**capability → factor_lab → replay → registry**。没有第二条路。",
        "> 前一段未 PASS,后面全部结算为 ⛔ —— 手填 PASS 也没用。",
        "",
        "| 特征 | " + " | ".join(STAGES) + " | 下一步 |",
        "|---|" + "|".join(["---"] * (len(STAGES) + 1)) + "|",
    ]
    for fid, record in sorted(records.items()):
        info = status(record)
        cells = [_MARK.get(info["stages"][s], "?") for s in STAGES]
        lines.append(f"| `{fid}` | " + " | ".join(cells) + " | "
                     + (info["next_stage"] or "**可进生产**") + " |")
    if not records:
        lines.append("| — | " + " | ".join(["—"] * (len(STAGES) + 1)) + " |")

    lines += ["", "## 各段的通过条件", ""]
    for stage in STAGES:
        lines += [f"### {stage}", ""]
        lines += [f"- {req}" for req in STAGE_REQUIREMENTS[stage]]
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="新特征统一闸门(§3.5 O5)")
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--feature")
    ap.add_argument("--stage", choices=list(STAGES))
    ap.add_argument("--verdict", default=PENDING, choices=[PASS, PENDING, BLOCKED])
    ap.add_argument("--description", default="")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--out", default="reports/research/feature_gate.md")
    a = ap.parse_args(argv)

    if a.feature:
        info = upsert(a.feature, stage=a.stage, verdict=a.verdict,
                      description=a.description, evidence=a.evidence, path=a.ledger)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    records = load(a.ledger)
    if a.list:
        for fid, record in sorted(records.items()):
            info = status(record)
            # 先拼好再插值:嵌套同引号的 f-string 是 3.12+ 语法,本项目 requires-python >=3.10
            detail = ", ".join(f"{s}={info['stages'][s]}" for s in STAGES)
            print(f"{fid}: 下一步={info['next_stage'] or '可进生产'} ({detail})")
        return 0
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(records), encoding="utf-8")
    print(f"[feature_gate] {len(records)} 个特征 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
