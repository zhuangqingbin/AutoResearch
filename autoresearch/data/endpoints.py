"""端点 policy registry —— 决定每个取数端点怎么 key、是否入湖、今天是否取新。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §B。

每条 policy:
  key    : "date" | "period" | "as_of" | "static" | None
           lake 文件名怎么取——date→交易日串;period→报告期;as_of→f"{entity}@{取数日}"
           的按取数日快照;static→单文件 "static";None→不入湖(live)。
  settle : "eod" | "live"
           eod → 收盘后结算、可入湖永不重取(过去日);live → 盘中实时、总取新不缓存。
  source : "tushare" | "akshare" | "fred" | "yfinance"  —— 路由到 sources.fetch 的取数后端。

分桶(对齐 spec §B):
  ① 入湖·永不重取(settle=eod, key=date/period):全市场历史快照、按交易日切。
  ② 入湖·按取数日快照(settle=eod, key=as_of):内容随取数日变(户数/解禁/卖方/news),
     用 entity@as_of 做键,取一次永久留底,换天再取写新键。
  ②' 静态(key=static):证券基础信息/交易日历——单文件,刷新即覆盖一份。
  ③ 不入湖·总取新(settle=live, key=None):盘中未结算(spot/资金流榜/涨停池)。

加新端点 = 加一行。policy(unknown) 抛 KeyError(逼显式登记,不让未知端点静默漏缓存)。
"""
from __future__ import annotations

# ───────────────────────── 端点登记表 ─────────────────────────

ENDPOINTS: dict[str, dict] = {
    # ── ① tushare 全市场历史(按交易日;收盘即结算,永不重取) ──
    "daily": {"key": "date", "settle": "eod", "source": "tushare"},
    "daily_basic": {"key": "date", "settle": "eod", "source": "tushare"},
    "stk_factor_pro": {"key": "date", "settle": "eod", "source": "tushare"},
    "cyq_perf": {"key": "date", "settle": "eod", "source": "tushare"},
    "moneyflow": {"key": "date", "settle": "eod", "source": "tushare"},
    "hk_hold": {"key": "date", "settle": "eod", "source": "tushare"},
    "margin_detail": {"key": "date", "settle": "eod", "source": "tushare"},
    "block_trade": {"key": "date", "settle": "eod", "source": "tushare"},
    "top_inst": {"key": "date", "settle": "eod", "source": "tushare"},      # 龙虎榜机构席位
    "top_list": {"key": "date", "settle": "eod", "source": "tushare"},      # 龙虎榜每日明细
    "limit_list_d": {"key": "date", "settle": "eod", "source": "tushare"},  # 涨跌停历史(macro 中观)
    "moneyflow_ind_ths": {"key": "date", "settle": "eod", "source": "tushare"},  # 同花顺行业资金流

    # ── ① tushare 公告类(按公告日切;过去公告永不变) ──
    "forecast": {"key": "date", "settle": "eod", "source": "tushare"},   # 业绩预告(ann_date)
    "express": {"key": "date", "settle": "eod", "source": "tushare"},    # 业绩快报(ann_date)
    "anns_d": {"key": "date", "settle": "eod", "source": "tushare"},     # 信息披露公告(ann_date;标题情感)

    # ── ① tushare 区间/标的级(按取数日快照——含到取数日为止的截面,按 as_of 留底) ──
    "moneyflow_hsgt": {"key": "as_of", "settle": "eod", "source": "tushare"},   # 沪深港通区间
    "margin": {"key": "as_of", "settle": "eod", "source": "tushare"},           # 两融区间汇总
    "index_dailybasic": {"key": "as_of", "settle": "eod", "source": "tushare"}, # 指数估值时序
    "stk_holdernumber": {"key": "as_of", "settle": "eod", "source": "tushare"}, # 股东户数(标的级)
    "pledge_stat": {"key": "as_of", "settle": "eod", "source": "tushare"},      # 质押统计(标的级)

    # ── ②' tushare 静态/日历 ──
    "stock_basic": {"key": "static", "settle": "eod", "source": "tushare"},
    "trade_cal": {"key": "static", "settle": "eod", "source": "tushare"},

    # ── ② akshare 按取数日快照(内容随取数日变,用 entity@as_of 留底) ──
    "stock_zh_a_gdhs_detail_em": {"key": "as_of", "settle": "eod", "source": "akshare"},      # 股东户数
    "stock_restricted_release_queue_em": {"key": "as_of", "settle": "eod", "source": "akshare"},  # 解禁队列
    "stock_news_em": {"key": "as_of", "settle": "eod", "source": "akshare"},                   # 个股新闻
    "stock_lhb_stock_statistic_em": {"key": "as_of", "settle": "eod", "source": "akshare"},    # 龙虎榜统计
    "stock_yjbb_em": {"key": "period", "settle": "eod", "source": "akshare"},                  # 业绩快报(报告期)

    # ── ② akshare 宏观(按取数日快照——月度序列,取一次留底) ──
    "macro_china_cpi_monthly": {"key": "as_of", "settle": "eod", "source": "akshare"},
    "macro_china_ppi": {"key": "as_of", "settle": "eod", "source": "akshare"},
    "macro_china_pmi": {"key": "as_of", "settle": "eod", "source": "akshare"},
    "macro_china_money_supply": {"key": "as_of", "settle": "eod", "source": "akshare"},
    "macro_china_lpr": {"key": "as_of", "settle": "eod", "source": "akshare"},
    "macro_china_shrzgm": {"key": "as_of", "settle": "eod", "source": "akshare"},

    # ── ③ akshare 盘中实时(不入湖,总取新) ──
    "stock_zh_a_spot_em": {"key": None, "settle": "live", "source": "akshare"},               # 全A实时快照
    "stock_individual_fund_flow_rank": {"key": None, "settle": "live", "source": "akshare"},  # 资金流排名(今日)
    "stock_individual_fund_flow": {"key": None, "settle": "live", "source": "akshare"},       # 个股资金流(今日)
    "stock_sector_fund_flow_rank": {"key": None, "settle": "live", "source": "akshare"},      # 板块资金流(今日)
    "stock_fund_flow_industry": {"key": None, "settle": "live", "source": "akshare"},         # 行业资金流(今日)
    "stock_hsgt_fund_flow_summary_em": {"key": None, "settle": "live", "source": "akshare"},  # 北向当日汇总
    "stock_zt_pool_em": {"key": None, "settle": "live", "source": "akshare"},                 # 涨停池(当日)

    # ── ① 宏观时序后端(FRED / yfinance,过去观测不变,永久留底) ──
    "fred": {"key": "as_of", "settle": "eod", "source": "fred"},        # FRED 任意 series(别名/原始 ID)
    "yfinance": {"key": "as_of", "settle": "eod", "source": "yfinance"},  # yfinance 历史价(跨资产)
}


def policy(endpoint: str) -> dict:
    """返回端点的 policy；未登记端点抛 KeyError（逼显式登记，不静默漏缓存）。"""
    try:
        return ENDPOINTS[endpoint]
    except KeyError:
        raise KeyError(
            f"unknown endpoint {endpoint!r}: register it in autoresearch.data.endpoints.ENDPOINTS"
        ) from None
