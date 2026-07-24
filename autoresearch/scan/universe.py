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
import contextlib
import dataclasses
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
    _pct,
    _wsum,
    composite_score,
    latest_reported_quarter,
    lens_growth,
    lens_momentum,
    lens_reversal,
    lens_value,
    pick_weights,
    prev_quarter,
)

# 帧构建(L0 取数 + 轻门 + 多日量价)已抽到 scan.frame(Phase 0,design:
# 2026-07-03-research-skills-altitude-refactor §5.1):run / L1Recall stage / 盘前预告 CLI 三处共用;
# _recall_gate_a / _harvest_vol_series 由此 re-export(tests/stages 旧导入路径兼容,patch 锚点在 frame)。
from autoresearch.data.contracts import degradations as contracts_degradations
from autoresearch.scan.frame import (  # noqa: F401 — re-export 兼容
    _harvest_vol_series,
    _recall_gate_a,
    build_market_frame,
)
from autoresearch.scan.user_config import load_pinned

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

# 10 因子组 / 先验权重 / _load_weights / _blend / _factor_groups / composite_score
# 在 autoresearch.common.scoring(scan/factor_lab/handler 三处同口径),顶部 import 复用。


# _recall_gate_a / _harvest_vol_series 已移 scan.frame(顶部 re-export,行为逐字不变)。


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


# ── pinned 强注(design: 2026-07-11-recall-gate-pinned-config-design.md §4.1;plan Task 3)──
# 保送票额外注入 L1 召回,标 lane="pinned"(镜像 quota_union 的 "(backfill)" 哨兵惯例)——
# 不占 recall_n(纯后处理,发生在 quota_union/composite 排序**已经**产出恰 recall_n 的结果
# 之后,故对其余票的入选结果零影响,"不挤他票"由此结构性保证);湖无该票数据 → 注入占位行
# `data_missing=True`(不编数,其余因子列经 concat 自动落 NaN)。`pinned` 为空/None → 原样
# 返回,不新增任何列(presence-gated parity)。
_PINNED_RANK_SENTINEL = 10**9


def _inject_pinned_l1(recall: pd.DataFrame, scored: pd.DataFrame,
                      pinned: list[dict] | None) -> pd.DataFrame:
    """L1 强注:pinned 每条 → 已在 recall 里只打标(不重复行,不动其真实 provenance);
    不在 → 从 scored 取真实行(找不到 → 占位 data_missing=True)。见模块顶部设计注释。
    """
    if not pinned:
        return recall
    out = recall.copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["pinned"] = False
    out["pinned_note"] = ""
    out["data_missing"] = False
    have = set(out["code"])

    scored_z = scored.assign(code=scored["code"].astype(str).str.zfill(6))
    new_rows: list[pd.DataFrame] = []
    seen_new: set[str] = set()
    for entry in pinned:
        code = str(entry["code"]).split(".")[0].zfill(6)
        note = entry.get("note", "")
        if code in have:                       # 已在自然召回里 → 只打标,不重复行
            m = out["code"] == code
            out.loc[m, "pinned"] = True
            out.loc[m, "pinned_note"] = note
            continue
        if code in seen_new:                   # 同票重复 pin 条目(用户笔误)→ 只注一次
            continue
        seen_new.add(code)
        lake_hit = scored_z[scored_z["code"] == code]
        if len(lake_hit):
            row = lake_hit.iloc[[0]].copy()
            row["data_missing"] = False
        else:                                    # 湖无该票数据 → 占位行,不编数
            row = pd.DataFrame([{"code": code, "data_missing": True}])
        row["pinned"] = True
        row["pinned_note"] = note
        if "recall_channels" in out.columns:      # multi 模式才有 provenance 列
            row["recall_channels"] = "pinned"
        if "n_channels" in out.columns:
            row["n_channels"] = 0
        if "best_rank" in out.columns:
            row["best_rank"] = _PINNED_RANK_SENTINEL
        new_rows.append(row)
    if new_rows:
        out = pd.concat([out, *new_rows], ignore_index=True, sort=False)
    return out


