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

# 热度快照的行数腰斩线(同一手法,只是"正常行数"由接口形态而非市场规模决定):
# 东财人气榜 = 固定一页 TOP100(pageSize=100,少一行就是截断);雪球全市场关注榜实测 5,619 行
# (内部 29 页 × 200),中途限流会掉到几千 —— 5,000 这条线专治"拉了一半就断了"。
# 残余风险:只掉最后 1 页(≤200 行)仍在线上,端点侧拿不到 API 自报的 `count` 做逐页对账。
_HOT_RANK_MIN_ROWS = 100
_XQ_FOLLOW_MIN_ROWS = 5000

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
    # **违约帧是否允许入湖**(2026-08-09 复核 C2)。默认 True = 现行为(B 级违约照样落盘,
    # 因为 date 键端点的"这天就是这样"是真事实,重拉也一样)。
    # 快照型端点必须置 False:它们的违约形态是**半截/空**(雪球 29 页中途限流 → akshare 自己
    # `except TypeError` 吞掉 → 静默返回 3000 行而不抛),一旦落盘 `path.exists()` 恒命中,
    # 这一天永远残缺、**重跑也自愈不了** —— 与 A 级"拒绝入湖"同一条理由,但不必阻断漏斗。
    persist_violations: bool = True


def _c(tier: str, cols: str = "", min_rows: int = 0, note: str = "",
       empty_ok: bool = False, persist_violations: bool = True) -> Contract:
    return Contract(tier, frozenset(cols.split()) if cols else frozenset(), min_rows, note,
                    empty_ok, persist_violations)


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
    # 热度快照(Wave12 T2;design docs/research/2026-08-09-hot-rank-probe.md):爬虫式榜单接口
    # 脆弱,一律 B 级——断采只损失当日、不阻断扫描,但必须记账(prewarm._hot_rank_snapshot 消费)。
    # 快照型数据:今天不采,今天的历史就永远没有了(接口不接受历史参数,不能像 daily 那样事后
    # 用 trade_date 回补)——这正是它排进夜间预热优先级最高的唯一理由。
    #
    # **行数下限 + 关键列 + 违约不入湖**(2026-08-09 复核 C2):这两个端点的行数是恒定的
    # (人气榜 = TOP100 固定页;雪球全市场 ≈5,600),是全仓最适合设行数下限的两个端点。
    # 没有下限时,雪球中途限流静默返回的 3000 行会被判"合规"→ 记成 ✓ → 永久钉死半截。
    "eastmoney_hot_rank": _c(TIER_DEGRADE, "sc rk", _HOT_RANK_MIN_ROWS,
                             note="东财人气榜:TOP100 热度快照(自采第一跳,绕开被封的 push2),"
                                  "不可回填历史;sc=前缀式代码 rk=当前排名",
                             persist_violations=False),
    "stock_hot_follow_xq": _c(TIER_DEGRADE, "股票代码 关注", _XQ_FOLLOW_MIN_ROWS,
                              note="雪球关注度:全市场关注数快照,不可回填历史;"
                                   "symbol=最热门→「关注」是**累计**数,symbol=本周新增→同名"
                                   "「关注」列其实是 follow7d(7 日新增),按分区键区分,勿混读",
                              persist_violations=False),
    "stock_yjbb_em": _c(TIER_DEGRADE, note="业绩(报告期)"),
    "fina_mainbz": _c(TIER_DEGRADE, note="分业务收入/利润(dossier 业务模型;小票/金融股披露口径可缺)",
                      empty_ok=True),
    # ── B 级:衍生品(design 2026-08-03 §2;I 类基建,消费者默认关闭)──
    # 全部 B 级:期权/转债缺失时漏斗照常成立(它们不进 composite、不进 L0 硬门)。
    "opt_daily": _c(TIER_DEGRADE, "ts_code trade_date vol oi",
                    note="期权日行情:**无 IV/Greeks**,必须联结 opt_basic 元数据才有意义"),
    "opt_basic": _c(TIER_DEGRADE, "ts_code exchange call_put",
                    note="期权合约元数据:**首页恰 12,000 = 分页上限**,不是全量;"
                         "必须强制分页 + coverage 对账"),
    "cb_daily": _c(TIER_DEGRADE, "ts_code trade_date close", note="可转债日行情"),
    "cb_basic": _c(TIER_DEGRADE, "ts_code stk_code",
                   note="可转债基础表:`conv_price` 是**当前截面**,"
                        "拿它重算历史转股价值会泄漏未来(F3 capability gate ①)"),
    "cb_call": _c(TIER_DEGRADE, note="可转债强赎:强赎状态走本端点/公告,不是 cb_basic",
                  empty_ok=True),
    "cb_price_chg": _c(TIER_DEGRADE, note="转债价格变动:当前 token 无权限 → "
                       "F3 历史溢价因子 BLOCKED_BY_DATA", empty_ok=True),

    "macro_china_cpi_monthly": _c(TIER_DEGRADE),
    "macro_china_ppi": _c(TIER_DEGRADE),
    "macro_china_pmi": _c(TIER_DEGRADE),
    "macro_china_money_supply": _c(TIER_DEGRADE),
    "macro_china_lpr": _c(TIER_DEGRADE),
    "macro_china_shrzgm": _c(TIER_DEGRADE),
    "fred": _c(TIER_DEGRADE, note="宏观时序"),
    "yfinance": _c(TIER_DEGRADE, note="跨资产历史价"),

    # ── B 级:外源实时信息扩面(design 2026-08-28-external-evidence-expansion §9)──
    # 九个键**全部 B 级**:它们是 **I 类基建;消费者 presence-gated** —— 缺席时漏斗/报告照常
    # 成立(不进 composite、不进 L0 硬门、不进 regime),只是相应块整块不出现。
    # `empty_ok` 只给**源模块明写过"源成功,真实空"**的端点(见各自 record_degradation 的
    # kind="legit_empty"),不凭想象加 —— 一个错立的"合法空"会把真失败伪装成正常。
    "global_tape": _c(TIER_DEGRADE,
                      "symbol market close status bar_date session_complete fetched_at",
                      note="隔夜 tape(22 标的):**每个请求标的恰一行**,失败也留行(少一行 = "
                           "失败被静默吞掉);列取自 yf_tape.TAPE_COLUMNS —— status 区分 "
                           "ok/empty/failed,缺它就分不出「源真空」与「我们没拿到」"),
    "us_options": _c(TIER_DEGRADE,
                     note="美股期权链快照:报价易腐、不可回填 → 空/半截**不入湖**(落了就 "
                          "path.exists 恒命中,这一天永远残缺)",
                     persist_violations=False),
    "us_ticker": _c(TIER_DEGRADE,
                    note="美股票面(news/earnings_dates/upgrades/holders/info):微观 full 用;"
                         "尚无生产者,先登记后接线(D-1 canary)"),
    "cboe_vix": _c(TIER_DEGRADE, "date close", 2000,
                   note="CBOE VIX 全历史 csv(≈9,261 行,1990 起)= VIX 分位备源。行数下限专治"
                        "「拉了一半就断了」——分位对整段历史敏感,少一段 2008/2020 会静默偏移"
                        "而读数看起来一模一样;半截**不入湖**(同 C2)",
                   persist_violations=False),
    "fred_calendar": _c(TIER_DEGRADE,
                        note="FRED releases/dates:区间内 0 条发布 = 真实空(源模块记 "
                             "kind='legit_empty')",
                        empty_ok=True),
    "fomc_calendar": _c(TIER_DEGRADE,
                        note="FOMC 年度日程(官方页物化):快照 → 空/半截不入湖",
                        persist_violations=False),
    "official_event_calendar": _c(TIER_DEGRADE,
                                  note="官方事件时刻(Fed/BLS/BEA/IR):只为候选补准确时刻,"
                                       "确认才升 TIMED、否则保持 DATE_ONLY;快照 → 不入湖违约帧",
                                  persist_violations=False),
    "edgar": _c(TIER_DEGRADE,
                note="SEC EDGAR submissions:近 90 日 0 条申报 = 真实空(源模块记 "
                     "kind='legit_empty');需官方 UA + 速率遵守",
                empty_ok=True),
    "us_earnings_dates": _c(TIER_DEGRADE,
                            note="美股财报日历(yfinance earnings_dates):未来日期 + 历史 8 次"
                                 "实际波动对齐;尚无生产者,先登记后接线(D-1 canary)"),
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


