#!/usr/bin/env python3
"""衍生品领先性普查 —— 期权/期货信息对**次日**市场级结果有没有超出 breadth+动量基线的领先性
(确定性,零 LLM;只读湖 + 自建衍生品缓存;**不回注、不接线、不改生产代码**)。

预注册(假设 / 判读规则 / 口径,**先写后看**):`docs/research/2026-08-24-derivatives-lead-census.md`
§0 —— 那一节的 commit 早于本仪器的任何读数。设计稿:
`docs/specs/2026-08-24-derivatives-lead-census-design.md`。

立案:`autoresearch/derivatives/`(2026-08-04 建成)至今**零生产调用方、磁盘零数据**,
08-03 设计稿 §2.2 写明的「最小证伪步」从未执行。而 08-24 探针证明 `opt_daily` 与 QVIX
都有可回补的历史 → 证伪不必等日积月累。本仪器把四族信号放进同一张表、同一把尺、同一个
基线,一次看完;读数全负则这条线不必接。

四族(§0.1):
  A · QVIX     —— 水平分位 / 单日急升 / VRP(隐含方差 − 已实现方差)
  B · PCR      —— 期权成交量/持仓 put-call 比(**对冲压力,不是方向观点**)
  C2 · 基差    —— 股指期货主力年化基差、IM−IF 风格温差
  D · 到期日历 —— 到期日 / 到期周 / 到期次日

主判读尺 = 信号自己标的指数的 close(T+1) → open(T+2)(与产品主尺 `gap_c1_o2` 同窗:
信号 T 盘后可知 → T 晚决策 → T+1 收盘买 → T+2 开盘卖)。

用法:
  uv run --no-sync python -m autoresearch.research.derivatives_census --backfill   # 一次性回补
  uv run --no-sync python -m autoresearch.research.derivatives_census              # 读缓存出表
→ `reports_<engine>/research/derivatives_census.md` + 同目录 `_derivatives_census.json`。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import stats as st, workspace as ws
from autoresearch.derivatives import options_lake as ol

SCHEMA_VERSION = 1
RULE_VERSION = "derivatives_census.v1"

# ───────────────────────── §0 预注册常量(跑前锁死,不许看完读数再调)─────────────────────────

PCTILE_WIN, PCTILE_MIN = 250, 120     # 滚动窗 / 最少历史(窗含当日、不含任何未来日)
HI_Q, LO_Q = 0.80, 0.20               # H 档 / L 档切点
NW_LAG = 5                            # Newey-West Bartlett 滞后
POS_T = 2.0                           # 正证据 t 门槛
MIN_BUCKET_N = 100                    # H/L 两档各自最小样本
MIN_FLAG_N = 20                       # D 族旗日最小样本
JUDGE_START = "20220302"              # 主判读窗起点 = lake/daily 起点(breadth 基线可得)
GAP_CLIP = 0.08                       # 指数 target 极值(个股 31% 板阈不适用于指数)
RV_WIN = 20                           # 已实现方差窗
MOM_WIN = 20                          # 基线动量窗
TAIL_THRESH = -0.015                  # A2 尾部事件:标的指数 T+1 收收 ≤ −1.5%
MIN_VALID_RUN = 60                    # QVIX 有效起点:连续有效交易日
BASIS_MIN_DAYS = 7                    # 剩余到期天数 <7 弃(避换月毛刺)
PCR_MIN_DAYS = 500                    # 品种入表的最少有效日(数据质量门,非挑选)
BACKFILL_START = "20210101"           # 回补起点 = 判读窗 − 250 日暖机
TRADING_DAYS = 252.0

# 判读三条(§0.3)全部成立才算正证据;「显著负」不适用于可翻符号的信号,见 render 脚注。
POS, UNPROVEN, THIN = "正证据", "未证", "样本不足"

# ───────────────────────── 标的映射(2026-08-24 实测字段)─────────────────────────

# akshare QVIX 序列 → 标的指数。**两条指数 QVIX(300index/1000index)不在此表**:
# 它们末行关键列全 NaN(§0.5 已记账,整条弃);中证1000 的暴露改由 C2 的 IM 基差承接。
QVIX_SERIES = {
    "50etf": ("index_option_50etf_qvix", "000016.SH"),
    "300etf": ("index_option_300etf_qvix", "000300.SH"),
    "500etf": ("index_option_500etf_qvix", "000905.SH"),
    "cyb": ("index_option_cyb_qvix", "399006.SZ"),
    "kcb": ("index_option_kcb_qvix", "000688.SH"),
    "100etf": ("index_option_100etf_qvix", "399330.SZ"),
}
DEAD_QVIX = ("index_option_300index_qvix", "index_option_1000index_qvix")

# `opt_basic.opt_code` → 标的指数(2026-08-24 实测:SSE 5 个 / SZSE 4 个 / CFFEX 3 个)
OPT_UNDERLYING = {
    "OP510050.SH": "000016.SH", "OP510300.SH": "000300.SH", "OP510500.SH": "000905.SH",
    "OP588000.SH": "000688.SH", "OP588080.SH": "000688.SH",
    "OP159901.SZ": "399330.SZ", "OP159915.SZ": "399006.SZ",
    "OP159919.SZ": "000300.SH", "OP159922.SZ": "000905.SH",
    "OP000016.SH": "000016.SH", "OP000300.SH": "000300.SH", "OP000852.SH": "000852.SH",
}
FUT_UNDERLYING = {"IF": "000300.SH", "IH": "000016.SH", "IC": "000905.SH", "IM": "000852.SH"}
BENCH_INDEXES = ("000300.SH", "000852.SH")     # D 族与公共观察列用的两个基准
INDEX_CODES = tuple(sorted({QVIX_SERIES[k][1] for k in QVIX_SERIES}
                           | set(OPT_UNDERLYING.values()) | set(FUT_UNDERLYING.values())))

# `fut_daily` 里混着 `IF.CFX` / `IFL1.CFX` 这类**合成连续合约**,它们的 oi 是整族加总 ——
# 不滤掉,「当日最大 OI = 主力」会永远选中那个合成行,而结果看起来完全正常。
_REAL_FUT = re.compile(r"^(IF|IH|IC|IM)(\d{4})\.CFX$")

CALENDAR_INDEX = "000300.SH"          # 交易日轴(A 股各指数共用同一日历)
EXCHANGES = ("SSE", "SZSE", "CFFEX")


def deriv_root() -> Path:
    return ws.lake_root() / "derivatives"


class CensusError(RuntimeError):
    """口径/覆盖违约 —— 宁可无读数,不要一个算错的领先性。"""


# ───────────────────────── 纯函数:统计原语(零网络、可单测)─────────────────────────


def nw_ols(y, X, lag: int = NW_LAG) -> dict:
    """OLS + Newey-West(Bartlett 核)标准误 → `{coef, se, t, n, k}`。

    为什么必须 NW:日频市场信号高度自相关,OLS 裸标准误会把 t 值系统性放大 ——
    一个自相关的噪声也能刷出 |t|>3。lag=0 即退化成裸 OLS(变异探针用它验红)。
    """
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X[:, None]
    n, k = X.shape
    if n <= k:
        return {"coef": [None] * k, "se": [None] * k, "t": [None] * k, "n": int(n), "k": k}
    xtx_inv = np.linalg.pinv(X.T @ X)
    beta = xtx_inv @ (X.T @ y)
    u = y - X @ beta
    xu = X * u[:, None]
    S = xu.T @ xu
    for j in range(1, min(lag, n - 1) + 1):
        w = 1.0 - j / (lag + 1.0)
        G = xu[j:].T @ xu[:-j]
        S += w * (G + G.T)
    V = xtx_inv @ S @ xtx_inv
    se = np.sqrt(np.maximum(np.diag(V), 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, beta / se, np.nan)
    return {"coef": [float(b) for b in beta], "se": [float(s) for s in se],
            "t": [None if not np.isfinite(v) else float(v) for v in t], "n": int(n), "k": k}


def t_pvalue(t: float | None, df: int) -> float | None:
    """双侧 t 检验 p 值 —— 走项目自带的 `betainc`(本仓不依赖 scipy)。"""
    if t is None or df <= 0 or not np.isfinite(t):
        return None
    return float(st.betainc(df / 2.0, 0.5, df / (df + float(t) ** 2)))


def fisher_greater(a: int, b: int, c: int, d: int) -> float | None:
    """2×2 单侧(greater)Fisher 精确 p —— 「H 档之后尾部事件更多」是有方向的假设。

    表格 `[[a, b], [c, d]]` = [[旗∧事件, 旗∧无事件], [非旗∧事件, 非旗∧无事件]]。
    """
    n = a + b + c + d
    if n <= 0 or min(a, b, c, d) < 0:
        return None
    row1, col1 = a + b, a + c

    def logc(nn: int, kk: int) -> float:
        return math.lgamma(nn + 1) - math.lgamma(kk + 1) - math.lgamma(nn - kk + 1)

    denom = logc(n, col1)
    total = 0.0
    for i in range(a, min(row1, col1) + 1):
        if col1 - i > n - row1:
            continue
        total += math.exp(logc(row1, i) + logc(n - row1, col1 - i) - denom)
    return float(min(max(total, 0.0), 1.0))


def rolling_pctile(s: pd.Series, win: int = PCTILE_WIN, min_periods: int = PCTILE_MIN) -> pd.Series:
    """滚动分位:**窗含当日、不含任何未来日**;比较基准是窗内**它之前**的值。

    与 `qvix.percentile_of_latest` 同口径(那里也用 `history = series[:-1]`)。手写而不用
    `rolling().rank()`:分位的「不含未来」是本仪器最容易被悄悄破坏的性质,写成一眼可读的
    表达式,变异探针才咬得住。
    """
    return s.rolling(win, min_periods=min_periods).apply(
        lambda w: float((w[:-1] < w[-1]).mean()), raw=True)


def rolling_z(s: pd.Series, win: int = PCTILE_WIN, min_periods: int = PCTILE_MIN) -> pd.Series:
    """滚动 z(同窗、同样不含未来日)—— §0.3 基线回归里的 `signal_z`。"""
    mu = s.rolling(win, min_periods=min_periods).mean()
    sd = s.rolling(win, min_periods=min_periods).std()
    return (s - mu) / sd.where(sd > 0)


def realized_var(logret: pd.Series, win: int = RV_WIN) -> pd.Series:
    """已实现方差(年化)—— VRP 的减项。"""
    return logret.rolling(win).var() * TRADING_DAYS


def vrp(qvix_close: pd.Series, rv: pd.Series) -> pd.Series:
    """VRP = 隐含方差 − 已实现方差。QVIX 以百分点计(18.5 = 18.5%)→ 先 /100 再平方。"""
    return (qvix_close / 100.0) ** 2 - rv


def annualized_basis(settle: float, spot: float, days_left: float) -> float | None:
    """年化基差 = (期货结算/现货 − 1) × 365 / 剩余自然日。

    **剩余 <7 日返回 None**:临交割那几天分母趋零,年化值会炸成一个巨大的假信号,
    而它每个月准时出现一次。
    """
    if not all(np.isfinite([settle, spot, days_left])) or spot <= 0 or days_left < BASIS_MIN_DAYS:
        return None
    return float((settle / spot - 1.0) * 365.0 / days_left)


def valid_start(frame: pd.DataFrame, *, min_run: int = MIN_VALID_RUN,
                value_column: str = "close", date_column: str = "date") -> str | None:
    """首个连续 ≥`min_run` 个交易日全有效的段的起点 —— **接口自报的日期轴不可信**。

    有效 = 非空 **且 > 0**:akshare 把 8 条 QVIX 序列统一回填到 2015-02-09(300ETF 期权
    2019-12 才上市),回填行是 NaN;而 1000 指数序列里还夹着 34 个 0 —— **0 不是波动率**。
    """
    if frame is None or not len(frame) or value_column not in frame.columns:
        return None
    v = pd.to_numeric(frame[value_column], errors="coerce")
    ok = (v.notna() & (v > 0)).to_numpy()
    dates = frame[date_column].astype(str).to_numpy()
    run = 0
    for i in range(len(ok)):
        if ok[i]:
            run += 1
            if run >= min_run:
                return str(dates[i - run + 1])
        else:
            run = 0
    return None


def expiry_flags(expiry_dates, trade_days) -> pd.DataFrame:
    """到期日历三旗:到期日 / 到期周(同 ISO 周内 ≤ 到期日)/ 到期次日。

    到期日直接取 `opt_basic.maturity_date` ∩ 交易日 —— **不自己算「第四个周三 / 第三个
    周五」**:那套规则遇节假日要顺延,自己算等于把交易所日历重实现一遍,而且算错了不会
    报错,只会让读数悄悄偏。
    """
    td = [str(d) for d in trade_days]
    idx = {d: i for i, d in enumerate(td)}
    day = np.zeros(len(td), dtype=bool)
    week = np.zeros(len(td), dtype=bool)
    post = np.zeros(len(td), dtype=bool)
    iso = [pd.Timestamp(d).isocalendar() for d in td]
    for e in sorted({str(x) for x in expiry_dates}):
        i = idx.get(e)
        if i is None:
            continue
        day[i] = True
        if i + 1 < len(td):
            post[i + 1] = True
        j = i
        while j >= 0 and (iso[j].year, iso[j].week) == (iso[i].year, iso[i].week):
            week[j] = True
            j -= 1
    return pd.DataFrame({"date": td, "expiry_day": day, "expiry_week": week,
                         "post_expiry": post})


def bucket_hl(signal: pd.Series, target: pd.Series, *, since: str | None = None,
              win: int = PCTILE_WIN, min_periods: int = PCTILE_MIN,
              lag: int = NW_LAG) -> dict:
    """H/L 档条件均值差 + NW t。档位切点走**滚动**分位(walk-forward,无全样本前视)。

    `since` 只截断**判读**行,不截断分位的暖机历史 —— 否则判读窗头 250 天全是 NaN 分位,
    等于把窗口起点又往后推了一年。

    H−L 的 t 由「target ~ 1 + is_H」在 H∪L 子样本上的 NW 回归给出 —— 与基线增量用同一个
    回归核,两处口径不会各写一遍再对不上。
    """
    s = signal.dropna()
    if not len(s):
        return {"n_h": 0, "n_l": 0, "diff": None, "t": None, "p": None,
                "mean_h": None, "mean_l": None}
    pct = rolling_pctile(s, win=win, min_periods=min_periods)
    df = pd.DataFrame({"s": s, "pct": pct, "y": target.reindex(s.index)}).dropna()
    if since is not None:
        df = df[df.index.astype(str) >= str(since)]
    if not len(df):
        return {"n_h": 0, "n_l": 0, "diff": None, "t": None, "p": None,
                "mean_h": None, "mean_l": None}
    hi, lo = df["pct"] >= HI_Q, df["pct"] <= LO_Q
    sub = df[hi | lo].copy()
    sub["is_h"] = hi[hi | lo].astype(float).to_numpy()
    n_h, n_l = int(sub["is_h"].sum()), int(len(sub) - sub["is_h"].sum())
    if n_h == 0 or n_l == 0:
        return {"n_h": n_h, "n_l": n_l, "diff": None, "t": None, "p": None,
                "mean_h": None, "mean_l": None}
    X = np.column_stack([np.ones(len(sub)), sub["is_h"].to_numpy()])
    res = nw_ols(sub["y"].to_numpy(), X, lag=lag)
    t = res["t"][1]
    return {
        "n_h": n_h, "n_l": n_l,
        "mean_h": float(sub.loc[sub["is_h"] > 0, "y"].mean()),
        "mean_l": float(sub.loc[sub["is_h"] == 0, "y"].mean()),
        "diff": res["coef"][1], "t": t,
        "p": t_pvalue(t, res["n"] - res["k"]),
    }


def baseline_increment(target: pd.Series, signal_z: pd.Series, breadth: pd.Series,
                       mom: pd.Series, *, lag: int = NW_LAG) -> dict:
    """`target ~ signal_z + breadth_T + idx_mom20_T` 的 signal 系数与 NW t(§0.3 第 3 条)。

    这一条才是「领先性」与「同期相关」的分界:被 breadth/动量解释掉的部分不算增量。
    """
    df = pd.DataFrame({"y": target, "z": signal_z, "b": breadth, "m": mom}).dropna()
    if len(df) < 30:
        return {"n": int(len(df)), "coef": None, "t": None, "p": None}
    X = np.column_stack([np.ones(len(df)), df["z"].to_numpy(), df["b"].to_numpy(),
                         df["m"].to_numpy()])
    res = nw_ols(df["y"].to_numpy(), X, lag=lag)
    t = res["t"][1]
    return {"n": res["n"], "coef": res["coef"][1], "t": t,
            "p": t_pvalue(t, res["n"] - res["k"])}


def tail_lift(flag: pd.Series, tail: pd.Series) -> dict:
    """旗日之后尾部事件的条件概率 vs 无条件基率 + 单侧 Fisher p(§0.2 A2 专测)。"""
    df = pd.DataFrame({"f": flag, "t": tail}).dropna()
    if not len(df):
        return {"n_flag": 0, "p_flag": None, "p_base": None, "lift": None, "fisher_p": None}
    f = df["f"].astype(bool)
    tl = df["t"].astype(bool)
    a, b = int((f & tl).sum()), int((f & ~tl).sum())
    c, d = int((~f & tl).sum()), int((~f & ~tl).sum())
    p_flag = a / (a + b) if (a + b) else None
    p_base = (a + c) / len(df) if len(df) else None
    return {
        "n_flag": a + b, "n_total": int(len(df)),
        "p_flag": p_flag, "p_base": p_base,
        "lift": (p_flag / p_base) if (p_flag is not None and p_base) else None,
        "fisher_p": fisher_greater(a, b, c, d),
    }


def verdict(hl: dict, inc: dict) -> str:
    """§0.3 判读:三条**同时**成立 = 正证据;任一档样本不足 = 样本不足;其余 = 未证。

    「显著负」在本普查不适用 —— 信号的符号可以自由翻转(预测跌与预测涨同样是信息),
    故只分「有没有领先性」。这是 §0.3 措辞的显式澄清,不是门槛的放松:三条判据一字未动。
    """
    if hl["n_h"] < MIN_BUCKET_N or hl["n_l"] < MIN_BUCKET_N:
        return THIN
    t_hl, t_inc = hl.get("t"), inc.get("t")
    if t_hl is None or t_inc is None:
        return UNPROVEN
    same_sign = (t_hl > 0) == (t_inc > 0)
    if abs(t_hl) >= POS_T and abs(t_inc) >= POS_T and same_sign:
        return POS
    return UNPROVEN


def flag_verdict(hl: dict, inc: dict) -> str:
    """D 族:旗日 vs 非旗日,旗日门槛 `MIN_FLAG_N`(§0.3 括号)。"""
    if hl["n_h"] < MIN_FLAG_N or hl["n_l"] < MIN_FLAG_N:
        return THIN
    t_hl, t_inc = hl.get("t"), inc.get("t")
    if t_hl is None or t_inc is None:
        return UNPROVEN
    if abs(t_hl) >= POS_T and abs(t_inc) >= POS_T and (t_hl > 0) == (t_inc > 0):
        return POS
    return UNPROVEN


def flag_hl(flag: pd.Series, target: pd.Series, *, lag: int = NW_LAG) -> dict:
    """旗日 vs 非旗日的均值差 + NW t(D 族不做滚动分位——旗是二元的,没有分位可言)。"""
    df = pd.DataFrame({"f": flag, "y": target}).dropna()
    if not len(df):
        return {"n_h": 0, "n_l": 0, "diff": None, "t": None, "p": None,
                "mean_h": None, "mean_l": None}
    is_f = df["f"].astype(bool).astype(float).to_numpy()
    n_h, n_l = int(is_f.sum()), int(len(df) - is_f.sum())
    if n_h == 0 or n_l == 0:
        return {"n_h": n_h, "n_l": n_l, "diff": None, "t": None, "p": None,
                "mean_h": None, "mean_l": None}
    X = np.column_stack([np.ones(len(df)), is_f])
    res = nw_ols(df["y"].to_numpy(), X, lag=lag)
    t = res["t"][1]
    return {"n_h": n_h, "n_l": n_l,
            "mean_h": float(df["y"].to_numpy()[is_f > 0].mean()),
            "mean_l": float(df["y"].to_numpy()[is_f == 0].mean()),
            "diff": res["coef"][1], "t": t, "p": t_pvalue(t, res["n"] - res["k"])}


# ───────────────────────── 缓存层(`--backfill`;唯一联网的部分)─────────────────────────


def _pro():
    from autoresearch.data.tushare_source import _pro as src

    return src()


def _call(fn):
    from autoresearch.data.tushare_source import _ts_call as src

    return src(fn)


def _save(df: pd.DataFrame, path: Path) -> None:
    """**全列落盘,不传 fields** —— lake 窄表毒化判例:cache key 不含 fields 时,
    一次少列的写入会让后续所有读到那个分区的人静默拿到整组 NaN。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _load(path: Path) -> pd.DataFrame | None:
    return pd.read_parquet(path) if path.exists() else None


