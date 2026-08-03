#!/usr/bin/env python3
"""L3 边际价值 —— 排序到底值多少(确定性,零 LLM,反事实估计而非因果实验)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §4.4

**为什么值得算**:L3 真选无正 alpha(07-16 去📌污染后真选 −3.8%;finalists 11 日
−0.39pp/2日 t≈−1.2),但 L4 深核推翻 L3 高确信两次全对、trend lane 高确信被翻案 21% ——
L3 的价值**可能在证据组装与防呆,不在排序**。值得计量,不值得拍脑袋撤。

**为什么以前算不了**:pass1 是 union/floor/round-robin 的**选择集**,不是一条确定性排名。
只有 `_l3_pass1_cut.csv` 的话根本无法回答「同样选 K 只、但不用 L3 的判断会怎样」——
没有 baseline 就没有反事实。本模块吃的是本波新增的 `_l3_pass1_kept.csv`
(带 `selection_reason`),这才让「按 lane/sector 匹配的 K 只」可构造。

## 两层(§4.4)

- **tier-1 历史配对估计**:在**同一 pass1 choice set** 内,以**相同 K** 做 lane/sector
  分层匹配的确定性 baseline(层内按当日确定性分取头部),再用多次分层随机抽样形成参考
  分布。actual 与 baseline 按**扫描日**配对做 date-cluster bootstrap。
  主尺 = `excess_2`(相对当日可交易成熟票中位)与**可买 winner capture**;
  `jaccard` 只量暴露差异,**不量价值**(单独一列,不进判据)。
  pinned / conviction_guard 这类强制补入行**从两侧同时剔除** —— 它们不是 L3 排序的产物,
  留着就是拿保送票的收益去证明排序有用(07-16 判例:L3 真选 −3.8% vs 保送 −12.3%)。
- **tier-2 live shadow**:tier-1 有足够 divergence 但区间仍不确定时,对 divergent picks
  补 L4 卡并等成熟 T+2。**不是**"tier-1 显著了才触发" —— 那样只会去验证已经知道的事;
  tier-2 存在的理由正是补功效。

## 判定纪律

预注册等价 margin + power gate,判决走 `experiment_template.conclude` 的五态。
**只有整个区间落入等价带才可说「排序无增量」**;未显著但区间宽 = `UNKNOWN`。
本模块产出一律称**关联/反事实估计**,不称因果实验 —— 它比较的是历史上同一天的两个
子集,而不是随机分配的两臂。

  uv run --no-sync python -m autoresearch.learning.l3_marginal
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import stats as st
from autoresearch.learning import experiment_template as et

SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("context/scan")
OUT_JSON = Path("reports/learning/l3_marginal.json")
OUT_MD = Path("reports/learning/l3_marginal.md")

# 强制补入的理由 —— 不是 L3 排序的产物,两侧同时剔除(§4.4「pinned/forced rows 分层或剔除」)
FORCED_REASONS = ("pinned", "conviction_guard")
N_REFERENCE_DRAWS = 200          # 分层随机参考分布的抽样次数(固定种子 → 可复现)
EQUIVALENCE_MARGIN = 0.005       # 预注册:±0.5pp 的日均 excess_2 差视为无实质差异
DIVERGENCE_MIN = 0.30            # tier-2 触发:actual 与 baseline 的不重合度下限

_TEMPLATE = et.ExperimentTemplate(
    experiment_id="l3_ranking_marginal_value",
    h0="L3 排序选出的 K 只与同 choice set 内 lane/sector 匹配的确定性 baseline 无差异",
    h1="L3 排序选出的 K 只 excess_2 更高",
    data_cutoff="按扫描日滚动;只用 T+2 已成熟的日",
    paired_unit="scan_day", clustering="date_cluster",
    min_units=st.MATURITY_MIN_SCAN_DAYS, target_power=0.8,
    primary_metric="excess_2_delta_vs_matched_baseline",
    direction=et.HIGHER_IS_BETTER,
    equivalence_margin=EQUIVALENCE_MARGIN, no_harm=False,
    multiple_testing="single_hypothesis", n_hypotheses=1,
    stopping_rule=f"固定 ≥{st.MATURITY_MIN_SCAN_DAYS} 个成熟扫描日;不可提前停",
    rollback="本模块只产账本,无生产消费者 —— 无需回滚",
    motivation="L4 深核推翻 L3 高确信两次全对,trend lane 高确信被翻案 21% —— "
               "排序价值需要计量而非拍脑袋",
)


def template() -> et.ExperimentTemplate:
    """预注册模板(margin / min_units / 停止规则都在这里定死)。"""
    return _TEMPLATE


# ───────────────────────── 单日:构造 choice set 与两侧 ─────────────────────────


def _codes(frame: pd.DataFrame | None, col: str = "code") -> set[str]:
    if frame is None or not len(frame) or col not in frame.columns:
        return set()
    return set(frame[col].astype(str).str.split(".").str[0].str.zfill(6))


def _read_csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001 — 读不动就是没有,不臆测
        return None


def day_frame(scan_dir: Path | str) -> pd.DataFrame | None:
    """单日 choice set 帧:pass1 kept × 当日已实现 → `[code, reason, lane, industry,
    score, excess_2, buyable_winner, actual]`。缺任一必需产物 → `None`(presence-gated)。

    `excess_2` 的市场基准 = **当日可交易且成熟票的 fwd_2_oc 中位**(与
    `rejection_attribution` / gate_attribution v3 / abstention v2 同源)。换基准就换了口径,
    跨模块比较立刻失真,所以这里不另造一个。
    """
    day = Path(scan_dir)
    kept = _read_csv(day / "_l3_pass1_kept.csv")
    attr = _read_csv(day / "retro" / "attribution.csv")
    if kept is None or attr is None or not len(kept) or not len(attr):
        return None
    if "selection_reason" not in kept.columns:
        return None                       # 旧日期(本波之前)没有 provenance → 无法构造反事实

    finalists = _read_csv(day / "finalists.csv")
    if finalists is None:
        return None
    actual = _codes(finalists)
    # 📌 保送在 finalists 里带 `pinned_note` —— 它们不是 L3 排序的产物,从 actual 侧剔除。
    # `fillna("")` 不能省:read_csv 把空单元读成 NaN,而 `str(nan)` 是 "nan" ≠ ""
    # —— 漏掉它会把**每一只** finalist 都当成保送票,actual 直接清空(单测已锁)。
    if "pinned_note" in finalists.columns:
        note = finalists["pinned_note"].fillna("").astype(str).str.strip()
        actual -= _codes(finalists[note != ""])

    a = attr.copy()
    a["code"] = a["code"].astype(str).str.zfill(6)
    a["fwd_2_oc"] = pd.to_numeric(a.get("fwd_2_oc"), errors="coerce")
    buyable = a["buyable"].fillna(True).astype(bool) if "buyable" in a.columns \
        else pd.Series(True, index=a.index)
    tradable = a["tradable"].fillna(True).astype(bool) if "tradable" in a.columns \
        else pd.Series(True, index=a.index)
    eligible = buyable & tradable & a["fwd_2_oc"].notna()
    if not eligible.any():
        return None
    market = float(a.loc[eligible, "fwd_2_oc"].median())
    winner = (a["winner"].fillna(False).astype(bool) if "winner" in a.columns
              else pd.Series(False, index=a.index))
    a["_excess_2"] = a["fwd_2_oc"] - market
    a["_buyable_winner"] = winner & buyable

    k = kept.copy()
    k["code"] = k["code"].astype(str).str.zfill(6)
    score_col = next((c for c in ("gbdt_score", "composite") if c in k.columns), None)
    k["_score"] = (pd.to_numeric(k[score_col], errors="coerce").fillna(-1e18)
                   if score_col else 0.0)
    merged = k.merge(
        a[["code", "_excess_2", "_buyable_winner", "fwd_2_oc"]], on="code", how="left")
    merged = merged[merged["_excess_2"].notna()].copy()
    if not len(merged):
        return None
    out = pd.DataFrame({
        "date": day.name,
        "code": merged["code"],
        "reason": merged["selection_reason"].astype(str),
        "lane": merged.get("selection_detail", pd.Series("", index=merged.index)).astype(str),
        "industry": merged.get("industry", pd.Series("?", index=merged.index)).astype(str),
        "score": merged["_score"].astype(float),
        "excess_2": merged["_excess_2"].astype(float),
        "buyable_winner": merged["_buyable_winner"].fillna(False).astype(bool),
        "actual": merged["code"].isin(actual),
        "forced": merged["selection_reason"].isin(FORCED_REASONS),
        "market_baseline": "median_tradable_mature",
    })
    return out.reset_index(drop=True)


def _strata(frame: pd.DataFrame, by: str) -> pd.Series:
    return frame["lane"].where(frame["lane"].astype(bool), "—") if by == "lane" \
        else frame["industry"]


def matched_baseline(frame: pd.DataFrame, *, by: str = "lane") -> set[str]:
    """确定性 baseline:与 actual **同 K、同层分布**,层内按当日确定性分取头部。

    「同层分布」是关键 —— 若 L3 恰好偏爱某个 lane,而那个 lane 当天整体涨得好,不做分层
    匹配的 baseline 会把 lane 暴露的收益记到「排序」头上。这正是 §4.4 说的
    「Jaccard 只量暴露差异,不量价值」的另一面:暴露必须先配平。
    """
    pool = frame[~frame["forced"]]
    if not len(pool):
        return set()
    actual = pool[pool["actual"]]
    counts = _strata(actual, by).value_counts().to_dict()
    strata = _strata(pool, by)
    picked: set[str] = set()
    for stratum, need in sorted(counts.items()):
        sub = pool[strata == stratum].sort_values(
            ["score", "code"], ascending=[False, True], kind="stable")
        picked |= set(sub["code"].head(int(need)))
    # 层内不够(actual 里有、pool 里被 forced 剔掉了)→ 按分补满到同一个 K
    need_total = int(len(actual)) - len(picked)
    if need_total > 0:
        rest = pool[~pool["code"].isin(picked)].sort_values(
            ["score", "code"], ascending=[False, True], kind="stable")
        picked |= set(rest["code"].head(need_total))
    return picked


def reference_draws(frame: pd.DataFrame, *, by: str = "lane",
                    draws: int = N_REFERENCE_DRAWS,
                    seed: int = st.DEFAULT_SEED) -> list[float]:
    """分层**随机**参考分布:同 K、同层分布,但层内随机取 —— 确定性 baseline 只是一条线,
    随机分布才说得清「L3 的成绩在同暴露的随机选法里排第几分位」。"""
    pool = frame[~frame["forced"]]
    actual = pool[pool["actual"]]
    if not len(pool) or not len(actual):
        return []
    counts = _strata(actual, by).value_counts().to_dict()
    strata = _strata(pool, by)
    rng = np.random.default_rng(seed)
    out: list[float] = []
    for _ in range(draws):
        picked: list[str] = []
        for stratum, need in sorted(counts.items()):
            codes = pool.loc[strata == stratum, "code"].to_numpy()
            take = min(int(need), len(codes))
            if take:
                picked.extend(rng.choice(codes, size=take, replace=False).tolist())
        if picked:
            out.append(float(pool.loc[pool["code"].isin(picked), "excess_2"].mean()))
    return out


def day_estimate(frame: pd.DataFrame, *, by: str = "lane") -> dict | None:
    """单日 actual vs matched baseline 的配对读数。actual 为空(0 finalist / 全保送)→ None。"""
    pool = frame[~frame["forced"]]
    actual_codes = set(pool.loc[pool["actual"], "code"])
    if not actual_codes:
        return None
    base_codes = matched_baseline(frame, by=by)
    if not base_codes:
        return None
    a = pool[pool["code"].isin(actual_codes)]
    b = pool[pool["code"].isin(base_codes)]
    draws = reference_draws(frame, by=by)
    actual_mean = float(a["excess_2"].mean())
    union = actual_codes | base_codes
    winners_in_pool = int(pool["buyable_winner"].sum())
    return {
        "date": str(frame["date"].iloc[0]),
        "k": len(actual_codes),
        "choice_set_n": int(len(pool)),
        "forced_excluded": int(frame["forced"].sum()),
        "actual_excess_2": actual_mean,
        "baseline_excess_2": float(b["excess_2"].mean()),
        "actual_winner_capture": (float(a["buyable_winner"].sum()) / winners_in_pool
                                  if winners_in_pool else None),
        "baseline_winner_capture": (float(b["buyable_winner"].sum()) / winners_in_pool
                                    if winners_in_pool else None),
        # Jaccard 只量暴露差异,不进判据 —— 单列出来是为了让「两边其实选的是同一批」这种
        # 情形一眼可见(那时候 delta≈0 是恒等式,不是发现)。
        "jaccard": round(len(actual_codes & base_codes) / max(1, len(union)), 4),
        "divergent_codes": sorted(actual_codes - base_codes),
        "reference_mean": float(np.mean(draws)) if draws else None,
        "reference_pctile": (float((np.asarray(draws) < actual_mean).mean())
                             if draws else None),
    }


# ───────────────────────── tier-1 汇总 + tier-2 触发 ─────────────────────────


def collect(scan_root: Path | str | None = None, *, by: str = "lane") -> pd.DataFrame:
    root = Path(scan_root or DEFAULT_ROOT)
    rows: list[dict] = []
    if not root.exists():
        return pd.DataFrame()
    for day in sorted(p for p in root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        frame = day_frame(day)
        if frame is None:
            continue
        est = day_estimate(frame, by=by)
        if est is not None:
            rows.append(est)
    return pd.DataFrame(rows)


def tier1(scan_root: Path | str | None = None, *, by: str = "lane") -> dict:
    """tier-1 反事实估计 —— 逐日配对 → date-cluster bootstrap → 五态裁决。"""
    daily = collect(scan_root, by=by)
    t = template()
    if not len(daily):
        interval = st.Interval(None, None, None, 0, 0, "empty")
        verdict = et.conclude(t, interval, observed_units=0)
        return {"schema_version": SCHEMA_VERSION, "tier": 1, "stratified_by": by,
                "n_days": 0, "daily": [], "verdict": verdict,
                "estimand": _ESTIMAND, "tier2": _no_tier2("no_days")}

    interval = st.paired_delta_interval(
        daily, "actual_excess_2", "baseline_excess_2", date_col="date")
    verdict = et.conclude(t, interval, observed_units=int(len(daily)))
    capture = st.paired_delta_interval(
        daily.dropna(subset=["actual_winner_capture", "baseline_winner_capture"]),
        "actual_winner_capture", "baseline_winner_capture", date_col="date") \
        if daily["actual_winner_capture"].notna().any() else st.Interval(
            None, None, None, 0, 0, "no_winner_days")
    return {
        "schema_version": SCHEMA_VERSION,
        "tier": 1,
        "stratified_by": by,
        "n_days": int(len(daily)),
        "daily": daily.to_dict("records"),
        "excess_2_delta": interval.as_dict(),
        "winner_capture_delta": capture.as_dict(),
        "mean_jaccard": round(float(daily["jaccard"].mean()), 4),
        "verdict": verdict,
        "estimand": _ESTIMAND,
        "tier2": tier2_plan(daily, verdict),
    }


_ESTIMAND = ("同一扫描日、同一 pass1 choice set 内,actual(L3 真选,已剔 pinned/强制补入)"
             "与同 K 同层分布的确定性 baseline 的 excess_2 差。**关联/反事实估计**,"
             "非因果实验 —— 两侧不是随机分配的两臂。")


def _no_tier2(reason: str) -> dict:
    return {"trigger": False, "reason": reason, "codes": [], "n_days": 0}


def tier2_plan(daily: pd.DataFrame, verdict: dict) -> dict:
    """tier-2 触发计划 —— **补功效**,不是等 tier-1 显著了才去验证。

    条件:① tier-1 裁决仍是 `UNKNOWN/IMMATURE`(即功效不足或区间跨 margin);
    ② 两侧确有分歧(divergence ≥ `DIVERGENCE_MIN`)—— 两边选的是同一批时补卡毫无信息。
    """
    if verdict["verdict"] not in ("UNKNOWN", "IMMATURE"):
        return _no_tier2(f"tier1_verdict={verdict['verdict']}")
    if not len(daily):
        return _no_tier2("no_days")
    divergence = 1.0 - float(daily["jaccard"].mean())
    if divergence < DIVERGENCE_MIN:
        return _no_tier2(f"divergence={divergence:.3f}<{DIVERGENCE_MIN}")
    picks = sorted({(str(r["date"]), c) for r in daily.to_dict("records")
                    for c in (r.get("divergent_codes") or [])})
    return {
        "trigger": True,
        "reason": (f"tier1={verdict['verdict']}(功效不足)且 divergence="
                   f"{divergence:.3f} ≥ {DIVERGENCE_MIN}"),
        "codes": [{"date": d, "code": c} for d, c in picks],
        "n_days": int(len(daily)),
        "action": "对 divergent picks 补 L4 卡,等成熟 T+2 后回填;补功效而非验证已知结论",
    }


# ───────────────────────── 渲染 / CLI ─────────────────────────


def render(result: dict) -> str:
    lines = [
        "# L3 边际价值(排序值多少)—— 反事实估计,非因果实验",
        "",
        f"> {result['estimand']}",
        "",
        f"- 分层维度 `{result['stratified_by']}` · 成熟扫描日 **{result['n_days']}**"
        + (f" · 平均 Jaccard {result.get('mean_jaccard')}" if result.get("mean_jaccard") is not None else ""),
        "",
        "## 裁决", "",
    ]
    lines.append(et.render(result["verdict"]))
    lines += ["> ⚠️ Jaccard 只量**暴露差异**,不量价值 —— 它不进判据。", ""]

    capture = result.get("winner_capture_delta") or {}
    if capture.get("point") is not None:
        lines += ["## 可买 winner capture 差(次尺)", "",
                  f"- 点估计 {capture['point']:+.4f} · 区间 "
                  + ("—" if capture["lo"] is None
                     else f"[{capture['lo']:+.4f}, {capture['hi']:+.4f}]")
                  + f" · 聚簇 {capture['n_clusters']}", ""]

    t2 = result.get("tier2") or {}
    lines += ["## tier-2(live shadow)", "",
              f"- 触发:**{'是' if t2.get('trigger') else '否'}** —— {t2.get('reason')}"]
    if t2.get("trigger"):
        lines += [f"- 待补 L4 卡:{len(t2['codes'])} 只 · {t2.get('action')}"]
    lines += ["", "## 逐日", "",
              "| 日期 | K | choice set | 强制剔除 | actual ex2 | baseline ex2 | "
              "Jaccard | 随机参考分位 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in result.get("daily", []):
        pct = row.get("reference_pctile")
        lines.append(
            f"| {row['date']} | {row['k']} | {row['choice_set_n']} | "
            f"{row['forced_excluded']} | {row['actual_excess_2']:+.4f} | "
            f"{row['baseline_excess_2']:+.4f} | {row['jaccard']} | "
            + ("—" if pct is None else f"{pct:.2f}") + " |")
    if not result.get("daily"):
        lines.append("| — | — | — | — | — | — | — | — |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="L3 边际价值(§4.4 tier-1/tier-2)")
    ap.add_argument("--scan-root", default=None)
    ap.add_argument("--by", default="lane", choices=["lane", "industry"])
    ap.add_argument("--json-out", default=str(OUT_JSON))
    ap.add_argument("--md-out", default=str(OUT_MD))
    a = ap.parse_args(argv)

    result = tier1(a.scan_root, by=a.by)
    for path, text in ((Path(a.json_out),
                        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"),
                       (Path(a.md_out), render(result))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(f"[l3_marginal] {result['n_days']} 日 · 裁决 {result['verdict']['verdict']} "
          f"· tier2 触发={result['tier2']['trigger']} → {a.json_out} / {a.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