def refuses_lake(endpoint: str, df: pd.DataFrame | None) -> bool:
    """这份帧**是否必须被拒之湖外**(B 级快照端点的空/半截)。`cache.get_or_fetch` 消费。

    2026-08-09 复核 C2:B 级违约照样落盘对 date 键端点没问题(那天就是那样),但对**快照型**
    端点是灾难 —— 落盘后 `path.exists()` 恒命中,这一天永远是半截/空,重跑也自愈不了
    (「cache 空 pickle 永不重拉」家训的 parquet 同族)。不落盘 = 同日重跑还能救回来。
    """
    con = CONTRACTS.get(endpoint)
    if con is None or con.persist_violations:
        return False
    return bool(violations(endpoint, df))


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

# ───── 覆盖率判据(2026-08-29 T3):**列在场 ≠ 列可用** ─────
#
# 门槛 0.90 = "这一列必须覆盖至少九成的股票,否则它代表的因子组已经残了"。
#
# 为什么要加:08-26(与 07-29)的生产帧里 `rsi6` / `rsi12` / `winner_rate` /
# `chip_concentration` / `price_to_cost` 五列的非空率**都是 0.5472** —— 21:xx 的 tushare
# 半载快照(`stk_factor_pro` / `cyq_perf` 只落了一半的票)。而当时这道门只查"列在不在"与
# "是不是整列全 NaN",半张表是 NaN 照样放行:`composite_score` 对半残的组按 `notna()` 逐股
# 重归一 —— **有值的那 54.7% 与没值的那 45.3% 用的是两套权重**,而打分照样输出 0–100、
# 漏斗照样跑完、退出码 0。08-29 复跑同一天(数据已补齐)实测 1.0000,坐实了那是取数窗口
# 问题而非市场事实 —— 但当晚**没有任何一行记录**说过这件事。
#
# 只查"列在场但覆盖率不足",不查"列整根缺席":缺席是合法降级(低权限 token 本就没有
# `stk_factor_pro`/`cyq_perf`),而且取数侧已经 `record_degradation` 记过账;半载才是那种
# "看起来一切正常"的失真。
_FRAME_MIN_COVERAGE = 0.90
# **阻断线**(2026-08-29 复核加):低于它才抛,介于两线之间只记账 + 告警。
#
# 为什么不是「低于 0.90 就阻断」:本条守卫立案时引的两个现场(08-26 与 07-29)实测覆盖率
# **0.547** —— 若 0.90 直接阻断,那两晚的扫描不是"带着半载 chip/tech 出报告",而是**整趟
# 不存在**。诊断书写的病是「**降级不留痕**」(composite 对缺失组自动剔分母、放大其余组权重,
# 而当晚没有任何一行记录说过),不是「不许降级」;把 warn 直接升成 kill 比证据要求的更强,
# 而代价是一个真实的夜间窗口(21:xx 正是 tushare 灌数半载、也正是扫描跑动的时刻)。
#
# 所以分两线,两种失败长得不一样:
#   coverage < 0.50  → A 级抛出(**这一组实际上不在了**,与「整列全 NaN / 行数腰斩」同族);
#   0.50 ≤ cov < 0.90 → `record_degradation` + 告警(进 `degraded.json` → 报告一行),
#                        让人看得见,但今晚照常有报告。
# 回滚杆 = 把 `_FRAME_BLOCK_COVERAGE` 提到 0.90(即恢复"低于 0.90 就阻断")。
_FRAME_BLOCK_COVERAGE = 0.50
# A 级来源列(空/半载 → 阻断):行情/动量/主力资金 + stk_factor_pro 与 cyq_perf 各一个代表。
_FRAME_COVERAGE_A = ("close", "pct_60d", "main_net_ratio", "rsi6", "winner_rate")
# B 级来源列(半载 → 降级记账,不阻断):北向 / 两融。
_FRAME_COVERAGE_B = ("hk_ratio", "rz_buy_intensity")


