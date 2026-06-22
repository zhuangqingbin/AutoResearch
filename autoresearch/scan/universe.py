#!/usr/bin/env python3
"""scan-market · L0–L2 — A股全市场确定性漏斗(零 LLM)。SCAN 层编排。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A(scan 层编排)。
        前身 scripts/screen_market.py(docs/specs/2026-06-20-scan-market-design.md)。

把 ~5,400 只 A股用纯 pandas + akshare/tushare bulk 端点砍到 top 板块内 ~100 只排序
survivors(喂给 L3a 的 LLM 轻量分诊)。**零 token。** 真正的深挖(全量
analyze-ticker)只发生在 L3b 的 ~30 只 finalists。

分层:
  L0 universe  ── 全 A股快照(spot)+ 业绩(yjbb)+ 资金流(fundflow)富化 + 硬门
  L1 四透镜    ── 动量 / 成长 / 价值 / 反转,各自"门→分位打分→top N"
  L2 板块聚合  ── survivors 映射行业,板块按 广度+跨透镜+资金+动量 排名 → top 板块

层界:取数走 DATA 层(`autoresearch.data.akshare_universe.fetch_universe` em 路径 /
`autoresearch.data.tushare_source.fetch_universe_tushare` 默认),打分走 `autoresearch.common.scoring`,
L2 学习重排走 `autoresearch.research.factor_lab.predict_scores`。本模块只做编排 + 板块聚合 + 输出。

`run()` 是 golden-parity 的对拍基准(tests/scan/test_parity.py 锁死新 Pipeline ≡ 本 run)。

用法:
  uv run --no-sync python -m autoresearch.scan.universe 2026-06-20
  uv run --no-sync python -m autoresearch.scan.universe --selftest   # 离线验证打分逻辑
  选项:--cap-floor 30 (市值地板,亿) --exclude-bj (排除北交所) --recall-n 1000 --l2-n 200
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# 纯打分原语(autoresearch.common.scoring),scan/factor_lab/handler 三处同口径复用。
from autoresearch.common.scoring import (
    _GROUPS,
    _PRIOR_WEIGHTS,
    _load_weights,
    _pct,
    _wsum,
    composite_score,
    latest_reported_quarter,
    lens_growth,
    lens_momentum,
    lens_reversal,
    lens_value,
    prev_quarter,
)

# 取数(DATA 层):em 路径的 fetch_universe + 硬门/原始数(_GATE_INFO)。
from autoresearch.data.akshare_universe import _GATE_INFO, fetch_universe

# 归一化 helpers(_num/_winsor/_pct/_pct_within/_wsum)与报告期 helpers
# (latest_reported_quarter/prev_quarter)在 autoresearch.common.scoring,顶部 import 复用。


# ───────────────────────── L1 四透镜 ─────────────────────────

LENS_NAMES = ["momentum", "growth", "value", "reversal"]

# 四透镜(lens_momentum/growth/value/reversal)在 autoresearch.common.scoring,顶部 import 复用。


def run_lenses(uni: pd.DataFrame, top_per_lens: int = 50) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """跑四透镜,返回(去重 survivors, 各透镜 topN 榜)。survivors 带 lens 命中标签 + 复合分。"""
    fns = {"momentum": lens_momentum, "growth": lens_growth,
           "value": lens_value, "reversal": lens_reversal}
    tops: dict[str, pd.DataFrame] = {}
    hit_cols = []
    base = uni.copy()
    for lens, fn in fns.items():
        scored = fn(uni)
        passed = scored[scored[f"{lens}_gate"]].nlargest(top_per_lens, f"{lens}_score")
        tops[lens] = passed
        # 合并该透镜分/命中回 base
        base = base.merge(passed[["code", f"{lens}_score", f"{lens}_signals"]], on="code", how="left")
        base[f"hit_{lens}"] = base["code"].isin(passed["code"])
        hit_cols.append(f"hit_{lens}")
    base["n_lens"] = base[hit_cols].sum(axis=1)
    survivors = base[base["n_lens"] > 0].copy()
    # 复合确信度 = 命中透镜数(主) + 各透镜分均值(次)
    score_cols = [f"{lens}_score" for lens in fns]
    survivors["lens_mean"] = survivors[score_cols].mean(axis=1, skipna=True).round(1)
    survivors["conviction"] = (survivors["n_lens"] * 100 + survivors["lens_mean"].fillna(0)).round(1)
    survivors = survivors.sort_values("conviction", ascending=False).reset_index(drop=True)
    print(f"[L1] survivors(命中≥1透镜,去重): {len(survivors)} "
          f"(各透镜 top {top_per_lens})", file=sys.stderr)
    return survivors, tops


# ───────────────────────── L2 板块聚合 ─────────────────────────


def aggregate_sectors(survivors: pd.DataFrame, uni: pd.DataFrame, top_sectors: int = 5) -> pd.DataFrame:
    """板块按 广度 + 跨透镜 + 资金 + 动量 排名。广度+跨透镜权重最高(确信度信号)。"""
    hit_cols = [f"hit_{lens}" for lens in LENS_NAMES]
    sector_size = uni.groupby("industry")["code"].count().rename("sector_size")
    rows = []
    for ind, grp in survivors.groupby("industry"):
        lenses_present = sum(int(grp[h].any()) for h in hit_cols)
        rows.append({
            "industry": ind,
            "n_survivors": len(grp),
            "n_lenses": lenses_present,                       # 跨透镜:1–4
            "median_inflow_yi": grp["main_inflow_yi"].median(skipna=True),
            "median_pct_60d": grp["pct_60d"].median(skipna=True),
            "median_conviction": grp["conviction"].median(),
        })
    sec = pd.DataFrame(rows).merge(sector_size, on="industry", how="left")
    sec["breadth"] = (sec["n_survivors"] / sec["sector_size"]).round(3)
    sec["sector_score"] = _wsum({
        "breadth": (_pct(sec["breadth"]), 30),
        "cross_lens": (sec["n_lenses"] / 4.0, 30),
        "inflow": (_pct(sec["median_inflow_yi"]), 20),
        "momentum": (_pct(sec["median_pct_60d"]), 20),
    })
    sec = sec[sec["industry"] != "未分类"].sort_values("sector_score", ascending=False).reset_index(drop=True)
    sec["is_top"] = sec.index < top_sectors
    print(f"[L2] 板块: {len(sec)} 个有 survivors,取 top {top_sectors}", file=sys.stderr)
    return sec


# ───────────────────────── L1 召回:轻门 + 行业条件化复合分 ─────────────────────────

# 9 因子组 / 先验权重 / _load_weights / _blend / _factor_groups / composite_score
# 在 autoresearch.common.scoring(scan/factor_lab/handler 三处同口径),顶部 import 复用。


def _recall_gate_a(df: pd.DataFrame, min_amount_yi: float = 0.0) -> pd.Series:
    """L1 召回轻门:只去真正不可交易/无核心数据的尾部(召回优先,尽量不误杀)。"""
    keep = df["amount_yi"].fillna(0) > min_amount_yi       # 有流动性/非停牌
    keep &= df["close"].notna()                            # 有价
    keep &= df["pct_60d"].notna() | df["pct_ytd"].notna()  # 有动量价(打分核心)
    return keep


def aggregate_sectors_overview(recall: pd.DataFrame, uni: pd.DataFrame) -> pd.DataFrame:
    """板块概览(L2 不再聚合截断;仅供 L5 描述):各行业召回数 / 中位复合分 / 中位动量 / 中位主力净占比。"""
    if "industry" not in recall.columns or not len(recall):
        return pd.DataFrame(columns=["industry", "n_recall", "median_composite", "is_top"])
    g = recall.groupby("industry")
    sec = pd.DataFrame({"industry": g.size().index, "n_recall": g.size().to_numpy(),
                        "median_composite": g["composite"].median().to_numpy()})
    for col, name in [("pct_60d", "median_pct_60d"), ("main_net_ratio", "median_main_net_ratio")]:
        if col in recall.columns:
            sec = sec.merge(g[col].median().rename(name).reset_index(), on="industry", how="left")
    sec = sec.sort_values("n_recall", ascending=False).reset_index(drop=True)
    sec["is_top"] = sec.index < 8
    return sec


def _harvest_vol_series(codes, analysis_date: str, lookback: int = 20) -> pd.DataFrame:
    """拉近 ~lookback 交易日 daily(high/low/close/amount)→ vol_series 算多日量价因子 per code。

    供 L1 召回的 volprice 组(快照层本来无序列)。tushare bulk by date(~lookback 次)→ pivot → 序列指标。
    无权限/失败 → 返回空帧(volprice 列缺失 → 组 NaN 重归一,recall 不破)。
    """
    try:
        from datetime import datetime, timedelta

        import autoresearch.common.vol_series as vol_series
        from autoresearch.data.tushare_source import (
            _code6,
            _pro,
            _trade_days,
            _ts_call,
            resolve_momentum_dates,
        )
        pro = _pro()
        last = resolve_momentum_dates(pro, analysis_date)[0]
        start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=lookback * 2 + 15)).strftime("%Y%m%d")
        days = _trade_days(pro, start, last)[-lookback:]
        if len(days) < 10:
            return pd.DataFrame(columns=["code"])
        want = {str(c).zfill(6) for c in codes}
        recs = []
        for d in days:
            df = _ts_call(lambda d=d: pro.daily(trade_date=d, fields="ts_code,high,low,close,amount"))
            if df is None or not len(df):
                continue
            df = df.assign(code=_code6(df["ts_code"]), date=d)
            recs.append(df[df["code"].isin(want)][["code", "date", "high", "low", "close", "amount"]])
        if not recs:
            return pd.DataFrame(columns=["code"])
        long = pd.concat(recs, ignore_index=True)
        piv = {f: long.pivot_table(index="code", columns="date", values=f)
               for f in ("high", "low", "close", "amount")}
        win = sorted(piv["close"].columns)
        H, L, C, A = (piv[f][win] for f in ("high", "low", "close", "amount"))
        out = pd.DataFrame({"code": list(C.index)})
        out["cmf_20"] = vol_series.cmf(H, L, C, A, win).to_numpy()
        out["obv_mom_20"] = vol_series.obv_momentum(C, A, win).to_numpy()
        out["price_vs_vwap_20"] = vol_series.price_vs_vwap(H, L, C, A, win).to_numpy()
        out["breakout_vol_20"] = vol_series.breakout_on_volume(C, A, win).to_numpy()
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 多日量价序列取数失败 → volprice 组置 NaN: {e}", file=sys.stderr)
        return pd.DataFrame(columns=["code"])


def recall_select(scored: pd.DataFrame, analysis_date: str, recall_n: int,
                  recall_mode: str = "multi", recall_channels=None):
    """L1 召回:multi=多路 quota_union(provenance)| composite=单复合分降序 top-n。

    返回 (recall_df, per_channel_long|None)。composite 模式逐值复现今天(parity 锚点);
    multi 模式 8 路 channel(全复用 scoring)各取 top-Kᶜ → quota union(floor 保底多样性)。
    单一来源:scan.universe.run(staging)与 L1Recall stage(trace)都调它。
    """
    if recall_mode == "composite":
        recall = scored.sort_values("composite", ascending=False).head(recall_n).reset_index(drop=True)
        return recall, None
    from autoresearch.scan.recall import CHANNEL_DEFAULTS, build, quota_union, registered_channels
    names = recall_channels or registered_channels()
    frames = {n: build(n)(scored, analysis_date, CHANNEL_DEFAULTS[n].quota) for n in names}
    recall, per_channel = quota_union(frames, CHANNEL_DEFAULTS, recall_n, scored)
    return recall, per_channel


# ───────────────────────── 编排 + 输出 ─────────────────────────


def run(analysis_date: str, cap_floor_yi: float = 30.0, include_bj: bool = True,
        recall_n: int = 1000, l2_n: int = 200, outdir: Path | None = None,
        source: str = "tushare", recall_mode: str = "multi", recall_channels=None,
        l2_model: str = "l2_fwd5") -> dict:
    """L0 选集 + L1 召回 + L2 粗排(GBDT 学习重排 → top l2_n)。全确定性,零 LLM。

    recall_mode:multi=多路策略召回(默认,带 provenance + L1_channels.csv)| composite=单复合分(对拍/回退)。
    """
    if source == "tushare":
        from autoresearch.data.tushare_source import (  # 默认源(东财 push2 常被封)
            _RAW_COUNT,
            fetch_universe_tushare,
        )
        uni = fetch_universe_tushare(analysis_date, cap_floor_yi=cap_floor_yi, include_bj=include_bj)
        n_raw = _RAW_COUNT.get("n", len(uni))
    else:
        uni = fetch_universe(analysis_date, cap_floor_yi=cap_floor_yi, include_bj=include_bj)
        n_raw = _GATE_INFO.get("n_raw", len(uni))   # em 路径同模块,可靠
    n_l0 = len(uni)

    # L1 召回:Step A 轻门 → Step B 复合分 → top recall_n
    uni = uni[_recall_gate_a(uni)].reset_index(drop=True)
    uni["code"] = uni["code"].astype(str).str.zfill(6)
    vps = _harvest_vol_series(uni["code"], analysis_date)          # 多日量价序列(CMF/OBV/...)→ volprice 组
    if len(vps):
        uni = uni.merge(vps, on="code", how="left")
    weights = _load_weights()
    scored = composite_score(uni, weights)
    recall, per_channel = recall_select(scored, analysis_date, recall_n, recall_mode, recall_channels)
    print(f"[L1 召回] L0 {n_l0} → 轻门 {len(uni)} → {recall_mode} top {len(recall)}", file=sys.stderr)
    sectors = aggregate_sectors_overview(recall, uni)

    outdir = outdir or Path("context/scan") / analysis_date
    outdir.mkdir(parents=True, exist_ok=True)
    keep = (["code", "name", "industry", "composite"] + [f"score_{g}" for g in _GROUPS]
            + ["mktcap_yi", "close", "amount_yi", "vol_ratio", "turnover", "cmf_20", "obv_mom_20",
               "pct_60d", "pct_ytd",
               "main_inflow_yi", "main_net_ratio",
               "retail_net_yi", "winner_rate", "chip_concentration", "price_to_cost", "hk_ratio",
               "rsi6", "rsi12", "pe", "pb", "dv_ratio", "np_yoy", "rev_yoy", "roe",
               "ma_bull", "above_ma60"])
    keep = keep + [c for c in ("recall_channels", "n_channels", "best_rank") if c in recall.columns]
    recall[[c for c in keep if c in recall.columns]].to_csv(outdir / "L1_recall_top1000.csv", index=False)
    if per_channel is not None and len(per_channel):           # multi:各路召回名单留底(provenance/复盘)
        per_channel.to_csv(outdir / "L1_channels.csv", index=False)
    # 全量打分(所有过门股,按 composite 降序 + recalled 标记)→ trace/ 留全阶段数据,不截断
    full = scored.sort_values("composite", ascending=False).reset_index(drop=True)
    full.insert(0, "rank", range(1, len(full) + 1))
    full.insert(1, "recalled", full["rank"] <= recall_n)
    full[["rank", "recalled"] + [c for c in keep if c in full.columns]].to_csv(
        outdir / "L1_scored_full.csv", index=False)

    # ── L2 粗排:champion 重排 recall → top l2_n(确定性,替旧 L2-AI keep/cut)──
    # 优先 zoo champion(swing,core 在召回帧可 predict)→ 回落 factor_lab GBDT → 回落 composite top。
    # 任一缺失/失败 → 下一级回落(自保:绝不比线性差)。L2Rank stage 共用 champion_scores → 口径一致。
    from autoresearch.scan.l2_model import champion_scores
    scores, l2_engine = champion_scores(recall, l2_model)
    if scores is None:
        import autoresearch.research.factor_lab as factor_lab
        g = factor_lab.predict_scores(recall)
        if g is not None:
            scores, l2_engine = g, "gbdt"
        else:
            l2_engine = "composite-linear(回落)"
    if scores is not None:
        l2 = recall.assign(gbdt_score=scores.to_numpy()).sort_values(
            "gbdt_score", ascending=False, kind="stable").head(l2_n).reset_index(drop=True)
    else:
        l2 = recall.head(l2_n).reset_index(drop=True)
    l2.insert(0, "l2_rank", range(1, len(l2) + 1))
    l2_cols = ["l2_rank", "gbdt_score", *keep]
    l2[[c for c in l2_cols if c in l2.columns]].to_csv(outdir / "L2_gbdt_top200.csv", index=False)
    print(f"[L2 粗排] recall {len(recall)} → {l2_engine} top {len(l2)}", file=sys.stderr)

    sectors.to_csv(outdir / "sectors.csv", index=False)
    (outdir / "meta.json").write_text(json.dumps({
        "analysis_date": analysis_date, "universe_raw": n_raw, "universe": n_l0, "after_gate_a": len(uni),
        "recall_n": len(recall), "l2_n": len(l2), "l2_engine": l2_engine,
        "cap_floor_yi": cap_floor_yi, "include_bj": include_bj, "source": source,
        "weights_source": weights.get("meta", {}).get("source", "weights.json"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] L1 召回 → {outdir}/L1_recall_top1000.csv ({len(recall)})", file=sys.stderr)
    return {"universe": n_l0, "after_gate_a": len(uni), "recall_n": len(recall),
            "l2_n": len(l2), "l2_engine": l2_engine, "sectors": len(sectors), "outdir": str(outdir)}


# ───────────────────────── 离线自测(无网络) ─────────────────────────


def _selftest() -> int:
    """用合成 canonical DataFrame 验证打分逻辑(分位/门/加权/板块聚合)——不碰 akshare/网络。"""
    rng = np.random.default_rng(42)
    n = 200
    inds = rng.choice(["半导体", "光模块", "白酒", "煤炭", "医药", "未分类"], n)
    df = pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)],
        "industry": inds,
        "close": rng.uniform(5, 300, n),
        "mktcap_yi": rng.uniform(20, 4000, n),
        "amount_yi": rng.uniform(0.5, 200, n),
        "pct_1d": rng.uniform(-10, 10, n),
        "pct_60d": rng.uniform(-50, 300, n),
        "pct_ytd": rng.uniform(-60, 400, n),
        "vol_ratio": rng.uniform(0.3, 5, n),
        "turnover": rng.uniform(0.1, 30, n),
        "pe": rng.uniform(-50, 200, n),
        "pb": rng.uniform(0.5, 30, n),
        "rev": rng.uniform(1e8, 5e10, n),
        "np_": rng.uniform(-1e9, 5e9, n),
        "rev_yoy": rng.uniform(-40, 120, n),
        "np_yoy": rng.uniform(-100, 300, n),
        "np_qoq": rng.uniform(-50, 80, n),
        "roe": rng.uniform(-10, 35, n),
        "gross_margin": rng.uniform(5, 70, n),
        "cfo_ps": rng.uniform(-1, 3, n),
        "np_yoy_prev": rng.uniform(-100, 200, n),
        "rev_yoy_prev": rng.uniform(-40, 100, n),
        "main_inflow_yi": rng.uniform(-5, 8, n),
    })
    # tushare 增强列(覆盖 value/momentum/reversal 的增强分支)
    df["dv_ratio"] = rng.uniform(0, 6, n)
    df["ma_bull"] = rng.integers(0, 2, n).astype(float)
    df["above_ma60"] = rng.integers(0, 2, n).astype(float)
    df["rsi6"] = rng.uniform(10, 95, n)
    df["winner_rate"] = rng.uniform(0, 100, n)
    df["cost_50pct"] = rng.uniform(5, 300, n)
    # v2 富因子(资金结构/筹码集中度/北向/RSI12)
    df["rsi12"] = rng.uniform(10, 95, n)
    df["main_net_ratio"] = rng.uniform(-0.1, 0.1, n)
    df["retail_net_yi"] = rng.uniform(-2, 2, n)
    df["chip_concentration"] = rng.uniform(0.1, 2.0, n)
    df["price_to_cost"] = rng.uniform(0.7, 1.5, n)
    df["hk_ratio"] = rng.uniform(0, 30, n)
    df["is_st"] = False

    fails = []
    # 1) 各透镜产出分 + 门,分在 [0,100],门是 bool
    for lens, fn in [("momentum", lens_momentum), ("growth", lens_growth),
                     ("value", lens_value), ("reversal", lens_reversal)]:
        g = fn(df)
        sc, gate = g[f"{lens}_score"], g[f"{lens}_gate"]
        if not ((sc.dropna() >= 0).all() and (sc.dropna() <= 100).all()):
            fails.append(f"{lens}: score 越界 [{sc.min()},{sc.max()}]")
        if gate.dtype != bool:
            fails.append(f"{lens}: gate 非 bool")
        if gate.sum() == 0:
            fails.append(f"{lens}: 松门竟无人通过(可疑)")

    # 2) 编排:survivors 有确信度排序 + 板块聚合
    survivors = run_lenses(df, top_per_lens=30)[0]
    if survivors.empty:
        fails.append("survivors 为空")
    if not survivors["conviction"].is_monotonic_decreasing:
        fails.append("survivors 未按 conviction 降序")
    if not (survivors["n_lens"].between(1, 4)).all():
        fails.append("n_lens 越界")
    sectors = aggregate_sectors(survivors, df, top_sectors=3)
    if "未分类" in set(sectors["industry"]):
        fails.append("板块榜未剔除 未分类")
    if not sectors["sector_score"].is_monotonic_decreasing:
        fails.append("板块未按 sector_score 降序")

    # 4) v2 召回:轻门 + 行业条件化复合分
    ga = _recall_gate_a(df)
    if ga.dtype != bool or ga.sum() == 0:
        fails.append("recall gate_a 异常(非 bool 或全剔)")
    comp = composite_score(df, _PRIOR_WEIGHTS)
    cs = comp["composite"]
    if not ((cs.dropna() >= 0).all() and (cs.dropna() <= 100).all()):
        fails.append(f"composite 越界 [{cs.min()},{cs.max()}]")
    for gname in ("momentum", "fund_main", "chip", "tech", "value"):
        if f"score_{gname}" not in comp.columns:
            fails.append(f"缺子分列 score_{gname}")

    # 3) 报告期 helper
    cases = {"2026-06-20": "20260331", "2026-09-15": "20260630",
             "2026-11-01": "20260930", "2026-02-01": "20250930"}
    for d, exp in cases.items():
        got = latest_reported_quarter(d)
        if got != exp:
            fails.append(f"latest_reported_quarter({d})={got} 期望 {exp}")
    if prev_quarter("20260331") != "20251231":
        fails.append("prev_quarter(Q1) 错")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print(f"SELFTEST ✅  四透镜 + v2召回(轻门/复合分)/编排/板块/报告期 全过 "
          f"(survivors={len(survivors)}, sectors={len(sectors)})")
    return 0


# ───────────────────────── CLI ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scan-market L0 选集 + L1 召回(确定性,零 LLM)")
    ap.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    ap.add_argument("--cap-floor", type=float, default=30.0, help="市值地板(亿),默认 30")
    ap.add_argument("--exclude-bj", action="store_true", help="排除北交所(默认纳入)")
    ap.add_argument("--recall-n", type=int, default=1000, help="召回数(复合分 top N),默认 1000")
    ap.add_argument("--l2-n", type=int, default=200, help="L2 粗排数(GBDT 重排 top N),默认 200")
    ap.add_argument("--source", choices=["em", "tushare"], default="tushare",
                    help="universe 取数源:tushare=默认(push2 常被封);em=东财 push2")
    ap.add_argument("--recall-mode", choices=["multi", "composite"], default="multi",
                    help="L1 召回:multi=多路策略召回(默认)| composite=单复合分(对拍/回退)")
    ap.add_argument("--recall-channels", default=None, help="启用 channel 子集(逗号分隔;缺省=全 8 路)")
    ap.add_argument("--selftest", action="store_true", help="离线验证打分逻辑(无网络)")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    analysis_date = args.date or date.today().isoformat()
    res = run(analysis_date, cap_floor_yi=args.cap_floor, include_bj=not args.exclude_bj,
              recall_n=args.recall_n, l2_n=args.l2_n, source=args.source,
              recall_mode=args.recall_mode,
              recall_channels=(args.recall_channels.split(",") if args.recall_channels else None))
    print(f"\nL0 universe={res['universe']} → 轻门 {res['after_gate_a']} → 召回 top{res['recall_n']} "
          f"→ L2 {res['l2_engine']} top{res['l2_n']} (板块概览 {res['sectors']} 个)"
          f"\n→ {res['outdir']}/L2_gbdt_top200.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
