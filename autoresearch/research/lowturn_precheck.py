#!/usr/bin/env python3
"""低位转强 · Gate 0 前置证伪(零 LLM)+ 活体双尺观察报告。

design: docs/specs/2026-08-21-lowturn-recall-l3-picture-display-design.md §7 / §9。

回测:读 factor_lab 成型日面板(`context/factor_lab`,≥60 日)→ 每日算四组掩码
(lowturn 旗 / healthy / 旧 reversal 门 / 修后 reversal_confirm 门)→ 组内主尺 `gap_c1_o2`
与参考尺 `fwd_5_oc`/`fwd_10_oc`(**只观察**)相对全市场截面中位的超额 → 跨日 t → 停机规则。
停机常量**先写后看**(本文件落盘早于任何读数)。

为什么先跑这一关:「光有前置低位 = 接刀」在旧尺 `fwd_2_oc` 下已被 107 个成型日证伪
(`dist_low_60` decile spread t=−2.06),但「低位 + 企稳 + 起爆」的**组合**从没测过,新尺
`gap_c1_o2` 下也没复跑过 —— 是没跑,不是被否。开工前证伪能省下整条 LLM 路(07-25 event
路教训:首读边际 −1.01pp)。

活体(`--live`):生产 scan 日的 `_l3_judged.json`(lane=lowturn)× `retro/attribution.csv`,
同一套 daily_stats/aggregate,只读不写账本。

用法:
  uv run --no-sync python -m autoresearch.research.lowturn_precheck [--cap-floor 30] [--out PATH]
  uv run --no-sync python -m autoresearch.research.lowturn_precheck --live [--out PATH]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler as _ruler
from autoresearch.common import workspace as ws

RULERS = ("gap_c1_o2", "fwd_5_oc", "fwd_10_oc")
GROUPS = ("lowturn", "healthy", "reversal_old", "reversal_confirm")
STOP_EXCESS_PP = -0.5      # 相对超额均值 ≤ −0.5pp …
STOP_T = -2.0              # … 且 t ≤ −2.0 …
STOP_MIN_DAYS = 40         # … 且有旗亮票的成型日 ≥40 → STOP_P3
SPARSE_MED = 3             # 每日旗亮中位 <3 只 → SPARSE(定义过严,预登记放宽一档后重跑一次)
MIN_CROSS_SECTION = 50
FOOTNOTE = ("_参考尺 `fwd_5_oc`/`fwd_10_oc` **只观察**;决策尺仍为 gap_c1_o2(2026-07-10 / 08-05 裁定),"
            "本表不构成换尺依据。相对超额 = 组内均值 − 当日可交易全集截面中位"
            "(与 stage_eval.channel_edge 同口径)。_")


def daily_stats(frame: pd.DataFrame, mask: pd.Series, ruler: str) -> dict | None:
    """一个成型日 × 一组 × 一把尺 → 读数;截面 <MIN_CROSS_SECTION 或组内空 → None(不算)。"""
    ok = _ruler.entry_tradable(frame, ruler_name=ruler)
    fwd = pd.to_numeric(frame[ruler], errors="coerce")
    valid = ok & fwd.notna()
    base = fwd[valid]
    grp = fwd[valid & mask.reindex(frame.index).fillna(False).astype(bool)]
    if len(base) < MIN_CROSS_SECTION or len(grp) == 0:
        return None
    mkt_med = float(base.median())
    return {"n": int(len(grp)), "mean": float(grp.mean()), "median": float(grp.median()),
            "hit": float((grp > 0).mean()), "excess": float(grp.mean() - mkt_med),
            "market_median": mkt_med}


def aggregate(days: list[dict]) -> dict:
    """跨日聚合:超额均值(pp)+ 配对 t(逐日超额的 one-sample t)。空 → NaN 行。"""
    if not days:
        return {"n_days": 0, "n_med_per_day": 0.0, "excess_mean_pp": float("nan"),
                "t": float("nan"), "hit_mean": float("nan"), "mean_pp": float("nan")}
    ex = np.array([d["excess"] for d in days], dtype=float)
    sd = ex.std(ddof=1) if len(ex) > 1 else 0.0
    t = float(ex.mean() / (sd / np.sqrt(len(ex)))) if sd > 0 else float("nan")
    return {"n_days": int(len(ex)), "n_med_per_day": float(np.median([d["n"] for d in days])),
            "excess_mean_pp": float(ex.mean() * 100), "t": t,
            "hit_mean": float(np.mean([d["hit"] for d in days])),
            "mean_pp": float(np.mean([d["mean"] for d in days]) * 100)}


def group_masks(frame: pd.DataFrame, cfg: dict | None = None) -> dict[str, pd.Series]:
    """四组掩码(全部走生产同一份谓词/门,不在本文件重写判据)。缺列 → 该组全 False。"""
    from autoresearch.common import scoring, turnup
    false = pd.Series(False, index=frame.index)
    lt = turnup.lowturn_mask(frame, cfg)
    masks = {"lowturn": lt.reindex(frame.index).fillna(False).astype(bool) if len(lt) else false}
    h = scoring.healthy_riser_mask(frame)
    masks["healthy"] = (h if h is not None else false).fillna(False).astype(bool)
    try:
        masks["reversal_old"] = scoring.lens_reversal(frame)["reversal_gate"].fillna(False).astype(bool)
    except (KeyError, ValueError):
        masks["reversal_old"] = false
    try:
        masks["reversal_confirm"] = (scoring.lens_reversal_confirm(frame)["reversal_confirm_gate"]
                                     .fillna(False).astype(bool))
    except (KeyError, ValueError):
        masks["reversal_confirm"] = false
    return masks


def enrich_frames(frames: list[pd.DataFrame], piv: dict, P: list[str]) -> list[pd.DataFrame]:
    """研究帧原只带三因子 → 并入 turnup 十列(含 dist_high_60/above_ma20 等旗所需列)。"""
    from autoresearch.common import turnup
    out = []
    sub = {k: piv[k] for k in ("high", "low", "close", "amount") if k in piv}
    for fr in frames:
        D = str(fr["date"].iloc[0])
        if D not in P:
            continue
        tp = turnup.panel_factors(sub, P[:P.index(D) + 1])
        fr2 = fr.drop(columns=[c for c in turnup.PANEL_COLS if c in fr.columns])
        out.append(fr2.merge(tp, left_on="code", right_index=True, how="left"))
    return out


def run_precheck(cap_floor: float = 30.0, cfg: dict | None = None):
    """→ (主表 四组×三尺, 停机裁决, lowturn×主尺 分 regime 表[只读,不进停机规则])。"""
    from autoresearch.common.regime import classify_regime
    from autoresearch.research import factor_lab as fl
    plan = pd.read_pickle(fl.OUT / "plan.pkl")
    P = plan["P"]
    piv = fl.load_price_pivots(P)
    frames = enrich_frames(fl._all_frames(cap_floor), piv, P)
    per_day: dict[tuple[str, str], list[dict]] = {(g, r): [] for g in GROUPS for r in RULERS}
    per_regime: dict[str, list[dict]] = {}
    for fr in frames:
        masks = group_masks(fr, cfg)
        for g in GROUPS:
            for r in RULERS:
                if r not in fr.columns:
                    continue
                st = daily_stats(fr, masks[g], r)
                if st:
                    per_day[(g, r)].append(st)
        try:
            regime = classify_regime(fr).label          # 研究帧带 pct_60d/above_ma60,同生产判据
        except Exception:  # noqa: BLE001 — 分桶只读,算不出记 NA
            regime = "NA"
        st_lt = daily_stats(fr, masks["lowturn"], "gap_c1_o2") if "gap_c1_o2" in fr.columns else None
        if st_lt:
            per_regime.setdefault(regime, []).append(st_lt)
    table = pd.DataFrame([{"group": g, "ruler": r, **aggregate(days)}
                          for (g, r), days in per_day.items()])
    regime_table = pd.DataFrame([{"regime": k, **aggregate(v)} for k, v in sorted(per_regime.items())])
    return table, stop_rule(table), regime_table


def stop_rule(table: pd.DataFrame) -> dict:
    """停机规则(**先写后看**;本函数与常量随代码落盘,早于任何读数)。"""
    if table is None or table.empty or "group" not in table.columns:
        return {"verdict": "NO_DATA", "why": "无可用成型日/无旗亮票"}
    r = table[(table["group"] == "lowturn") & (table["ruler"] == "gap_c1_o2")]
    if r.empty:
        return {"verdict": "NO_DATA", "why": "lowturn×gap_c1_o2 无行"}
    r = r.iloc[0]
    base = {"n_days": int(r["n_days"]), "excess_mean_pp": float(r["excess_mean_pp"]),
            "t": float(r["t"]), "n_med_per_day": float(r["n_med_per_day"])}
    if (r["n_days"] >= STOP_MIN_DAYS and r["excess_mean_pp"] <= STOP_EXCESS_PP
            and pd.notna(r["t"]) and r["t"] <= STOP_T):
        return {"verdict": "STOP_P3",
                "why": f"相对超额 {r['excess_mean_pp']:+.2f}pp,t={r['t']:.2f},"
                       f"n_days={int(r['n_days'])} ≥{STOP_MIN_DAYS} —— 1~2 日尺下为显著负 edge,"
                       "P3(L3 席位)不上线", **base}
    if r["n_med_per_day"] < SPARSE_MED:
        return {"verdict": "SPARSE",
                "why": f"每日旗亮中位 {r['n_med_per_day']:.0f} <{SPARSE_MED}:定义过严,按预登记"
                       "放宽一档(min_vol_ratio_20 1.2→1.0 / max_dist_high_60 −15→−10)重跑一次", **base}
    return {"verdict": "PROCEED", "why": "未触发停机(不显著 ≠ 有 alpha;进入 ≥10 扫描日活体裁决)",
            **base}


def render(table: pd.DataFrame, verdict: dict, regime_table: pd.DataFrame | None = None) -> str:
    lines = ["# 低位转强 · Gate 0 读数", "",
             f"**裁决:{verdict.get('verdict')}** —— {verdict.get('why', '')}", "",
             "| 组 | 尺 | n_days | 每日 n 中位 | 组内均值 pp | 相对超额 pp | t | 胜率 |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in table.itertuples(index=False):
        lines.append(f"| {row.group} | `{row.ruler}` | {row.n_days} | {row.n_med_per_day:.0f} | "
                     f"{row.mean_pp:+.2f} | {row.excess_mean_pp:+.2f} | {row.t:.2f} | {row.hit_mean:.0%} |")
    if regime_table is not None and len(regime_table):
        lines += ["", "## lowturn × gap_c1_o2 分 regime(只读;risk_off 样本薄,不据此调参)", "",
                  "| regime | n_days | 每日 n 中位 | 相对超额 pp | t | 胜率 |",
                  "|---|---:|---:|---:|---:|---:|"]
        for row in regime_table.itertuples(index=False):
            lines.append(f"| {row.regime} | {row.n_days} | {row.n_med_per_day:.0f} | "
                         f"{row.excess_mean_pp:+.2f} | {row.t:.2f} | {row.hit_mean:.0%} |")
    lines += ["", FOOTNOTE, "",
              f"_停机规则(先写后看):相对超额 ≤{STOP_EXCESS_PP}pp ∧ t ≤{STOP_T} ∧ n_days "
              f"≥{STOP_MIN_DAYS} → STOP_P3;每日旗亮中位 <{SPARSE_MED} → SPARSE;其余 PROCEED。_"]
    return "\n".join(lines) + "\n"


def run_live(scan_root: Path | str | None = None) -> tuple[pd.DataFrame, str]:
    """生产日 lane=lowturn finalist 的双尺读数(只读)。无成熟日 → 空表 + 说明。"""
    root = Path(scan_root or ws.scan_root())
    per_day: dict[str, list[dict]] = {r: [] for r in RULERS}
    n_days_seen = 0
    if root.is_dir():
        for day in sorted(p for p in root.iterdir() if p.is_dir()):
            jp, ap = day / "_l3_judged.json", day / "retro" / "attribution.csv"
            if not (jp.exists() and ap.exists()):
                continue
            try:
                judged = json.loads(jp.read_text(encoding="utf-8"))
                attr = pd.read_csv(ap, dtype={"code": str})
            except Exception:  # noqa: BLE001 — 坏日跳过,不编
                continue
            codes = {str(e.get("code", "")).zfill(6) for e in judged
                     if isinstance(e, dict) and e.get("lane") == "lowturn" and e.get("finalist")}
            if not codes:
                continue
            n_days_seen += 1
            attr["code"] = attr["code"].astype(str).str.zfill(6)
            mask = attr["code"].isin(codes)
            for r in RULERS:
                if r in attr.columns:
                    st = daily_stats(attr, mask, r)
                    if st:
                        per_day[r].append(st)
    table = pd.DataFrame([{"group": "lowturn(live finalist)", "ruler": r, **aggregate(days)}
                          for r, days in per_day.items()])
    note = f"_活体:{n_days_seen} 个有 lowturn finalist 的扫描日;成熟日按尺计入 n_days。_"
    return table, note


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cap-floor", type=float, default=30.0)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", type=str, default=None)
    ap.add_argument("--cfg", type=str, default=None,
                    help="JSON 字符串,覆盖 LOWTURN_DEFAULTS 阈值(预登记放宽一档用)")
    a = ap.parse_args(argv)
    cfg = json.loads(a.cfg) if a.cfg else None
    if a.live:
        table, note = run_live()
        md = render(table, {"verdict": "LIVE", "why": note})
        out = Path(a.out) if a.out else ws.reports_root() / "research" / "lowturn_live.md"
    else:
        table, verdict, regime_table = run_precheck(a.cap_floor, cfg)
        md = render(table, verdict, regime_table)
        out = Path(a.out) if a.out else ws.reports_root() / "research" / "lowturn_precheck.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"[done] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