def _coverage(df: pd.DataFrame, col: str) -> float:
    return float(df[col].notna().mean())


def check_market_frame(df: pd.DataFrame, *, with_vol_series: bool = True) -> pd.DataFrame:
    """全市场因子帧的出口契约(`scan.frame.build_market_frame` 末尾调)——**漏斗地基的最后一道门**。

    前面每一道校验都可能被绕过(新的 try/except、新的取数路径、湖里的历史脏数据),但**打分帧本身
    残缺就是残缺**:这里查的是"喂给 composite_score 的东西到底全不全",与它从哪来无关。

    三条判据:① 行数(规模,受 `CHECK_ROWS` 开关)② 关键列在不在 / 是不是整列全 NaN
    ③ **覆盖率**(2026-08-29 新增):A 级来源列 `{close, pct_60d, main_net_ratio, rsi6,
    winner_rate}` 非空率 < **0.90** → `DataContractError` 阻断;B 级来源列
    `{hk_ratio, rz_buy_intensity}` < 0.90 → `record_degradation` 降级记账。

    判据 ③ 补的是判据 ② 的盲区:08-26 的生产帧里 rsi6/winner_rate 非空率 **0.547**(21:xx
    的 tushare 半载快照),列在场、不是全 NaN,于是这道门放行,而 composite 对半残的组按
    `notna()` 逐股重归一 —— 有值的那一半和没值的那一半用的是两套权重,打分照样漂亮。
    08-29 复跑同一天实测 1.0000:那是取数窗口,不是市场事实,而当晚账本上一行记录都没有。

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
        dead: list[str] = []
        if miss:
            v.append(f"缺关键列 {miss}")
        else:                       # 列在但整列全 NaN = 同样的死法(列在场骗过了列检查)
            dead = [c for c in need if c != "code" and df[c].isna().all()]
            if dead:
                v.append(f"整列全 NaN {dead}")
        # ③ 覆盖率:列在场、也不是全 NaN,但半张表是 NaN(已报过的列不重复刷)
        seen = set(miss) | set(dead)
        for c in _FRAME_COVERAGE_B:                 # B 级先记账:A 级抛出后这一趟就没了
            if c in df.columns and c not in seen and _coverage(df, c) < _FRAME_MIN_COVERAGE:
                record_degradation(
                    "market_frame",
                    f"{c} 非空率 {_coverage(df, c):.3f} < {_FRAME_MIN_COVERAGE:.2f}"
                    f"(B 级来源半载/缺席)→ 该组对没值的股票被重归一跳过,不阻断",
                    key=f"coverage_{c}")
        thin = [(c, _coverage(df, c)) for c in _FRAME_COVERAGE_A
                if c in df.columns and c not in seen and _coverage(df, c) < _FRAME_MIN_COVERAGE]
        # 半载分两档:低于阻断线 = 这一组实际上不在了(抛);两线之间 = 记账 + 告警,今晚照常出报告。
        for c, cov in thin:
            if cov >= _FRAME_BLOCK_COVERAGE:
                record_degradation(
                    "market_frame",
                    f"{c} 非空率 {cov:.3f} < {_FRAME_MIN_COVERAGE:.2f}(半载快照:列在场、"
                    f"不全 NaN,只覆盖了一部分票)→ composite 对没值的股票剔该组分母、"
                    f"放大其余组权重;≥{_FRAME_BLOCK_COVERAGE:.2f} 故不阻断,但这一趟的打分"
                    f"**不是满配**,读排名时按此折价",
                    key=f"coverage_{c}")
        blocking = [(c, cov) for c, cov in thin if cov < _FRAME_BLOCK_COVERAGE]
        if blocking:
            v.append("A 级列非空率低于阻断线 "
                     + f"{_FRAME_BLOCK_COVERAGE:.2f}:"
                     + ", ".join(f"{c}={cov:.3f}" for c, cov in blocking)
                     + "(半载快照:列在场、不全 NaN,但多数票没值 = 该组实际上不在了)")
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
