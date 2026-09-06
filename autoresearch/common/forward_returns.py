#!/usr/bin/env python3
"""前瞻收益的**纯计算**单一 owner —— 湖/缓存/网络一概不碰,输入是已读好的 pivot。

2026-09-06(工作包 E4)从 `research.factor_lab` 与 `research.edge_census` **机械搬入**:
函数体逐字未改,`research` 侧两个旧入口改为同对象转发。搬的理由是依赖方向:`scan.outcome`
/ `scan.populations` / `scan.ledger_views` 都要算前瞻收益,而 `scan → research` 是一条
**向上**的边(见 `tests/contracts/test_layering.py::KNOWN_UPWARD`);收益口径本身与「研究
仪器」无关,属于 common。

搬迁 parity 由 `tests/common/test_forward_returns_parity.py` 锁死:**同对象**(证明旧入口
是转发)+ **golden 逐位**(golden 在搬迁前从原实现录制,证明数值没变)。只有前者会让
「两边指向同一个被改坏的函数」照样全绿。

**尺子沿革**(与 `factor_lab` 模块 docstring 同一份,搬迁时一并带过来):**主尺**自
2026-08-05 用户裁定起是隔夜尺 `common.ruler.MAIN_RULER` = `gap_c1_o2`(T+1 收盘买 →
T+2 开盘卖);本模块产出的 `fwd_2_oc`(D+1 开盘买 → D+2 收盘卖)、`fwd_5_oc`/`fwd_10_oc`
都是**参考尺**,降为只观察,不作判读依据。下面函数 docstring 里「超短主尺」是 2026-07-10
当时的措辞,原文保留不改写(搬迁只搬,不顺手改历史注释)。

`_board_limit` 是历史代理的**规则近似**:本次只搬迁,不宣称它已覆盖所有日期、ST/新股
状态或实际成交制度。真实可交易规则由工作包 C 的版本化来源解释;要改规则得另写行为
差异测试,不能借搬迁悄悄改。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.common import ruler as _ruler


def _board_limit(code: str) -> float:
    """涨跌停幅度(%):科创(688)/创业板(30)=20;北交所(8/4/920)=30;其余主板=10。"""
    if code.startswith("688") or code.startswith("30"):
        return 20.0
    if code.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


def forward_returns(piv: dict, P: list[str], D: str, fwd: int) -> pd.DataFrame:
    """D 的前瞻收益(D+1 开盘进):cc=收盘到收盘;oo=次日开到再次日开;oc/ocN=开盘到第N日收盘;fwd_2_oc=超短主尺(2026-07-10 用户裁定持仓 1~2 日)。

    并标 D+1 一字涨停(open==close==high 且涨幅近板)= 买不到 → unbuyable。

    另产**隔夜尺三列**(Wave11 批A;2026-08-05 裁定,gap_c1_o2 = open[D+2]/close[D+1] − 1,
    T+1 收盘买 → T+2 开盘卖;单点常量见 `autoresearch.common.ruler`,本函数仍只加列不改主尺):
    `gap_c1_o2`(隔夜前瞻收益,float64,数缺→NaN)、`buyable_c1`(T+1 收盘未封涨停,买腿可
    执行→剔样本用)、`unsellable_o2`(T+2 一字跌停开,卖腿受限→标旗不剔,剔了会美化账本)。
    后两者是 pandas 可空 `boolean` dtype(2026-08-07 review fix):D+1/D+2 数据缺失时取
    `pd.NA`(未知),不是 `False`——调用方要用 `.astype(bool)` 或布尔索引前必须先显式决定
    如何处理 `<NA>`(如 `.fillna(...)`),不会被静默当成"确定可买/可卖"。
    """
    idx = P.index(D)
    c, o, h = piv["close"], piv["open"], piv["high"]
    pc = piv["pct_chg"]
    codes = c.index
    res = pd.DataFrame(index=codes)
    cD = c[D]

    def col(piv_, k):
        j = idx + k
        if not (0 <= j < len(P)) or P[j] not in piv_.columns:   # 越界 / 日历日在但 EOD 未发布
            return pd.Series(np.nan, index=codes)
        return piv_[P[j]]

    o1 = col(o, 1)
    res["fwd_1_cc"] = col(c, 1) / cD - 1.0
    res["fwd_1_oo"] = col(o, 2) / o1 - 1.0
    res["fwd_2_oc"] = col(c, 2) / o1 - 1.0            # 超短主尺:D+1 开买 → D+2 收卖(成熟同 fwd_1_oo)
    h1, h2 = col(h, 1), col(h, 2)
    res["hi_2_oc"] = np.maximum(h1, h2) / o1 - 1.0   # np.maximum NaN 传染:D+2 缺→NaN,与 fwd_2_oc 成熟配对
    res["fwd_5_oc"] = col(c, 5) / o1 - 1.0
    res["fwd_10_oc"] = col(c, min(10, fwd)) / o1 - 1.0
    # D+1 一字涨停(开=收=高,且涨幅≥板*0.98)→ 买不到
    pc1, o1h, c1 = col(pc, 1), o1, col(c, 1)
    lim = pd.Series([_board_limit(x) for x in codes], index=codes)
    sealed = (pc1 >= lim * 0.98) & (c1 >= h1 - 1e-6) & (o1h >= h1 - 1e-6)
    res["buyable"] = ~sealed.fillna(False)

    # 隔夜尺三列(Wave11 批A;复用既有 pc1/h1/lim/c1,只补 o2/l2;不动上面的旧 buyable/sealed)
    o2, l2 = col(o, 2), col(piv["low"], 2)
    res["gap_c1_o2"] = o2 / c1 - 1.0     # 隔夜主尺(2026-08-05 裁定):T+1 收买 → T+2 开卖
    # review fix(2026-08-07):col() 对缺数返回全 NaN,但 <=/>= 对 NaN 操作数按 IEEE754/
    # numpy 语义恒返回 False、不传染 —— 旧写法 fillna(False) 对一个本就不含 NaN 的纯 bool
    # 列是无效兜底,会把"不知道"误读成"确定卖得出/买得进"(data-contracts-fail-fast 同族
    # 反模式:降级不留痕)。转 pandas 可空 Float64 比较,&/~ 走三值逻辑,缺数正确传染成
    # <NA> 而不是伪造的 False。
    pc1n, c1n, h1n, o2n, l2n = (s.astype("Float64") for s in (pc1, c1, h1, o2, l2))
    # 买腿可执行:T+1 收盘未封涨停(收盘≈日高 且 当日涨幅≈板)—— 封板收盘买不进;
    # D+1 缺数 → <NA>(未知,不是"可买")
    buy_sealed = (pc1n >= lim * 0.98) & (c1n >= h1n - 1e-6)
    res["buyable_c1"] = ~buy_sealed
    # 卖腿受限:T+2 一字跌停开(开≈日低 且 开盘较 c1 跌≈板)—— 标旗不剔;
    # D+2 缺数 → <NA>(未知,不是"卖得出")
    open_limit_dn = (o2n <= l2n + 1e-6) & (o2n <= c1n * (1 - lim * 0.98 / 100.0))
    res["unsellable_o2"] = open_limit_dn
    return res


def forward_frame(piv: dict, P: list[str], D: str) -> pd.DataFrame | None:
    """D 日全湖前向收益帧(index=code;列含三尺 + buyable/buyable_c1)。D 不在 P 或 pivot 空 → None。
    `|gap_c1_o2| > GAP_CLIP` 在板制度下不可能 → 视作数据错,置 NaN(计数进 meta)。"""
    if not piv or D not in P:
        return None
    fr = forward_returns(piv, P, D, fwd=10)
    bad = fr[_ruler.MAIN_RULER].abs() > _ruler.GAP_CLIP
    fr.attrs["n_clipped"] = int(bad.fillna(False).sum())
    fr.loc[bad.fillna(False), _ruler.MAIN_RULER] = np.nan
    return fr
