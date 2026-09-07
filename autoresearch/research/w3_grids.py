#!/usr/bin/env python3
"""F6:W3 三格普查 —— 首板缩量回调 / 晚封板 / 机构席位,零 LLM、只读、不回注。

预注册(**先写后看**):`docs/research/2026-09-07-w3-three-grids-family.spec.json`,由
`contracts.research_experiment.validate_spec` 校验。读数落
`reports_<engine>/research/w3_grids/<experiment_id>/`,**不进生产**。

## 为什么是独立仪器,不是往 08-28 隔夜普查里加三格

`overnight_census.families.CELL_ORDER` 自带一道守卫:「格清单漂移」→ AssertionError,注释写着
「防**读完结果再加格**」。那份普查的格表就是它的预注册,读数已经发布;往里加格 = 事后改预注册,
正是那道守卫要拦的事。所以三格是**新的**预注册研究,复用同一批原语:

- 前瞻收益:`common.forward_returns.forward_returns`(与账本/普查/漏斗同一实现);
- 可交易性:`common.ruler.entry_tradable` / `ENTRY_FLAG` / `EXIT_FLAG`;
- 区间与判读:`overnight_census.core.cell_stats` / `judge`(日等权、逐年同号、两半同号、样本门);
- 多重比较:`common.stats.family_adjustment(dependence="arbitrary")` → BY(三格共享市场日,不独立);
- 块长敏感性:`research.robustness.block_sensitivity`(1/5/10 全报)。

## 三格的定义与它们各自的诚实边界

**G1 首板缩量回调**:D 日首板(`limit_list_d.limit=='U'` ∧ `limit_times==1`),D+1 **量比**
(`daily_basic.volume_ratio`,A 股标准指标 = 当日每分钟均量 / 过去 5 日每分钟均量)`< 0.6`
且回调(`close[D+1]/close[D] − 1 ∈ [−5%, 0)`)。入场 = D+1 收盘,标签 = 主尺。

**两条诚实边界**:① 14:45 决策时只看得到当日**截至那时**的量,这里用 D+1 全日量比作代理 ——
差最后 15 分钟,是**已知方向的乐观**偏差(收盘前放量会让本该落选的票留在样本里);
② `signal_coverage` 里带 `g1_threshold` 诊断:首板次日量比的分位数与逐级命中数。它存在的理由是
**空样本必须可解释** —— 「这一格没有观测」与「这一格的阈值在这批数据上几乎不可达」是两回事,
读数得让人分得出。阈值是预注册的,看完分布**不许回头改**;要改就是新 experiment_id。

**G2 晚封板**:D 日涨停且**末次**封板时刻 ∈ [14:00, 15:00)。按 `buyable_c1` 分两桶报告:
可买桶才有可执行含义;不可买桶是 X_ORACLE(08-28 已证「收益随买得到的可能性单调递减」),
**只报不判**。

**G3 机构席位**:D 日龙虎榜「机构专用」席位合计净买 > 0。标签是**敏感尺** `fwd_5_oc` /
`fwd_10_oc`(周级);主尺并列输出**只观察**,不构成本格判据(Q-F)。

用法:

    uv run --no-sync python -m autoresearch.research.w3_grids --spec <spec.json> \\
        [--since 20220301] [--until 20260901] [--out-parent <目录>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler as _ruler, workspace as ws
from autoresearch.common.forward_returns import forward_returns
from autoresearch.common.stats import DEFAULT_BOOT, DEFAULT_SEED, block_mean_test, family_adjustment
from autoresearch.contracts.research_experiment import validate_spec
from autoresearch.data.market_panel import lake_trade_days, load_lake_pivots
from autoresearch.research import experiment_io as eio
from autoresearch.research.registration import (
    file_manifest,
    parse_maturity_policy,
    registered_date_slice,
    registered_test_range,
    verify_code_provenance,
    verify_engine,
    verify_manifest,
    verify_modes,
)
from autoresearch.research.overnight_census import core
from autoresearch.research.robustness import block_sensitivity

G1, G2, G3 = "w3_first_board_pullback", "w3_late_seal_premium", "w3_institution_seat_5_20d"
GRID_ORDER: tuple[str, ...] = (G1, G2, G3)
GRID_LABEL = {G1: "首板缩量回调低吸", G2: "晚封板次日溢价", G3: "机构席位 5–20 日"}
#: 每格的主标签(判据所用的尺)。G3 是敏感尺 —— 主尺对它**只观察**。
GRID_LABEL_COL = {G1: "gap_pp", G2: "gap_pp", G3: "fwd5_pp"}
GRID_OBSERVE_COLS = {G1: ("fwd5_pp", "fwd10_pp"), G2: ("fwd5_pp", "fwd10_pp"),
                     G3: ("fwd10_pp", "gap_pp")}
LABEL_BLOCK = {"gap_pp": 1, "fwd5_pp": 5, "fwd10_pp": 10}

#: 预注册常量(与 spec.json 逐字对应,改这里 = 改预注册)。
VOL_SHRINK_MAX = 0.6            # G1:D+1 的标准量比(daily_basic.volume_ratio)上限
PULLBACK_BAND = (-5.0, 0.0)     # G1:D+1 相对 D 的收盘涨跌幅(%),左闭右开
LATE_SEAL_WINDOW = (140000, 150000)   # G2:末次封板时刻 HHMMSS,左闭右开
INST_SEAT_KEYWORD = "机构专用"
FWD_LOOKAHEAD = 12              # 装 pivot 时往后多装的交易日数(fwd_10 + 余量)
PP = 100.0                      # 小数 → pp
EVIDENCE_MODES = {"EOD_PROXY"}
COST_MODELS = {"none(门槛以 COST_PP=0.15pp 绝对毛计)"}
BEHAVIOR_ROOTS = (
    "autoresearch/research/w3_grids.py",
    "autoresearch/research/registration.py",
    "autoresearch/research/robustness.py",
    "autoresearch/common/stats.py",
    "autoresearch/common/forward_returns.py",
    "autoresearch/common/ruler.py",
    "autoresearch/contracts/research_experiment.py",
)

POPULATION_COL_BY_LABEL = {
    "gap_pp": "in_pop_gap",
    "fwd5_pp": "in_pop_fwd5",
    "fwd10_pp": "in_pop_fwd10",
}
PANEL_COLS = ("date", "code", "gap_pp", "fwd5_pp", "fwd10_pp", "rel_gap_pp",
              "buyable_c1", "in_pop_gap", "in_pop_fwd5", "in_pop_fwd10",
              "vol_ratio_d", "close_d")


def _z6(value) -> str:
    return str(value).split(".")[0].zfill(6)


def _read_day(table: str, day: str, lake_root: Path | None = None) -> pd.DataFrame | None:
    path = (Path(lake_root) if lake_root else ws.lake_root()) / table / f"{day}.parquet"
    if not path.exists():
        return None
    try:
        got = pd.read_parquet(path)
    except (OSError, ValueError):
        return None
    return got if len(got) else None


# ───────────────────────── 面板 ─────────────────────────

def _volume_ratio(day: str, lake_root: Path | None = None) -> pd.Series:
    """`daily_basic.volume_ratio`(A 股标准量比)。表缺 → 空 Series(由 coverage 说,不补 0)。"""
    got = _read_day("daily_basic", day, lake_root)
    if got is None or "volume_ratio" not in got:
        return pd.Series(dtype=float)
    out = got.copy()
    out["code"] = out["ts_code"].astype(str).str[:6]
    return pd.to_numeric(out.set_index("code")["volume_ratio"], errors="coerce")


def build_panel(days: list[str], *, window: list[str], lake_daily: Path | None = None,
                lake_root: Path | None = None) -> pd.DataFrame:
    """`days` 里每个数据日 D 一段 → 三把尺 + 可交易旗 + D 日量价。

    `rel_gap_pp` = 主尺 − 当日**全湖可交易**等权均值(与 `ruler.REL_MARKET` 同口径)。
    D+2 未落湖的日子照样出行,收益为 NaN(**状态不是故障**),由 `cell_stats` 的 `n_days` 说话。
    """
    piv = load_lake_pivots(window, lake_daily)
    if not piv:
        return pd.DataFrame(columns=list(PANEL_COLS))
    frames = []
    for day in days:
        if day not in window:
            continue
        fr = forward_returns(piv, window, day, fwd=10)
        if fr is None or fr.empty:
            continue
        gap = pd.to_numeric(fr[_ruler.MAIN_RULER], errors="coerce")
        gap = gap.where(gap.abs() <= _ruler.GAP_CLIP)          # 板制度下不可能 → 数据错,置 NaN
        in_pop_gap = _ruler.entry_tradable(
            fr, ruler_name="gap_c1_o2"
        ).fillna(False).astype(bool)
        in_pop_fwd5 = _ruler.entry_tradable(
            fr, ruler_name="fwd_5_oc"
        ).fillna(False).astype(bool)
        in_pop_fwd10 = _ruler.entry_tradable(
            fr, ruler_name="fwd_10_oc"
        ).fillna(False).astype(bool)
        base = gap[in_pop_gap.to_numpy()].mean()
        close = piv["close"][day] if day in piv["close"].columns else pd.Series(dtype=float)
        vr = _volume_ratio(day, lake_root)
        frames.append(pd.DataFrame({
            "date": day, "code": fr.index.astype(str),
            "gap_pp": (gap * PP).to_numpy(),
            "fwd5_pp": (pd.to_numeric(fr["fwd_5_oc"], errors="coerce") * PP).to_numpy(),
            "fwd10_pp": (pd.to_numeric(fr["fwd_10_oc"], errors="coerce") * PP).to_numpy(),
            "rel_gap_pp": ((gap - base) * PP).to_numpy() if base == base else np.nan,
            "buyable_c1": pd.array(fr[_ruler.ENTRY_FLAG].to_numpy(), dtype="boolean"),
            "in_pop_gap": in_pop_gap.to_numpy(),
            "in_pop_fwd5": in_pop_fwd5.to_numpy(),
            "in_pop_fwd10": in_pop_fwd10.to_numpy(),
            "vol_ratio_d": vr.reindex(fr.index).to_numpy() if len(vr) else np.nan,
            "close_d": close.reindex(fr.index).to_numpy(),
        }))
    if not frames:
        return pd.DataFrame(columns=list(PANEL_COLS))
    return pd.concat(frames, ignore_index=True)[list(PANEL_COLS)]


# ───────────────────────── 信号 ─────────────────────────

def first_board_days(day: str, *, lake_root: Path | None = None) -> set[str]:
    """D 日首板 = `limit=='U'` ∧ `limit_times==1`。表缺 → 空集(**空集 ≠ 查过了没有**,由 coverage 说)。"""
    got = _read_day("limit_list_d", day, lake_root)
    if got is None or "limit" not in got or "limit_times" not in got:
        return set()
    sel = got[(got["limit"].astype(str) == "U")
              & (pd.to_numeric(got["limit_times"], errors="coerce") == 1)]
    return {_z6(c) for c in sel["ts_code"]}


def late_seal_days(day: str, *, lake_root: Path | None = None) -> set[str]:
    """D 日涨停且**末次**封板时刻 ∈ [14:00, 15:00)。`last_time` 是 HHMMSS 字符串。"""
    got = _read_day("limit_list_d", day, lake_root)
    if got is None or "limit" not in got or "last_time" not in got:
        return set()
    t = pd.to_numeric(got["last_time"], errors="coerce")
    lo, hi = LATE_SEAL_WINDOW
    sel = got[(got["limit"].astype(str) == "U") & t.ge(lo) & t.lt(hi)]
    return {_z6(c) for c in sel["ts_code"]}


def inst_seat_days(day: str, *, lake_root: Path | None = None) -> set[str]:
    """D 日「机构专用」席位**合计**净买 > 0。逐席位求和后再判号,不是任一席位为正。"""
    got = _read_day("top_inst", day, lake_root)
    if got is None or "exalter" not in got or "net_buy" not in got:
        return set()
    inst = got[got["exalter"].astype(str).str.contains(INST_SEAT_KEYWORD, na=False)].copy()
    if inst.empty:
        return set()
    inst["net_buy"] = pd.to_numeric(inst["net_buy"], errors="coerce")
    agg = inst.groupby("ts_code")["net_buy"].sum()
    return {_z6(c) for c in agg[agg > 0].index}


def signal_coverage(days: list[str], *, lake_root: Path | None = None) -> dict:
    """每个信号源在窗口里有几天有表 —— 「空集」与「没查」得分得开。"""
    out = {}
    for table in ("limit_list_d", "top_inst", "daily_basic"):
        present = sum(_read_day(table, d, lake_root) is not None for d in days)
        out[table] = {"days_with_table": present, "days_requested": len(days)}
    return out


def g1_threshold_diagnostics(panel: pd.DataFrame, *, signal_days: list[str],
                             lake_root: Path | None = None) -> dict:
    """首板次日量比的分位数 + 逐级命中数 —— 让 G1 的样本量**可解释**。

    「这一格没有观测」与「这一格的阈值在这批数据上几乎不可达」是两回事,读数得让人分得出。
    这是**诊断**,不是判据:阈值是预注册的,看完分布不许回头改(要改就是新 experiment_id)。
    """
    if panel.empty:
        return {"n_first_board": 0, "note": "面板为空"}
    all_days = sorted(panel["date"].astype(str).unique())
    order = {d: i for i, d in enumerate(all_days)}
    by_day = dict(panel.groupby("date").__iter__())
    ratios, n_fb, n_shrink, n_band, n_both = [], 0, 0, 0, 0
    for day in [d for d in all_days if d in set(signal_days)]:
        nxt = all_days[order[day] + 1] if order[day] + 1 < len(all_days) else None
        if nxt is None:
            continue
        cur, nb = by_day[day].set_index("code"), by_day[nxt].set_index("code")
        for code in sorted(first_board_days(day, lake_root=lake_root) & set(cur.index) & set(nb.index)):
            n_fb += 1
            vr1 = nb.at[code, "vol_ratio_d"]
            c0, c1 = cur.at[code, "close_d"], nb.at[code, "close_d"]
            if not all(pd.notna(x) for x in (vr1, c0, c1)) or c0 <= 0:
                continue
            ratios.append(float(vr1))
            pct = (c1 / c0 - 1.0) * PP
            shrink = vr1 < VOL_SHRINK_MAX
            band = PULLBACK_BAND[0] <= pct < PULLBACK_BAND[1]
            n_shrink += shrink
            n_band += band
            n_both += shrink and band
    s = pd.Series(ratios, dtype=float)
    return {"n_first_board": n_fb, "n_with_volume_ratio": int(len(s)),
            "n_pass_shrink": int(n_shrink), "n_pass_pullback": int(n_band), "n_pass_both": int(n_both),
            "volume_ratio_quantiles": ({str(q): round(float(s.quantile(q)), 4)
                                        for q in (0.05, 0.25, 0.5, 0.75, 0.95)} if len(s) else None),
            "registered_threshold": VOL_SHRINK_MAX,
            "note": "分布是诊断,不是判据 —— 阈值预注册,看完不许回头改"}


# ───────────────────────── 格 ─────────────────────────

def build_cells(panel: pd.DataFrame, *, lake_root: Path | None = None,
                signal_days: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """三格的观测行。G2 额外按 `buyable_c1` 分两桶(可买 / 不可买),不可买桶只报不判。

    `signal_days` = 在哪些数据日 D 上找信号(缺省 = 面板里全部日)。**面板可以比它多带一天**:
    G1 的「D+1 缩量回调」要查下一日的量价,不多带那一天,窗口最后一天的 G1 会静默落空
    (不是「那天没信号」,是「没查」)。多带的那天不参与 G2/G3 的信号扫描。
    """
    if panel.empty:
        return {key: panel.head(0).copy() for key in (*GRID_ORDER, f"{G2}__unbuyable")}
    all_days = sorted(panel["date"].astype(str).unique())
    days = [d for d in all_days if signal_days is None or d in set(signal_days)]
    order = {d: i for i, d in enumerate(all_days)}
    by_day = dict(panel.groupby("date").__iter__())
    first_hits, late_hits, seat_hits = {}, {}, {}
    for day in days:
        first_hits[day] = first_board_days(day, lake_root=lake_root)
        late_hits[day] = late_seal_days(day, lake_root=lake_root)
        seat_hits[day] = inst_seat_days(day, lake_root=lake_root)

    rows = {key: [] for key in (*GRID_ORDER, f"{G2}__unbuyable")}
    for day in days:
        block = by_day[day]
        idx = order[day]
        nxt = all_days[idx + 1] if idx + 1 < len(all_days) else None
        # ── G1:首板 ∧ D+1 缩量回调 ──
        if nxt is not None and first_hits[day]:
            nb = by_day[nxt].set_index("code")
            cur = block.set_index("code")
            codes = sorted(first_hits[day] & set(cur.index) & set(nb.index))
            for code in codes:
                vr1 = nb.at[code, "vol_ratio_d"]
                c0, c1 = cur.at[code, "close_d"], nb.at[code, "close_d"]
                if not all(pd.notna(x) for x in (vr1, c0, c1)) or c0 <= 0:
                    continue
                pct = (c1 / c0 - 1.0) * PP
                if vr1 < VOL_SHRINK_MAX and PULLBACK_BAND[0] <= pct < PULLBACK_BAND[1]:
                    rows[G1].append(cur.loc[[code]].assign(code=code, date=day))
        # ── G2:晚封板,按可买性分桶 ──
        if late_hits[day]:
            hit = block[block["code"].isin(late_hits[day])]
            buyable = hit["buyable_c1"].fillna(False).astype(bool)
            rows[G2].append(hit[buyable])
            rows[f"{G2}__unbuyable"].append(hit[~buyable])
        # ── G3:机构席位 ──
        if seat_hits[day]:
            rows[G3].append(block[block["code"].isin(seat_hits[day])])

    out = {}
    for key, parts in rows.items():
        frame = pd.concat(parts, ignore_index=True) if parts else panel.head(0).copy()
        base = GRID_LABEL_COL.get(key.split("__")[0], "gap_pp")
        population_col = POPULATION_COL_BY_LABEL[base]
        pop = frame if key.startswith(G2) else frame[frame[population_col].astype(bool)]
        out[key] = pop[pop[base].notna()].reset_index(drop=True)
    return out


def validate_population_declarations(spec: dict) -> None:
    """Reject population exclusions the current deterministic panel cannot execute."""
    unsupported = ("剔 ST", "剔ST", "新股", "北交所")
    claims = [str(spec.get("population_rule") or "")]
    claims.extend(str(item.get("population") or "") for item in spec.get("hypotheses", ()))
    if any(token in claim for claim in claims for token in unsupported):
        raise ValueError(
            "UNSUPPORTED_POPULATION_RULE: ST/新股/北交所排除未由当前 W3 面板实现"
        )


def _registered_judge(st: dict, *, min_scan_days: int) -> str:
    if int(st.get("n_days") or 0) < min_scan_days:
        return "样本不足"
    ci_low, ci_high = st.get("ci_low_pp"), st.get("ci_high_pp")
    if (ci_low is not None and ci_low >= core.CI_LOWER_PP
            and st.get("yearly_sign_ok") and st.get("halves_sign_ok")):
        return "正证据"
    if ci_high is not None and ci_high < 0:
        return "显著负"
    return "未证"


def judge_cells(cells: dict[str, pd.DataFrame], *, seed: int,
                min_scan_days: int = core.MIN_DAYS_EVENT) -> dict:
    """每格:主标签的四态判读 + 敏感尺并列 + 块长敏感性。不可买桶强制 `只报不判`。"""
    stats = {}
    for key, frame in cells.items():
        grid = key.split("__")[0]
        label = GRID_LABEL_COL[grid]
        primary = core.cell_stats(frame, value_col=label, sample_kind="event", seed=seed)
        verdict = ("只报不判(X_ORACLE:封板买不进)" if key.endswith("__unbuyable")
                   else _registered_judge(primary, min_scan_days=min_scan_days))
        observed = {col: core.cell_stats(frame, value_col=col, sample_kind="event", seed=seed)
                    for col in GRID_OBSERVE_COLS[grid] if col in frame.columns}
        daily = (frame.groupby("date")[label].mean().sort_index().to_numpy(dtype=float)
                 if len(frame) else np.array([], dtype=float))
        stats[key] = {"grid": grid, "label": GRID_LABEL[grid], "label_col": label,
                      "is_sensitivity_label": label != "gap_pp",
                      "verdict": verdict, "primary": primary,
                      "maturity": {"status": ("MATURE" if primary["n_days"] >= min_scan_days
                                                else "IMMATURE"),
                                   "min_scan_days": min_scan_days,
                                   "observed_scan_days": primary["n_days"]},
                      "observe_only": {k: {"mean_pp": v["mean_pp"], "n_days": v["n_days"]}
                                       for k, v in observed.items()},
                      "block_sensitivity": (block_sensitivity(daily, seed=seed)
                                            if len(daily) >= 2 else None),
                      # Inference input only; stripped before any public artifact is written.
                      "_daily_primary_pp": daily.tolist()}
    return stats


def family_correction(stats: dict, *, alpha: float = 0.05,
                      seed: int = DEFAULT_SEED,
                      n_boot: int = DEFAULT_BOOT) -> list[dict]:
    """Use real block-bootstrap p-values, then BY-adjust only tested hypotheses."""
    keys = [k for k in GRID_ORDER if k in stats]
    rows: list[dict] = []
    tested: list[tuple[int, float]] = []
    for key in keys:
        item = stats[key]
        point = item.get("primary", {}).get("mean_pp")
        direction = ("unknown" if point is None else
                     ("positive" if point > 0 else "negative" if point < 0 else "zero"))
        row = {
            "grid": key,
            "direction": direction,
            "status": "NOT_TESTED",
            "p_raw": None,
            "q_by": None,
            "rejected": None,
            "method": "BY",
            "block": LABEL_BLOCK[item["label_col"]],
            "seed": seed,
            "n_boot": n_boot,
            "note": "样本未过成熟门,不进入检验族",
        }
        maturity = item.get("maturity", {})
        mature = (maturity.get("status") == "MATURE" if maturity
                  else item.get("verdict") != "样本不足")
        if mature:
            result = block_mean_test(
                item.get("_daily_primary_pp", ()), block=row["block"],
                seed=seed, n_boot=n_boot,
            )
            row["test_status"] = result.status
            if result.pvalue is not None:
                row.update(status="TESTED", p_raw=result.pvalue,
                           note="日等权序列的双侧 moving-block bootstrap 均值检验")
                tested.append((len(rows), result.pvalue))
            else:
                row["note"] = f"块检验不可计算:{result.status}"
        rows.append(row)

    if tested:
        adjusted = family_adjustment(
            [p for _, p in tested], dependence="arbitrary", alpha=alpha
        )
        for (row_index, _), adjusted_row in zip(tested, adjusted, strict=True):
            rows[row_index]["q_by"] = adjusted_row["q"]
            rows[row_index]["rejected"] = adjusted_row["rejected"]
    return rows


def _public_stats(stats: dict) -> dict:
    return {
        key: {name: value for name, value in item.items() if not name.startswith("_")}
        for key, item in stats.items()
    }


# ───────────────────────── 读数 ─────────────────────────

def _readout(spec: dict, stats: dict, coverage: dict, family: list[dict], window: dict) -> str:
    lines = [f"# W3 三格普查读数 — {spec['experiment_id']}", "",
             f"> 预注册:`{spec['experiment_family']}`,方案在同目录 `spec.json`(跑前冻结)。"
             f"窗口 {window['since']} → {window['until']},{window['n_days']} 个数据日。"
             f"门槛 = 日等权 95% CI 下界 ≥ {core.CI_LOWER_PP}pp 绝对毛(= 往返成本 {core.COST_PP}pp)"
             f"∧ 逐年同号 ∧ 两半同号 ∧ 过样本门。**过门 ≠ 上线**:只是进入 08-28 裁决树。", "",
             "| 格 | 判据尺 | 事件 | 日 | 均值(pp) | 95% CI | 逐年同号 | 两半同号 | 判读 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for key in (*GRID_ORDER, f"{G2}__unbuyable"):
        if key not in stats:
            continue
        s = stats[key]
        st = s["primary"]
        ci = ("—" if st["ci_low_pp"] is None
              else f"[{st['ci_low_pp']:+.3f}, {st['ci_high_pp']:+.3f}]")
        mean = "—" if st["mean_pp"] is None else f"{st['mean_pp']:+.3f}"
        tag = "(敏感尺)" if s["is_sensitivity_label"] else ""
        name = s["label"] + ("·不可买桶" if key.endswith("__unbuyable") else
                             ("·可买桶" if key == G2 else ""))
        lines.append(f"| {name} | `{s['label_col']}`{tag} | {st['n_events']} | {st['n_days']} | "
                     f"{mean} | {ci} | {'✓' if st['yearly_sign_ok'] else '✗'} | "
                     f"{'✓' if st['halves_sign_ok'] else '✗'} | {s['verdict']} |")
    lines += ["", "## 并列观察(不判读)", ""]
    for key in (*GRID_ORDER, f"{G2}__unbuyable"):
        if key in stats and stats[key]["observe_only"]:
            obs = ", ".join(f"`{k}` {v['mean_pp']:+.3f}pp/{v['n_days']}日" if v["mean_pp"] is not None
                            else f"`{k}` —" for k, v in stats[key]["observe_only"].items())
            lines.append(f"- {stats[key]['label']}:{obs}")
    lines += ["", "## 多重比较(三格一族,BY / 任意依赖)", "", "```json",
              json.dumps(family, ensure_ascii=False, indent=1), "```", "",
              "## 信号源覆盖", "", "```json", json.dumps(coverage, ensure_ascii=False, indent=1),
              "```", "", "## 诚实边界", "",
              "- **G1 用 D+1 全日量作 14:45 量比的代理**(差最后 15 分钟),方向已知:收盘前放量会"
              "让本该落选的票留在样本里 —— 这是**乐观**偏差,正读数要按此打折。",
              "- **G2 不可买桶是 X_ORACLE**:封板买不进,只报不判(08-28「收益随买得到的可能性单调递减」)。",
              "- **G3 判据在敏感尺**(`fwd_5_oc`),主尺读数并列只观察,**不构成换尺依据**(Q-F)。",
              "- 「未证」不是「已证不存在」:区间跨零只说明这批样本没量出来。",
              "- 过门的格进 08-28 Q9 裁决树,BUY owner 仍是 14:45 工具与 `relative_buy`(I04)。", ""]
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _registered_selection(spec: dict, *, lake_daily: Path | None,
                          since: str | None = None, until: str | None = None) -> dict:
    all_days = lake_trade_days(lake_daily)
    start, end = registered_test_range(spec)
    days = [day for day in all_days if start <= day < end]
    if not days:
        raise ValueError("注册测试窗口内湖里没有任何交易日")
    norm_since = str(since).replace("-", "") if since is not None else None
    norm_until = str(until).replace("-", "") if until is not None else None
    if norm_since is not None and norm_since != days[0]:
        raise ValueError("CLI since conflicts with registered test interval")
    if norm_until is not None and norm_until != days[-1]:
        raise ValueError("CLI until conflicts with registered test interval")
    first, last = all_days.index(days[0]), all_days.index(days[-1])
    window = all_days[first:min(len(all_days), last + FWD_LOOKAHEAD)]
    tail = all_days[last + 1] if last + 1 < len(all_days) else None
    panel_days = days + ([tail] if tail else [])
    return {"all_days": all_days, "days": days, "window": window,
            "panel_days": panel_days, "tail": tail}


def registered_input_manifest(spec: dict, *, lake_daily: Path | None = None,
                              lake_root: Path | None = None) -> dict:
    """Build the exact W3 file manifest for the spec's registered test interval."""
    selection = _registered_selection(spec, lake_daily=lake_daily)
    daily_root = Path(lake_daily) if lake_daily is not None else ws.lake_root() / "daily"
    root = Path(lake_root) if lake_root is not None else ws.lake_root()
    paths = [daily_root / f"{day}.parquet" for day in selection["window"]]
    paths.extend(root / "daily_basic" / f"{day}.parquet" for day in selection["panel_days"])
    for table in ("limit_list_d", "top_inst"):
        paths.extend(root / table / f"{day}.parquet" for day in selection["days"])
    date_slice = {
        **registered_date_slice(spec),
        "selected_days": selection["days"],
        "panel_days": selection["panel_days"],
        "forward_window": selection["window"],
    }
    return file_manifest(paths, date_slice)