def recall_select(scored: pd.DataFrame, analysis_date: str, recall_n: int,
                  recall_mode: str = "multi", recall_channels=None,
                  pinned: list[dict] | None = None,
                  channel_quotas: dict[str, int] | None = None,
                  channel_floors: dict[str, int] | None = None):
    """L1 召回:multi=多路 quota_union(provenance)| composite=单复合分降序 top-n。

    返回 (recall_df, per_channel_long|None)。composite 模式逐值复现今天(parity 锚点);
    multi 模式 9 路 channel(全复用 scoring)各取 top-Kᶜ → quota union(floor 保底多样性)。
    单一来源:scan.universe.run(staging)与 L1Recall stage(trace)都调它。

    `pinned`(可选,直出 `user_config.load_pinned(...)["kept"]`):保送票强注,见
    `_inject_pinned_l1`;None/空列表 → 不触碰返回值(parity)。

    `channel_quotas`/`channel_floors`(可选,scan_config.json funnel 或 CLI 覆盖;design:
    2026-07-11-recall-gate-pinned-config-design.md §4.2):按 channel 名覆盖 `CHANNEL_DEFAULTS`
    的 quota(build 时的 top-k 截断)/floor(quota_union 的保底多样性)——
    `effective = {n: dataclasses.replace(CHANNEL_DEFAULTS[n], quota=q?, floor=f?) for n in names}`,
    build 与 quota_union 都吃 effective;两者均 None(默认)→ effective 逐值等于 CHANNEL_DEFAULTS
    (parity,tests/scan/test_parity.py 锁死)。composite 模式不涉及 channel,两参数被忽略。
    """
    if recall_mode == "composite":
        recall = scored.sort_values("composite", ascending=False).head(recall_n).reset_index(drop=True)
        return _inject_pinned_l1(recall, scored, pinned), None
    from autoresearch.scan.recall import CHANNEL_DEFAULTS, build, quota_union, registered_channels
    names = recall_channels or registered_channels()
    quotas, floors = channel_quotas or {}, channel_floors or {}
    effective = {n: dataclasses.replace(CHANNEL_DEFAULTS[n],
                                        quota=quotas.get(n, CHANNEL_DEFAULTS[n].quota),
                                        floor=floors.get(n, CHANNEL_DEFAULTS[n].floor))
                for n in names}
    frames = {n: build(n)(scored, analysis_date, effective[n].quota) for n in names}
    recall, per_channel = quota_union(frames, effective, recall_n, scored)
    return _inject_pinned_l1(recall, scored, pinned), per_channel


def write_shadow_variants(outdir: Path, scored: pd.DataFrame, recall: pd.DataFrame,
                          analysis_date: str, recall_n: int, l2_n: int,
                          l2_floors: dict | None, l2_sector_cap: float, l2_cols: list[str],
                          recall_mode: str = "multi",
                          include_bj: bool = True, source: str = "tushare",
                          l0_min_amount_yi: float = 0.0, l0_min_list_days: int = 0,
                          recall_channels=None, regime_aware: bool = False,
                          channel_quotas: dict[str, int] | None = None,
                          channel_floors: dict[str, int] | None = None) -> list[str]:
    """影子漏斗:变体 L2 落 <outdir>/shadow/(确定性 A/B,只落 staging 不喂 L3)。返回变体名。

    - nostrat:纯 composite 序(分层到底救了还是害了);
    - nocap:分层但无行业上限(cap 挡了多少赢家);
    - pre_healthy:**旧 9 路口径反事实**(无 healthy 通道、无健康桶)——healthy 通道的
      捕获增量由 retro 对照直接可测(2026-07-03 起)。仅 multi 模式有意义。
    - capfloor20:cap_floor_yi=20(默认 30)重跑 L0→L1→L2——验证 pr_20260624_001(小盘/北交所
      领涨日 30亿地板系统性漏判赢家)是否值得放宽。**唯一非零成本变体**:前三个都在本次已取好
      的 `scored`/`recall` 上重组,capfloor20 要真重新拉取 universe(网络/湖),故单独 try/except
      兜底——它失败不牵连前三个已经算好、只等落盘的零成本变体。
    """
    from autoresearch.scan.recall.l2_stratify import DEFAULT_FLOORS, select_l2
    sh = outdir / "shadow"
    sh.mkdir(exist_ok=True)
    nostrat = recall.sort_values("composite", ascending=False).head(l2_n).copy()
    nostrat.insert(0, "l2_rank", range(1, len(nostrat) + 1))
    variants: dict[str, pd.DataFrame] = {"nostrat": nostrat}
    variants["nocap"], _ = select_l2(recall, l2_n, floors=l2_floors, sector_cap_frac=1.0)
    if recall_mode == "multi":
        from autoresearch.scan.recall.registry import registered_channels
        old = [n for n in registered_channels() if n != "healthy"]
        re9, _ = recall_select(scored, analysis_date, recall_n, "multi", old,
                               channel_quotas=channel_quotas, channel_floors=channel_floors)
        f9 = {k: v for k, v in (l2_floors or DEFAULT_FLOORS).items() if k != "健康"}
        variants["pre_healthy"], _ = select_l2(re9, l2_n, floors=f9, sector_cap_frac=l2_sector_cap)
    try:
        uni20, _ = build_market_frame(analysis_date, cap_floor_yi=20.0, include_bj=include_bj,
                                      source=source, l0_min_amount_yi=l0_min_amount_yi,
                                      l0_min_list_days=l0_min_list_days)
        weights20, _ = pick_weights(uni20, regime_aware)
        scored20 = composite_score(uni20, weights20)
        recall20, _ = recall_select(scored20, analysis_date, recall_n, recall_mode, recall_channels,
                                    channel_quotas=channel_quotas, channel_floors=channel_floors)
        variants["capfloor20"], _ = select_l2(recall20, l2_n, floors=l2_floors, sector_cap_frac=l2_sector_cap)
    except Exception as e:  # noqa: BLE001 — 唯一重取数变体,失败不阻其余零成本变体落盘
        print(f"[warn] shadow capfloor20 失败(不阻其余变体): {e}", file=sys.stderr)
    for vname, vdf in variants.items():
        vdf[[c for c in l2_cols if c in vdf.columns]].to_csv(sh / f"L2_{vname}.csv", index=False)
    return list(variants)


