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
import dataclasses
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import workspace as ws

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
    prev_quarter,
    resolve_weights,
)
from autoresearch.common.turnup import PANEL_COLS

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


# ── 行业席位强注(2026-09-24 §2.3;裁定③:接上涨侧、席位制、不做排序驱动)──
# 当日 healthy top3 行业里非落刀的健康上涨成员,直通 L1→L2(镜像 `_inject_pinned_l1`
# 的"已在 recall 只打标,不在 → 从 scored 取真实行"结构)。`seats` 空 → 原样返回
# (presence-gated parity)。选股逻辑本身在 `autoresearch.scan.sector_seats.pick_sector_seats`
# ——本函数只管注入,不重新判断谁该入席。
def _inject_sector_seats_l1(recall: pd.DataFrame, scored: pd.DataFrame,
                            seats: list[dict]) -> pd.DataFrame:
    """行业席位 L1 强注(镜像 `_inject_pinned_l1`):已在 recall 只打标;不在 → 从 scored 取真实行。
    `seats` 空 → 原样返回(parity)。"""
    if not seats:
        return recall
    out = recall.copy()
    out["code"] = out["code"].astype(str).str.zfill(6)
    out["sector_seat"] = False
    out["sector_seat_industry"] = ""
    have = set(out["code"])
    scored_z = scored.assign(code=scored["code"].astype(str).str.zfill(6))
    new_rows: list[pd.DataFrame] = []
    for seat in seats:
        code = str(seat["code"]).zfill(6)
        if code in have:
            m = out["code"] == code
            out.loc[m, "sector_seat"] = True
            out.loc[m, "sector_seat_industry"] = seat["industry"]
            continue
        hit = scored_z[scored_z["code"] == code]
        if not len(hit):
            continue
        row = hit.iloc[[0]].copy()
        row["sector_seat"] = True
        row["sector_seat_industry"] = seat["industry"]
        if "recall_channels" in out.columns:
            row["recall_channels"] = "sector_seat"
        if "n_channels" in out.columns:
            row["n_channels"] = 0
        if "best_rank" in out.columns:
            row["best_rank"] = None
        new_rows.append(row)
        have.add(code)
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


def _lowturn_counts(scored, recall, l2, cfg: dict) -> dict:
    """低位转强旗的**三段计数**(纯函数,零取数):全帧 → L1 → L2。

    2026-08-21 首跑事故的探针化:那天 `lowturn_mask` 全帧 120 只亮旗、L1 剩 17、**L2 恰 0**,
    于是 L3 侧整套(旗列/pass1 强留/守卫⑥/G 条)空转一整天而没有任何一行日志喊过。
    「上线即恒 0」这种死法没有报错,只有一个谁也没在数的数——所以把它数出来:进 `meta.json`
    与 prelude 汇总屏,并由 `self_review` 探针 14 在 L2 到货为 0 时 warn。
    (同族配方:「自动学习的腿必须有一个会变的量做断言,否则它死了也像活着」。)

    三帧任一算不出(缺列/坏值)→ 该键为 None,不抛(B 级观测腿不得阻断扫描)。
    """
    from autoresearch.common.turnup import lowturn_mask
    out: dict = {}
    for key, frame in (("lowturn_full", scored), ("lowturn_l1", recall), ("lowturn_l2", l2)):
        try:
            out[key] = int(lowturn_mask(frame, cfg).sum()) if frame is not None and len(frame) else 0
        except Exception:  # noqa: BLE001 — 观测腿失败不挡扫描,记 None 让下游知道"没量到"
            out[key] = None
    return out


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