def _ymd(s) -> str:
    return str(s).replace("-", "")[:8]


def backfill_index_daily(pro, codes=INDEX_CODES) -> dict:
    """现货指数整段(target + 基差的现货腿 + 交易日轴)。空帧 = 拉取失败,不落盘。"""
    out = {}
    for code in codes:
        df = _call(lambda c=code: pro.index_daily(ts_code=c, start_date="20150101",
                                                  end_date="20301231"))
        if df is None or not len(df):
            raise CensusError(f"index_daily({code}) 空 —— 指数腿缺了,target 无从算起")
        df = df.sort_values("trade_date").reset_index(drop=True)
        _save(df, deriv_root() / "index_daily" / f"{code}.parquet")
        out[code] = int(len(df))
        time.sleep(0.15)
    return out


def trade_days(since: str | None = None) -> list[str]:
    """交易日轴 = 缓存的沪深300 日线(A 股各指数共用同一日历;不另拉交易日历端点)。"""
    df = _load(deriv_root() / "index_daily" / f"{CALENDAR_INDEX}.parquet")
    if df is None or not len(df):
        raise CensusError("交易日轴缺失 —— 先跑 `--backfill`")
    days = sorted(_ymd(d) for d in df["trade_date"])
    return [d for d in days if since is None or d >= _ymd(since)]


