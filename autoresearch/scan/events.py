"""全市场事件计数(Wave4 Task 2;确定性,零 LLM)。

plan: docs/plans/2026-07-24-wave4-event-recall-plan.md。

与 `agents/l3_catalyst.py` 的关系:**分类逻辑 100% 复用它的 `catalyst_counts` 纯函数**
(增减持 in_de / 回购 proc / 调研计数,已确定性、已单测),本模块只改两件事——
①覆盖面:L2-200 → **全市场**(不传 want 过滤);②时机:L3 阶段 → **L1 之前**
(要能把没进 L2 的票也捞出来,否则事件召回路无意义)。

三端点均已入湖(`ann_date`/`trade_date` 键,eod 不可变)→ `get_or_fetch` 湖命中零网络。
降级三层,层层不许沉默:①单端点单日失败 → 跳过该次,余下照拉;②单腿(某端点)全窗口
零产出 → 单独告警(Review Round 1 I-4:与紧邻的上一个 commit 治的"anns_d 退役静默产
零值三扫描日无人察觉"逐字同族病灶,不许躲进"另外两条腿还在出数"里沉默);③三腿全失败
→ 空帧 + stderr 告警,不静默返回空。
"""
from __future__ import annotations

import sys

import pandas as pd

from autoresearch.scan.agents.l3_catalyst import _ENDPOINTS, catalyst_counts

# catalyst_counts 原始列 → 本模块 ev_ 前缀列(量纲一致:每条记录都算 1 件,可以直接相加)。
_RENAME = {"rep_impl": "ev_rep_impl", "rep_plan": "ev_rep_plan",
           "holder_in": "ev_holder_in", "holder_de": "ev_holder_de", "surv_n": "ev_surv_n"}
_RAW_EVENT_COLS: tuple[str, ...] = tuple(_RENAME.values())

# 正催化列源,与 learning/catalyst_ledger._POS 同一组列名(rep_impl/rep_plan/holder_in/surv_n,
# 减持 holder_de 均不算正)——**用法不同,不是口径分叉**:catalyst_ledger 只把这几列当**布尔
# 旗**用(`cat[pos_cols].sum(axis=1) > 0`,只问"有没有"),从没把它们加总成一个可排序的量,
# 所以它不曾继承下面这个病。本模块要产出一个可排序的"事件强度"分数(`ev_pos` 要喂
# `recall/channels.py` 的 `gate_rank` 排序),若把量纲完全不同的计数直接相加,会被
# `surv_n`(调研接待机构家数,理论上不封顶)主导——
# **2026-07-21 真湖实证(Review Round 1 I-5)**:全市场裸 `ev_pos` 总量 1078,`surv_n` 占
# **693(64%)**;按裸 `ev_pos` 降序 **top10 全部 10/10 是纯调研**(max=82:000729 一次接待
# 82 家机构 → 裸 `ev_pos`=82;而一次**回购实施**只算 1);裸 `ev_pos≥5` 的 35 只里
# **29 只(83%)零回购零增持**;取 top50,**只有 26%** 带任何实体回购/增持事件。照此,
# 这条召回路实际是"近 10 日被调研机构家数排行",不是"事件强度"。故 `ev_pos` 不能是裸
# 求和,见下方 `ev_hard`/`ev_pos` 的定义——**改回裸相加前,请先重新核对这组数字是否仍然
# 反直觉**。
_POS_SRC = ("rep_impl", "rep_plan", "holder_in", "surv_n")
# ev_hard 只取 _POS_SRC 里的"真实公司行为"三项——调研(surv_n)单独处理,见 ev_pos。
_HARD_SRC: tuple[str, ...] = tuple(_RENAME[k] for k in _POS_SRC if k != "surv_n")

EVENT_COLS: tuple[str, ...] = (*_RAW_EVENT_COLS, "ev_hard", "ev_pos")


def _empty() -> pd.DataFrame:
    return pd.DataFrame({"code": pd.Series(dtype=str),
                         **{c: pd.Series(dtype=float) for c in EVENT_COLS}})


def _fetch_day(endpoint: str, day: str, date: str) -> pd.DataFrame:
    """单端点单日取数(湖优先);module-attr 派发便于测试注入。"""
    from autoresearch.data.cache import get_or_fetch
    return get_or_fetch(endpoint, {_ENDPOINTS[endpoint]: day}, today=date)