# ───────────────────────── 编排 + 输出 ─────────────────────────


def _funnel_overlay(recall_channels, channel_quotas, channel_floors):
    """scan_config.json funnel 兜底(仅补 None 的键;显式参数恒优先)。缺文件/坏文件 → 原样返回。"""
    if recall_channels is not None and channel_quotas is not None and channel_floors is not None:
        return recall_channels, channel_quotas, channel_floors
    try:
        from autoresearch.scan.user_config import load_user_config
        fn = (load_user_config() or {}).get("funnel") or {}
    except Exception as e:  # noqa: BLE001 — 配置层故障不挡确定性扫描
        print(f"[warn] scan_config 读取失败({e!r})→ funnel 用注册表默认", file=sys.stderr)
        fn = {}
    if recall_channels is None:
        recall_channels = fn.get("recall_channels")
    if channel_quotas is None:
        channel_quotas = fn.get("channel_quotas")
    if channel_floors is None:
        channel_floors = fn.get("channel_floors")
    return recall_channels, channel_quotas, channel_floors


def run(analysis_date: str, cap_floor_yi: float = 30.0, include_bj: bool = True,
        recall_n: int = 1000, l2_n: int = 200, outdir: Path | None = None,
        source: str = "tushare", recall_mode: str = "multi", recall_channels=None,
        pinned_path=None,                                                # 保送 pinned.json 路径(None=默认路径;缺文件→kept=[]→no-op parity)
        regime_aware: bool = False,                                      # L1 权重按 regime 选(默认关=parity)
        shadow: bool = True,                                              # 影子漏斗变体 L2(纯增量文件,可 --no-shadow 关)
        l0_min_amount_yi: float = 0.0, l0_min_list_days: int = 0,         # L0 流动性/次新硬门(默认 0=关=parity)
        l2_floors: dict | None = None, l2_sector_cap: float = 0.20,
        channel_quotas: dict[str, int] | None = None,                     # 覆盖各路 quota(None=CHANNEL_DEFAULTS,parity)
        channel_floors: dict[str, int] | None = None,                     # 覆盖各路 floor(None=CHANNEL_DEFAULTS,parity)
        weights_path: str | None = None) -> dict:                         # L1 权重文件(None=默认路径=parity;回放器注入 as-of 快照防前视)
    """L0 选集 + L1 召回 + L2 粗排(确定性分层采样 → top l2_n)。全确定性,零 LLM。

    recall_mode:multi=多路策略召回(默认,带 provenance + L1_channels.csv)| composite=单复合分(对拍/回退)。

    `weights_path`:透传 `pick_weights(path=...)`。**None = 现行为**(读
    `context/factor_lab/weights.json`)→ 逐字节 parity,生产路径不受影响。存在的理由是
    **权重 PIT**(design 2026-07-12-funnel-replay-l35-removal-design.md Part B §2.3):
    weights.json 是 retro 用含未来前向收益校准出来的,拿它回放历史 = 用未来的权重预测过去;
    `research.replay` 因此注入先验/as-of 权重快照。
    """
    # FN-1 缝第三修:生产真身(workflow→prelude→本函数直调)不经 cli._config_from_args,scan_config
    # 的 funnel 在真跑动从未生效(2026-07-11 冒烟坐实:仍 11 路旧配额)。兜底下沉:调用方没显式给的
    # 键从 scan_config.json 读(显式参数恒优先;缺文件/缺键=None=注册表默认 parity;镜像 pinned
    # 166e4d1 的"默认路径在 run 本体读"先例)。坏配置响亮警告后按默认跑,不让扫描失败。
    recall_channels, channel_quotas, channel_floors = _funnel_overlay(
        recall_channels, channel_quotas, channel_floors)
    # L0 取数 + L1 轻门 + 多日量价富化 → 全市场因子帧(scan.frame 单一代码路径,Phase 0 抽取)
    uni, _counts = build_market_frame(analysis_date, cap_floor_yi=cap_floor_yi, include_bj=include_bj,
                                      source=source, l0_min_amount_yi=l0_min_amount_yi,
                                      l0_min_list_days=l0_min_list_days)
    n_raw, n_l0 = _counts["universe_raw"], _counts["universe"]
    # weights_path=None → 不传 path,吃 pick_weights 的默认值(=现行为,parity);给了才覆盖。
    weights, _regime = pick_weights(uni, regime_aware,
                                    **({"path": weights_path} if weights_path else {}))
    scored = composite_score(uni, weights)
    with contextlib.suppress(Exception):   # Wave4:事件列(湖优先);失败=列缺失,event 路自动空帧降级
        from autoresearch.scan.events import attach_event_cols, market_event_counts
        scored = attach_event_cols(scored, market_event_counts(analysis_date))
    pinned = load_pinned(analysis_date, path=pinned_path)["kept"]   # 保送票强注 L1(缺 pinned.json→kept=[]→no-op parity)
    recall, per_channel = recall_select(scored, analysis_date, recall_n, recall_mode,
                                        recall_channels, pinned=pinned,
                                        channel_quotas=channel_quotas, channel_floors=channel_floors)
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
    keep = keep + [c for c in ("recall_channels", "n_channels", "best_rank",
                               "pinned", "pinned_note") if c in recall.columns]  # pinned 列 presence-gated:无保送不出现=parity
    recall[[c for c in keep if c in recall.columns]].to_csv(outdir / "L1_recall_top1000.csv", index=False)
    if per_channel is not None and len(per_channel):           # multi:各路召回名单留底(provenance/复盘)
        per_channel.to_csv(outdir / "L1_channels.csv", index=False)
    # 全量打分(所有过门股,按 composite 降序 + recalled 标记)→ trace/ 留全阶段数据,不截断
    full = scored.sort_values("composite", ascending=False).reset_index(drop=True)
    full.insert(0, "rank", range(1, len(full) + 1))
    full.insert(1, "recalled", full["rank"] <= recall_n)
    full[["rank", "recalled"] + [c for c in keep if c in full.columns]].to_csv(
        outdir / "L1_scored_full.csv", index=False)

    # ── L2 粗排:确定性分层多样性采样器(ML-free;sector-neutral composite + 风格 floor + sector cap)──
    # 实证:确定性 L2 无稳健 alpha、regime 依赖 → 不预测、不赌 regime,只给 L3/L4 建均衡菜单;alpha 在 L3/L4。
    # L2Rank stage 共用 select_l2 → golden parity。(design: 2026-06-25-l2-stratified-sampler)
    from autoresearch.scan.recall.l2_stratify import select_l2
    l2, l2_engine = select_l2(recall, l2_n, floors=l2_floors, sector_cap_frac=l2_sector_cap)
    l2_cols = ["l2_rank", "gbdt_score", "l2_lane_reserved", "sector_mom", *keep]
    l2[[c for c in l2_cols if c in l2.columns]].to_csv(outdir / "L2_gbdt_top200.csv", index=False)
    print(f"[L2 粗排] recall {len(recall)} → {l2_engine} top {len(l2)}", file=sys.stderr)

    if shadow:   # ── 影子漏斗:变体 L2(确定性 A/B;只落 staging 不喂 L3;前三免费+capfloor20 重取数)──
        try:
            names = write_shadow_variants(outdir, scored, recall, analysis_date, recall_n,
                                          l2_n, l2_floors, l2_sector_cap, l2_cols, recall_mode,
                                          include_bj=include_bj, source=source,
                                          l0_min_amount_yi=l0_min_amount_yi,
                                          l0_min_list_days=l0_min_list_days,
                                          recall_channels=recall_channels, regime_aware=regime_aware,
                                          channel_quotas=channel_quotas, channel_floors=channel_floors)
            print(f"[shadow] 变体 L2 ×{len(names)}({'/'.join(names)})→ {outdir / 'shadow'}"
                  "(retro 对照赢家捕获)", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — 影子失败不阻主漏斗
            print(f"[warn] 影子漏斗失败: {e}", file=sys.stderr)

    sectors.to_csv(outdir / "sectors.csv", index=False)
    (outdir / "meta.json").write_text(json.dumps({
        "analysis_date": analysis_date, "universe_raw": n_raw, "universe": n_l0, "after_gate_a": len(uni),
        "recall_n": len(recall), "l2_n": len(l2), "l2_engine": l2_engine,
        "l2_sector_cap": l2_sector_cap,
        "cap_floor_yi": cap_floor_yi, "include_bj": include_bj, "source": source,
        "regime": _regime,                                    # 当日 regime(regime_aware 关 = null)
        "weights_source": weights.get("meta", {}).get("source", "weights.json"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    try:                                                      # 重放快照:当日实际权重固化进现场
        (outdir / "weights_used.json").write_text(
            json.dumps(weights, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — 快照失败只警告,不阻断漏斗
        print(f"[warn] weights_used.json 快照失败: {e}", file=sys.stderr)
    # B 级数据降级落盘(design 2026-07-12-data-contracts-design.md §3):A 级违约已在取数处抛异常
    # 阻断,能跑到这里说明地基是全的;剩下的是增强端点缺失(北向/质押/席位/公告…)——它们**必须
    # 可见**,否则又回到"系统有降级能力、没有传达能力"的老路。presence-gated:无降级不落文件。
    degraded = contracts_degradations()
    if degraded:
        (outdir / "degraded.json").write_text(
            json.dumps(degraded, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[数据契约] 本次 B 级降级 {len(degraded)} 条 → {outdir / 'degraded.json'}", file=sys.stderr)
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
    ap.add_argument("--l2-n", type=int, default=200, help="L2 粗排数(分层采样 top N),默认 200")
    ap.add_argument("--source", choices=["em", "tushare"], default="tushare",
                    help="universe 取数源:tushare=默认(push2 常被封);em=东财 push2")
    ap.add_argument("--recall-mode", choices=["multi", "composite"], default="multi",
                    help="L1 召回:multi=多路策略召回(默认)| composite=单复合分(对拍/回退)")
    ap.add_argument("--recall-channels", default=None, help="启用 channel 子集(逗号分隔;缺省=全 9 路)")
    ap.add_argument("--l2-sector-cap", type=float, default=0.20,
                    help="L2 分层采样:任一申万一级 ≤ 此比例,默认 0.20(=40/200);≥1.0=关")
    ap.add_argument("--regime-aware", action="store_true",
                    help="L1 权重按当日 regime 选(需 weights.json regimes 块;默认关=parity)")
    ap.add_argument("--no-shadow", action="store_true",
                    help="关掉影子漏斗变体 L2(默认开;纯增量文件,retro 做确定性 A/B)")
    ap.add_argument("--selftest", action="store_true", help="离线验证打分逻辑(无网络)")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    analysis_date = args.date or date.today().isoformat()
    res = run(analysis_date, cap_floor_yi=args.cap_floor, include_bj=not args.exclude_bj,
              recall_n=args.recall_n, l2_n=args.l2_n, source=args.source,
              recall_mode=args.recall_mode, l2_sector_cap=args.l2_sector_cap,
              recall_channels=(args.recall_channels.split(",") if args.recall_channels else None),
              regime_aware=args.regime_aware, shadow=not args.no_shadow)
    print(f"\nL0 universe={res['universe']} → 轻门 {res['after_gate_a']} → 召回 top{res['recall_n']} "
          f"→ L2 {res['l2_engine']} top{res['l2_n']} (板块概览 {res['sectors']} 个)"
          f"\n→ {res['outdir']}/L2_gbdt_top200.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