def backfill_qvix() -> dict:
    """8 条 QVIX(6 条登记 + 2 条已知死序列)—— 死的也拉、也落盘、也记账,
    这样「它是死的」是本次跑出来的证据,不是转述 08-04 的先验。"""
    import akshare as ak

    from autoresearch.derivatives import qvix as qv

    report = {}
    for name, (fn_name, target) in QVIX_SERIES.items():
        fn = getattr(ak, fn_name, None)
        if fn is None:
            report[name] = {"status": "NO_FUNC", "rows": 0}
            continue
        df = fn()
        df = df.copy()
        df["date"] = df["date"].astype(str)
        _save(df, deriv_root() / "qvix" / f"{name}.parquet")
        v = qv.check_series(name, df)
        report[name] = {"status": v.status, "rows": int(len(df)), "target": target,
                        "valid_start": valid_start(df), "problems": v.problems}
    for fn_name in DEAD_QVIX:
        fn = getattr(ak, fn_name, None)
        df = fn() if fn else None
        v = qv.check_series(fn_name, df)
        report[fn_name] = {"status": v.status, "rows": int(len(df)) if df is not None else 0,
                           "valid_start": valid_start(df) if df is not None else None,
                           "dropped": True, "problems": v.problems}
    return report


def backfill_opt_basic(pro) -> dict:
    """三所 `opt_basic` 全量快照 —— **强制分页**(SSE 首页恰 12,000 = 分页上限,不是全量)。"""
    out = {}
    for ex in EXCHANGES:
        res = ol.paginate(lambda off, lim, e=ex: _call(
            lambda: pro.opt_basic(exchange=e, offset=off, limit=lim)))
        frame = res["frame"]
        if res["truncated"] or not len(frame):
            raise CensusError(f"opt_basic({ex}) 分页未拉完或为空:{res}")
        _save(frame, deriv_root() / "opt_basic" / f"{ex}.parquet")
        out[ex] = {"rows": res["rows_dedup"], "pages": res["pages"]}
    return out


