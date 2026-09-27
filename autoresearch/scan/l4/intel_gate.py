#!/usr/bin/env python3
"""intel 死票门(2026-09-26 daily-engine §4 A3):slim 数字已决绝为负的 finalist 不派活体情报。

design: docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md §4 A3
plan:   docs/superpowers/plans/2026-09-26-daily-engine-batch6-intel-gate.md

**默认关**(`l4_intel.skip_when_dead: false`)= 不落任何文件、逐字 parity。开旋钮是用户动作,
而且派发两条路径(`l4-stock.js` / session_v1 runner)读 `_intel_gate.json` 是批 6 Task 3 ——
冻结窗之后才接线,在那之前打开它**不会**少派任何一份 intel。

谓词(`decide_row`)只读确定性事实,**不读卡、不读情报**(阈值住代码常量 = 行为归属):

- L1 因子行 `main_inflow_yi`(主力绝对净额,亿)< 0 ∧ `cmf_20` < 0 ∧ `obv_mom_20` < 0;
- 且无催化:无 📅 日历催化(`calendar.csv` 的预约披露 / 指数调样,与 L4 简报
  `calendar.calendar_flags` 印 📅 的同一口径)**也**无近 10 日事件(`L3_catalyst.csv` 的
  回购 / 增减持 / 调研计数任一 > 0)—— 两个来源取并集,宁多派不漏派;
- 📌 保送(lane=pinned 或 pinned_note 非空)与 composite 证据席(guard=composite_seat)**永不**跳过。

数据缺失 ≠ 死:三列任一缺(NaN / 空 / L1 缺行)→ 不判死,note 写「因子缺列」(Review Focus 1)。

  uv run --no-sync python -m autoresearch.scan.l4.intel_gate decide <scan_dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from autoresearch.contracts import artifacts as _artifacts
from autoresearch.scan.l4.intel_status import (
    _EVENT_ROW,
    _SCORE_RE,
    _header_index,
    _window_head,
)

GATE_FILENAME = _artifacts.by_name("intel_gate").path
SCHEMA_VERSION = 1
DEAD_MAIN_INFLOW_YI_MAX = 0.0    # 主力绝对净额 < 0
DEAD_CMF_MAX = 0.0               # cmf_20 < 0
DEAD_OBV_MAX = 0.0               # obv_mom_20 < 0
FACTORS = (("main_inflow_yi", DEAD_MAIN_INFLOW_YI_MAX), ("cmf_20", DEAD_CMF_MAX),
           ("obv_mom_20", DEAD_OBV_MAX))
#: `L3_catalyst.csv` 的事件计数列(`scan/agents/l3_catalyst._COLS` 去掉 code)。
EVENT_COLUMNS = ("rep_impl", "rep_plan", "holder_in", "holder_de", "surv_n")
#: 指数调样里**不是**催化的相位:生效前夜是「禁止」事实(L4 简报印 ⛔),不是 📅。
_EVE_PHASE = "passive_close_eve"


@dataclass(frozen=True)
class Verdict:
    dead: bool
    note: str


def decide_row(row: dict) -> Verdict:
    """一只 finalist 的判决。顺序:保护 → 催化 → 缺列 → 三线同负。"""
    if bool(row.get("pinned")) or str(row.get("guard") or "") == "composite_seat":
        return Verdict(False, "📌/证据席恒派 intel")
    if bool(row.get("has_catalyst")):
        return Verdict(False, str(row.get("catalyst_note") or "带日期催化,派 intel"))
    for key, _cap in FACTORS:
        value = row.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return Verdict(False, f"因子缺列 {key},不判死")
        if math.isnan(number):
            return Verdict(False, f"因子缺列 {key},不判死")
    dead = all(float(row[key]) < cap for key, cap in FACTORS)
    return Verdict(dead, "三线同负且无催化" if dead else "有一线不为负")


def is_dead(row: dict) -> bool:
    return decide_row(row).dead


def _read(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype={"code": str})
    except (OSError, ValueError, pd.errors.EmptyDataError):
        return pd.DataFrame()


def _codes(frame: pd.DataFrame) -> pd.Series:
    return frame["code"].astype(str).str.split(".").str[0].str.zfill(6)


def dated_catalyst_codes(scan_dir: Path | str) -> set[str]:
    """📅 日历催化票:`calendar.csv` 的预约披露 + 指数调样(生效前夜除外)。

    与 `calendar.calendar_flags` 印 📅 的口径逐条一致(那边按行渲染、依赖目录名是日期;
    这里只要集合,冻结 staging 的目录名是 `staging` 也能用)—— 反漂移测试锁两边同集合。
    """
    cal = _read(Path(scan_dir) / "calendar.csv")
    if cal.empty or not {"code", "kind"} <= set(cal.columns):
        return set()
    kind = cal["kind"].astype(str)
    phase = cal.get("detail", pd.Series("", index=cal.index)).astype(str).str.partition("|")[2]
    dated = (kind == "disclosure") | ((kind == "index_rebalance") & (phase != _EVE_PHASE))
    return set(_codes(cal[dated]))


def event_catalyst_codes(scan_dir: Path | str) -> set[str]:
    """近 10 日事件票:`L3_catalyst.csv` 的回购 / 增减持 / 调研计数任一 > 0。"""
    cat = _read(Path(scan_dir) / "L3_catalyst.csv")
    cols = [c for c in EVENT_COLUMNS if c in cat.columns]
    if cat.empty or "code" not in cat.columns or not cols:
        return set()
    counts = cat[cols].apply(pd.to_numeric, errors="coerce").fillna(0)
    return set(_codes(cat[counts.sum(axis=1) > 0]))


def gate_rows(scan_dir: Path | str) -> list[dict]:
    """finalists.csv × L1 因子行 × 催化 × 📌/证据席 → 逐票谓词输入(`decide`/`replay` 共用)。"""
    scan = Path(scan_dir)
    fin = _read(scan / "finalists.csv")
    if fin.empty or "code" not in fin.columns:
        return []
    l1 = _read(scan / "L1_scored_full.csv")
    factors: dict[str, dict] = {}
    if not l1.empty and "code" in l1.columns:
        keep = [k for k, _cap in FACTORS if k in l1.columns]
        for code, rec in zip(_codes(l1), l1[keep].to_dict("records"), strict=True):
            factors.setdefault(code, rec)
    dated, events = dated_catalyst_codes(scan), event_catalyst_codes(scan)
    rows = []
    for fin_row in fin.to_dict("records"):
        code = str(fin_row.get("code")).split(".")[0].zfill(6)
        note = ""
        if code in dated:
            note = "带日期催化(📅 日历),派 intel"
        elif code in events:
            note = "近 10 日事件(回购/增减持/调研),派 intel"
        pinned_note = fin_row.get("pinned_note")
        rows.append({
            "code": code,
            **factors.get(code, {}),
            "pinned": (str(fin_row.get("lane") or "").strip() == "pinned"
                       or (isinstance(pinned_note, str) and bool(pinned_note.strip()))),
            "guard": str(fin_row.get("guard") or "").strip() if isinstance(
                fin_row.get("guard"), str) else "",
            "has_catalyst": bool(note), "catalyst_note": note,
        })
    return rows


def decide(scan_dir: Path | str) -> dict:
    """判死;旋钮开才落 `_intel_gate.json`。返回 `{enabled, skipped, checked}`。"""
    from autoresearch.scan.user_config import knob

    scan = Path(scan_dir)
    enabled = bool(knob("l4_intel", "skip_when_dead", None, False))
    rows = gate_rows(scan)
    verdicts = {r["code"]: decide_row(r) for r in rows}
    skipped = [code for code, v in verdicts.items() if v.dead] if enabled else []
    result = {"enabled": enabled, "skipped": skipped, "checked": len(rows)}
    if enabled:
        doc = {"schema_version": SCHEMA_VERSION, "date": scan.name, **result,
               "notes": {code: v.note for code, v in verdicts.items()}}
        (scan / GATE_FILENAME).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
    return result


# ───────────────────────── 离线回放(批 6 Task 2;上线前的证据门) ─────────────────────────
#
# 对真跑日的**冻结** staging 重判谓词,再对照该场真实派出的 `_l4_intel_<code>.md` 事件段与
# `_final_ratings.json` 终评级 —— 问的是:被判死的票里,intel 有没有抓到过 T0 负面、卡最后
# 有没有 ≥Hold。**不受旋钮影响**(生产默认关;回放必须看谓词本身会怎么判)。

#: 预注册停机规则(看回放读数之前写死;改 = 重新立案)。plan:T0 负面率 >10% 或 ≥Hold 率 >5%
#: → 不上线;spec §4 A3:命中卡里**出现过** ≥OW 或 T0 负面硬信号 → 收窄或放弃。两条都过才
#: `launch_eligible`。T0 负面率按**最坏情况**算:无法测(旧稿无时效窗列 / 没派 intel)的票
#: 算进分子 —— 证明不了没有,就不能当没有。
STOP_T0_NEGATIVE_SHARE = 0.10
STOP_GE_HOLD_SHARE = 0.05
#: T0 行净分 ≤ −1 = 负面(l4-intel 契约:−1 偏空 / −2 重大利空)。
T0_NEGATIVE_MAX = -1.0
GE_HOLD = ("Buy", "Overweight", "Hold")
GE_OW = ("Buy", "Overweight")
REPLAY_COLUMNS = ("run", "code", "dead", "note", "final_rating", "intel_schema",
                  "intel_events", "intel_t0_negative", "intel_t0_min")


def intel_event_facts(text: str) -> dict:
    """intel 稿事件段 → `{schema, events, t0_negative, t0_min}`。

    表头按列名定位(`intel_status._header_index`,两代 schema 并存);旧稿没有时效窗列 →
    `t0_negative=None`(不猜)。净分格复用 `intel_status._SCORE_RE`,先把全角减号 `−` 归一
    成 `-` —— 真稿两种都有,不归一会把 `−2.0` 静默漏成「无负面」。
    """
    lines = text.splitlines()
    header = _header_index(lines)
    if header is None:
        return {"schema": "no_event_table", "events": 0, "t0_negative": None, "t0_min": None}
    _, idx = header
    events, t0_scores = 0, []
    for line in lines:
        if not _EVENT_ROW.match(line):
            continue
        events += 1
        cells = line.strip().strip("|").split("|")
        if "window" not in idx or max(idx["window"], idx["score"]) >= len(cells):
            continue
        if _window_head(cells[idx["window"]]) != "T0":
            continue
        m = _SCORE_RE.match(cells[idx["score"]].replace("−", "-"))
        if m:
            t0_scores.append(float(m.group("num")))
    if "window" not in idx:
        return {"schema": "legacy_no_window", "events": events, "t0_negative": None,
                "t0_min": None}
    return {"schema": "window", "events": events,
            "t0_negative": any(s <= T0_NEGATIVE_MAX for s in t0_scores),
            "t0_min": min(t0_scores) if t0_scores else None}


def _run_label(scan_dir: Path) -> str:
    """`reports_<engine>/scan/<run>/trace/staging` → `<run>`;其它形状 → 目录名。"""
    if scan_dir.name == "staging" and scan_dir.parent.name == "trace":
        return scan_dir.parent.parent.name
    return scan_dir.name


def replay(scan_dirs) -> list[dict]:
    """逐场冻结 staging → 逐票 `REPLAY_COLUMNS` 行(纯读,不写任何文件)。"""
    rows: list[dict] = []
    for raw in scan_dirs:
        scan = Path(raw)
        ratings: dict = {}
        path = scan / "_final_ratings.json"
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                ratings = {str(k).zfill(6): str(v) for k, v in loaded.items()}
            except (OSError, ValueError, AttributeError):
                ratings = {}
        for row in gate_rows(scan):
            verdict = decide_row(row)
            intel = scan / f"_l4_intel_{row['code']}.md"
            facts = (intel_event_facts(intel.read_text(encoding="utf-8")) if intel.is_file()
                     else {"schema": "absent", "events": 0, "t0_negative": None, "t0_min": None})
            rows.append({"run": _run_label(scan), "code": row["code"], "dead": verdict.dead,
                         "note": verdict.note, "final_rating": ratings.get(row["code"], ""),
                         "intel_schema": facts["schema"], "intel_events": facts["events"],
                         "intel_t0_negative": facts["t0_negative"],
                         "intel_t0_min": facts["t0_min"]})
    return rows


def replay_summary(rows: list[dict]) -> dict:
    """汇总 + 预注册停机规则判定。"""
    dead = [r for r in rows if r["dead"]]
    runs = sorted({r["run"] for r in rows})
    n_dead = len(dead)
    t0_neg = sum(1 for r in dead if r["intel_t0_negative"] is True)
    t0_unmeasured = sum(1 for r in dead if r["intel_t0_negative"] is None)
    ge_hold = sum(1 for r in dead if r["final_rating"] in GE_HOLD)
    ge_ow = sum(1 for r in dead if r["final_rating"] in GE_OW)
    out = {
        "n_runs": len(runs), "n_checked": len(rows), "n_dead": n_dead,
        "n_dead_t0_negative": t0_neg, "n_dead_t0_unmeasured": t0_unmeasured,
        "n_dead_rated_ge_hold": ge_hold, "n_dead_rated_ge_ow": ge_ow,
        "dead_rating_dist": dict(sorted(Counter(r["final_rating"] or "—" for r in dead).items())),
        "saved_intel_per_run": (round(n_dead / len(runs), 3) if runs else None),
    }
    if n_dead == 0:
        out.update(t0_negative_share=None, t0_negative_share_worst=None, ge_hold_share=None,
                   plan_rule_pass=False, spec_rule_pass=False, launch_eligible=False,
                   stop_reason="回放期一只票都没判死 —— 上线无收益")
        return out
    worst = (t0_neg + t0_unmeasured) / n_dead
    ge_hold_share = ge_hold / n_dead
    plan_ok = worst <= STOP_T0_NEGATIVE_SHARE and ge_hold_share <= STOP_GE_HOLD_SHARE
    spec_ok = ge_ow == 0 and t0_neg + t0_unmeasured == 0
    out.update(t0_negative_share=t0_neg / n_dead, t0_negative_share_worst=worst,
               ge_hold_share=ge_hold_share, plan_rule_pass=plan_ok, spec_rule_pass=spec_ok,
               launch_eligible=plan_ok and spec_ok)
    return out


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="intel 死票门(A3;默认关)")
    sub = ap.add_subparsers(dest="command", required=True)
    p_decide = sub.add_parser("decide", help="判死;旋钮开才落 _intel_gate.json")
    p_decide.add_argument("scan_dir", type=Path)
    p_replay = sub.add_parser("replay", help="离线回放:stdout 逐票 CSV,stderr 汇总 JSON")
    p_replay.add_argument("scan_dirs", type=Path, nargs="+")
    args = ap.parse_args(argv)
    if args.command == "decide":
        print(json.dumps(decide(args.scan_dir), ensure_ascii=False))
        return 0
    if args.command == "replay":
        rows = replay(args.scan_dirs)
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(REPLAY_COLUMNS)
        for r in rows:
            writer.writerow([_cell(r.get(c)) for c in REPLAY_COLUMNS])
        print(json.dumps(replay_summary(rows), ensure_ascii=False), file=sys.stderr)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
