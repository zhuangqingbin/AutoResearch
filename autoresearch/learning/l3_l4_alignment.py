#!/usr/bin/env python3
"""L3→L4 对齐账本 —— 高 L3 排名到底有没有换来更高的过门率(确定性,零 LLM)。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §C2.1

**先量后试**。07-31 的 4/7 倒在主力门,而 L3 用的是 20 日 `cmf/obv` + 当日 `main_net`、
L4 要的是当日真金 —— 目标错位是个**看起来很有道理的假说**,但此前没有任何逐票账把它量过。
本模块只建账,不改 L3(§C2.1 明写「先建 ledger,不直接改 L3」)。

20 个成熟日后回答两个问题:

1. 高 L3 排名是否**显著**提高 L4 三门通过率与 T+2 excess?
2. 主力门错杀是否**集中于某种 L3 画像**(如高 cmf20 但当日 main_net 为负)?

只有当「misalignment 稳定存在 ∧ 某个可预注册 challenger 有独立 T+2 edge」时才设计 EXP-4;
**不得以「提高过门率」为目标函数** —— 过门率上升但 excess/左尾不改善 = 失败(§C2.1)。

  uv run --no-sync python -m autoresearch.learning.l3_l4_alignment
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = 1
MATURE_DAYS_REQUIRED = 20         # §C2.1 的固定成熟门
UNMEASURED = "UNMEASURED"

_L3_FIELDS = ("rank", "finalist", "conviction")
_L2_FIELDS = ("main_net_ratio", "cmf_20", "obv_20", "main_dist", "sector_state")
_COLS = ["date", "code", "l3_rank", "l3_finalist", "l3_conviction",
         "main_net_1d", "cmf20", "obv20", "main_dist", "sector_state",
         "l4_first_rejection", "gate_main", "gate_earnings", "gate_valuation",
         "excess_2", "outcome"]


def _l3_judged(scan_dir: Path) -> dict[str, dict]:
    """`_l3_judged.json` → {code: {rank, finalist, conviction}};缺文件 → {}。"""
    path = scan_dir / "_l3_judged.json"
    if not path.exists():
        return {}
    with contextlib.suppress(Exception):
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("judged") or []
        out = {}
        for i, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").split(".")[0].zfill(6)
            if not code or code == "000000":
                continue
            out[code] = {
                "rank": item.get("rank", i),
                "finalist": bool(item.get("finalist")),
                "conviction": item.get("conviction"),
            }
        return out
    return {}


def _l2_row(scan_dir: Path) -> dict[str, dict]:
    path = scan_dir / "L2_gbdt_top200.csv"
    if not path.exists():
        return {}
    with contextlib.suppress(Exception):
        frame = pd.read_csv(path, dtype={"code": str})
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        return {r["code"]: r for r in frame.to_dict("records")}
    return {}


def build_day(scan_dir: Path | str) -> pd.DataFrame:
    """单日逐票对齐行。L3 判断 × L2 确定性画像 × L4 门结局 × T+2 excess。"""
    from autoresearch.learning.gate_attribution import build_participation_day
    from autoresearch.scan.decision_read_model import read_decisions

    scan = Path(scan_dir)
    judged = _l3_judged(scan)
    if not judged:
        return pd.DataFrame(columns=_COLS)
    l2 = _l2_row(scan)

    decisions = {}
    if (scan / "decision_records.json").exists():
        with contextlib.suppress(Exception):
            decisions = read_decisions(scan)

    part = build_participation_day(scan)
    excess = {}
    gate_outcome: dict[str, dict[str, str]] = {}
    if len(part):
        for row in part.itertuples(index=False):
            excess.setdefault(row.code, row.excess_2)
            gate_outcome.setdefault(row.code, {})[row.gate] = row.outcome

    rows = []
    for code, l3 in sorted(judged.items()):
        priors = l2.get(code, {})
        decision = decisions.get(code)
        states = getattr(decision, "gate_states", {}) or {}
        rows.append({
            "date": scan.name,
            "code": code,
            "l3_rank": l3.get("rank"),
            "l3_finalist": l3.get("finalist"),
            "l3_conviction": l3.get("conviction"),
            "main_net_1d": priors.get("main_net_ratio"),
            "cmf20": priors.get("cmf_20"),
            "obv20": priors.get("obv_20"),
            "main_dist": priors.get("main_dist"),
            "sector_state": priors.get("sector_state"),
            "l4_first_rejection": getattr(decision, "first_rejection_stage", None),
            "gate_main": states.get("主力真在", UNMEASURED),
            "gate_earnings": states.get("业绩真兑现", UNMEASURED),
            "gate_valuation": states.get("估值不透支", UNMEASURED),
            "excess_2": excess.get(code),
            "outcome": (gate_outcome.get(code) or {}).get("主力真在", UNMEASURED),
        })
    return pd.DataFrame(rows, columns=_COLS)


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    root = Path(scan_root or "context/scan")
    if not root.exists():
        return pd.DataFrame(columns=_COLS)
    frames = [f for day in sorted(p for p in root.iterdir() if p.is_dir())
              if len(f := build_day(day))]
    if not frames:
        return pd.DataFrame(columns=_COLS)
    # 全 NA 列会让 concat 的 dtype 推断在新 pandas 里变行为(FutureWarning)。
    # 逐帧补齐列 + 显式 object dtype:结果不变(读的人只关心值),但不再依赖将被改掉的推断。
    return pd.concat([f.reindex(columns=_COLS).astype(object) for f in frames],
                     ignore_index=True)


def summarize(ledger: pd.DataFrame) -> dict:
    """finalist vs bench 的三门通过率与 T+2 excess 对照。**样本不足只给 n,不给结论。**"""
    if ledger is None or not len(ledger):
        return {"mature_days": 0, "ready": False, "reason": "无数据"}
    days = int(ledger["date"].nunique())
    out: dict = {"mature_days": days, "ready": days >= MATURE_DAYS_REQUIRED,
                 "required_days": MATURE_DAYS_REQUIRED}
    for label, subset in (("finalist", ledger[ledger["l3_finalist"] == True]),   # noqa: E712
                          ("bench", ledger[ledger["l3_finalist"] != True])):     # noqa: E712
        pass_n = int((subset["gate_main"] == "PASS").sum())
        known = int(subset["gate_main"].isin(["PASS", "FAIL"]).sum())
        ex = pd.to_numeric(subset["excess_2"], errors="coerce").dropna()
        out[label] = {
            "n": int(len(subset)),
            "gate_main_pass": pass_n,
            "gate_main_known": known,
            "gate_main_pass_rate": round(pass_n / known, 4) if known else None,
            "excess2_n": int(len(ex)),
            "excess2_mean": round(float(ex.mean()), 6) if len(ex) else None,
        }
    return out


def render(ledger: pd.DataFrame) -> list[str]:
    stats = summarize(ledger)
    out = ["# L3→L4 对齐账本(高排名换来更高过门率了吗)", ""]
    if not stats.get("mature_days"):
        return out + ["_无 `_l3_judged.json` × 门结局数据_"]
    ready = stats["ready"]
    out += [
        f"- 成熟日 **{stats['mature_days']}/{stats['required_days']}**"
        + ("" if ready else " → **IMMATURE,以下只作分母展示,不得据此下结论**"),
        "",
        "| L3 分层 | n | 主力门通过 | 可判分母 | 通过率 | excess2 n | excess2 均值 |",
        "|---|---:|---:|---:|---|---:|---|",
    ]
    for label in ("finalist", "bench"):
        s = stats[label]
        rate = "—" if s["gate_main_pass_rate"] is None else f"{s['gate_main_pass_rate']:.1%}"
        mean = "—" if s["excess2_mean"] is None else f"{s['excess2_mean'] * 100:+.2f}%"
        out.append(f"| {label} | {s['n']} | {s['gate_main_pass']} | {s['gate_main_known']} "
                   f"| {rate} | {s['excess2_n']} | {mean} |")
    out += [
        "",
        "_**目标函数不是过门率**(§C2.1):过门率上升但 excess/左尾不改善 = 失败。_",
        "_只有「misalignment 稳定存在 ∧ 某 challenger 有独立 T+2 edge」才设计 EXP-4;"
        "若涉及 L3 prompt,必须先处置在册的 `exp_20260729_l3_hard_constraint_f`。_",
    ]
    return out


def main(argv: list[str] | None = None) -> int:
    ledger = roll()
    target = Path("reports/learning/l3_l4_alignment.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(render(ledger)) + "\n", encoding="utf-8")
    table = Path("context/learning/l3_l4_alignment.csv")
    table.parent.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(table, index=False)
    print(f"[l3_l4_alignment] {len(ledger)} 行 / {ledger['date'].nunique() if len(ledger) else 0} 日 "
          f"→ {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