def _backfill_daily(pro, endpoint: str, sub: Path, days: list[str], *,
                    exchanges=("CFFEX",), sleep: float = 0.12) -> dict:
    """逐日 × 交易所落盘,断点续传(文件在即跳过)。"""
    fetched = skipped = empty = 0
    t0 = time.time()
    for day in days:
        for ex in exchanges:
            fp = sub / ex / f"{day}.parquet"
            if fp.exists():
                skipped += 1
                continue
            df = _call(lambda d=day, e=ex: getattr(pro, endpoint)(trade_date=d, exchange=e))
            if df is None:
                df = pd.DataFrame()
            if not len(df):
                empty += 1
            _save(df, fp)
            fetched += 1
            time.sleep(sleep)
    return {"fetched": fetched, "skipped": skipped, "empty": empty,
            "seconds": round(time.time() - t0, 1)}


def backfill_fut_basic(pro) -> dict:
    """股指期货合约元数据(`delist_date` = 最后交易日 = 交割日)。"""
    df = _call(lambda: pro.fut_basic(exchange="CFFEX", fut_type="1"))
    if df is None or not len(df):
        raise CensusError("fut_basic(CFFEX) 空 —— 基差腿没有到期日就只能瞎猜剩余天数")
    _save(df, deriv_root() / "fut_basic" / "CFFEX.parquet")
    codes = set(df["fut_code"].astype(str))
    missing = sorted(set(FUT_UNDERLYING) - codes)
    if missing:
        raise CensusError(f"fut_basic 缺品种 {missing} —— 基差腿会静默少一条")
    return {"rows": int(len(df)), "fut_codes": sorted(codes & set(FUT_UNDERLYING))}


def backfill_breadth(lake_daily: Path | None = None) -> dict:
    """`lake/daily` → 每日 breadth(可交易票中 pct_chg>0 的占比)。增量:已算过的日子不重算。

    可交易 = `pct_chg` 非空 ∧ `amount` > 0(停牌/无成交的票不该进分母)。**与 `frame.py`
    的 regime breadth 不是同一个口径**,读数表脚注要说这件事。
    """
    d = Path(lake_daily) if lake_daily else ws.lake_root() / "daily"
    fp = deriv_root() / "breadth.parquet"
    have = _load(fp)
    known = set(have["date"].astype(str)) if have is not None and len(have) else set()
    rows = []
    for p in sorted(d.glob("*.parquet")):
        day = p.stem[:8]
        if not day.isdigit() or day in known:
            continue
        df = pd.read_parquet(p, columns=["pct_chg", "amount"])
        pc = pd.to_numeric(df["pct_chg"], errors="coerce")
        amt = pd.to_numeric(df["amount"], errors="coerce")
        ok = pc.notna() & (amt > 0)
        if not int(ok.sum()):
            continue
        rows.append({"date": day, "breadth": float((pc[ok] > 0).mean()),
                     "n_tradable": int(ok.sum())})
    new = pd.DataFrame(rows)
    merged = (pd.concat([have, new], ignore_index=True) if have is not None and len(have)
              else new)
    if not len(merged):
        raise CensusError("breadth 为空 —— lake/daily 没有可读分区")
    merged = merged.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    _save(merged, fp)
    return {"days": int(len(merged)), "new": int(len(new)),
            "range": [merged["date"].iloc[0], merged["date"].iloc[-1]]}


def run_backfill(since: str = BACKFILL_START) -> dict:
    """一次性回补(断点续传;重跑只补缺口)。约 4~5 千次 tushare 调用,预计 30~45 分钟。"""
    pro = _pro()
    meta = {"since": since}
    meta["index_daily"] = backfill_index_daily(pro)
    days = trade_days(since)
    meta["trade_days"] = {"n": len(days), "first": days[0], "last": days[-1]}
    meta["qvix"] = backfill_qvix()
    meta["opt_basic"] = backfill_opt_basic(pro)
    meta["fut_basic"] = backfill_fut_basic(pro)
    meta["opt_daily"] = _backfill_daily(pro, "opt_daily", deriv_root() / "opt_daily",
                                        days, exchanges=EXCHANGES)
    meta["fut_daily"] = _backfill_daily(pro, "fut_daily", deriv_root() / "fut_daily", days)
    meta["breadth"] = backfill_breadth()
    return meta


# ───────────────────────── 派生面板(缓存 → 信号;零网络)─────────────────────────


