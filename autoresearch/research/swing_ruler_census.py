#!/usr/bin/env python3
"""10 日尺(`common.ruler.SWING_RULER` = fwd_10_oc)预注册普查 —— H1–H4 读数。

design:       docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md §5 B2
registration: docs/research/2026-09-26-swing-ruler-family.spec.json(先冻后跑)

只读、零网络、零 LLM、**只记不学**:读 `$RPT/scan/_ledger/recommendations.csv`(批 5 Task 1
之后带 `outcome_status_swing`/`t10`)与共享湖,读数只落
`$RPT/research/swing_ruler/<experiment_id>/`(目录已存在即拒)。不写 run 目录、不写 staging、
不改生产账本,不回注任何评级/BUY/E6/主尺 —— 它是 B4 裁决的证据,不是开关。

口径全部复用既有原语,不另造第二套:

- 行值 = 账本 `fwd_10_oc` − 同日全湖 fwd_10_oc 在 D+1 开盘可买票上的中位(pp)。市场帧走
  `common.forward_returns.forward_frame` + `data.market_panel`(与 outcome/edge_census 同一
  实现),入场旗走 `ruler.entry_tradable(ruler_name=SWING)`(= `ruler.swing_entry_flag()`);
  日期取账本里可信日历算出的 `t1`/`t10`,湖分区必须恰好覆盖 D..t10 共 11 个 session。
- 描述统计走 `overnight_census.core.cell_stats`(日内等权 → 跨日等权);其 `judge` 的四态作
  对照列 `oc_judge` 照报 —— 它的门(≥60 日且 ≥300 事件、2022–2025 逐年同号)2026 单年账本
  按构造过不了,所以**判读用登记里的规则**,不用它。
- 显著性只用块 bootstrap:相邻分析日的 fwd_10 窗口重叠 9 个 session,逐日独立的区间窄到
  假。区间 = `research.robustness.block_sensitivity`(块长 1/5/10 全报不挑,判读取登记的
  决策块 10),p 值 = `common.stats.block_mean_test`,H1–H3 一族走
  `common.stats.family_adjustment(dependence="arbitrary")`(BY)。

预注册纪律:判读旋钮(评级集合、块长、区间水平、FDR 水平、样本门)全部读自冻结的 spec,
CLI 上只有路径与 `--since`;`--since` 收窄登记窗口时整张表降为 EXPLORATORY。度量原语的
代码出处按 spec 的 `code_sha` 核验(`registration.verify_code_provenance`),漂移即拒 ——
改任何口径 = 新 experiment_id。

  uv run --no-sync python -m autoresearch.research.swing_ruler_census \\
      --spec docs/research/2026-09-26-swing-ruler-family.spec.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import forward_returns as _fwd, ruler, workspace as ws
from autoresearch.common.atomic import atomic_write_bytes, atomic_write_json, sha256_file
from autoresearch.common.stats import DEFAULT_ALPHA, block_mean_test, family_adjustment
from autoresearch.contracts.research_experiment import validate_spec
from autoresearch.data import market_panel as _panel
from autoresearch.research import experiment_io as eio
from autoresearch.research.edge_census import pinned_codes
from autoresearch.research.overnight_census.core import cell_stats, judge
from autoresearch.research.registration import (
    parse_maturity_policy,
    registered_test_range,
    verify_code_provenance,
    verify_engine,
    verify_modes,
)
from autoresearch.research.robustness import BLOCK_SENSITIVITY, block_sensitivity
from autoresearch.scan import outcome as _outcome
from autoresearch.scan.populations import FROZEN_BASES

SCHEMA_VERSION = 1
RULE_VERSION = "swing_ruler_census.v1"
SWING = ruler.SWING_RULER
MAIN = ruler.MAIN_RULER

H1 = "swing_h1_hold_plus_finalists"
H2 = "swing_h2_lowturn_lane"
H3 = "swing_h3_rejection_negative"
H4 = "swing_h4_e6_r_tier_sign"
DIRECTIONAL = (H1, H2, H3)
HYPOTHESES = (*DIRECTIONAL, H4)

POSITIVE = "POSITIVE"          # 块 CI 下界 > 0 且 BY q ≤ fdr_alpha
NEGATIVE = "NEGATIVE"          # 块 CI 上界 < 0 且 BY q ≤ fdr_alpha
UNPROVEN = "UNPROVEN"          # 过了样本门但没量出来 —— **不等于**已证不存在
INSUFFICIENT = "INSUFFICIENT"  # n_days < 登记的样本门:不判读
DESCRIPTIVE = "DESCRIPTIVE"    # H4:只报符号一致率
EXPLORATORY = "EXPLORATORY"    # `--since` 收窄了登记窗口:照算,不判读

EVIDENCE_MODES = {"EOD_PROXY"}
COST_MODELS = {"none"}
#: 度量原语 —— 登记冻结时(spec.code_sha)就已存在的实现。它们变了,同一个假设量出来的就
#: 不再是同一个数,`verify_code_provenance` 逐路径比对、漂移或工作区脏即拒跑。
BEHAVIOR_ROOTS = (
    "autoresearch/common/stats.py",
    "autoresearch/common/ruler.py",
    "autoresearch/common/forward_returns.py",
    "autoresearch/data/market_panel.py",
    "autoresearch/research/robustness.py",
    "autoresearch/research/overnight_census/core.py",
    "autoresearch/research/edge_census.py",
    "autoresearch/contracts/research_experiment.py",
    "autoresearch/scan/outcome.py",
)
REPO_ROOT = Path(__file__).resolve().parents[2]
_RULE_KEYS = frozenset({"ge_hold_ratings", "veto_ratings", "lowturn_lane", "h4_exclude_tiers",
                        "h4_mode", "decision_block", "block_lengths", "ci_level", "fdr_alpha"})
#: 一格一行;`ci_lo`/`ci_hi` 是登记决策块(10)的区间,`*_b1`/`*_b5` 是敏感性(全报不挑)。
CELL_COLUMNS = (
    "hypothesis_id", "expected_direction", "n_rows", "n_days", "mean_pp", "median_pp", "hit",
    "ci_lo", "ci_hi", "block", "block_status", "p_block", "q_by",
    "ci_lo_b1", "ci_hi_b1", "ci_lo_b5", "ci_hi_b5", "half1_pp", "half2_pp",
    "sign_agree", "oc_judge", "verdict", "supports",
)
_FRAME_COLUMNS = ("analysis_date", "code", "excess_pp")


# ───────────────────────── 登记 → 判读旋钮 ─────────────────────────

def registered_rule(spec: dict) -> dict:
    """判读旋钮只从冻结方案的 `selection_rule` 取;缺键/多键/与原语不相容一律拒。"""
    rule = spec.get("selection_rule")
    if not isinstance(rule, dict):
        raise ValueError("selection_rule must register the census knobs as an object")
    missing, unknown = sorted(_RULE_KEYS - set(rule)), sorted(set(rule) - _RULE_KEYS)
    if missing or unknown:
        raise ValueError(f"selection_rule knobs mismatch (missing={missing}, unknown={unknown})")
    blocks = tuple(int(b) for b in rule["block_lengths"])
    if blocks != BLOCK_SENSITIVITY:
        raise ValueError(f"block_lengths must be the preregistered {BLOCK_SENSITIVITY}")
    decision = int(rule["decision_block"])
    if decision not in blocks:
        raise ValueError("decision_block must be one of block_lengths")
    if not math.isclose(float(rule["ci_level"]), 1.0 - DEFAULT_ALPHA):
        raise ValueError(f"block_sensitivity only yields {1.0 - DEFAULT_ALPHA:.2f} intervals")
    fdr = float(rule["fdr_alpha"])
    if not 0.0 < fdr < 1.0:
        raise ValueError("fdr_alpha must be in (0, 1)")
    return {"ge_hold": frozenset(map(str, rule["ge_hold_ratings"])),
            "veto": frozenset(map(str, rule["veto_ratings"])),
            "lowturn_lane": str(rule["lowturn_lane"]),
            "h4_exclude_tiers": frozenset(map(str, rule["h4_exclude_tiers"])),
            "h4_mode": str(rule["h4_mode"]),
            "decision_block": decision, "blocks": blocks,
            "ci_level": float(rule["ci_level"]), "fdr_alpha": fdr}


# ───────────────────────── 人口:账本 → MATURE_10 ∧ 非 📌 ─────────────────────────

def _s(value: object) -> str:
    return "" if value is None else str(value).strip()


def _compact(value: object) -> str:
    return _s(value).replace("-", "")[:8]


def _z6(value: object) -> str:
    return _s(value).split(".")[0].zfill(6)


def _num(value: object) -> float | None:
    try:
        number = float(_s(value))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _read_ledger(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _frozen_pins(scan_root: Path, run_id: str) -> tuple[set[str], bool]:
    """该 run 冻结 staging 里的 📌(`edge_census.pinned_codes`,两代标记都认)。
    冻结副本找不到 → `(空, False)`,只剩账本自己的 lane/role 旗,计数进 manifest。"""
    run = scan_root / run_id
    for base in FROZEN_BASES:
        folder = run / base
        if (folder / "finalists.csv").is_file():
            return pinned_codes(folder), True
    return set(), False


def select_population(rows: list[dict], *, test_range: tuple[str, str], since: str | None,
                      scan_root: Path) -> tuple[list[dict], dict]:
    """登记窗口 ∩ `--since` → 同日只留 run_id 最大的 run → 只留 MATURE_10 → 剔 📌。

    未成熟与缺章的行**逐类计数后剔除**,不当 0、不静默参与均值(Review Focus 2)。
    """
    start, end = test_range
    since_c = _compact(since) if since else ""
    window = [r for r in rows
              if start <= _compact(r.get("analysis_date")) < end
              and (not since_c or _compact(r.get("analysis_date")) >= since_c)]
    last_run: dict[str, str] = {}
    for r in window:
        day, run_id = _compact(r.get("analysis_date")), _s(r.get("run_id"))
        if run_id > last_run.get(day, ""):
            last_run[day] = run_id
    selected = [r for r in window if _s(r.get("run_id")) == last_run[_compact(r.get("analysis_date"))]]
    status = Counter(_s(r.get("outcome_status_swing")) for r in selected)
    known = (_outcome.MATURE_10, _outcome.PENDING_10, _outcome.MISSING_MARKET_DATA)
    mature = [r for r in selected if _s(r.get("outcome_status_swing")) == _outcome.MATURE_10]
    pins: dict[str, set[str]] = {}
    staged = 0
    for run_id in sorted({_s(r.get("run_id")) for r in mature}):
        pins[run_id], found = _frozen_pins(scan_root, run_id)
        staged += int(found)
    kept, n_pinned = [], 0
    for r in mature:
        if ("pinned" in (_s(r.get("lane")), _s(r.get("role")))
                or _z6(r.get("code")) in pins.get(_s(r.get("run_id")), set())):
            n_pinned += 1
            continue
        kept.append(r)
    counts = {
        "ledger_rows": len(rows), "rows_in_window": len(window),
        "rows_dropped_same_day_other_runs": len(window) - len(selected),
        "swing_status": {**{k: status.get(k, 0) for k in known},
                         "UNKNOWN": status.get("", 0),
                         "OTHER": sum(n for k, n in status.items() if k and k not in known)},
        "pinned_excluded": n_pinned,
        "runs_with_frozen_staging": staged,
        "runs_ledger_flags_only": len(pins) - staged,
    }
    return kept, counts


# ───────────────────────── 市场基准:同日全湖可交易中位 ─────────────────────────

def market_baselines(days: dict[str, tuple[str, str]], *,
                     lake_daily: Path | None = None) -> dict[str, dict]:
    """`{D: (t1, t10)}` → `{D: {"status": "OK", "median", "n_market", "tradable"}}`。

    日期是账本里可信日历算出的 T+1/T+10;湖分区必须**恰好**是 D..t10 的 11 个 session 且首二末
    对得上,否则当日基准 `UNAVAILABLE`(不顺延、不猜 —— 2026-09-12 日历完整性同一纪律)。
    """
    lake_days = _panel.lake_trade_days(lake_daily)
    out: dict[str, dict] = {}
    need: set[str] = set()
    for day, (t1, t10) in sorted(days.items()):
        window = [d for d in lake_days if day <= d <= t10] if (t1 and t10) else []
        if len(window) != 11 or window[0] != day or window[1] != t1 or window[-1] != t10:
            out[day] = {"status": "UNAVAILABLE",
                        "reason": f"湖分区 {len(window)} 个,不等于可信日历 D..t10 的 11 个 session"}
            continue
        out[day] = {"status": "LOAD", "window": window}
        need.update(window)
    piv = _panel.load_lake_pivots(sorted(need), lake_daily) if need else {}
    for day, info in out.items():
        if info["status"] != "LOAD":
            continue
        fr = _fwd.forward_frame(piv, info["window"], day) if piv else None
        if fr is None or fr.empty or SWING not in fr.columns:
            out[day] = {"status": "UNAVAILABLE", "reason": "前向收益帧为空"}
            continue
        tradable = ruler.entry_tradable(fr, ruler_name=SWING) & fr[SWING].notna()
        values = pd.to_numeric(fr.loc[tradable, SWING], errors="coerce").dropna()
        if values.empty:
            out[day] = {"status": "UNAVAILABLE", "reason": "当日无可交易 fwd_10 截面"}
            continue
        out[day] = {"status": "OK", "median": float(values.median()), "n_market": int(len(values)),
                    "tradable": tradable, "window": info["window"]}
    return out


# ───────────────────────── 分格 ─────────────────────────

def hypothesis_frames(population: list[dict], baselines: dict[str, dict],
                      rule: dict) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, dict]:
    """人口 → H1–H3 的 `(analysis_date, code, excess_pp)` 帧 + H4 的符号一致帧 + 逐格剔除计数。"""
    rows: dict[str, list[dict]] = {h: [] for h in DIRECTIONAL}
    drops = {h: {"candidates": 0, "baseline_unavailable": 0, "fwd10_missing": 0,
                 "not_tradable_d1_open": 0} for h in DIRECTIONAL}
    h4_rows: list[dict] = []
    for r in population:
        day, code = _compact(r.get("analysis_date")), _z6(r.get("code"))
        finalist = _s(r.get("role")) != "rated"
        rating, lane = _s(r.get("rating")), _s(r.get("lane"))
        members = [h for h, hit in ((H1, rating in rule["ge_hold"]),
                                    (H2, lane == rule["lowturn_lane"]),
                                    (H3, rating in rule["veto"])) if finalist and hit]
        if members:
            base, fwd10 = baselines.get(day) or {}, _num(r.get(SWING))
            if base.get("status") != "OK":
                reason = "baseline_unavailable"
            elif fwd10 is None:
                reason = "fwd10_missing"
            elif not bool(base["tradable"].get(code, False)):
                reason = "not_tradable_d1_open"
            else:
                reason = None
            for h in members:
                drops[h]["candidates"] += 1
                if reason:
                    drops[h][reason] += 1
                else:
                    rows[h].append({"analysis_date": day, "code": code,
                                    "excess_pp": (fwd10 - base["median"]) * 100.0})
        if (_s(r.get("e6_buy")).lower() == "true" and _s(r.get("mode")) == rule["h4_mode"]
                and _s(r.get("buy_tier")) not in rule["h4_exclude_tiers"]):
            gap, fwd10 = _num(r.get(MAIN)), _num(r.get(SWING))
            if gap is not None and fwd10 is not None:
                h4_rows.append({"analysis_date": day, "code": code,
                                "agree": bool(np.sign(gap) == np.sign(fwd10))})
    frames = {h: pd.DataFrame(v, columns=list(_FRAME_COLUMNS)) for h, v in rows.items()}
    return frames, pd.DataFrame(h4_rows, columns=["analysis_date", "code", "agree"]), drops


# ───────────────────────── 读数 + 判读 ─────────────────────────

def directional_cell(hypothesis_id: str, frame: pd.DataFrame, *, expected: str, rule: dict,
                     seed: int, n_boot: int) -> dict:
    """一格的描述统计(`cell_stats`)+ 全部登记块长的区间 + 决策块的 p 值。"""
    stats = cell_stats(frame, value_col="excess_pp", date_col="analysis_date",
                       sample_kind="event", seed=seed)
    daily = (frame.groupby("analysis_date")["excess_pp"].mean().sort_index().to_numpy(dtype=float)
             if len(frame) else np.asarray([], dtype=float))
    blocks = {b["block"]: b for b in block_sensitivity(daily, seed=seed, blocks=rule["blocks"],
                                                        n_boot=n_boot)}
    decision = blocks[rule["decision_block"]]
    test = block_mean_test(daily, block=rule["decision_block"], seed=seed, n_boot=n_boot)
    return {
        "hypothesis_id": hypothesis_id, "expected_direction": expected,
        "n_rows": stats["n_events"], "n_days": stats["n_days"], "mean_pp": stats["mean_pp"],
        "median_pp": stats["median_pp"], "hit": stats["hit"],
        "ci_lo": decision["lo"], "ci_hi": decision["hi"], "block": rule["decision_block"],
        "block_status": decision["status"], "p_block": test.pvalue, "q_by": None,
        "ci_lo_b1": blocks.get(1, {}).get("lo"), "ci_hi_b1": blocks.get(1, {}).get("hi"),
        "ci_lo_b5": blocks.get(5, {}).get("lo"), "ci_hi_b5": blocks.get(5, {}).get("hi"),
        "half1_pp": stats["half1_pp"], "half2_pp": stats["half2_pp"],
        "sign_agree": None, "oc_judge": judge(stats),
    }


def verdict(cell: dict, *, min_days: int, fdr_alpha: float, registered: bool) -> str:
    """登记规则:窗口先判(探索性)、样本门次之,再看块区间与 BY。"""
    if not registered:
        return EXPLORATORY
    if int(cell.get("n_days") or 0) < min_days:
        return INSUFFICIENT
    lo, hi, q = cell.get("ci_lo"), cell.get("ci_hi"), cell.get("q_by")
    if lo is None or hi is None or q is None:
        return UNPROVEN
    if q <= fdr_alpha and lo > 0:
        return POSITIVE
    if q <= fdr_alpha and hi < 0:
        return NEGATIVE
    return UNPROVEN


def _supports(expected: str, result: str) -> bool | None:
    """与预期同向显著 → True;反向显著(证伪)→ False;其余(未证/不足/描述)→ None。"""
    wanted = {"positive": POSITIVE, "negative": NEGATIVE}.get(expected)
    if wanted is None or result not in (POSITIVE, NEGATIVE):
        return None
    return result == wanted


def build_cells(frames: dict[str, pd.DataFrame], h4: pd.DataFrame, *, spec: dict, rule: dict,
                min_days: int, registered: bool) -> list[dict]:
    seed, n_boot = int(spec["bootstrap"]["seed"]), int(spec["bootstrap"]["n_boot"])
    expected = {h["hypothesis_id"]: h["expected_direction"] for h in spec["hypotheses"]}
    cells = {h: directional_cell(h, frames[h], expected=expected[h], rule=rule, seed=seed,
                                 n_boot=n_boot) for h in DIRECTIONAL}
    pvalues = [cells[h]["p_block"] for h in DIRECTIONAL]
    adjusted = family_adjustment([1.0 if p is None else p for p in pvalues],
                                 dependence="arbitrary", alpha=rule["fdr_alpha"])
    for h, p, adj in zip(DIRECTIONAL, pvalues, adjusted, strict=True):
        cells[h]["q_by"] = None if p is None else adj["q"]
        cells[h]["verdict"] = verdict(cells[h], min_days=min_days, fdr_alpha=rule["fdr_alpha"],
                                      registered=registered)
        cells[h]["supports"] = _supports(expected[h], cells[h]["verdict"])
    h4_cell = dict.fromkeys(CELL_COLUMNS)
    h4_cell.update(hypothesis_id=H4, expected_direction=expected[H4], n_rows=int(len(h4)),
                   n_days=int(h4["analysis_date"].nunique()) if len(h4) else 0,
                   sign_agree=(float(h4["agree"].mean()) if len(h4) else None),
                   verdict=DESCRIPTIVE if registered else EXPLORATORY, supports=None)
    return [cells[h] for h in DIRECTIONAL] + [h4_cell]


# ───────────────────────── 落盘 ─────────────────────────

def _fmt(value: object) -> str:
    """确定性格子文本:None → 空;bool → True/False;浮点 round(6)。"""
    if value is None:
        return ""
    if isinstance(value, (bool, np.bool_)):
        return "True" if value else "False"
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return "" if not math.isfinite(number) else repr(round(number, 6) + 0.0)
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return str(value)


def render_cells(cells: list[dict]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(CELL_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for cell in cells:
        writer.writerow({k: _fmt(cell.get(k)) for k in CELL_COLUMNS})
    return buf.getvalue()


def _pp(value: object, digits: int = 2) -> str:
    return "—" if value is None else f"{float(value):+.{digits}f}"


def render_readout(spec: dict, cells: list[dict], manifest: dict) -> str:
    by_id = {c["hypothesis_id"]: c for c in cells}
    rule = manifest["selection_rule"]
    counts, swing = manifest["counts"], manifest["counts"]["swing_status"]
    lines = [
        f"# 10 日尺预注册普查读数 · {spec['experiment_id']}",
        "",
        f"> 登记:`{manifest['spec']['path']}`(冻结 code_sha `{spec['code_sha'][:7]}`,"
        f"本次 HEAD `{str(manifest['code_sha'])[:7]}`)· 只读账本 + 共享湖 · 零网络 · "
        "不回注任何评级/BUY/E6/主尺。",
        f"> 窗口 `[{manifest['test_range'][0]}, {manifest['test_range'][1]})`"
        + (f" ∩ `--since {manifest['since']}`" if manifest.get("since") else "")
        + ("" if manifest["registered_window"] else " —— **收窄了登记窗口,全表只作探索性读数**")
        + f" · 样本门 n_days ≥ {manifest['min_days']} · 决策区间 = 块长 "
        f"{rule['decision_block']} 的块 bootstrap {round(rule['ci_level'] * 100)}% · "
        f"H1–H3 一族 BY(arbitrary)q ≤ {rule['fdr_alpha']}",
        "",
        "## 判读",
        "",
        "| 假设 | 预期 | n_days | n_rows | 均值 pp | 块10 CI pp | q_BY | 判读 | 与预期 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for h in DIRECTIONAL:
        c = by_id[h]
        ci = ("—" if c["ci_lo"] is None or c["ci_hi"] is None
              else f"[{_pp(c['ci_lo'])}, {_pp(c['ci_hi'])}]")
        q = "—" if c["q_by"] is None else f"{c['q_by']:.3f}"
        agree = {True: "同向", False: "**反向证伪**", None: "—"}[c["supports"]]
        lines.append(f"| `{h}` | {c['expected_direction']} | {c['n_days']} | {c['n_rows']} | "
                     f"{_pp(c['mean_pp'])} | {ci} | {q} | {c['verdict']} | {agree} |")
    h4 = by_id[H4]
    rate = "—" if h4["sign_agree"] is None else f"{100 * h4['sign_agree']:.0f}%"
    lines += [
        "",
        f"H4(描述性,不入 BY 族):E6 相对 BUY {h4['n_rows']} 笔 / {h4['n_days']} 天,"
        f"fwd_10_oc 与 gap_c1_o2 符号一致率 {rate} —— {h4['verdict']}。",
        "",
        "## 块长敏感性(全报不挑)",
        "",
        "| 假设 | 块 1 | 块 5 | 块 10(决策) |",
        "|---|---|---|---|",
    ]
    for h in DIRECTIONAL:
        c = by_id[h]

        def iv(lo, hi):
            return "—" if lo is None or hi is None else f"[{_pp(lo)}, {_pp(hi)}]"

        lines.append(f"| `{h}` | {iv(c['ci_lo_b1'], c['ci_hi_b1'])} | "
                     f"{iv(c['ci_lo_b5'], c['ci_hi_b5'])} | {iv(c['ci_lo'], c['ci_hi'])} |")
    base = manifest["baselines"]
    lines += [
        "",
        "## 人口与剔除(未成熟行不进分母)",
        "",
        f"- 账本 {counts['ledger_rows']} 行;登记窗口内 {counts['rows_in_window']} 行;"
        f"同日多 run 只留最后一个,剔 {counts['rows_dropped_same_day_other_runs']} 行。",
        f"- 10 日尺成熟章:MATURE_10 {swing[_outcome.MATURE_10]} · PENDING_10 "
        f"{swing[_outcome.PENDING_10]} · MISSING_MARKET_DATA {swing[_outcome.MISSING_MARKET_DATA]}"
        f" · 缺章(旧行){swing['UNKNOWN']} · 其它 {swing['OTHER']} —— 只有 MATURE_10 进人口。",
        f"- 📌 剔 {counts['pinned_excluded']} 行(冻结 staging 可查的 run "
        f"{counts['runs_with_frozen_staging']} 个,只凭账本旗的 run {counts['runs_ledger_flags_only']} 个)。",
        f"- 人口:{manifest['n_rows']} 行 / {manifest['n_days']} 个分析日。",
        f"- 市场基准:可得 {base['ok']} 天,不可得 {base['unavailable']} 天"
        + (f"({', '.join(base['unavailable_days'])})" if base["unavailable_days"] else "") + "。",
    ]
    for h in DIRECTIONAL:
        d = manifest["cell_drops"][h]
        lines.append(f"- `{h}`:候选 {d['candidates']} 行,剔 基准不可得 {d['baseline_unavailable']} · "
                     f"fwd_10 缺值 {d['fwd10_missing']} · D+1 开盘不可买 {d['not_tradable_d1_open']}。")
    lines += [
        "",
        "## 读法与局限",
        "",
        "- **observational**:finalist/评级不是随机分配的;正号只说明这批票事后跑赢同日市场中位,"
        "不构成「评级造成超额」的因果主张。",
        "- H2/H3 的动机读数(08-21 低位转强、09-24 否决价值)与本账本样本重叠:这两格是按登记口径"
        "的稳健性复核,不是独立确认。",
        "- 块 bootstrap 按观察到的扫描日序列重采样(扫描日有缺口时一个块跨越的交易日更多,重叠"
        "相关只会被多覆盖)。",
        "- `oc_judge` 列 = `overnight_census.core.judge`(≥60 日且 ≥300 事件、2022–2025 逐年同号、"
        "0.15pp 成本门),2026 单年账本按构造过不了,只作对照,不作判读。",
        "- UNPROVEN **不等于**已证无效;INSUFFICIENT 只说明样本还没攒够(B4 需 ≥40 个交易日)。",
        "- 停机规则(spec §5 B2):H1 与 H2 同时不成立 → B 线止于「观察席只展示不推」;不追加第五个假设。",
        "",
    ]
    return "\n".join(lines)


def _measure(*, rule: dict, test_range: tuple[str, str], since: str | None, scan: Path,
             lake: Path | None) -> tuple:
    """账本 → 人口 → 市场基准 → 分格(`run_census` 与 `census_sizes` 共用同一条路径)。"""
    ledger_path = _outcome.ledger_root(scan) / _outcome.LEDGER_CSV
    if not ledger_path.is_file():
        raise FileNotFoundError(f"账本不存在:{ledger_path}")
    population, counts = select_population(_read_ledger(ledger_path), test_range=test_range,
                                           since=since, scan_root=scan)
    pairs = {_compact(r.get("analysis_date")): (_compact(r.get("t1")), _compact(r.get("t10")))
             for r in population}
    baselines = market_baselines(pairs, lake_daily=lake)
    frames, h4, drops = hypothesis_frames(population, baselines, rule)
    return ledger_path, population, counts, baselines, frames, h4, drops


def census_sizes(*, spec_path: Path | str, since: str | None = None,
                 scan_root: Path | str | None = None, reports_root: Path | str | None = None,
                 lake_daily: Path | str | None = None) -> dict:
    """样本量探针:只报每格 `n_days`/`n_rows` 与 H1 是否到样本门,**不报任何收益、不建目录**。

    stop_rule 规定 B4 只采用「冻结窗结束后第一次 H1 n_days ≥ 样本门」的读数,而读数目录是
    一次性的(已存在即拒)。这个探针让人知道那一刻到没到,又不消耗目录、不泄露读数。
    """
    spec = validate_spec(json.loads(Path(spec_path).read_text(encoding="utf-8")))
    verify_engine(spec)
    rule = registered_rule(spec)
    min_days = parse_maturity_policy(spec["maturity_policy"])
    rpt = Path(reports_root) if reports_root is not None else ws.reports_root()
    scan = Path(scan_root) if scan_root is not None else rpt / "scan"
    _path, _pop, _counts, _base, frames, h4, _drops = _measure(
        rule=rule, test_range=registered_test_range(spec), since=since, scan=scan,
        lake=Path(lake_daily) if lake_daily is not None else None)
    sizes = {h: {"n_days": int(frames[h]["analysis_date"].nunique()), "n_rows": int(len(frames[h]))}
             for h in DIRECTIONAL}
    sizes[H4] = {"n_days": int(h4["analysis_date"].nunique()) if len(h4) else 0,
                 "n_rows": int(len(h4))}
    return {"sizes": sizes, "min_days": min_days, "h1_ready": sizes[H1]["n_days"] >= min_days}


def run_census(*, spec_path: Path | str, since: str | None = None,
               scan_root: Path | str | None = None, reports_root: Path | str | None = None,
               lake_daily: Path | str | None = None,
               repo_root: Path | str | None = None) -> Path:
    """按冻结方案跑一次普查 → `$RPT/research/swing_ruler/<experiment_id>/`。

    全部校验(方案形状/引擎/模式/旋钮/代码出处/账本在场)都在建目录之前:被拒的跑法一个字节
    都不落盘。目录已存在 → `FileExistsError`(没有 --force:改口径就换 experiment_id)。
    """
    spec_file = Path(spec_path)
    spec = validate_spec(json.loads(spec_file.read_text(encoding="utf-8")))
    verify_engine(spec)
    verify_modes(spec, evidence_modes=EVIDENCE_MODES, cost_models=COST_MODELS)
    rule = registered_rule(spec)
    min_days = parse_maturity_policy(spec["maturity_policy"])
    test_range = registered_test_range(spec)
    provenance = verify_code_provenance(spec, BEHAVIOR_ROOTS,
                                        repo_root=Path(repo_root) if repo_root else REPO_ROOT)
    rpt = Path(reports_root) if reports_root is not None else ws.reports_root()
    scan = Path(scan_root) if scan_root is not None else rpt / "scan"
    lake = Path(lake_daily) if lake_daily is not None else None
    ledger_path, population, counts, baselines, frames, h4, drops = _measure(
        rule=rule, test_range=test_range, since=since, scan=scan, lake=lake)
    registered = not since or _compact(since) <= test_range[0]
    cells = build_cells(frames, h4, spec=spec, rule=rule, min_days=min_days,
                        registered=registered)

    lake_root = lake if lake is not None else ws.lake_root() / "daily"
    used_days = sorted({d for info in baselines.values() if info["status"] == "OK"
                        for d in info["window"]})
    inputs = [{"path": str(ledger_path.resolve()), "sha256": sha256_file(ledger_path)}]
    inputs += [{"path": str((lake_root / f"{d}.parquet").resolve()),
                "sha256": sha256_file(lake_root / f"{d}.parquet")}
               for d in used_days if (lake_root / f"{d}.parquet").is_file()]
    unavailable = sorted(d for d, info in baselines.items() if info["status"] != "OK")
    manifest = {
        "schema_version": SCHEMA_VERSION, "rule_version": RULE_VERSION, "engine": ws.ENGINE,
        "experiment_id": spec["experiment_id"], "experiment_family": spec["experiment_family"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec": {"path": str(spec_file.resolve()), "sha256": sha256_file(spec_file)},
        "code_sha": provenance.get("observed"), "code_identity": provenance,
        "inputs": inputs, "n_rows": len(population),
        "n_days": len({_compact(r.get("analysis_date")) for r in population}),
        "counts": counts, "cell_drops": drops,
        "baselines": {"ok": len(baselines) - len(unavailable), "unavailable": len(unavailable),
                      "unavailable_days": unavailable},
        "since": since, "registered_window": registered, "test_range": list(test_range),
        "min_days": min_days, "bootstrap": dict(spec["bootstrap"]),
        "selection_rule": dict(spec["selection_rule"]), "ruler": MAIN, "swing_ruler": SWING,
    }
    out_dir = eio.create_experiment_dir(rpt / "research" / "swing_ruler", spec["experiment_id"])
    eio.freeze_spec(out_dir, spec)
    atomic_write_bytes(out_dir / "cells.csv", render_cells(cells).encode("utf-8"))
    atomic_write_json(out_dir / "manifest.json", manifest)
    atomic_write_bytes(out_dir / "readout.md", render_readout(spec, cells, manifest).encode("utf-8"))
    return out_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="10 日尺预注册普查(只读;读数落 $RPT/research/swing_ruler/)")
    ap.add_argument("--spec", required=True, type=Path, help="已冻结的方案 spec.json")
    ap.add_argument("--since", default=None, help="收窄登记窗口 → 全表 EXPLORATORY(不判读)")
    ap.add_argument("--reports-root", type=Path, default=None, help="缺省 = reports_<engine>/")
    ap.add_argument("--scan-root", type=Path, default=None, help="账本与 run 目录根,缺省 = <reports-root>/scan")
    ap.add_argument("--lake", type=Path, default=None, help="lake/daily,缺省 = lake/daily")
    ap.add_argument("--sizes-only", action="store_true",
                    help="只报每格 n_days/n_rows 与 H1 是否到样本门;不报收益、不建目录")
    args = ap.parse_args(argv)
    try:
        if args.sizes_only:
            sizes = census_sizes(spec_path=args.spec, since=args.since, scan_root=args.scan_root,
                                 reports_root=args.reports_root, lake_daily=args.lake)
            print(json.dumps({"ok": True, **sizes}, ensure_ascii=False))
            return 0
        out = run_census(spec_path=args.spec, since=args.since, scan_root=args.scan_root,
                         reports_root=args.reports_root, lake_daily=args.lake)
    except FileExistsError as exc:
        print(f"[swing_ruler_census] 落点已存在,拒绝覆盖(改口径 = 换 experiment_id):{exc}",
              file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
