#!/usr/bin/env python3
"""DataHandler —— 从 Parquet 湖物化横截面特征面板(取一次永不重取,零重复取数)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §B/§C。

**golden-parity-critical**:`materialize(...)` 的 "core" 特征帧必须与 `scripts/factor_lab.py`
`factor_frame()` 的输出**逐值相等**——E3 用它对拍新旧两条管道。故本类是 factor_frame +
load_price_pivots + forward_returns + _board_limit + _load_basic 的**平行实现**,公式逐行照搬,
**只换数据访问**:把 factor_lab 的 `pd.read_pickle(CACHE/<endpoint>/<day>.pkl)` 换成读 lake
parquet(`autoresearch.data.cache.lake_path` + pyarrow → DataFrame)。同一份原始数据 → 同一份特征值。

打分原语(_factor_groups / lens_momentum / _pct / _wsum)复用 autoresearch.common.scoring,
多日量价序列(cmf/obv/vwap/breakout)复用 autoresearch.common.vol_series——与 factor_lab 同口径。

输出:对 dates 逐成型日算横截面 → 纵向 concat 成面板,列含 date/code/<core 特征>/fwd_1_oo/buyable。
factor_lab 不改(它仍读 pkl,供 calibrate/train CLI);本类是新管道的 lake-reading 版本,**匹配**它。
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

import autoresearch.common.vol_series as vol_series
from autoresearch.common.scoring import (
    _factor_groups,
    _load_weights,
    _num,
    _pct,
    _wsum,
    composite_score,
    lens_momentum,
)
from autoresearch.data.cache import lake_path
from autoresearch.data.features import LABEL, feature_columns

# core 派生模型特征:8 组分位 g_*(取 _factor_groups 的这 8 组)与 composite 的成员组定义,
# 镜像 factor_lab.GBDT_GROUPS——保证 handler 物化的 g_* 与 factor_lab.gbdt_features 同口径。
_GBDT_GROUPS = ("momentum", "fund_main", "fund_retail", "chip", "north", "tech", "value", "volprice")


def _moneyflow_struct_cols(mf: pd.DataFrame) -> pd.DataFrame:
    """moneyflow → 主力净额(大+特大单)/ 散户净额(小单),单位亿。延迟拿 tushare_source 的实现。

    tushare_source 顶层 `from screen_market import ...`(scripts/ 桥),为不在 import 期拉起该桥,
    把取 _moneyflow_struct_cols 推迟到调用时;sources._ensure_scripts_on_path 先把 scripts/ 挂上
    sys.path。与 factor_lab 共用同一口径实现(非复制)。
    """
    from autoresearch.data.sources import _ensure_scripts_on_path
    _ensure_scripts_on_path()
    from autoresearch.data.tushare_source import _moneyflow_struct_cols as _impl
    return _impl(mf)


def _derive_model_features(fr: pd.DataFrame) -> pd.DataFrame:
    """给 factor_frame 帧补 core 的派生模型特征:g_*(8 组分位)+ composite(线性锚定分)。

    与 factor_lab 下游同口径:g_* = _factor_groups(fr) 取 GBDT_GROUPS 这 8 组(同 gbdt_features);
    composite = composite_score(fr, _load_weights())["composite"](同 train_gbdt 注入锚定特征)。
    这两步在 factor_lab 里发生在 factor_frame **之后**(gbdt_features / train_gbdt),故不在 parity
    对拍的 factor_frame 列内;handler 把它们一并物化,交付完整 FEATURE_SETS["core"]。
    """
    out = fr.copy()
    groups = _factor_groups(out)
    for k in _GBDT_GROUPS:
        out[f"g_{k}"] = groups[k].to_numpy()
    out["composite"] = composite_score(out, _load_weights())["composite"].to_numpy()
    return out

# factor_lab 缺一日 daily_basic / 横截面 < 此数 → 跳过该成型日(与 factor_frame 同阈值)。
_MIN_CROSS_SECTION = 300


def _code6(s: pd.Series) -> pd.Series:
    return s.astype(str).str.split(".").str[0].str.zfill(6)


def _read_lake(endpoint: str, date: str) -> pd.DataFrame:
    """读 lake 里某 (endpoint, 交易日) 的 parquet → DataFrame;文件不存在 → 空帧。

    date = 紧凑串 YYYYMMDD(lake 按交易日 key,见 endpoints/migrate_cache);等价于 factor_lab
    的 `pd.read_pickle(CACHE/endpoint/<day>.pkl)`,只是底层格式 pkl→parquet。
    """
    path = lake_path(endpoint, {"trade_date": date})
    if not path.exists():
        return pd.DataFrame()
    return pq.read_table(path).to_pandas()


def _board_limit(code: str) -> float:
    """涨跌停幅度(%):科创(688)/创业板(30)=20;北交所(8/4/920)=30;其余主板=10。"""
    if code.startswith("688") or code.startswith("30"):
        return 20.0
    if code.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


class DataHandler:
    """统一数据层句柄:从 lake 物化特征面板。`materialize` 是 factor_lab.factor_frame 的湖版镜像。"""

    def load_basic(self) -> pd.DataFrame:
        """证券基础信息(name/list_date/market/industry)——lake stock_basic/static.parquet。

        镜像 factor_lab._load_basic(只换 read_pickle→parquet)。
        """
        path = lake_path("stock_basic", {}, today=None)  # static key
        if not path.exists():
            return pd.DataFrame(columns=["code", "name", "list_date", "market", "industry"])
        df = pq.read_table(path).to_pandas()
        return pd.DataFrame({
            "code": _code6(df["ts_code"]), "name": df["name"].astype(str),
            "list_date": df["list_date"].astype(str),
            "market": df.get("market", pd.Series(["主板"] * len(df))).astype(str),
            "industry": df.get("industry", pd.Series(["未分类"] * len(df))).astype(str),
        })

    def load_price_pivots(self, price_dates: list[str]) -> dict[str, pd.DataFrame]:
        """把 lake 的 daily(逐日)拼成 {字段: pivot[code × date]}。镜像 factor_lab.load_price_pivots。"""
        frames = []
        for d in price_dates:
            df = _read_lake("daily", d)
            if df.empty:
                continue
            df = pd.DataFrame({
                "code": _code6(df["ts_code"]),
                "date": d,
                "open": _num(df["open"]), "high": _num(df["high"]),
                "low": _num(df["low"]), "close": _num(df["close"]),
                "pct_chg": _num(df["pct_chg"]), "amount": _num(df["amount"]),
            })
            frames.append(df)
        long = pd.concat(frames, ignore_index=True)
        return {f: long.pivot_table(index="code", columns="date", values=f) for f in
                ("open", "high", "low", "close", "pct_chg", "amount")}

    def forward_returns(self, piv: dict, price_dates: list[str], D: str, fwd: int) -> pd.DataFrame:
        """D 的前瞻收益(D+1 开盘进)+ D+1 一字涨停标记。镜像 factor_lab.forward_returns。"""
        P = price_dates
        idx = P.index(D)
        c, o, h = piv["close"], piv["open"], piv["high"]
        pc = piv["pct_chg"]
        codes = c.index
        res = pd.DataFrame(index=codes)
        cD = c[D]

        def col(piv_, k):
            j = idx + k
            return piv_[P[j]] if 0 <= j < len(P) else pd.Series(np.nan, index=codes)

        o1 = col(o, 1)
        res["fwd_1_cc"] = col(c, 1) / cD - 1.0
        res["fwd_1_oo"] = col(o, 2) / o1 - 1.0
        res["fwd_5_oc"] = col(c, 5) / o1 - 1.0
        res["fwd_10_oc"] = col(c, min(10, fwd)) / o1 - 1.0
        pc1, o1h, c1, h1 = col(pc, 1), o1, col(c, 1), col(h, 1)
        lim = pd.Series([_board_limit(x) for x in codes], index=codes)
        sealed = (pc1 >= lim * 0.98) & (c1 >= h1 - 1e-6) & (o1h >= h1 - 1e-6)
        res["buyable"] = ~sealed.fillna(False)
        return res

    def factor_frame(self, D: str, piv: dict, price_dates: list[str], basic: pd.DataFrame,
                     cap_floor: float, fwd: int) -> pd.DataFrame | None:
        """组装 D 的横截面:canonical 因子 + tushare 增强 + 真·动量透镜分 + 前瞻收益 + 门。

        逐行镜像 factor_lab.factor_frame——唯一区别:`pd.read_pickle(CACHE/...)` → `_read_lake(...)`。
        """
        P = price_dates
        db = _read_lake("daily_basic", D)
        if db.empty:
            return None
        f = pd.DataFrame({
            "code": _code6(db["ts_code"]),
            "close": _num(db["close"]),
            "turnover": _num(db["turnover_rate"]),
            "vol_ratio": _num(db["volume_ratio"]),
            "pe": _num(db["pe_ttm"]),
            "pb": _num(db["pb"]),
            "dv_ratio": _num(db["dv_ratio"]),
            "mktcap_yi": _num(db["total_mv"]) / 1e4,
            "circ_mv": _num(db["circ_mv"]),
        })
        # 技术因子
        sf = _read_lake("stk_factor_pro", D)
        if not sf.empty:
            cc = _num(sf["close"])
            m5, m10, m20, m60 = (_num(sf[f"ma_qfq_{n}"]) for n in (5, 10, 20, 60))
            tech = pd.DataFrame({
                "code": _code6(sf["ts_code"]),
                "ma_bull": ((m5 > m10) & (m10 > m20) & (m20 > m60)).astype(float),
                "above_ma60": (cc > m60).astype(float),
                "rsi6": _num(sf["rsi_qfq_6"]),
                "rsi12": _num(sf["rsi_qfq_12"]),
                "macd": _num(sf["macd_qfq"]),
            })
            f = f.merge(tech, on="code", how="left")
        # 筹码
        cy = _read_lake("cyq_perf", D)
        if not cy.empty:
            c50 = _num(cy["cost_50pct"])
            chip = pd.DataFrame({
                "code": _code6(cy["ts_code"]),
                "winner_rate": _num(cy["winner_rate"]),
                "cost_50pct": c50,
                "chip_concentration": (_num(cy["cost_85pct"]) - _num(cy["cost_15pct"])) / c50,
            })
            f = f.merge(chip, on="code", how="left")
            f["cost_premium"] = f["close"] / f["cost_50pct"] - 1.0
            f["price_to_cost"] = f["close"] / f["cost_50pct"]
        # 资金
        mf = _read_lake("moneyflow", D)
        if not mf.empty:
            flow = _moneyflow_struct_cols(mf)
            flow["main_inflow_yi"] = _num(mf["net_mf_amount"]) / 1e4
            f = f.merge(flow, on="code", how="left")
        else:
            for c in ("main_net_yi", "retail_net_yi", "main_inflow_yi"):
                f[c] = np.nan
        amt_yi = piv["amount"][D].reindex(f["code"]).to_numpy() / 1e5
        f["main_net_ratio"] = f["main_net_yi"] / pd.Series(np.where(amt_yi > 0, amt_yi, np.nan), index=f.index)
        f["inflow_to_cap"] = f["main_inflow_yi"] / f["mktcap_yi"]
        # 北向持股占比
        hk = _read_lake("hk_hold", D)
        if not hk.empty:
            f = f.merge(pd.DataFrame({"code": _code6(hk["ts_code"]), "hk_ratio": _num(hk["ratio"])}),
                        on="code", how="left")

        # ── UZI 增量因子:融资融券 / 大宗交易 / 龙虎榜机构席位 ──
        amt_yuan = piv["amount"][D].reindex(f["code"]).to_numpy() * 1e3
        amt_pos = np.where(amt_yuan > 0, amt_yuan, np.nan)
        circ_pos = (f["circ_mv"] * 1e4).replace(0, np.nan)
        mg = _read_lake("margin_detail", D)
        if not mg.empty:
            mg2 = pd.DataFrame({"code": _code6(mg["ts_code"]),
                                "rzye": _num(mg["rzye"]), "rzmre": _num(mg["rzmre"])})
            f = f.merge(mg2, on="code", how="left")
            f["rz_ratio"] = f["rzye"] / circ_pos
            f["rz_buy_intensity"] = f["rzmre"] / amt_pos
        blk = _read_lake("block_trade", D)
        if not blk.empty:
            b = blk.assign(code=_code6(blk["ts_code"]))
            g = b.groupby("code").agg(blk_amt=("amount", "sum"), blk_px=("price", "mean")).reset_index()
            f = f.merge(g, on="code", how="left")
            f["block_premium"] = f["blk_px"] / f["close"] - 1.0
            f["block_intensity"] = f["blk_amt"] / f["circ_mv"]
        ti = _read_lake("top_inst", D)
        if not ti.empty:
            t = ti.assign(code=_code6(ti["ts_code"]))
            inst = t[t["exalter"].astype(str).str.contains("机构专用", na=False)]
            if len(inst):
                gi = inst.groupby("code")["net_buy"].sum().reset_index()
                gi.columns = ["code", "inst_net"]
                f = f.merge(gi, on="code", how="left")
                f["lhb_inst_net"] = _num(f["inst_net"]) / amt_pos

        # 动量(从价格面板算 pct_*)
        close_piv = piv["close"]
        idx = P.index(D)
        cD = close_piv[D].reindex(f["code"]).values

        def lag_ret(k):
            j = idx - k
            if j < 0:
                return np.full(len(f), np.nan)
            return cD / close_piv[P[j]].reindex(f["code"]).values - 1.0

        f["pct_5d"] = lag_ret(5) * 100
        f["pct_20d"] = lag_ret(20) * 100
        f["pct_60d"] = lag_ret(60) * 100
        ys = next((d for d in P[:idx + 1] if d[:4] == D[:4]), P[0])
        f["pct_ytd"] = (cD / close_piv[ys].reindex(f["code"]).values - 1.0) * 100

        # 名称/板块/次新/ST
        f = f.merge(basic, on="code", how="left")
        f["is_st"] = f["name"].fillna("").str.contains("ST", case=False) | f["name"].fillna("").str.contains("退")

        # 硬门(与 screen_market 一致)
        amtD = piv["amount"][D].reindex(f["code"]).values
        keep = (~f["is_st"]) & (f["mktcap_yi"] >= cap_floor) & (np.nan_to_num(amtD) > 0)
        keep &= ~f["code"].str.match(r"^(8|4|920)")
        ld = pd.to_numeric(f["list_date"], errors="coerce")
        d60 = P[max(0, idx - 60)]
        keep &= ~(ld > int(d60))
        f = f[keep].reset_index(drop=True)
        if len(f) < _MIN_CROSS_SECTION:
            return None

        # 真·动量透镜分(lens_momentum)
        try:
            lm = lens_momentum(f)
            f["momentum_score"] = lm["momentum_score"]
            f["momentum_gate"] = lm["momentum_gate"]
        except Exception as e:  # noqa: BLE001
            print(f"[warn] lens_momentum({D}) 失败: {e!r}", file=sys.stderr)

        # 动量变体
        if {"above_ma60", "ma_bull"} <= set(f.columns):
            rs = 0.6 * _pct(f["pct_60d"]) + 0.4 * _pct(f["pct_ytd"])
            trend = 0.5 * _num(f["above_ma60"]).fillna(0.0) + 0.5 * _num(f["ma_bull"]).fillna(0.0)
            overheat = _pct(f["pct_60d"]) > 0.95
            if "rsi6" in f.columns:
                overheat = overheat | (_num(f["rsi6"]) > 85)

            def _pen(s):
                return (s - overheat.astype(float) * 15).clip(lower=0)

            inflow = _pct(f["main_inflow_yi"])
            f["mom_v2_noVol"] = _pen(_wsum({"rs": (rs, 40), "inflow": (inflow, 30), "trend": (trend, 30)}))
            f["mom_v3_capnorm"] = _pen(_wsum({"rs": (rs, 40), "inflow": (_pct(f["inflow_to_cap"]), 30), "trend": (trend, 30)}))
            f["mom_v4_rsHeavy"] = _pen(_wsum({"rs": (rs, 50), "trend": (trend, 30), "inflow": (inflow, 20)}))

        # 多日量价序列因子(OBV/CMF/VWAP偏离/量价突破)
        win = P[max(0, idx - 19):idx + 1]
        if len(win) >= 10:
            H, L, C, A = piv["high"], piv["low"], piv["close"], piv["amount"]
            f["cmf_20"] = vol_series.cmf(H, L, C, A, win).reindex(f["code"]).to_numpy()
            f["obv_mom_20"] = vol_series.obv_momentum(C, A, win).reindex(f["code"]).to_numpy()
            f["price_vs_vwap_20"] = vol_series.price_vs_vwap(H, L, C, A, win).reindex(f["code"]).to_numpy()
            f["breakout_vol_20"] = vol_series.breakout_on_volume(C, A, win).reindex(f["code"]).to_numpy()

        # 前瞻收益
        fr = self.forward_returns(piv, P, D, fwd)
        f = f.merge(fr, left_on="code", right_index=True, how="left")
        f["date"] = D
        return f

    def materialize(self, dates: list[str], feature_set: str = "core", kind: str = "core",
                    cap_floor: float = 30.0, *, price_dates: list[str] | None = None,
                    fwd: int = 10) -> pd.DataFrame:
        """物化 dates 的横截面特征面板(纵向 concat)→ columns: date/code/<core 特征>/fwd_1_oo/buyable。

        从 lake 读 daily 价格面板(price_dates 缺省=用 daily 端点下所有可见交易日)+ stock_basic,
        对每个成型日跑 factor_frame(湖版),再裁到 feature_columns(feature_set) 列(+ key/标签/门)。
        与 factor_lab.factor_frame 同公式、同口径 → 同特征值。
        """
        if kind != "core":
            raise NotImplementedError(f"DataHandler.materialize kind={kind!r} 未实现(Phase 1 仅 core)")
        price_dates = price_dates if price_dates is not None else self._discover_price_dates()
        piv = self.load_price_pivots(price_dates)
        basic = self.load_basic()
        cols = feature_columns(feature_set)
        keep_extra = [LABEL, "buyable"]
        frames = []
        for D in dates:
            fr = self.factor_frame(D, piv, price_dates, basic, cap_floor, fwd)
            if fr is None:
                continue
            fr = _derive_model_features(fr)     # g_*(_factor_groups)+ composite(线性锚定),补齐 core 派生列
            for c in cols:                      # 该成型日缺某列(如稀疏 UZI 因子)→ 补 NaN,列齐
                if c not in fr.columns:
                    fr[c] = np.nan
            ordered = ["date", "code", *cols, *keep_extra]
            frames.append(fr[[c for c in ordered if c in fr.columns]])
        if not frames:
            return pd.DataFrame(columns=["date", "code", *cols, *keep_extra])
        return pd.concat(frames, ignore_index=True)

    def _discover_price_dates(self) -> list[str]:
        """lake daily/ 下所有 <date>.parquet 的交易日(升序)——materialize 缺省价格面板。"""
        from autoresearch.data import cache
        ddir = cache.LAKE / "daily"
        if not ddir.exists():
            return []
        return sorted(p.stem for p in ddir.glob("*.parquet"))