def build_pcr_panel(days: list[str], *, rebuild: bool = False) -> tuple[pd.DataFrame, dict]:
    """逐日 `opt_daily × opt_basic` → 每个 underlying 的成交量/持仓 PCR。

    跨**期限桶**求和是合法的(同一 underlying 内合约乘数相同);跨 **underlying** 不加总
    —— 那才是 `AGGREGATION_RULE` 禁的事。
    """
    fp = deriv_root() / "pcr_panel.parquet"
    have = None if rebuild else _load(fp)
    known = set(have["date"].astype(str)) if have is not None and len(have) else set()
    todo = [d for d in days if d not in known]
    basics = [b for b in (_load(deriv_root() / "opt_basic" / f"{ex}.parquet")
                          for ex in EXCHANGES) if b is not None]
    if not basics:
        raise CensusError("opt_basic 快照缺失 —— 先跑 `--backfill`")
    basic = pd.concat(basics, ignore_index=True).drop_duplicates("ts_code")
    rows, cov_missing, cov_days = [], 0, 0
    for day in todo:
        frames = [f for f in (_load(deriv_root() / "opt_daily" / ex / f"{day}.parquet")
                              for ex in EXCHANGES) if f is not None and len(f)]
        if not frames:
            continue
        daily = pd.concat(frames, ignore_index=True)
        cov = ol.coverage_report(daily, basic)
        cov_missing += int(cov["n_missing_metadata"])
        cov_days += 1
        metrics = ol.bucket_metrics(ol.join_asof(daily, basic, trade_date=day))
        if not len(metrics):
            continue
        g = metrics.groupby("underlying")[["vol_call", "vol_put", "oi_call", "oi_put"]].sum()
        for under, r in g.iterrows():
            rows.append({
                "date": day, "underlying": str(under),
                "pcr_vol": float(r["vol_put"] / r["vol_call"]) if r["vol_call"] > 0 else np.nan,
                "pcr_oi": float(r["oi_put"] / r["oi_call"]) if r["oi_call"] > 0 else np.nan,
            })
    new = pd.DataFrame(rows)
    panel = (pd.concat([have, new], ignore_index=True)
             if have is not None and len(have) else new)
    if len(panel):
        panel = (panel.drop_duplicates(["date", "underlying"])
                 .sort_values(["underlying", "date"]).reset_index(drop=True))
        _save(panel, fp)
    return panel, {"days_built": len(todo), "coverage_days_this_run": cov_days,
                   "coverage_missing_this_run": cov_missing,
                   "panel_days": int(panel["date"].nunique()) if len(panel) else 0}


def build_basis_panel(days: list[str], index_frames: dict, *,
                      rebuild: bool = False) -> tuple[pd.DataFrame, dict]:
    """逐日股指期货**主力**(当日最大 OI 的真实合约)年化基差。

    真实合约靠 `_REAL_FUT` 正则筛 —— `fut_daily` 里混着 `IF.CFX` / `IFL1.CFX` 这种合成
    连续行,它们的 oi 是整族加总,不滤掉「最大 OI = 主力」会永远选中合成行。
    """
    fp = deriv_root() / "basis_panel.parquet"
    have = None if rebuild else _load(fp)
    known = set(have["date"].astype(str)) if have is not None and len(have) else set()
    todo = [d for d in days if d not in known]
    fb = _load(deriv_root() / "fut_basic" / "CFFEX.parquet")
    if fb is None:
        raise CensusError("fut_basic 缺失 —— 先跑 `--backfill`")
    delist = {str(r["ts_code"]): _ymd(r["delist_date"]) for _, r in fb.iterrows()}
    spot = {code: df.set_index("date")["close"] for code, df in index_frames.items()}
    rows, dropped_synth = [], 0
    for day in todo:
        df = _load(deriv_root() / "fut_daily" / "CFFEX" / f"{day}.parquet")
        if df is None or not len(df):
            continue
        codes = df["ts_code"].astype(str)
        m = codes.str.match(_REAL_FUT)
        dropped_synth += int((~m).sum())
        real = df[m].copy()
        real["fut_code"] = codes[m].str[:2]
        rec = {"date": day}
        for fut_code, idx_code in FUT_UNDERLYING.items():
            grp = real[real["fut_code"] == fut_code]
            grp = grp[pd.to_numeric(grp["oi"], errors="coerce").notna()]
            if not len(grp):
                continue
            main = grp.loc[pd.to_numeric(grp["oi"], errors="coerce").idxmax()]
            dl = delist.get(str(main["ts_code"]))
            s = spot.get(idx_code)
            if dl is None or s is None or day not in s.index:
                continue
            days_left = (pd.Timestamp(dl) - pd.Timestamp(day)).days
            rec[f"basis_{fut_code}"] = annualized_basis(
                float(pd.to_numeric(main["settle"], errors="coerce")),
                float(s.loc[day]), float(days_left))
        if len(rec) > 1:
            rows.append(rec)
    new = pd.DataFrame(rows)
    panel = (pd.concat([have, new], ignore_index=True)
             if have is not None and len(have) else new)
    if len(panel):
        panel = panel.drop_duplicates("date").sort_values("date").reset_index(drop=True)
        if "basis_IM" in panel.columns and "basis_IF" in panel.columns:
            panel["style_gap"] = panel["basis_IM"] - panel["basis_IF"]
        _save(panel, fp)
    # 计数器是**本次跑**的:面板是增量的,续跑时大部分日子来自缓存 → 这两个数会小于
    # 面板真实规模。读数表要把它标成「本次构建」,别让人读成「全量只丢了这么多」。
    return panel, {"days_built": len(todo), "synthetic_rows_dropped_this_run": dropped_synth,
                   "panel_days": int(panel["date"].nunique()) if len(panel) else 0}


# ───────────────────────── target / 信号装配 ─────────────────────────


def load_index_frames() -> dict:
    """缓存的现货指数 → `{code: DataFrame[date, open, close]}`(升序、去重)。"""
    out = {}
    for code in INDEX_CODES:
        df = _load(deriv_root() / "index_daily" / f"{code}.parquet")
        if df is None or not len(df):
            continue
        f = pd.DataFrame({
            "date": df["trade_date"].map(_ymd),
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "close": pd.to_numeric(df["close"], errors="coerce"),
        }).drop_duplicates("date").sort_values("date").reset_index(drop=True)
        out[code] = f
    if CALENDAR_INDEX not in out:
        raise CensusError("交易日轴指数缺失 —— 先跑 `--backfill`")
    return out