def run(analysis_date: str, cap_floor_yi: float | None = None, include_bj: bool | None = None,
        recall_n: int | None = None, l2_n: int | None = None, outdir: Path | None = None,
        source: str | None = None, recall_mode: str = "multi", recall_channels=None,
        pinned_path=None,                                                # 保送 pinned.json 路径(None=默认路径;缺文件→kept=[]→no-op parity)
        regime_aware: bool | None = None,                                # L1 权重按 regime 选(None→config funnel.regime_aware;内建 False)
        l0_min_amount_yi: float | None = None, l0_min_list_days: int | None = None,  # L0 流动性/次新硬门(内建 0=关=parity)
        l2_floors: dict | None = None, l2_sector_cap: float | None = None,
        l2_knife_cap: bool | None = None,                                 # L2 落刀帽总开关(None→config l2.knife_cap;内建 False=parity)
        l2_sector_seats: dict | None = None,                              # 行业席位总开关+参数(None→config l2.sector_seats;内建 {}=关=parity)
        channel_quotas: dict[str, int] | None = None,                     # 覆盖各路 quota(None=CHANNEL_DEFAULTS,parity)
        channel_floors: dict[str, int] | None = None,                     # 覆盖各路 floor(None=CHANNEL_DEFAULTS,parity)
        weights_path: str | None = None,                                  # L1 权重文件(None=默认路径=parity;回放器注入 as-of 快照防前视)
        weight_profile: str | None = None,                                # 召回权重档(None→config funnel.weight_profile;内建 "calibrated"=parity)
        preference_weights: dict | None = None) -> dict:                  # preference 档的十组权重(None→config funnel.preference_weights)
    """L0 选集 + L1 召回 + L2 粗排(确定性分层采样 → top l2_n)。全确定性,零 LLM。

    recall_mode:multi=多路策略召回(默认,带 provenance + L1_channels.csv)| composite=单复合分(对拍/回退)。

    L1 权重经 `resolve_weights`(scoring.py)唯一入口解析:`weight_profile="calibrated"`(内建
    默认,**None = 现行为**)→ 逐字委派 `pick_weights`,`weights_path` 透传给它(**None** 读
    `context/factor_lab/weights.json` → 逐字节 parity,生产路径不受影响)。存在的理由是
    **权重 PIT**(design 2026-07-12-funnel-replay-l35-removal-design.md Part B §2.3):
    weights.json 是用含未来前向收益的面板校准出来的,拿它回放历史 = 用未来的权重预测过去;
    (原 `research.replay` 因此注入先验/as-of 权重快照;该回放器已随 2026-08-21 闭环退役删除。)

    `weight_profile="preference"`(2026-09-24 §2.1):固定偏好档,十组权重取自
    `preference_weights`(None→config `funnel.preference_weights`),**不看 regime_aware、不读
    weights_path**——符号是产品偏好、量级是裁定,不是拟合出来的,所以没有自动重标定这回事。
    """
    # FN-1 缝第三修:生产真身(workflow→prelude→本函数直调)不经 cli._config_from_args,scan_config
    # 的 funnel 在真跑动从未生效(2026-07-11 冒烟坐实:仍 11 路旧配额)。兜底下沉:调用方没显式给的
    # 键从 scan_config.json 读(显式参数恒优先;缺文件/缺键=None=注册表默认 parity;镜像 pinned
    # 166e4d1 的"默认路径在 run 本体读"先例)。坏配置响亮警告后按默认跑,不让扫描失败。
    recall_channels, channel_quotas, channel_floors = _funnel_overlay(
        recall_channels, channel_quotas, channel_floors)
    # 运行旋钮兜底(2026-08-11 配置单一事实源波,同 _funnel_overlay 语义):None 的形参从
    # scan_config 补,显式恒优先;缺文件/缺键 = 内建值(parity)。在 run 本体解析(而非只在
    # build_market_frame 里)是因为 meta/weights_used 要记**实际生效值**(可复现凭据)。
    # ⚠️ regime_aware 内建缺省两处不同:直调 run()/universe CLI 历史缺省 False(此处),
    # prelude 生产路缺省 True(在 run_prelude 入口回填后传进来的是具体值)——两处 parity 各自成立。
    from autoresearch.scan.user_config import knob, load_user_config as _luc
    try:
        _ucfg = _luc() or {}
    except Exception as e:  # noqa: BLE001 — 配置层故障不挡确定性扫描,但必须留痕
        print(f"[warn] scan_config 读取失败({e!r})→ 运行旋钮全用内建默认", file=sys.stderr)
        _ucfg = {}
    cap_floor_yi = float(knob("l0", "cap_floor_yi", cap_floor_yi, 30.0, cfg=_ucfg))
    include_bj = bool(knob("l0", "include_bj", include_bj, True, cfg=_ucfg))
    source = str(knob("l0", "source", source, "tushare", cfg=_ucfg))
    l0_min_amount_yi = float(knob("l0", "min_amount_yi", l0_min_amount_yi, 0.0, cfg=_ucfg))
    l0_min_list_days = int(knob("l0", "min_list_days", l0_min_list_days, 0, cfg=_ucfg))
    recall_n = int(knob("funnel", "recall_n", recall_n, 1000, cfg=_ucfg))
    l2_n = int(knob("funnel", "l2_n", l2_n, 200, cfg=_ucfg))
    regime_aware = bool(knob("funnel", "regime_aware", regime_aware, False, cfg=_ucfg))
    # 召回权重档(2026-09-24 §2.1):"calibrated"=旧行为(读 weights.json/内置先验,回滚杆)/
    # "preference"=固定偏好档(十组权重就在 preference_weights 里,唯一事实源,无自动重标定)。
    weight_profile = str(knob("funnel", "weight_profile", weight_profile, "calibrated", cfg=_ucfg))
    preference_weights = knob("funnel", "preference_weights", preference_weights, None, cfg=_ucfg)
    l2_sector_cap = float(knob("l2", "sector_cap", l2_sector_cap, 0.20, cfg=_ucfg))
    l2_floors = knob("l2", "floors", l2_floors, None, cfg=_ucfg)
    l2_knife_cap = bool(knob("l2", "knife_cap", l2_knife_cap, False, cfg=_ucfg))
    l2_sector_seats = dict(knob("l2", "sector_seats", l2_sector_seats, {}, cfg=_ucfg) or {})
    # L0 取数 + L1 轻门 + 多日量价富化 → 全市场因子帧(scan.frame 单一代码路径,Phase 0 抽取)
    uni, _counts = build_market_frame(analysis_date, cap_floor_yi=cap_floor_yi, include_bj=include_bj,
                                      source=source, l0_min_amount_yi=l0_min_amount_yi,
                                      l0_min_list_days=l0_min_list_days)
    n_raw, n_l0 = _counts["universe_raw"], _counts["universe"]
    # L1 权重的唯一入口(2026-09-24 §2.1):calibrated 分支逐字委派 pick_weights(weights_path=None
    # → 不传 path,吃其默认值 = 现行为,parity);preference 分支固定档、不看 regime、不读文件。
    # 模块级导入(非函数体内局部导入):测试靠 `monkeypatch.setattr(U, "resolve_weights", ...)`
    # patch 这条缝,函数体内 `from ... import` 每次调用都现取,不留可 patch 的模块属性
    # (fix round 1,2026-09-25:此前局部导入让 tests/scan/test_events.py 的
    # `U.pick_weights` patch 失效——真身早已换成 resolve_weights,模块上却没这个名字)。
    weights, _regime = resolve_weights(uni, profile=weight_profile, preference_weights=preference_weights,
                                       regime_aware=regime_aware, path=weights_path)
    scored = composite_score(uni, weights)
    try:                                   # Wave4:事件列(湖优先),B 级增强腿,失败不阻扫描
        from autoresearch.scan.events import attach_event_cols, market_event_counts
        scored = attach_event_cols(scored, market_event_counts(analysis_date))
    except Exception as e:  # noqa: BLE001 — 但**必须留痕**:Review Round 1 I-2 实测,原
        # `contextlib.suppress(Exception)` 只有"三腿取数全失败"那一种形态有痕(那是
        # `market_event_counts` 内部自己打的);聚合层抛 / `attach_event_cols` 抛 / import
        # 失败这三种形态下 `scored` **根本没有 ev_* 列**,却一个字都不打 —— 而这恰是危害
        # 更大的一类:event 路当天静默 0 召回,`channel_audit` 会把它记成"这路没 edge"
        # 而不是"取数/挂载坏了"(本 repo 记过账的 FN-1「降级不留痕」变体)。
        print(f"[events] ⚠️ 事件列挂载失败({e!r})→ scored 无 ev_* 列,event 路本日"
              "等同停用(channel_audit 读数按'停用'而非'无 edge'解释)。", file=sys.stderr)
    pinned = load_pinned(analysis_date, path=pinned_path)["kept"]   # 保送票强注 L1(缺 pinned.json→kept=[]→no-op parity)
    recall, per_channel = recall_select(scored, analysis_date, recall_n, recall_mode,
                                        recall_channels, pinned=pinned,
                                        channel_quotas=channel_quotas, channel_floors=channel_floors)
    # outdir 在这里(recall_select 之后、而非等 L2 落盘前)一次性解析:行业席位产物
    # `_sector_seats.json` 要在 L2 之前落盘,若仍在下方"L2 粗排"之后才
    # `outdir = outdir or ws.scan_root() / analysis_date`,就得在两处各算一次同一个默认
    # 路径——两个必须相等的表达式是等着出错的地雷(2026-09-24 §2.3 controller review:
    # 上移这一行,而不是在席位块里再算一次)。必须严格晚于 `recall_select(...)` 这条语句
    # 真正跑完(而非只晚于 build_market_frame):test_config_knobs.py 三个 spy 测试炸在
    # build_market_frame 内部、test_events.py 的 `_run_to_recall` 炸在 `recall_select`
    # 本体(把它整个替身成会立即 raise 的 spy)——两类"raise 后不该有任何副作用"的
    # knob/接线锁都靠 tests/scan/conftest.py 的生产路径写守卫抓「过早建目录」,
    # 这一行必须排在 `recall_select(...)` 调用**之后**才两边都不撞。
    outdir = outdir or ws.scan_root() / analysis_date
    outdir.mkdir(parents=True, exist_ok=True)
    # 行业席位(2026-09-24 §2.3):healthy top3 行业内非落刀健康上涨成员直通 L1→L2,
    # presence-gated(默认关=parity)。剔已 📌 保送的码(pinned 走自己的直通车,不重复占席)。
    sector_seats: list[dict] = []
    if bool(l2_sector_seats.get("enabled", False)):
        from autoresearch.scan.sector_seats import SECTOR_SEATS_FILENAME, pick_sector_seats
        sector_seats = pick_sector_seats(
            scored, per_sector=int(l2_sector_seats.get("per_sector", 2)),
            max_sectors=int(l2_sector_seats.get("max_sectors", 3)),
            exclude={str(p["code"]).split(".")[0].zfill(6) for p in pinned})
        recall = _inject_sector_seats_l1(recall, scored, sector_seats)
        (outdir / SECTOR_SEATS_FILENAME).write_text(json.dumps(
            {"schema_version": 1, "date": analysis_date, "seats": sector_seats},
            ensure_ascii=False, indent=1), encoding="utf-8")
    # Wave5 ①:计数行走 stdout —— 编排层的 bash-agent 只回报 stdout,进 stderr 等于白打
    # ([warn]/异常仍走 stderr,别把告警混进给人看的进度流)。
    print(f"[L1 召回] L0 {n_l0} → 轻门 {len(uni)} → {recall_mode} top {len(recall)}")
    sectors = aggregate_sectors_overview(recall, uni)

    # outdir 已在上方(recall_select 之后)解析 + 建目录,这里不再重复计算同一个默认路径。
    keep = (["code", "name", "industry", "composite"] + [f"score_{g}" for g in _GROUPS]
            + ["mktcap_yi", "close", "amount_yi", "vol_ratio", "turnover", "cmf_20", "obv_mom_20",
               "pct_1d", "pct_60d", "pct_ytd",     # pct_1d(Wave6 Q3):market_pack 的当日切面块
                                                    # 从 staging 入口读的就是本文件 —— 不投影 = L4 消费侧恒空
               "main_inflow_yi", "main_net_ratio",
               "retail_net_yi", "winner_rate", "chip_concentration", "price_to_cost", "hk_ratio",
               "rsi6", "rsi12", "pe", "pb", "dv_ratio", "np_yoy", "rev_yoy", "roe",
               "ma_bull", "above_ma60",
               *PANEL_COLS])          # 2026-08-21 低位转强波:turnup 十列(B 级;缺列在下一行被过滤掉=parity)
    keep = keep + [c for c in ("recall_channels", "n_channels", "best_rank",
                               "pinned", "pinned_note",
                               "sector_seat", "sector_seat_industry") if c in recall.columns]
    # pinned/sector_seat 列均 presence-gated:未触发 → 不出现 = parity
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
    # 未启用通道的桶 floor 运行时归零(2026-08-22):multi 模式才有「启用通道集」这个概念;
    # composite 单路模式与 recall_channels 缺省(=全注册路)都传 None/全集 = 原样 parity。
    _enabled = None
    if recall_mode == "multi":
        from autoresearch.scan.recall import registered_channels
        _enabled = list(recall_channels) if recall_channels else registered_channels()
    from autoresearch.common.scoring import falling_knife_mask
    _knife = falling_knife_mask(scored) if l2_knife_cap else None
    l2_knife_cap_share = (float(_knife.fillna(False).mean()) if _knife is not None else None)
    l2, l2_engine = select_l2(recall, l2_n, floors=l2_floors, sector_cap_frac=l2_sector_cap,
                              enabled_channels=_enabled, knife_cap_share=l2_knife_cap_share)
    # T16(Wave12 F1-3):select_l2 早算出 selection_reason/selection_detail(每票「因何进菜单」:
    # merit 核/风格桶救回/行业 cap/保送/回填,见 l2_stratify.py),此前这两列漏投影进白名单——
    # 内存里的 l2 有它们,CSV 却没有,导致 l2_slo._guards 的分布 guard 分支 31 天从未触发过
    # (guards 自己的单测靠手搭 fixture 绕过了本落盘点,盖不住这个洞)。纯新增列,零名单影响。
    l2_cols = ["l2_rank", "gbdt_score", "l2_lane_reserved", "sector_mom",
              "selection_reason", "selection_detail", "knife_cap_swap", *keep]
    l2[[c for c in l2_cols if c in l2.columns]].to_csv(outdir / "L2_gbdt_top200.csv", index=False)
    print(f"[L2 粗排] recall {len(recall)} → {l2_engine} top {len(l2)}")

    # (2026-08-21 learning 层退役:影子漏斗 5 变体〔nostrat/nocap/pre_healthy/plus_event/
    #  plus_sectormom/capfloor20〕整段删除。它存在的唯一理由是喂 retro 对照与
    #  `channel_audit --variant` 的 `unique_excess_t2`,两个消费者都随闭环没了;其中
    #  capfloor20 还是**唯一重取数**变体,留着 = 每跑一次白付一次全市场取数换一堆没人读的 CSV。)

    sectors.to_csv(outdir / "sectors.csv", index=False)
    # 低位转强三段计数(2026-08-22):两把开关任一开就量 —— 通道启用(生产者)或 L3 旗启用
    # (消费者)。两个都关 = 该特性整体不在场,不量、不落键(逐字 parity)。
    _lt_counts: dict = {}
    try:
        from autoresearch.scan.recall.channels import _lowturn_cfg
        _lt_cfg = _lowturn_cfg()
        if (_enabled and "lowturn" in _enabled) or bool(_lt_cfg.get("enabled")):
            _lt_counts = _lowturn_counts(scored, recall, l2, _lt_cfg)
    except Exception as e:  # noqa: BLE001 — 观测腿失败不挡扫描
        print(f"[warn] lowturn 三段计数失败({e!r})→ meta 不落该三键", file=sys.stderr)
    (outdir / "meta.json").write_text(json.dumps({
        "analysis_date": analysis_date, "universe_raw": n_raw, "universe": n_l0, "after_gate_a": len(uni),
        "recall_n": len(recall), "l2_n": len(l2), "l2_engine": l2_engine,
        "l2_sector_cap": l2_sector_cap,
        "l2_knife_cap_share": l2_knife_cap_share,
        "sector_seat_n": len(sector_seats),                   # 2026-09-24 §2.3:行业席位到货数(关=恒 0)
        **_lt_counts,
        "cap_floor_yi": cap_floor_yi, "include_bj": include_bj, "source": source,
        "regime": _regime,                                    # 当日 regime(regime_aware 关 = null;preference 档恒 null)
        "weights_source": weights.get("meta", {}).get("source", "weights.json"),
        "weight_profile": weight_profile,                     # 2026-09-24 §2.1:calibrated|preference,本跑实际生效档
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
        print(f"[数据契约] 本次 B 级降级 {len(degraded)} 条 → {outdir / 'degraded.json'}")
    print(f"[done] L1 召回 → {outdir}/L1_recall_top1000.csv ({len(recall)})")
    return {"universe": n_l0, "after_gate_a": len(uni), "recall_n": len(recall),
            "l2_n": len(l2), "l2_engine": l2_engine, "sectors": len(sectors), "outdir": str(outdir),
            **_lt_counts}


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
    ap.add_argument("--cap-floor", type=float, default=None,
                    help="市值地板(亿);缺省=scan_config l0.cap_floor_yi→30")
    ap.add_argument("--exclude-bj", action="store_true", help="排除北交所(缺省=scan_config l0.include_bj→纳入)")
    ap.add_argument("--recall-n", type=int, default=None,
                    help="召回数(top N);缺省=scan_config funnel.recall_n→1000")
    ap.add_argument("--l2-n", type=int, default=None,
                    help="L2 粗排数(分层采样 top N);缺省=scan_config funnel.l2_n→200")
    ap.add_argument("--source", choices=["em", "tushare"], default=None,
                    help="universe 取数源;缺省=scan_config l0.source→tushare(push2 常被封)")
    ap.add_argument("--recall-mode", choices=["multi", "composite"], default="multi",
                    help="L1 召回:multi=多路策略召回(默认)| composite=单复合分(对拍/回退)")
    ap.add_argument("--recall-channels", default=None, help="启用 channel 子集(逗号分隔;缺省=scan_config funnel.recall_channels)")
    ap.add_argument("--l2-sector-cap", type=float, default=None,
                    help="L2 分层采样:任一申万一级 ≤ 此比例;缺省=scan_config l2.sector_cap→0.20;≥1.0=关")
    ap.add_argument("--regime-aware", action="store_true",
                    help="L1 权重按当日 regime 选(需 weights.json regimes 块;缺省=scan_config funnel.regime_aware→关)")
    ap.add_argument("--no-regime-aware", action="store_true",
                    help="强制关 regime 权重(覆盖 scan_config)")
    ap.add_argument("--selftest", action="store_true", help="离线验证打分逻辑(无网络)")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    analysis_date = args.date or date.today().isoformat()
    res = run(analysis_date, cap_floor_yi=args.cap_floor,
              include_bj=(False if args.exclude_bj else None),
              recall_n=args.recall_n, l2_n=args.l2_n, source=args.source,
              recall_mode=args.recall_mode, l2_sector_cap=args.l2_sector_cap,
              recall_channels=(args.recall_channels.split(",") if args.recall_channels else None),
              regime_aware=(True if args.regime_aware
                            else (False if args.no_regime_aware else None)))
    print(f"\nL0 universe={res['universe']} → 轻门 {res['after_gate_a']} → 召回 top{res['recall_n']} "
          f"→ L2 {res['l2_engine']} top{res['l2_n']} (板块概览 {res['sectors']} 个)"
          f"\n→ {res['outdir']}/L2_gbdt_top200.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
