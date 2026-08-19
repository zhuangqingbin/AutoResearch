#!/usr/bin/env python3
"""General OW/sell-review ensemble fold outcome ledger.

## 预定义裁决规则(Wave8 W8-17 落账;届时按数据裁,**人拍板**)

把规则写在开裁**之前**,是为了不让读数出来后再挑一个好看的门槛 —— 这条账本要回答
「复核跑三次值不值」,而"值不值"的判据必须先于数据固定。

**开裁条件**:≥5 折**已成熟**(T+2 到期可判对错)且其中含 ≥1 例**分歧场景**
(`spread>0`,即三跑没给出同一档)。全一致的折回不含信息量 —— 它只说明"复核没改变
任何事",拿它当样本会把裁决推向"降到 1 跑"的方向。

**裁决规则(SELL 复核 3 跑 → 1 跑)**:
- 折回**救对率 <50%** → 降为 1 跑(复核在做无用功,省 token);
- 折回**救对率 ≥50%** → 维持 3 跑,再攒 5 折复裁。

「救对」定义:折回后的终评比卡片原判**更接近**实现方向(主尺 `fwd_2_oc` 相对市场中位)。
2026-07-28 产生首批 3 折(300857 Sell→Sell / 688766 UW→**Hold** / 601869 UW→UW),
其中 688766 是唯一分歧场景,07-30 收盘成熟。

**买单侧同款**:`buy_ledger` 的 OW 复核降档裁决用同一套救对率规则,开裁条件 = 买单 n≥10
(2026-07-29 现 n=9,下一单即触发)。

⚠️ 这些规则**不自动执行** —— 与 Wave5 治理边界一致:软件只出建议,改生产要人批,
且任何改动先经 `experiment_registry` 预注册。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.common.ruler import MAIN_RULER, entry_tradable
from autoresearch.scan.decision_read_model import read_decisions

_WS_SCAN_ROOT = ws.scan_root()  # B008 修法:默认值须为模块级单例(def 时求值,与旧字面量常量同语义)

ECONOMIC_BAND = 0.02
MIN_TRIGGER_FOLDS = 10
_COLUMNS = [
    "date",
    "code",
    "trigger",
    "source_rating",
    "run_ratings",
    "early_stopped",
    "degraded",
    "spread",
    "final_rating",
    # M-8 修复(review 2026-08-08):这不是"参考尺"——列名固定叫 fwd_2_oc(持久化 ledger
    # 列,不因换尺改名),但**取值**实际来自 MAIN_RULER(见 day_rows() 的 value=MAIN_RULER
    # 那一行),会随换尺漂移;真身靠下面 :53 的 `ruler` 列记账。旧注释说"勿随主尺漂移"
    # 会让下一个人读反(以为这列的数是永远等于 fwd_2_oc 的旧尺,其实不是)。
    "fwd_2_oc",  # 列名固定,取值随 MAIN_RULER 漂移(读数请配 `ruler` 列)
    "market_fwd_2",
    "excess_2",
    "verdict",
    "ruler",   # fwd_2_oc/excess_2/verdict 取值来自哪个 MAIN_RULER(写入那一刻的真值)
]
_MATURE_VERDICTS = {"FOLD_RIGHT", "FOLD_WRONG", "FOLD_NEUTRAL"}


def fold_outcome(
    source_rating: str,
    final_rating: str,
    trigger: str,
    excess_2: float | None,
    *,
    degraded: bool = False,
) -> str:
    """Classify the intervention, keeping failed reviews undecidable."""
    if degraded:
        return "UNDECIDABLE"
    if excess_2 is None or pd.isna(excess_2):
        return "IMMATURE"
    if source_rating == final_rating:
        return "NO_FOLD"
    if -ECONOMIC_BAND < excess_2 < ECONOMIC_BAND:
        return "FOLD_NEUTRAL"
    if trigger == "ow_review":
        return "FOLD_WRONG" if excess_2 >= ECONOMIC_BAND else "FOLD_RIGHT"
    if trigger == "sell_review":
        return "FOLD_RIGHT" if excess_2 >= ECONOMIC_BAND else "FOLD_WRONG"
    return "UNDECIDABLE"


def load_fold_facts(scan_dir: Path | str) -> dict[str, dict]:
    """Read source/final ratings from structured decision and ensemble facts."""
    from autoresearch.scan.decision_finalize import (
        _apply_ensemble_fold,
        _load_ensemble,
    )

    scan = Path(scan_dir)
    ensemble = _load_ensemble(scan)
    decisions = (
        read_decisions(scan)
        if (scan / "decision_records.json").exists()
        else {}
    )
    facts = {}
    for code, record in sorted(ensemble.items()):
        decision = decisions.get(code)
        ratings = list(
            decision.ensemble_ratings
            if decision is not None and decision.ensemble_ratings
            else record.get("ratings") or []
        )
        source = (
            decision.source_rating
            if decision is not None
            else (ratings[0] if ratings else "")
        )
        final = (
            decision.final_rating
            if decision is not None
            else _apply_ensemble_fold(source, record)
        )
        facts[code] = {
            "code": code,
            "trigger": str(record.get("trigger") or "ow_review"),
            "source_rating": source,
            "run_ratings": ratings,
            "early_stopped": bool(record.get("early_stopped", False)),
            "degraded": bool(record.get("degraded", False)),
            "spread": int(record.get("spread") or 0),
            "final_rating": final,
        }
    return facts


def _read_attr(scan: Path) -> pd.DataFrame | None:
    path = scan / "retro" / "attribution.csv"
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None
    if "code" not in frame.columns:
        return None
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    return frame.set_index("code")


def _bool_value(value, default: bool = True) -> bool:
    if value is None or pd.isna(value):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "是"}
    return bool(value)


def _market_fwd2(attr: pd.DataFrame | None) -> float | None:
    if attr is None or MAIN_RULER not in attr.columns:
        return None
    values = pd.to_numeric(attr[MAIN_RULER], errors="coerce")
    # C1 修复(Wave12-T5,final-review 2026-08-08 §A1):入场旗跟随 MAIN_RULER 选
    # (entry_tradable 单点),不是硬编码 `attr["buyable"]`——那条换尺后仍读 D+1 开盘旗,
    # 把「盘中开板、尾盘封死」的票误剔出折回分母。
    buyable = entry_tradable(attr)
    tradable = (
        attr["tradable"].map(_bool_value)
        if "tradable" in attr.columns
        else pd.Series(True, index=attr.index)
    )
    usable = values[buyable & tradable & values.notna()]
    return None if not len(usable) else float(usable.median())


def day_rows(scan_dir: Path | str) -> pd.DataFrame:
    scan = Path(scan_dir)
    facts = load_fold_facts(scan)
    attr = _read_attr(scan)
    market = _market_fwd2(attr)
    # C1 修复(Wave12-T5,final-review 2026-08-08 §A1):入场旗跟随 MAIN_RULER 选
    # (entry_tradable 单点),对整个 attr 帧一次性算好(镜像 `_market_fwd2`),循环内按 code
    # 查——不是逐行硬编码 `row.get("buyable", True)`(那条换尺后仍读 D+1 开盘旗)。
    entry_ok = entry_tradable(attr) if attr is not None else None
    rows = []
    for code, fact in facts.items():
        fwd = None
        if attr is not None and code in attr.index and MAIN_RULER in attr.columns:
            row = attr.loc[code]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            value = pd.to_numeric(
                pd.Series([row.get(MAIN_RULER)]),
                errors="coerce",
            ).iloc[0]
            ok = entry_ok.loc[code]
            if isinstance(ok, pd.Series):
                ok = ok.iloc[0]
            if (
                not pd.isna(value)
                and bool(ok)
                and _bool_value(row.get("tradable", True))
            ):
                fwd = float(value)
        excess = None if fwd is None or market is None else fwd - market
        rows.append(
            {
                "date": scan.name,
                **fact,
                "fwd_2_oc": None if fwd is None else round(fwd, 6),
                "market_fwd_2": (
                    None if market is None else round(market, 6)
                ),
                "excess_2": None if excess is None else round(excess, 6),
                "verdict": fold_outcome(
                    fact["source_rating"],
                    fact["final_rating"],
                    fact["trigger"],
                    excess,
                    degraded=fact["degraded"],
                ),
                "ruler": MAIN_RULER,
            }
        )
    return pd.DataFrame(rows, columns=_COLUMNS)


def roll(scan_root: Path | str = _WS_SCAN_ROOT) -> pd.DataFrame:
    root = Path(scan_root)
    frames = []
    days = sorted(
        {
            path.parent
            for pattern in ("*/_ensemble.json", "*/_ensemble_*.json")
            for path in root.glob(pattern)
        }
    )
    for day in days:
        frame = day_rows(day)
        if len(frame):
            frames.append(frame)
    return (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=_COLUMNS)
    )


def trigger_summary(rows: pd.DataFrame) -> pd.DataFrame:
    """按 trigger 汇总折回计数 —— 供人工"救对率 <50% → 降为 1 跑"裁决直接读数。

    Wave11 review 发现(task-13-review.md §3.3):`verdict`(FOLD_RIGHT/WRONG/NEUTRAL)由
    该行自己的 `excess_2` 判出,取值来自写入当天的 MAIN_RULER(冻结快照);旧实现只按
    `trigger` 分组,T16 flip 后同一 trigger 下 fwd_2_oc 尺判出的折对/折错次数会与
    gap_c1_o2 尺的直接相加 —— 人工手算救对率时会不自知地把两把尺的结果混一个分母。
    分组键补 `ruler` 后,单尺场景(今天)每个 trigger 仍只出一行,不改变现行为;真正
    跨尺时才会为同一 trigger 拆成多行,救对率必须分尺各算各的。
    """
    columns = [
        "trigger",
        "ruler",
        "n_records",
        "n_mature_folds",
        "fold_right",
        "fold_wrong",
        "fold_neutral",
        "status",
    ]
    if rows is None or not len(rows):
        return pd.DataFrame(columns=columns)
    rows = rows.copy()
    if "ruler" in rows.columns:
        rows["ruler"] = rows["ruler"].fillna("fwd_2_oc")
    else:
        rows["ruler"] = "fwd_2_oc"
    output = []
    for (trigger, ruler), group in rows.groupby(["trigger", "ruler"]):
        mature = group[group["verdict"].isin(_MATURE_VERDICTS)]
        output.append(
            {
                "trigger": trigger,
                "ruler": ruler,
                "n_records": len(group),
                "n_mature_folds": len(mature),
                "fold_right": int((mature["verdict"] == "FOLD_RIGHT").sum()),
                "fold_wrong": int((mature["verdict"] == "FOLD_WRONG").sum()),
                "fold_neutral": int(
                    (mature["verdict"] == "FOLD_NEUTRAL").sum()
                ),
                "status": (
                    "MATURE"
                    if len(mature) >= MIN_TRIGGER_FOLDS
                    else "IMMATURE"
                ),
            }
        )
    return pd.DataFrame(output, columns=columns).sort_values(
        ["trigger", "ruler"]
    ).reset_index(drop=True)


def render(rows: pd.DataFrame) -> str:
    summary = trigger_summary(rows)
    lines = [
        "# Ensemble 折回结果账",
        "",
        "_OW 买单复核与 pinned SELL 复核分桶记账；不自动修改折回规则。_",
        "",
    ]
    if not len(summary):
        return "\n".join(lines + ["_无 ensemble 事实。_"]) + "\n"
    lines += [
        "| trigger | 尺 | records | 成熟折回 | 折对 | 折错 | 中性 | 状态 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary.itertuples(index=False):
        lines.append(
            f"| {row.trigger} | {row.ruler} | {row.n_records} | {row.n_mature_folds}"
            f" | {row.fold_right} | {row.fold_wrong} | {row.fold_neutral}"
            f" | {row.status} |"
        )
    n_rulers = summary["ruler"].nunique()
    if n_rulers > 1:
        lines.append("")
        lines.append(
            f"- ⚠️ 本表跨 {n_rulers} 种尺,同一 trigger 已按尺拆行(见上)——手算"
            "「救对率」时**不得**把不同尺的折对/折错行合并成一个分母,分母必须限定在同一尺内。"
        )
    lines += [
        "",
        "_每个 trigger family 成熟折回 n<10 时禁止修改机制；degraded 单列"
        " UNDECIDABLE，同档早止保留 early_stopped=true。_",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = roll()
    output = ws.reports_root() / "learning/ensemble_ledger.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(rows), encoding="utf-8")
    detail = output.with_suffix(".json")
    detail.write_text(
        json.dumps(rows.to_dict(orient="records"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"[ensemble_ledger] {len(rows)} folds → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