def build_targets(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """一个指数的 target 帧(全部对齐到**信号日 T**)+ 极值剔除计数。

    `gap` = close(T+1) → open(T+2):信号 T 盘后可知 → T 晚决策 → T+1 收盘买 → T+2 开盘卖,
    与产品主尺 `gap_c1_o2` 完全同窗。`oc_t1` / `d_breadth_t1` 只观察不判读;`cc_t1` 供
    A2 尾部专测;`mom20` 是基线控制项(T 日已知)。
    """
    f = frame.sort_values("date").reset_index(drop=True)
    close, open_ = f["close"], f["open"]
    gap = open_.shift(-2) / close.shift(-1) - 1.0
    n_clip = int((gap.abs() > GAP_CLIP).sum())
    out = pd.DataFrame({
        "date": f["date"],
        "gap": gap.where(gap.abs() <= GAP_CLIP),
        "oc_t1": close.shift(-1) / open_.shift(-1) - 1.0,
        "cc_t1": close.shift(-1) / close - 1.0,
        "mom20": close / close.shift(MOM_WIN) - 1.0,
    })
    return out, n_clip


def load_breadth() -> pd.DataFrame:
    df = _load(deriv_root() / "breadth.parquet")
    if df is None or not len(df):
        raise CensusError("breadth 缺失 —— 先跑 `--backfill`")
    b = df.copy()
    b["date"] = b["date"].astype(str)
    b = b.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    b["d_breadth_t1"] = b["breadth"].shift(-1) - b["breadth"]
    return b


def _series(frame: pd.DataFrame, col: str) -> pd.Series:
    return pd.Series(frame[col].to_numpy(), index=frame["date"].astype(str).to_numpy())


def build_qvix_signals(index_frames: dict) -> tuple[list[dict], dict]:
    """A 族三信号 × 6 条活序列。有效起点之前的回填行**整段弃**(§0.4)。"""
    sigs, report = [], {}
    for name, (_, idx_code) in QVIX_SERIES.items():
        df = _load(deriv_root() / "qvix" / f"{name}.parquet")
        if df is None or not len(df):
            report[name] = {"status": "MISSING"}
            continue
        f = df.copy()
        f["date"] = f["date"].astype(str).str.replace("-", "")
        f = f.drop_duplicates("date").sort_values("date").reset_index(drop=True)
        vs = valid_start(f)
        if vs is None:
            report[name] = {"status": "NO_VALID_RUN", "rows": int(len(f))}
            continue
        f = f[f["date"] >= vs].reset_index(drop=True)
        close = pd.to_numeric(f["close"], errors="coerce")
        close = close.where(close > 0)                     # 0 不是波动率(§0.5)
        s_close = pd.Series(close.to_numpy(), index=f["date"].to_numpy())
        report[name] = {"status": "OK", "valid_start": vs, "rows": int(len(f)),
                        "target": idx_code, "nonnull": int(close.notna().sum())}
        sigs.append({"family": "A1", "signal": f"qvix_pctile[{name}]",
                     "target_index": idx_code, "series": s_close})
        sigs.append({"family": "A2", "signal": f"qvix_spike_z[{name}]",
                     "target_index": idx_code,
                     "series": rolling_z(s_close.diff())})
        idx = index_frames.get(idx_code)
        if idx is not None:
            px = _series(idx, "close")
            rv = realized_var(np.log(px / px.shift(1)))
            sigs.append({"family": "A3", "signal": f"vrp[{name}]",
                         "target_index": idx_code,
                         "series": vrp(s_close, rv.reindex(s_close.index))})
    return sigs, report


def build_pcr_signals(panel: pd.DataFrame) -> tuple[list[dict], dict]:
    """B 族:每个 underlying 的 `pcr_vol` / `pcr_oi` / `Δpcr_oi`。

    有效日 < `PCR_MIN_DAYS` 的品种不入表 —— 那是数据质量门(新上市/低流动性品种),
    被挡下来的品种要**点名**,不能悄悄消失。
    """
    sigs, dropped = [], {}
    if not len(panel):
        return sigs, {"dropped": dropped, "kept": []}
    kept = []
    for under, g in panel.groupby("underlying"):
        g = g.sort_values("date")
        idx_code = OPT_UNDERLYING.get(str(under))
        n_ok = int(g["pcr_oi"].notna().sum())
        if idx_code is None or n_ok < PCR_MIN_DAYS:
            dropped[str(under)] = {"n_valid_days": n_ok,
                                   "reason": "无标的映射" if idx_code is None
                                   else f"有效日 {n_ok} < {PCR_MIN_DAYS}"}
            continue
        kept.append(str(under))
        s_vol = pd.Series(g["pcr_vol"].to_numpy(), index=g["date"].astype(str).to_numpy())
        s_oi = pd.Series(g["pcr_oi"].to_numpy(), index=g["date"].astype(str).to_numpy())
        sigs.append({"family": "B", "signal": f"pcr_vol[{under}]",
                     "target_index": idx_code, "series": s_vol})
        sigs.append({"family": "B", "signal": f"pcr_oi[{under}]",
                     "target_index": idx_code, "series": s_oi})
        sigs.append({"family": "B", "signal": f"d_pcr_oi[{under}]",
                     "target_index": idx_code, "series": s_oi.diff()})
    return sigs, {"dropped": dropped, "kept": sorted(kept)}


def build_basis_signals(panel: pd.DataFrame) -> tuple[list[dict], dict]:
    """C2 族:四品种年化基差 + IM−IF 风格温差。"""
    sigs, report = [], {}
    if not len(panel):
        return sigs, report
    p = panel.sort_values("date")
    dates = p["date"].astype(str).to_numpy()
    for fut_code, idx_code in FUT_UNDERLYING.items():
        col = f"basis_{fut_code}"
        if col not in p.columns:
            continue
        s = pd.Series(pd.to_numeric(p[col], errors="coerce").to_numpy(), index=dates)
        report[col] = {"n_valid": int(s.notna().sum()),
                       "first": str(s.dropna().index.min()) if s.notna().any() else None}
        sigs.append({"family": "C2", "signal": col, "target_index": idx_code, "series": s})
    if "style_gap" in p.columns:
        s = pd.Series(pd.to_numeric(p["style_gap"], errors="coerce").to_numpy(), index=dates)
        report["style_gap"] = {"n_valid": int(s.notna().sum()),
                               "first": str(s.dropna().index.min()) if s.notna().any() else None}
        # 风格温差量的是小票 vs 大票的对冲需求差 → 主判读挂中证1000
        sigs.append({"family": "C2", "signal": "style_gap", "target_index": "000852.SH",
                     "series": s})
    return sigs, report


def build_calendar_signals(days: list[str]) -> tuple[list[dict], dict]:
    """D 族:到期日历三旗 × 两个基准指数。"""
    basics = [b for b in (_load(deriv_root() / "opt_basic" / f"{ex}.parquet")
                          for ex in EXCHANGES) if b is not None]
    if not basics:
        raise CensusError("opt_basic 快照缺失 —— 先跑 `--backfill`")
    exp = set()
    for b in basics:
        exp |= {_ymd(x) for x in b["maturity_date"].dropna().astype(str)}
    flags = expiry_flags(exp, days)
    sigs = []
    for col in ("expiry_day", "expiry_week", "post_expiry"):
        s = pd.Series(flags[col].to_numpy(), index=flags["date"].to_numpy())
        for idx_code in BENCH_INDEXES:
            sigs.append({"family": "D", "signal": f"{col}", "target_index": idx_code,
                         "series": s, "is_flag": True})
    in_window = {c: int(flags[flags["date"] >= JUDGE_START][c].sum())
                 for c in ("expiry_day", "expiry_week", "post_expiry")}
    return sigs, {"n_expiry_dates": len(exp & set(days)), "flag_days_in_window": in_window}


# ───────────────────────── 普查主体 ─────────────────────────


def _judge_one(sig: dict, targets: dict, breadth: pd.DataFrame, since: str) -> dict:
    """一个信号 × 主 target 的完整判读行(含两列观察 target)。"""
    tgt = targets.get(sig["target_index"])
    if tgt is None:
        return {}
    t_frame = tgt[0]
    is_flag = bool(sig.get("is_flag"))
    s = sig["series"]
    s.index = s.index.astype(str)
    gap = _series(t_frame, "gap")
    oc = _series(t_frame, "oc_t1")
    mom = _series(t_frame, "mom20")
    b = pd.Series(breadth["breadth"].to_numpy(), index=breadth["date"].astype(str).to_numpy())
    db = pd.Series(breadth["d_breadth_t1"].to_numpy(),
                   index=breadth["date"].astype(str).to_numpy())

    if is_flag:
        f = s.astype(float)
        hl = flag_hl(f[f.index >= since], gap.reindex(f.index)[f.index >= since])
        reg = f
        obs_oc = flag_hl(f[f.index >= since], oc.reindex(f.index)[f.index >= since])
        obs_db = flag_hl(f[f.index >= since], db.reindex(f.index)[f.index >= since])
        long_hl = flag_hl(f, gap.reindex(f.index))
    else:
        hl = bucket_hl(s, gap, since=since)
        reg = rolling_z(s.dropna())
        obs_oc = bucket_hl(s, oc, since=since)
        obs_db = bucket_hl(s, db, since=since)
        long_hl = bucket_hl(s, gap)             # 全史,不设 since
    # 长史列**只观察不判读**(§0.4):判读窗外没有 breadth 基线,基线增量那一条无从检验,
    # 三条判据缺一条 = 不能判。它的用处是「样本大得多的时候,同一个 H−L 长什么样」。
    long_start = min((d for d in s.dropna().index), default=None)

    idx = [d for d in reg.index if d >= since]
    inc = baseline_increment(gap.reindex(idx), reg.reindex(idx), b.reindex(idx),
                             mom.reindex(idx))
    v = (flag_verdict if is_flag else verdict)(hl, inc)
    return {
        "family": sig["family"], "signal": sig["signal"], "target": sig["target_index"],
        "n_h": hl["n_h"], "n_l": hl["n_l"],
        "mean_h": hl["mean_h"], "mean_l": hl["mean_l"], "diff": hl["diff"],
        "t": hl["t"], "p": hl["p"],
        "inc_coef": inc["coef"], "inc_t": inc["t"], "inc_n": inc["n"],
        "obs_oc_diff": obs_oc["diff"], "obs_oc_t": obs_oc["t"],
        "obs_dbreadth_diff": obs_db["diff"], "obs_dbreadth_t": obs_db["t"],
        "long_n": long_hl["n_h"] + long_hl["n_l"], "long_diff": long_hl["diff"],
        "long_t": long_hl["t"], "long_start": long_start,
        "verdict": v,
    }


def _tail_rows(sigs: list[dict], targets: dict, since: str) -> list[dict]:
    """A2 尾部专测:急升 H 档之后 P(标的 T+1 收收 ≤ −1.5%) vs 无条件基率。"""
    rows = []
    for sig in sigs:
        if sig["family"] != "A2":
            continue
        tgt = targets.get(sig["target_index"])
        if tgt is None:
            continue
        s = sig["series"].dropna()
        s.index = s.index.astype(str)
        pct = rolling_pctile(s)
        cc = _series(tgt[0], "cc_t1").reindex(s.index)
        df = pd.DataFrame({"pct": pct, "cc": cc}).dropna()
        df = df[df.index.astype(str) >= since]
        if not len(df):
            continue
        r = tail_lift(df["pct"] >= HI_Q, df["cc"] <= TAIL_THRESH)
        rows.append({"signal": sig["signal"], "target": sig["target_index"], **r})
    return rows


def attach_fdr(table: pd.DataFrame) -> pd.DataFrame:
    """给可判格补 BH-FDR 的 q 列(§0.3「正证据数必须与总共测了多少格一起报」)。

    **补充列,不改判读** —— 预注册的三条判据一字未动;q 只用来看「正证据 vs 测了多少格」。
    样本不足的格不进 FDR 池:它们没被判,不该占一个检验名额。

    这段是纯粹的位置粘合(p 的顺序 ↔ 行的标签),错位不会报错、只会让 q 挂到别人身上,
    所以单拎出来给测试咬。
    """
    out = table.copy()
    if not len(out):
        out["fdr_q"] = None
        return out
    judged = out[out["verdict"] != THIN]
    labels = [i for i, pv in zip(judged.index, judged["p"], strict=True) if pv is not None]
    ps = [pv for pv in judged["p"] if pv is not None]
    qs = st.bh_fdr(ps) if ps else []
    qmap = {lab: qs[k]["q"] for k, lab in enumerate(labels)}
    out["fdr_q"] = [qmap.get(i) for i in out.index]
    return out


def run_census(since: str = JUDGE_START, *, rebuild: bool = False,
               backfill_since: str = BACKFILL_START) -> tuple[pd.DataFrame, list[dict], dict]:
    index_frames = load_index_frames()
    targets = {code: build_targets(f) for code, f in index_frames.items()}
    breadth = load_breadth()
    axis = trade_days()
    panel_days = [d for d in axis if d >= _ymd(backfill_since)]

    pcr_panel, pcr_meta = build_pcr_panel(panel_days, rebuild=rebuild)
    basis_panel, basis_meta = build_basis_panel(panel_days, index_frames, rebuild=rebuild)

    qvix_sigs, qvix_report = build_qvix_signals(index_frames)
    pcr_sigs, pcr_report = build_pcr_signals(pcr_panel)
    basis_sigs, basis_report = build_basis_signals(basis_panel)
    cal_sigs, cal_report = build_calendar_signals(axis)
    sigs = qvix_sigs + pcr_sigs + basis_sigs + cal_sigs

    rows = [r for r in (_judge_one(s, targets, breadth, since) for s in sigs) if r]
    table = pd.DataFrame(rows)

    table = attach_fdr(table)
    judged = table[table["verdict"] != THIN] if len(table) else table

    meta = {
        "schema_version": SCHEMA_VERSION, "rule_version": RULE_VERSION,
        "judge_since": since, "backfill_since": _ymd(backfill_since),
        "axis": {"n": len(axis), "first": axis[0], "last": axis[-1]},
        "breadth": {"days": int(len(breadth)), "first": str(breadth["date"].iloc[0]),
                    "last": str(breadth["date"].iloc[-1])},
        "gap_clipped": {c: int(t[1]) for c, t in targets.items()},
        "qvix": qvix_report, "pcr": {**pcr_meta, **pcr_report},
        "basis": {**basis_meta, "series": basis_report}, "calendar": cal_report,
        "n_cells": int(len(table)), "n_judged": int(len(judged)),
        "n_positive": int((table["verdict"] == POS).sum()) if len(table) else 0,
        "thresholds": {"pctile_win": PCTILE_WIN, "hi_q": HI_Q, "lo_q": LO_Q,
                       "nw_lag": NW_LAG, "pos_t": POS_T, "min_bucket_n": MIN_BUCKET_N,
                       "min_flag_n": MIN_FLAG_N, "gap_clip": GAP_CLIP},
    }
    return table, _tail_rows(sigs, targets, since), meta


# ───────────────────────── 渲染 ─────────────────────────


def _pp(x) -> str:
    """pp 渲染。None 与 NaN 一视同仁 —— pandas 会把含 None 的列静默转成 NaN,
    只判 `is None` 的渲染会把 `nan` 三个字母原样印进读数表。"""
    return "—" if x is None or pd.isna(x) else f"{float(x) * 100:+.3f}"


def _f(x, nd: int = 2) -> str:
    return "—" if x is None or pd.isna(x) else f"{float(x):+.{nd}f}"


_FAMILY_LABEL = {"A1": "A1·QVIX 分位", "A2": "A2·QVIX 急升", "A3": "A3·VRP",
                 "B": "B·PCR", "C2": "C2·基差", "D": "D·到期日历"}


def render(table: pd.DataFrame, tails: list[dict], meta: dict) -> str:
    L = [f"# 衍生品领先性普查 · 读数({meta['judge_since']} 起)", "",
         f"> 预注册 §0 见 `docs/research/2026-08-24-derivatives-lead-census.md`(commit 早于本读数)。"
         f"rule `{meta['rule_version']}`。", "",
         f"- 判读窗 **{meta['judge_since']} → {meta['axis']['last']}**;缓存窗自 "
         f"{meta['backfill_since']}(暖机 250 日供滚动分位)",
         f"- 共 **{meta['n_cells']} 格**(可判 {meta['n_judged']});**正证据 "
         f"{meta['n_positive']}** 格", ""]

    npos = meta["n_positive"]
    L += [f"**H0「四族无一有领先性」:{'被推翻' if npos else '未被推翻'}。**", "",
          "## 1. 主判读表(target = 信号自标的指数 `close(T+1)→open(T+2)`)", "",
          "| 族 | 信号 | 标的 | n_H | n_L | H 均值 pp | L 均值 pp | H−L pp | NW t | 基线增量 t | FDR q | 判读 |",
          "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    if len(table):
        order = {k: i for i, k in enumerate(("A1", "A2", "A3", "B", "C2", "D"))}
        tv = table.copy()
        tv["_o"] = tv["family"].map(order).fillna(9)
        tv = tv.sort_values(["_o", "signal", "target"])
        for _, r in tv.iterrows():
            mark = "**" if r["verdict"] == POS else ""
            L.append(
                f"| {_FAMILY_LABEL.get(r['family'], r['family'])} | `{r['signal']}` | "
                f"{r['target']} | {r['n_h']} | {r['n_l']} | {_pp(r['mean_h'])} | "
                f"{_pp(r['mean_l'])} | {mark}{_pp(r['diff'])}{mark} | {_f(r['t'])} | "
                f"{_f(r['inc_t'])} | "
                + ("—" if pd.isna(r.get("fdr_q")) else f"{r['fdr_q']:.3f}")
                + f" | {mark}{r['verdict']}{mark} |")
    else:
        L.append("| — | — | — | — | — | — | — | — | — | — | — | — |")

    L += ["", "## 2. 观察列(不判读)", "",
          "长史列 = **不设判读窗起点**的同一个 H−L(§0.4:判读窗外没有 breadth 基线,"
          "三条判据缺一条,只能观察)。", "",
          "| 信号 | 标的 | H−L(T+1 日内 oc)pp | t | H−L(Δbreadth T+1)pp | t | 长史起点 | 长史 n | 长史 H−L pp | 长史 t |",
          "|---|---|---:|---:|---:|---:|---|---:|---:|---:|"]
    if len(table):
        for _, r in table.sort_values("signal").iterrows():
            L.append(f"| `{r['signal']}` | {r['target']} | {_pp(r['obs_oc_diff'])} | "
                     f"{_f(r['obs_oc_t'])} | {_pp(r['obs_dbreadth_diff'])} | "
                     f"{_f(r['obs_dbreadth_t'])} | {r.get('long_start') or '—'} | "
                     f"{r.get('long_n', 0)} | {_pp(r.get('long_diff'))} | "
                     f"{_f(r.get('long_t'))} |")
    else:
        L.append("| — | — | — | — | — | — | — | — | — | — |")

    L += ["", f"## 3. A2 哨兵尾部专测(P(标的 T+1 收收 ≤ {TAIL_THRESH:.1%}))", "",
          "| 信号 | 标的 | 急升日 n | 条件概率 | 无条件基率 | 提升倍数 | Fisher p(单侧) |",
          "|---|---|---:|---:|---:|---:|---:|"]
    for r in tails:
        L.append(f"| `{r['signal']}` | {r['target']} | {r['n_flag']} | "
                 + ("—" if r["p_flag"] is None else f"{r['p_flag']:.3f}") + " | "
                 + ("—" if r["p_base"] is None else f"{r['p_base']:.3f}") + " | "
                 + ("—" if r["lift"] is None else f"{r['lift']:.2f}×") + " | "
                 + ("—" if r["fisher_p"] is None else f"{r['fisher_p']:.4f}") + " |")
    if not tails:
        L.append("| — | — | — | — | — | — | — |")

    q = meta["qvix"]
    L += ["", "## 4. 数据体检", "", "### 4.1 QVIX 有效起点(接口自报的日期轴不可信)", "",
          "| 序列 | 状态 | 行数 | 有效起点 | 标的 |", "|---|---|---:|---|---|"]
    for name, v in q.items():
        L.append(f"| `{name}` | {v.get('status')} | {v.get('rows', 0)} | "
                 f"{v.get('valid_start') or '—'} | {v.get('target', '—')} |")

    pcr = meta["pcr"]
    L += ["", "### 4.2 PCR 覆盖", "",
          f"- 面板 **{pcr.get('panel_days', 0)}** 日;本次构建 {pcr.get('days_built', 0)} 日、"
          f"其中联结 {pcr.get('coverage_days_this_run', 0)} 日,元数据缺失合约 "
          f"**{pcr.get('coverage_missing_this_run', 0)}** 个"
          + ("(✅ 对平)" if not pcr.get("coverage_missing_this_run") else " 🚨")
          + " —— 计数器是本次跑的,面板增量续跑时它小于面板规模",
          f"- 入表品种:{'、'.join(f'`{u}`' for u in pcr.get('kept', [])) or '无'}"]
    for u, d in (pcr.get("dropped") or {}).items():
        L.append(f"  - 挡下 `{u}`:{d['reason']}")

    b = meta["basis"]
    L += ["", "### 4.3 基差 / 日历", "",
          f"- 面板 **{b.get('panel_days', 0)}** 日;本次构建 {b.get('days_built', 0)} 日,"
          f"丢弃合成连续合约行 **{b.get('synthetic_rows_dropped_this_run', 0)}** 行"
          "(`IF.CFX`/`IFL1.CFX` 这类,其 oi 是整族加总;同为本次跑计数)"]
    for k, v in (b.get("series") or {}).items():
        L.append(f"  - `{k}`:有效 {v['n_valid']} 日,自 {v['first'] or '—'}")
    cal = meta["calendar"]
    L += [f"- 到期日 **{cal['n_expiry_dates']}** 个;判读窗内旗日:"
          + "、".join(f"{k} {v}" for k, v in cal["flag_days_in_window"].items()),
          f"- 指数 target 极值剔除(|gap|>{GAP_CLIP:.0%}):"
          + "、".join(f"{c} {n}" for c, n in meta["gap_clipped"].items() if n)
          + (" —— 无" if not any(meta["gap_clipped"].values()) else ""),
          f"- breadth {meta['breadth']['days']} 日({meta['breadth']['first']} → "
          f"{meta['breadth']['last']})", ""]

    th = meta["thresholds"]
    L += ["---", "",
          f"_判读规则(§0.3 预注册):正证据 = H−L 的 NW t(lag={th['nw_lag']})|t|≥{th['pos_t']} "
          f"∧ 基线增量 t 同号且 |t|≥{th['pos_t']} ∧ 两档各 n≥{th['min_bucket_n']}"
          f"(D 族旗日 n≥{th['min_flag_n']});其余未证;样本不足只印数不判。_", "",
          "_「显著负」不适用于本普查:信号的符号可以自由翻转(预测跌与预测涨同样是信息),"
          "故只分「有没有领先性」。这是 §0.3 措辞的显式澄清,三条判据一字未动。_", "",
          "_breadth 口径 = `lake/daily` 中 `pct_chg` 非空 ∧ `amount`>0 的票里涨的占比,"
          "**与 `frame.py` 的 regime breadth 不是同一个口径**。_", "",
          "_FDR q 是补充列(BH,跨全部可判格),不参与判读 —— 用来看「正证据数 vs 测了多少格」。_",
          "", "_PCR = 对冲/持仓压力,**不是方向观点**:它分不出买 put(看空)与卖 put(看多)。_", ""]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true", help="一次性回补衍生品缓存(联网,约 30~45 分钟)")
    ap.add_argument("--backfill-since", default=BACKFILL_START, help="回补起点(默认 20210101)")
    ap.add_argument("--since", default=JUDGE_START, help="判读窗起点(默认 20220302 = lake/daily 起点)")
    ap.add_argument("--rebuild", action="store_true", help="重建派生面板(PCR/基差)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    if a.backfill:
        meta = run_backfill(a.backfill_since)
        print(json.dumps(meta, ensure_ascii=False, indent=1, default=str))
        return 0

    table, tails, meta = run_census(_ymd(a.since), rebuild=a.rebuild,
                                    backfill_since=a.backfill_since)
    out = Path(a.out) if a.out else ws.reports_root() / "research" / "derivatives_census.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(table, tails, meta), encoding="utf-8")
    (out.parent / "_derivatives_census.json").write_text(json.dumps(
        {"meta": meta, "tails": tails,
         "rows": json.loads(table.to_json(orient="records", force_ascii=False))
         if len(table) else []},
        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"[done] {meta['n_cells']} 格 · 正证据 {meta['n_positive']} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
