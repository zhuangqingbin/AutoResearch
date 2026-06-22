#!/usr/bin/env python3
"""特征集 registry —— 命名视图 → 列清单(DataHandler 物化哪些列、Trainer 喂哪些)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §B/§C(三态 feature_set)。

`FEATURE_SETS["core"]` 是**横截面金标准列清单**,与 `scripts/factor_lab.py` 完全对齐:
  CANDIDATES 因子列(IC 候选)+ GBDT 特征(GBDT_GROUPS 的 g_* 组分位 + GBDT_RAW 原始因子
  + composite 线性锚定),去重保序。E3 用它把新管道(DataHandler.materialize)对拍现管道
  (factor_lab.factor_frame),故**列清单必须与 factor_lab 同源**——下方常量是 factor_lab
  `CANDIDATES / GBDT_GROUPS / GBDT_RAW` 的镜像,`tests/data/test_handler.py` 有 parity 断言
  锁死二者一致(factor_lab 改了而这里没跟 → 测试红)。

每列来源端点(lake `<endpoint>` parquet,见 autoresearch.data.endpoints):
  daily(价/量,算动量·量价序列)、daily_basic(close/turnover/vol_ratio/pe/pb/dv_ratio/
  total_mv/circ_mv)、stk_factor_pro(MA→ma_bull/above_ma60、rsi6/rsi12、macd)、
  cyq_perf(winner_rate/cost→chip_concentration/price_to_cost/cost_premium)、
  moneyflow(主力/散户净流入→main_*/retail_net_yi)、hk_hold(hk_ratio)、
  margin_detail(rz_ratio/rz_buy_intensity)、block_trade(block_premium/block_intensity)、
  top_inst(lhb_inst_net)。派生列(momentum_score/mom_v*/g_*/composite)由打分原语
  (autoresearch.common.scoring)从上述原始列算得。
"""
from __future__ import annotations

# ── factor_lab.CANDIDATES 的因子列(IC 候选;含动量/资金/技术/筹码/估值/UZI/多日量价) ──
# 端点标注见模块 docstring;镜像 scripts/factor_lab.py 的 CANDIDATES(顺序一致)。
_CANDIDATE_COLS: list[str] = [
    "pct_5d", "pct_20d", "pct_60d", "pct_ytd",          # daily(动量)
    "main_inflow_yi", "inflow_to_cap",                   # moneyflow + daily_basic(去规模)
    "ma_bull", "above_ma60", "rsi6", "macd",             # stk_factor_pro
    "vol_ratio", "turnover",                             # daily_basic
    "winner_rate", "cost_premium",                       # cyq_perf + daily(现价/成本)
    "pe", "pb", "dv_ratio",                              # daily_basic
    "momentum_score",                                    # 派生:lens_momentum(scoring)
    "mom_v2_noVol", "mom_v3_capnorm", "mom_v4_rsHeavy",  # 派生:动量变体
    "main_net_ratio", "retail_net_yi",                   # moneyflow(结构)/ daily(成交额)
    "chip_concentration", "price_to_cost",               # cyq_perf
    "rsi12",                                             # stk_factor_pro
    "hk_ratio",                                          # hk_hold
    "rz_ratio", "rz_buy_intensity",                      # margin_detail + daily_basic/daily
    "block_premium", "block_intensity",                  # block_trade
    "lhb_inst_net",                                      # top_inst
    "cmf_20", "obv_mom_20", "price_vs_vwap_20", "breakout_vol_20",  # daily(多日量价序列)
]

# ── GBDT 特征(factor_lab.gbdt_features):8 组分位 g_* + 双侧都有的原始因子 + composite 锚定 ──
# 镜像 scripts/factor_lab.py 的 GBDT_GROUPS / GBDT_RAW。
_GBDT_GROUPS: list[str] = [
    "momentum", "fund_main", "fund_retail", "chip", "north", "tech", "value", "volprice",
]
_GBDT_GROUP_COLS: list[str] = [f"g_{g}" for g in _GBDT_GROUPS]  # _factor_groups 派生(scoring)

_GBDT_RAW: list[str] = [
    "pct_60d", "pct_ytd", "vol_ratio", "turnover",
    "winner_rate", "chip_concentration", "price_to_cost",
    "main_inflow_yi", "main_net_ratio", "retail_net_yi", "hk_ratio",
    "rsi6", "rsi12", "pe", "pb", "dv_ratio",
    "cmf_20", "obv_mom_20", "ma_bull", "above_ma60",
]


def _dedup(*lists: list[str]) -> list[str]:
    """拼接多份列清单,去重保序(首次出现的位置定序)。"""
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for c in lst:
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


# core = CANDIDATES ∪ g_* ∪ GBDT_RAW ∪ {composite}(去重保序;45 列)。
_CORE_COLS: list[str] = _dedup(
    _CANDIDATE_COLS, _GBDT_GROUP_COLS, _GBDT_RAW, ["composite"]
)

# ── 序列特征(kind="seq"):每股 SEQ_WINDOW 日滚动窗 × SEQ_FEATURES 个日级特征,展平为
# `{feat}_t{w}`(**时间主序**:w=0 最旧 … W-1 最新)→ 序列模型 reshape [N, W, K]。
# 日级特征从 lake daily 算:r=日收益、rng=日内振幅(高-低)/收、amt=每股窗内标准化对数成交额。
SEQ_FEATURES: list[str] = ["r", "rng", "amt"]
SEQ_WINDOW: int = 20
_SEQ_COLS: list[str] = [f"{f}_t{w}" for w in range(SEQ_WINDOW) for f in SEQ_FEATURES]

# 命名视图 registry(graph 留后续,见 spec §C 三态 DataHandler)。
FEATURE_SETS: dict[str, list[str]] = {
    "core": _CORE_COLS,
    "seq": _SEQ_COLS,
}

# 训练标签:T+1 开到开 rank-norm 口径(可交易、无前视),与 factor_lab.GBDT_LABEL 同。
LABEL: str = "fwd_1_oo"


def feature_columns(name: str = "core") -> list[str]:
    """返回某命名视图的列清单(副本,调用方改不到 registry)。未登记 → KeyError。"""
    try:
        return list(FEATURE_SETS[name])
    except KeyError:
        raise KeyError(
            f"unknown feature_set {name!r}: known = {sorted(FEATURE_SETS)}"
        ) from None