def run(*, spec_path: Path, since: str | None = None, until: str | None = None,
        lake_daily: Path | None = None, lake_root: Path | None = None,
        parent: Path | None = None) -> Path:
    spec = validate_spec(json.loads(Path(spec_path).read_text(encoding="utf-8")))
    validate_population_declarations(spec)
    verify_engine(spec)
    verify_modes(spec, evidence_modes=EVIDENCE_MODES, cost_models=COST_MODELS)
    maturity_days = parse_maturity_policy(spec["maturity_policy"])
    code_identity = verify_code_provenance(spec, BEHAVIOR_ROOTS)
    selection = _registered_selection(
        spec, lake_daily=lake_daily, since=since, until=until
    )
    days, window = selection["days"], selection["window"]
    input_identity = registered_input_manifest(
        spec, lake_daily=lake_daily, lake_root=lake_root
    )
    input_digest = verify_manifest(spec, input_identity)
    base = Path(parent) if parent is not None else ws.reports_root() / "research" / "w3_grids"
    output = eio.create_experiment_dir(base, spec["experiment_id"])
    eio.freeze_spec(output, spec)

    # 面板多建一天:G1 要查 D+1 的量价。多带的那天不参与信号扫描(见 build_cells)。
    tail = selection["tail"]
    panel = build_panel(days + ([tail] if tail else []), window=window, lake_daily=lake_daily,
                        lake_root=lake_root)
    cells = build_cells(panel, lake_root=lake_root, signal_days=days)
    seed = int(spec["bootstrap"]["seed"])
    stats = judge_cells(cells, seed=seed, min_scan_days=maturity_days)
    family = family_correction(
        stats, seed=seed, n_boot=int(spec["bootstrap"]["n_boot"])
    )
    stats = _public_stats(stats)
    coverage = signal_coverage(days, lake_root=lake_root)
    coverage["g1_threshold"] = g1_threshold_diagnostics(panel, signal_days=days, lake_root=lake_root)
    win = {"since": days[0], "until": days[-1], "n_days": len(days), "n_panel_rows": int(len(panel))}

    frames = [f.assign(cell=key) for key, f in cells.items() if len(f)]
    (pd.concat(frames, ignore_index=True) if frames else panel.head(0).assign(cell=pd.Series(dtype=str))) \
        .to_csv(output / "cells.csv", index=False)
    (output / "statistics.json").write_text(
        json.dumps({"window": win, "stats": stats, "family_correction": family},
                   ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    (output / "signal_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (output / "input_manifest.json").write_text(
        json.dumps({"declared_sha256": spec["input_manifest_hash"],
                    "observed_sha256": input_digest, "manifest": input_identity},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (output / "readout.md").write_text(_readout(spec, stats, coverage, family, win), encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "experiment_id": spec["experiment_id"], "engine": spec["engine"],
        "as_of": datetime.now(timezone.utc).isoformat(), "spec_sha256": eio.spec_digest(output),
        "window": win, "seed": seed, "ruler": _ruler.MAIN_RULER,
        "sensitivity_rulers": spec["sensitivity_rulers"],
        "statistics_version": core.STATISTICS_VERSION,
        "ci_lower_pp": core.CI_LOWER_PP, "cost_pp": core.COST_PP,
        "calendar_source": "lake/daily filenames",
        "registration": {"input_manifest_sha256": input_digest,
                         "code": code_identity,
                         "test_interval": registered_date_slice(spec),
                         "maturity_min_scan_days": maturity_days,
                         "split_application": "TEST_ONLY_NO_TRAIN_ROWS"},
        "outputs": {name: _sha256(output / name) for name in
                    ("cells.csv", "statistics.json", "signal_coverage.json",
                     "input_manifest.json", "readout.md")},
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W3 三格普查(零 LLM、只读、不回注)")
    ap.add_argument("--spec", default="docs/research/2026-09-07-w3-three-grids-family.spec.json")
    ap.add_argument("--since", default=None, help="起始数据日 YYYYMMDD")
    ap.add_argument("--until", default=None, help="终止数据日 YYYYMMDD")
    ap.add_argument("--lake-daily", default=None)
    ap.add_argument("--lake-root", default=None)
    ap.add_argument("--out-parent", default=None)
    a = ap.parse_args(argv)
    try:
        out = run(spec_path=Path(a.spec), since=a.since, until=a.until,
                  lake_daily=Path(a.lake_daily) if a.lake_daily else None,
                  lake_root=Path(a.lake_root) if a.lake_root else None,
                  parent=Path(a.out_parent) if a.out_parent else None)
    except FileExistsError as exc:
        print(f"[w3_grids] 落点已存在,拒绝覆盖(换 experiment_id):{exc}", file=sys.stderr)
        return 2
    print(f"[w3_grids] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
