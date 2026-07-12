#!/usr/bin/env python3
"""S3 纸面仓位 sizer —— shadow_buys 影子账本的 sized 轨(分数 Kelly × 波动率目标 × 流动性 cap)。

spec: 2026-07-11 六问 brainstorm §8 拍板(方案 C·纸面法庭)+ W1(2026-07-12)。纯纸面,零真金,
公式 v1 全部硬编码(不进 config);与 `paper_nav.py` 既有等权轨(固定 10% slot)**并列**记账,
互不改动老行为——本模块只产出"权重",真正的 NAV 滚动仍是 `paper_nav.simulate()`。

## 公式(v1,自上而下;全部常数见下方模块级变量,改动请连这段 docstring 一起改)

1. **edge**(conviction 映射期望优势,已并入 1/4 Kelly 分数)::

       edge_i = clip((conviction_i − 55) / 45, 0, 1) × 0.25

   conviction ≤ 55 → edge=0 → 该票 sized 权重直接为 0(**不是**回退等权——这正是 sized 轨
   想暴露的分歧:等权轨仍会给它 10% 等权仓位,sized 轨认为它不配拿仓位)。

2. **raw**(近 20 日年化波动倒数缩放,"多少 edge 换多少风险预算")::

       raw_i = edge_i / vol_i        (vol_i = 近 20 个交易日 pct_chg 的年化标准差)

3. **组合波动目标缩放**(15% 年化;呼应 `docs/specs/2026-07-07-memory-astrategy-optimization-
   design.md` §S3 原始设计"单票波动贡献 ≤ 组合目标 vol/√N"——零相关性假设下把 N 票的贡献
   按平方和累加,一次性解出让"组合"命中 15% 年化目标的缩放标量)::

       k   = VOL_TARGET / sqrt(Σ (raw_i · vol_i)²)     (Σ 只算 edge>0 且 vol 可得的当日 picks)
       w_i = k · raw_i

   诚实局限(v1 简化,不建协方差矩阵):零相关性假设在 A 股同 regime/同板块票同涨同跌时会
   低估真实组合波动——本公式只求"有量纲的粗仓位纪律",不是精确风控模型。

   **已知特性**(算术推论,非 bug):因 raw_i·vol_i ≡ edge_i,单票日(n=1 有效 pick)时
   w_1 = VOL_TARGET / vol_1,与 conviction 大小无关(只要 edge>0 即可)——这是"先定总风险
   预算、再按 edge/vol 比例切分"这套风险预算哲学的必然结果:只有一个候选时,"切分比例"
   无意义,只剩"该拿多少风险预算"由 vol_target/vol_i 决定。多票日里,conviction 仍通过
   raw_i 的相对大小决定各票切走风险预算的比例(见下方单测)。

4. **单票 cap**(取更紧的一个;无成交额数据 → 只用 40% 硬顶,presence-gated)::

       liq_cap_i = (avg_amount_i(元) × 0.5%) / ASSUMED_AUM    (avg_amount_i = 近20日日均成交额)
       cap_i     = min(0.40, liq_cap_i)   若 avg_amount_i 可得,否则 cap_i = 0.40
       w_i       = min(w_i, cap_i)

   ASSUMED_AUM(v1 硬编码假设,见下方常量注释)把"日均成交额的 0.5%"换算成仓位占比——
   spec 明示 v1 不进 config,故此处直接量化一个假设纸面组合规模。cap 削掉的部分**不**
   回补给其他票(不做 waterfilling 迭代),直接进现金,与"残余进现金"一致。

5. **presence-gated 回退**:conviction 缺失/不可解析,或 vol 数据不足(近 20 日样本 < 2 /
   该票在窗口内查无数据)→ 该票 sized 权重整体退化为 EQUAL_SLOT(与等权轨相同的固定槽),
   不参与第 3 步的组合缩放求和——保证 sized 轨与等权轨**持有同一组标的**,只是仓位不同。
   若当日全部 picks 都落入此回退(如近期湖 schema 退化、只剩 ts_code/pct_chg 两列)→
   整日退化为纯等权,与"presence-gated:无波动数据回退等权"的验收要求一致。
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

_EDGE_FLOOR = 55.0              # conviction 映射下限(≤ 此值 edge=0)
_EDGE_SPAN = 45.0                # (conviction-55)/45 归一到 [0,1]
_KELLY_FRAC = 0.25               # 1/4 Kelly 分数
_VOL_WINDOW = 20                 # 近 N 个交易日
_ANN = 252                       # 年化交易日数
_VOL_TARGET = 0.15               # 组合目标年化波动 15%
_NAME_CAP = 0.40                 # 单票硬顶 40%
_LIQ_PCT = 0.005                 # 流动性 cap = 近20日日均成交额 × 0.5%
_ASSUMED_AUM = 10_000_000.0      # v1 硬编码假设纸面组合规模(RMB 1000万)——"成交额 0.5%→仓位占比"
                                 # 需要一个参照 AUM,spec 明示 v1 不进 config、无从读配置;量级选择
                                 # 让流动性 cap 对偏小盘/薄成交票有实际约束力,对多数正常成交的票
                                 # 通常不 binding(让 40% 硬顶接管)——与"纸面小盘"的定位一致。
_EQUAL_SLOT = 0.10               # presence-gated 回退目标 = 与 paper_nav 等权轨相同的固定槽

_LAKE_DAILY = Path("context/lake/daily")


def edge(conviction: object) -> float:
    """conviction → 期望优势(已并入 1/4 Kelly 分数);非数值/NaN/≤55 → 0.0。"""
    try:
        c = float(conviction)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if c != c:  # NaN
        return 0.0
    return max(0.0, min(1.0, (c - _EDGE_FLOOR) / _EDGE_SPAN)) * _KELLY_FRAC


def annualized_vol(pct_chg: Iterable[float], window: int = _VOL_WINDOW,
                    ann: int = _ANN) -> float | None:
    """近 window 个交易日 pct_chg(百分比,如 2.3 = +2.3%)→ 年化波动。

    样本 < 2 或标准差为 0/NaN → None(数据不足,调用方据此回退等权)。
    """
    s = pd.to_numeric(pd.Series(list(pct_chg)), errors="coerce").dropna()
    if len(s) < 2:
        return None
    daily = (s.tail(window) / 100.0)
    if len(daily) < 2:
        return None
    sigma = float(daily.std())          # pandas 默认 ddof=1(样本标准差)
    if not sigma or sigma != sigma:
        return None
    return sigma * (ann ** 0.5)


def trailing_stats(codes: Iterable[str], date: str, lake: Path | str | None = None,
                    window: int = _VOL_WINDOW) -> dict[str, dict[str, float | None]]:
    """{code6: {"vol": 年化波动|None, "avg_amount": 近20日日均成交额(元)|None}}。

    截至 date(含)最近 window 个交易日;单遍扫描服务同日多票(避免逐票重复读盘)。
    amount 列缺失(近期湖 schema 退化,如仅剩 ts_code/pct_chg 两列)→ avg_amount=None,
    不报错(presence-gated,调用方据此只用 40% 硬顶)。date 兼容 YYYY-MM-DD / YYYYMMDD。
    """
    from autoresearch.data.tushare_source import _code6
    lake = Path(lake or _LAKE_DAILY)
    want = {str(c).zfill(6) for c in codes}
    if not want or not lake.exists():
        return {c: {"vol": None, "avg_amount": None} for c in want}
    d = str(date).replace("-", "")
    days = sorted(p.stem for p in lake.glob("*.parquet")
                  if len(p.stem) == 8 and p.stem.isdigit() and p.stem <= d)[-window:]
    rets: dict[str, list[float]] = {c: [] for c in want}
    amts: dict[str, list[float]] = {c: [] for c in want}
    for dd in days:
        p = lake / f"{dd}.parquet"
        has_amount = True
        try:
            df = pd.read_parquet(p, columns=["ts_code", "pct_chg", "amount"])
        except Exception:  # noqa: BLE001 — amount 列缺失/坏分区,退化只读 pct_chg
            has_amount = False
            try:
                df = pd.read_parquet(p, columns=["ts_code", "pct_chg"])
            except Exception:  # noqa: BLE001 — 连 pct_chg 都没有 → 整个分区跳过
                continue
        df = df.assign(_c=_code6(df["ts_code"]))
        hit = df[df["_c"].isin(want)]
        for r in hit.to_dict("records"):
            c = r["_c"]
            if pd.notna(r.get("pct_chg")):
                rets[c].append(float(r["pct_chg"]))
            if has_amount and pd.notna(r.get("amount")):
                amts[c].append(float(r["amount"]))
    out: dict[str, dict[str, float | None]] = {}
    for c in want:
        vol = annualized_vol(rets[c], window=window) if rets[c] else None
        avg_amount = (sum(amts[c]) / len(amts[c]) * 1000.0) if amts[c] else None  # 千元 → 元
        out[c] = {"vol": vol, "avg_amount": avg_amount}
    return out


def size_weights(picks: list[dict], *, vol_target: float = _VOL_TARGET,
                  name_cap: float = _NAME_CAP, liq_pct: float = _LIQ_PCT,
                  assumed_aum: float = _ASSUMED_AUM,
                  equal_slot: float = _EQUAL_SLOT) -> dict[str, float]:
    """picks(同一天的 top-k)→ {code: NAV 占比权重}。纯函数,公式见模块 docstring。

    picks 项:{"code", "conviction", "vol"(年化,可选), "avg_amount"(元,可选)}。
    """
    by_code = {str(p["code"]).zfill(6): p for p in picks}
    weights: dict[str, float] = {}
    raws: dict[str, float] = {}
    vols: dict[str, float] = {}
    for code, p in by_code.items():
        vol = p.get("vol")
        if vol is None or vol != vol or vol <= 0:
            weights[code] = equal_slot              # presence-gated 回退:无波动数据
            continue
        e = edge(p.get("conviction"))
        if e <= 0:
            weights[code] = 0.0                      # conviction 未过门槛:sized 仓位=0(非回退)
            continue
        raws[code] = e / vol
        vols[code] = vol
    port_var = sum((raws[c] * vols[c]) ** 2 for c in raws)   # 零相关性假设下的组合方差代理
    k = (vol_target / port_var ** 0.5) if port_var > 0 else 0.0
    for code, raw in raws.items():
        w = k * raw
        avg_amount = by_code[code].get("avg_amount")
        cap = name_cap
        if avg_amount is not None and avg_amount == avg_amount:  # 非 None 且非 NaN
            cap = min(name_cap, (avg_amount * liq_pct) / assumed_aum)
        weights[code] = max(0.0, min(w, cap))
    return weights


def size_shadow_signals(signals: list[dict], lake: Path | str | None = None,
                         window: int = _VOL_WINDOW,
                         equal_slot: float = _EQUAL_SLOT) -> list[dict]:
    """`paper_nav.shadow_signals()` 的输出(含 conviction)→ 回填 "weight" 键的信号列表。

    按 date 分组(同一天的 top-k 是一次"组合"决策);组内 codes 一次性批量 `trailing_stats`,
    再喂 `size_weights`。presence-gated:某票缺 vol/conviction → weight=equal_slot;全天
    无一票有 vol 数据 → 整天退化等权(纯函数无副作用,不改 signals 原字典,返回新列表)。
    """
    by_date: dict[str, list[dict]] = {}
    for s in signals:
        by_date.setdefault(str(s["date"]), []).append(s)
    out: list[dict] = []
    for date, group in by_date.items():
        codes = [g["code"] for g in group]
        stats = trailing_stats(codes, date, lake=lake, window=window)
        picks = [{"code": g["code"], "conviction": g.get("conviction"),
                  "vol": stats.get(str(g["code"]).zfill(6), {}).get("vol"),
                  "avg_amount": stats.get(str(g["code"]).zfill(6), {}).get("avg_amount")}
                 for g in group]
        weights = size_weights(picks, equal_slot=equal_slot)
        for g in group:
            code6 = str(g["code"]).zfill(6)
            out.append({**g, "weight": weights.get(code6, equal_slot)})
    return out
