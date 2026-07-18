#!/usr/bin/env python3
"""数据契约 —— 每次取数(含**湖命中**)的内容校验:地基数据空/残缺 → 抛异常阻断;增强数据降级但记账。

design: docs/specs/2026-07-12-data-contracts-design.md。用户裁定(2026-07-12):
"取数以后要有一个全面校验,为空的时候要抛出异常阻断"。

## 为什么需要它:三条"空"汇入同一个下水道

底层 `_ts_call` 是对的(重试 4 次后 `raise`)。病在上一层:

1. **调用方把异常吞成空**:`_harvest_vol_series` 失败 → 返回空帧;`_fetch_hk_hold` 失败 → None;
   `tushare_enrich`/`keyless`/`handler` 另有 8 处同款。每处只打印一行 warn 就继续跑。
2. **缓存把"空"永久钉死**:`cache._atomic_write` 空帧也写("存在==取过且为空")→ 一次失败拉到的空
   入湖后**永不重拉**(湖内实测:hk_hold 14/419 空、stk_surv 2/12 空)。同族:窄表毒化(见
   `cache._lake_params`)、空 pickle(factor_lab)。
3. **真实的空**(合法):无北向额度的日子 hk_hold 就是空;某日无公告 → forecast/anns_d 也是空。

而这三条空最后都汇入 `scoring.composite_score`:

    comp  += (s - 0.5).fillna(0.0) * w          # 某组全 NaN → 贡献 0
    wabs  += s.notna().astype(float) * w.abs()  # 该组从分母里消失
    raw    = comp / wabs.replace(0, np.nan)     # 其余组权重被自动放大

**某个因子组整组死掉,composite 照样输出一个 0–100 的漂亮分数**,漏斗照常跑完、退出码 0。
2026-07-12 实证(漏斗回放器 M1 对拍逮到):lake 的 daily 被窄表毒化 → volprice 组整组 NaN →
全市场打分失真 **98.8%**、L2 名单 jaccard 掉到 **0.36**,唯一信号是一行淹没在日志里的 warn。
**系统有降级能力,没有"我降级了"的传达能力** —— 这才是真正要修的东西。

## 为什么不是"见空就抛"

无差别抛异常会打断两类合法路径:真实的空(上文 3)、以及 presence-gated 的增强端点(质押/席位/
调研/一致预期缺失时漏斗仍成立——那是设计,不是 bug)。故分两级:

- **A 级(地基)**:行情/估值/资金/筹码/技术因子/证券基础。空、行数腰斩、关键列缺失 →
  `DataContractError` **阻断**,且**拒绝入湖**(否则脏数据被钉死,重跑也自愈不了)。
- **B 级(增强)**:北向/两融/龙虎榜/公告/质押/新闻/宏观。缺失只降级,但**必须记账**
  (`degradations()`)——降级从此是显式的、可见的、可审计的。

哲学承接 `tushare_source.assert_tushare_ready`(「空结果 → 抛错中止,不静默跑残缺」),把它从
"3 个端点的发布就绪探测"推广成"每个端点、每次取数的内容契约",并补上它管不到的两处:
**取数后的内容**(行数/列)与**湖命中路径**(已入湖的脏数据读出来同样要被逮到)。

CLI(湖体检):
    uv run --no-sync python -m autoresearch.data.contracts doctor          # 列出违约文件
    uv run --no-sync python -m autoresearch.data.contracts doctor --purge  # 删掉待重拉
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

TIER_BLOCKING = "A"     # 地基:空/残缺 → 抛异常阻断,且不入湖
TIER_DEGRADE = "B"      # 增强:缺失 → 降级 + 记账,不阻断

# 全市场端点的行数腰斩线:A股 ~5,400 只存续 → 正常日 ~5,000+ 行。低于此 = 拉了一半就断了
# (ChunkedEncodingError 半途而废是已知偶发,见 workflow 的 universe 重试),不是"今天股票变少了"。
_MARKET_MIN_ROWS = 3000

# **规模性检查开关**(行数腰斩)。生产恒开;`tests/conftest.py` 全局关掉 —— 单测用合成小 fixture
# (几十~几百只)是常态,拿生产的全市场行数线去卡它是误报。
#
# 结构性检查(空帧 / 缺列 / 整列全 NaN)**不受此开关影响**:那是无论数据规模都成立的 bug,
# 也正是真实事故的形态(窄表毒化的签名是"行数够、但缺 high/low/amount")。两者性质不同,别合并。
CHECK_ROWS = True


class DataContractError(RuntimeError):
    """地基数据违约(空/行数腰斩/关键列缺失)——**阻断整条流程**,拒绝静默跑残缺。"""


@dataclass(frozen=True)
class Contract:
    tier: str
    required_cols: frozenset[str] = field(default_factory=frozenset)
    min_rows: int = 0
    note: str = ""
    # B 级专用:某交易日 0 行 = **真实合法空**(forecast/express 无公告日就是没有)。
    # 记账仍留痕(审计不丢),但不进告警渲染(`render`)——降级告警面只留"无权限/报错"类真降级
    # (Minor-1,survey 2026-07-13 线 D)。
    empty_ok: bool = False


def _c(tier: str, cols: str = "", min_rows: int = 0, note: str = "",
       empty_ok: bool = False) -> Contract:
    return Contract(tier, frozenset(cols.split()) if cols else frozenset(), min_rows, note, empty_ok)


# ───────────────────────── 契约表 ─────────────────────────
#
# A 级 = 漏斗的地基,缺了打分就残废(且残废得看不出来——见模块 docstring)。
# required_cols 只列**下游真正会读的列**(不是端点的全部字段),避免把 tushare 加字段/改字段
# 变成误报。min_rows 只给全市场端点(标的级/公告类端点的行数天然随日期波动)。

CONTRACTS: dict[str, Contract] = {
    # ── A 级:地基 ──
    "daily": _c(TIER_BLOCKING, "ts_code open high low close amount pct_chg", _MARKET_MIN_ROWS,
                "行情:high/low/amount 喂 volprice 组(cmf/obv);缺列即整组 NaN"),
    "daily_basic": _c(TIER_BLOCKING, "ts_code close turnover_rate pe_ttm pb total_mv circ_mv",
                      _MARKET_MIN_ROWS, "估值/市值:L0 硬门(cap_floor)与 value 组的地基"),
    "moneyflow": _c(TIER_BLOCKING, "ts_code", _MARKET_MIN_ROWS, "主力资金:fund_main 组"),
    "cyq_perf": _c(TIER_BLOCKING, "ts_code", _MARKET_MIN_ROWS, "筹码:chip 组"),
    "stk_factor_pro": _c(TIER_BLOCKING, "ts_code", _MARKET_MIN_ROWS, "技术因子:tech 组(rsi/ma)"),
    "stock_basic": _c(TIER_BLOCKING, "ts_code name list_date", _MARKET_MIN_ROWS,
                      "证券基础:名称 + 上市日(剔次新硬门)"),
    "trade_cal": _c(TIER_BLOCKING, "", 1, "交易日历:所有 as-of 推导的地基"),

    # ── B 级:增强(缺失 → presence-gated 降级 + 记账)──
    "hk_hold": _c(TIER_DEGRADE, note="北向持股:已知有真实空档期(2026-07-08/09 即是)"),
    "margin_detail": _c(TIER_DEGRADE, note="两融明细:rz 组"),
    "block_trade": _c(TIER_DEGRADE, note="大宗交易:已入湖未接消费"),
    "top_inst": _c(TIER_DEGRADE, note="龙虎榜机构席位:L4 advisory"),
    "top_list": _c(TIER_DEGRADE, note="龙虎榜明细:L4 advisory"),
    "limit_list_d": _c(TIER_DEGRADE, note="涨跌停:温度计五序列(缺 → 相位降级 None)"),
    "moneyflow_ind_ths": _c(TIER_DEGRADE, note="行业资金流:sector pack"),
    "forecast": _c(TIER_DEGRADE, note="业绩预告:某日无公告 = 真实空", empty_ok=True),
    "express": _c(TIER_DEGRADE, note="业绩快报:同上", empty_ok=True),
    "anns_d": _c(TIER_DEGRADE, note="信息披露公告:已退役(2026-07-18):结构化公告标题流由 "
                 "stock_news_em 头条 + l4-intel 活体盲搜双重覆盖;empty_rate=1.0 为 "
                 "expected(no-permission),非告警"),
    "stk_holdertrade": _c(TIER_DEGRADE, note="股东增减持:催化"),
    "repurchase": _c(TIER_DEGRADE, note="回购:催化"),
    "stk_surv": _c(TIER_DEGRADE, note="机构调研:某日无调研 = 真实空", empty_ok=True),
    "moneyflow_hsgt": _c(TIER_DEGRADE, note="沪深港通区间"),
    "margin": _c(TIER_DEGRADE, note="两融区间汇总"),
    "index_dailybasic": _c(TIER_DEGRADE, note="指数估值时序"),
    "stk_holdernumber": _c(TIER_DEGRADE, note="股东户数"),
    "pledge_stat": _c(TIER_DEGRADE, note="质押统计:L4 陷阱旗"),
    "stock_zh_a_gdhs_detail_em": _c(TIER_DEGRADE),
    "stock_restricted_release_queue_em": _c(TIER_DEGRADE, note="解禁队列"),
    "stock_news_em": _c(TIER_DEGRADE, note="个股新闻"),
    "stock_lhb_stock_statistic_em": _c(TIER_DEGRADE),
    "stock_yjbb_em": _c(TIER_DEGRADE, note="业绩(报告期)"),
    "macro_china_cpi_monthly": _c(TIER_DEGRADE),
    "macro_china_ppi": _c(TIER_DEGRADE),
    "macro_china_pmi": _c(TIER_DEGRADE),
    "macro_china_money_supply": _c(TIER_DEGRADE),
    "macro_china_lpr": _c(TIER_DEGRADE),
    "macro_china_shrzgm": _c(TIER_DEGRADE),
    "fred": _c(TIER_DEGRADE, note="宏观时序"),
    "yfinance": _c(TIER_DEGRADE, note="跨资产历史价"),
    # live 端点(spot/资金流榜/涨停池)不入湖、不校验 —— 见 `check` 的 policy 短路。
}


# ───────────────────────── 降级记账(B 级) ─────────────────────────
#
# 降级必须是**显式的、可见的、可审计的** —— 这是本模块存在的另一半理由。进程级累积,漏斗末端
# 由 `universe.run` 落 `degraded.json` 并汇总进报告(见 `render`)。

_DEGRADED: list[dict] = []


def degradations() -> list[dict]:
    """本进程累积的 B 级降级记录(端点/原因/来源/key)。"""
    return list(_DEGRADED)


def record_degradation(endpoint: str, reason: str, *, key: str = "", kind: str = "degraded") -> None:
    """手工记一笔 B 级降级 —— 给**不走 `cache.get_or_fetch`** 的降级点用。

    有些增强数据是直接调 tushare 的(`_fetch_hk_hold` 就是:`try/except → return None`),
    契约层看不见它们。但它们的降级同样必须可见:2026-07-09 的 `hk_ratio` 全表为空(北向因子
    整组失效),当时唯一的痕迹就是一行 `[warn] hk_hold 取数失败 → 北向因子降级`,没人看见,
    也没有任何账本记得这件事 —— 直到回放器对拍时才被发现。

    `kind`:"degraded"(默认,进告警渲染)| "legit_empty"(合法空,留痕不告警,见 `render`)。
    """
    _DEGRADED.append({"endpoint": endpoint, "key": key, "source": "direct", "reasons": [reason],
                      "kind": kind})
    print(f"[数据契约·B级降级] {endpoint}" + (f"[{key}]" if key else "") + f":{reason}(已记账)",
          file=sys.stderr)


def clear_degradations() -> None:
    _DEGRADED.clear()


def render(records: list[dict] | None = None) -> str:
    """降级清单 → 一行 md(空 → "");供 meta/报告显示。

    **告警面过滤**(Minor-1,survey 2026-07-13 线 D):`kind == "legit_empty"`(forecast/express
    这类"某日 0 行 = 真实空",契约 `empty_ok=True`)不进这行 —— 但仍留在 `degradations()`/
    `degraded.json` 里可审计。旧记录无 `kind` 字段 → 按降级渲染(向后兼容,老现场不静默消失)。
    """
    recs = degradations() if records is None else records
    recs = [r for r in recs if r.get("kind", "degraded") != "legit_empty"]
    if not recs:
        return ""
    by_ep: dict[str, int] = {}
    for r in recs:
        by_ep[r["endpoint"]] = by_ep.get(r["endpoint"], 0) + 1
    items = ", ".join(f"{k}×{v}" for k, v in sorted(by_ep.items(), key=lambda x: -x[1]))
    return f"- **⚠️ 数据降级**(B 级增强端点缺失,漏斗仍成立但相关因子/旗为空):{items}"


# ───────────────────────── 校验 ─────────────────────────


def violations(endpoint: str, df: pd.DataFrame | None, *, cols: bool = True) -> list[str]:
    """纯函数:返回违约清单(空 = 合规)。不 raise、不记账 —— 供 `check` 与湖体检共用。

    `cols=False`:跳过**列**契约,只查空/行数。用于未结算日的窄查询(见 `check`)。
    """
    con = CONTRACTS.get(endpoint)
    if con is None:
        return []                                   # 未登记契约 = 不校验(policy 层已逼登记端点)
    if df is None:
        return ["返回 None"]
    out: list[str] = []
    if len(df) == 0:
        out.append("空帧(0 行)")
    elif CHECK_ROWS and con.min_rows and len(df) < con.min_rows:
        out.append(f"行数腰斩({len(df)} < {con.min_rows} —— 多半是取数半途而废,不是市场变小了)")
    if cols:
        missing = sorted(con.required_cols - set(df.columns))
        if missing and len(df):                     # 空帧已报过,不重复刷列缺失
            out.append(f"缺列 {missing}")
    return out


def check(endpoint: str, df: pd.DataFrame | None, *, key: str = "", source: str = "fetch",
          cols: bool = True):
    """取数/湖命中后的契约校验。

    - **A 级违约 → `DataContractError`**(阻断整条流程)。调用方(`cache.get_or_fetch`)据此
      **拒绝把脏数据写入湖** —— 否则它会被钉死,之后每次命中都是脏的,重跑也自愈不了。
    - B 级违约 → 记账进 `degradations()` 并 warn,**不阻断**(presence-gated 是设计)。
      其中契约 `empty_ok=True` 的端点**恰为空帧** = 真实合法空(某日无预告/快报/调研)→
      记 `kind="legit_empty"`:留痕可审计,但不进告警渲染(`render` 过滤);
      返回 None/缺列等报错形态不在此列,照旧按降级告警。

    `source`:'fetch'(刚拉的)| 'lake'(湖命中的)—— 湖命中同样要校验:历史脏数据(空帧/窄表)
    读出来一样会毒化下游,而且它**不会**再触发取数路径的任何检查。

    `cols`:**列契约只管会被持久化/复用的数据**。未结算日(date>=today)的查询不入湖、只服务当次
    调用,调用方要哪几列是它自己的事(温度计就只要 `ts_code,pct_chg`)——对它套"全字段"是错的,
    故 `cols=False`。但**空仍然要抛**:A 级端点在盘中拉到空 = 数据还没发布,下游必然残废,
    与 `assert_tushare_ready` 同一立场("晚点再跑,或跑前一交易日")。
    """
    v = violations(endpoint, df, cols=cols)
    if not v:
        return df
    con = CONTRACTS[endpoint]
    where = f"{endpoint}" + (f"[{key}]" if key else "") + f"(来源:{'湖命中' if source == 'lake' else '取数'})"
    if con.tier == TIER_BLOCKING:
        hint = ("湖里的这份数据已损坏 → `python -m autoresearch.data.contracts doctor --purge` "
                "删掉后重拉" if source == "lake" else
                "取数残缺(限频/网络/权限?)→ 稍后重试;若持续,查 tushare 权限与 assert_tushare_ready")
        raise DataContractError(
            f"[数据契约·A级] {where} 违约:{'; '.join(v)}\n"
            f"  用途:{con.note or '漏斗地基'}\n"
            f"  为什么阻断:该组因子会整组 NaN,而 composite_score 会把它从分母剔除、放大其余组权重\n"
            f"           → 打分照样输出 0–100,漏斗照样跑完,**残废得看不出来**(2026-07-12 实证:失真 98.8%)。\n"
            f"  怎么办:{hint}")
    kind = "legit_empty" if (con.empty_ok and v == ["空帧(0 行)"]) else "degraded"
    _DEGRADED.append({"endpoint": endpoint, "key": key, "source": source, "reasons": v, "kind": kind})
    if kind == "legit_empty":
        print(f"[数据契约·B级合法空] {where}:该日 0 行为真实空(留痕不告警)", file=sys.stderr)
    else:
        print(f"[数据契约·B级降级] {where}:{'; '.join(v)} → 相关因子/旗置空(已记账)", file=sys.stderr)
    return df


# ───────────────────────── 因子帧契约(漏斗地基的最后一道门) ─────────────────────────

# 每列 = 一个因子组的代表列(或 L0 硬门的输入):缺它 = 该组整组 NaN / 该门失效,而 composite
# 照样输出漂亮分数。列名与 `scoring._factor_groups` 的输入对齐。
_FRAME_CORE = ("code", "close", "mktcap_yi", "pct_60d", "main_net_ratio")
_FRAME_VOLPRICE = ("cmf_20", "obv_mom_20")     # volprice 组:唯一的多日序列组,最易静默丢失

_FRAME_MIN_ROWS = 2000    # L0 硬门(市值/次新/流动性)后仍应有数千只;低于此 = 上游残缺


def check_market_frame(df: pd.DataFrame, *, with_vol_series: bool = True) -> pd.DataFrame:
    """全市场因子帧的出口契约(`scan.frame.build_market_frame` 末尾调)——**漏斗地基的最后一道门**。

    前面每一道校验都可能被绕过(新的 try/except、新的取数路径、湖里的历史脏数据),但**打分帧本身
    残缺就是残缺**:这里查的是"喂给 composite_score 的东西到底全不全",与它从哪来无关。

    `with_vol_series=False`(盘前只要 regime/哨兵的省时路径)→ 不查 volprice 列(显式跳过 ≠ 静默丢失)。
    """
    v: list[str] = []
    if df is None or not len(df):
        v.append("因子帧为空(L0 取数或硬门把全市场筛没了?)")
    else:
        if CHECK_ROWS and len(df) < _FRAME_MIN_ROWS:
            v.append(f"行数腰斩({len(df)} < {_FRAME_MIN_ROWS})")
        need = list(_FRAME_CORE) + (list(_FRAME_VOLPRICE) if with_vol_series else [])
        miss = [c for c in need if c not in df.columns]
        if miss:
            v.append(f"缺关键列 {miss}")
        else:                       # 列在但整列全 NaN = 同样的死法(列在场骗过了列检查)
            dead = [c for c in need if c != "code" and df[c].isna().all()]
            if dead:
                v.append(f"整列全 NaN {dead}")
    if v:
        raise DataContractError(
            f"[数据契约·A级] 全市场因子帧违约:{'; '.join(v)}\n"
            f"  为什么阻断:composite_score 对缺失/全 NaN 的组会自动把它从分母剔除、放大其余组\n"
            f"           权重 → 打分照样输出 0–100,漏斗照样跑完,**残废得看不出来**\n"
            f"           (2026-07-12 实证:volprice 组静默丢失 → 全市场打分失真 98.8%)。\n"
            f"  怎么办:`python -m autoresearch.data.contracts doctor --purge` 清湖内毒源后重跑。")
    return df


# ───────────────────────── 湖体检 CLI ─────────────────────────


def doctor(lake: Path | str | None = None, purge: bool = False) -> dict:
    """湖体检:逐 parquet 跑契约,列出(可选删除)违约文件。

    A 级违约文件 = 毒源(空帧/窄表/坏文件),`--purge` 删掉即可让下次取数重拉。
    B 级空帧多为真实的空(无北向额度的日子),**不删**——删了只会每天重拉一次空。
    """
    from autoresearch.data.cache import LAKE

    root = Path(lake) if lake else LAKE
    out: dict = {"blocking": [], "degraded": [], "unreadable": [], "purged": []}
    if not root.exists():
        return out
    for p in sorted(root.glob("*/*.parquet")):
        endpoint = p.parent.name
        con = CONTRACTS.get(endpoint)
        if con is None:
            continue
        try:
            df = pd.read_parquet(p)
        except Exception as e:  # noqa: BLE001 — 坏文件本身就是违约
            out["unreadable"].append({"file": str(p), "error": repr(e)[:80]})
            if purge:
                p.unlink()
                out["purged"].append(str(p))
            continue
        v = violations(endpoint, df)
        if not v:
            continue
        rec = {"file": str(p), "endpoint": endpoint, "reasons": v}
        if con.tier == TIER_BLOCKING:
            out["blocking"].append(rec)
            if purge:
                p.unlink()
                out["purged"].append(str(p))
        else:
            out["degraded"].append(rec)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(prog="contracts", description="数据契约:湖体检")
    ap.add_argument("cmd", choices=["doctor"])
    ap.add_argument("--purge", action="store_true", help="删掉 A 级违约与坏文件(下次取数重拉)")
    ap.add_argument("--lake", default=None)
    a = ap.parse_args(argv)

    res = doctor(a.lake, purge=a.purge)
    print(f"🚨 A级违约(毒源,必须清): {len(res['blocking'])}")
    for r in res["blocking"][:20]:
        print(f"   {r['file']} — {'; '.join(r['reasons'])}")
    print(f"⚠️  B级空帧(多为真实的空,不删): {len(res['degraded'])}")
    print(f"💥 坏文件: {len(res['unreadable'])}")
    if a.purge:
        print(f"🧹 已删除 {len(res['purged'])} 个 → 下次取数自动重拉")
    elif res["blocking"] or res["unreadable"]:
        print("\n→ 跑 `python -m autoresearch.data.contracts doctor --purge` 清掉毒源")
    print(json.dumps({k: len(v) for k, v in res.items()}, ensure_ascii=False))
    return 1 if (res["blocking"] or res["unreadable"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