def market_event_counts(date: str, *, lookback_days: int = 10, fetch_fn=None) -> pd.DataFrame:
    """近 lookback_days 交易日三端点全市场事件计数;列 = code + EVENT_COLS。

    `ev_hard` = 真实公司行为(回购实施 + 回购预案 + 增持),每次都算 1 件,量纲一致可以
    直接相加。`ev_pos` = `ev_hard + min(ev_surv_n, 1)`——调研(机构接待家数)最多贡献 1,
    只表达"近 lookback_days 交易日有没有被调研"而非"被多少家机构调研",理由见上方
    `_POS_SRC` 注释的真湖数字。**两者都不是"正催化加权和"**(brief 原文写的"加权"从未
    定义权重,如实澄清,不是实现漏了权重)。
    """
    from autoresearch.scan.agents.l3_news import _trade_days_for
    fetch = fetch_fn or _fetch_day
    frames: dict[str, list[pd.DataFrame]] = {ep: [] for ep in _ENDPOINTS}
    ok_by_ep: dict[str, int] = dict.fromkeys(_ENDPOINTS, 0)
    for day in _trade_days_for(date, lookback_days):
        for ep in _ENDPOINTS:
            try:
                df = fetch(ep, day, date)
            except Exception:  # noqa: BLE001 — 单端点单日失败跳过,余下照拉
                continue
            if df is not None and len(df):
                frames[ep].append(df)
                ok_by_ep[ep] += 1
    ok = sum(ok_by_ep.values())
    if not ok:
        print(f"[events] ⚠️ 事件取数三腿全失败({date},近 {lookback_days} 交易日)"
              "→ 事件列全 0,事件召回路本日等同停用。", file=sys.stderr)
        return _empty()
    # I-4:三腿只要还有一条出数,上面的 all-or-nothing 分支就不会触发——但被拖累的那一两条
    # 腿仍会让对应 ev_* 列静默恒 0,`ev_pos` 却看上去仍然"健康"。与紧邻的上一个 commit
    # (Wave4 Task 1)治的"anns_d 退役静默产零值三扫描日无人察觉"逐字同族,这里逐腿单独
    # 记账,不许躲进整体 ok>0 里(现实相关性:2026-07-21 真湖 `stk_surv` 没有 20260721
    # 而另两腿有)。
    for ep, n in ok_by_ep.items():
        if n == 0:
            print(f"[events] ⚠️ 事件取数单腿全失败:{ep}(近 {lookback_days} 交易日 0 条出数)"
                  "→ 对应 ev_* 列本日静默恒 0,其余腿正常不受此影响。", file=sys.stderr)

    want = {str(c).split(".")[0].zfill(6)
            for ep in frames for df in frames[ep] for c in df.get("ts_code", [])}
    if not want:
        return _empty()
    cnt = catalyst_counts(frames, want)
    if cnt is None or not len(cnt):
        return _empty()
    out = cnt.rename(columns=_RENAME)
    for c in _RAW_EVENT_COLS:
        if c not in out.columns:
            out[c] = 0.0
        out[c] = out[c].astype(float)      # m-1:两条返回路统一 float(_empty() 路本就是 float)
    out["ev_hard"] = sum(out[c] for c in _HARD_SRC)
    out["ev_pos"] = out["ev_hard"] + out[_RENAME["surv_n"]].clip(upper=1.0)
    return out[["code", *EVENT_COLS]]


def attach_event_cols(scored: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    """事件列左连接进 scored(不就地改入参);缺 → 0.0。

    挂在 `composite_score` **之后**——不进 `build_market_frame`,避开 A 级规模契约。
    """
    out = scored.copy()
    out["code"] = out["code"].astype(str).str.split(".").str[0].str.zfill(6)
    if ev is None or not len(ev):
        for c in EVENT_COLS:
            out[c] = 0.0
        return out
    e = ev.copy()
    e["code"] = e["code"].astype(str).str.split(".").str[0].str.zfill(6)
    # m-2(顺手修,今天不可达但成本低于收益):对已挂过事件列的帧重复调用会撞列名,merge
    # 产生 `_x`/`_y` 后缀、原列名从结果里消失,随后下面 `else 0.0` 分支会把 ev_pos 等静默
    # 归零。merge 前先丢弃 out 里可能已存在的同名事件列,保证重复调用幂等(后调用者赢)。
    out = out.drop(columns=[c for c in EVENT_COLS if c in out.columns], errors="ignore")
    out = out.merge(e[["code", *[c for c in EVENT_COLS if c in e.columns]]],
                    on="code", how="left")
    for c in EVENT_COLS:
        out[c] = out[c].fillna(0.0) if c in out.columns else 0.0
    return out
