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
    "stk_holdertrade": {"key": "date", "settle": "eod", "source": "tushare"},  # 股东增减持(ann_date;催化)
    "repurchase": {"key": "date", "settle": "eod", "source": "tushare"},       # 回购(ann_date;催化)
    "stk_surv": {"key": "date", "settle": "eod", "source": "tushare"},         # 机构调研(trade_date;催化)

    # ── ① tushare 财务数据(按 ts_code+period 切;报告期披露后永不变,镜像 forecast 的 eod 类) ──
    "fina_mainbz": {"key": "period", "settle": "eod", "source": "tushare"},  # 分业务收入/利润(ts_code+period+type=P)

    # ── ① tushare 区间/标的级(按取数日快照——含到取数日为止的截面,按 as_of 留底) ──
    "moneyflow_hsgt": {"key": "as_of", "settle": "eod", "source": "tushare"},   # 沪深港通区间
    "margin": {"key": "as_of", "settle": "eod", "source": "tushare"},           # 两融区间汇总
    "index_dailybasic": {"key": "as_of", "settle": "eod", "source": "tushare"}, # 指数估值时序
    "stk_holdernumber": {"key": "as_of", "settle": "eod", "source": "tushare"}, # 股东户数(标的级)
    "pledge_stat": {"key": "as_of", "settle": "eod", "source": "tushare"},      # 质押统计(标的级)

    # ── ① tushare 衍生品(design 2026-08-03 §2;**消费者默认关闭**,I 类数据基建)──
    # opt_daily 按交易日切(收盘即结算);opt_basic 是合约元数据,内容随取数日增长
    # (新合约挂牌)→ as_of 快照。**opt_basic 首页恰 12,000 行 = 分页上限,不是全量**,
    # 取数侧必须强制分页(见 derivatives/options_lake.paginate)。
    "opt_daily": {"key": "date", "settle": "eod", "source": "tushare"},
    "opt_basic": {"key": "as_of", "settle": "eod", "source": "tushare"},
    # 可转债:行情可达;`cb_price_chg` 当前 token 无权限 → F3 整线 BLOCKED_BY_DATA。
    "cb_daily": {"key": "date", "settle": "eod", "source": "tushare"},
    "cb_basic": {"key": "as_of", "settle": "eod", "source": "tushare"},
    "cb_call": {"key": "date", "settle": "eod", "source": "tushare"},
    "cb_price_chg": {"key": "date", "settle": "eod", "source": "tushare"},

    # ── ②' tushare 静态/日历 ──
    "stock_basic": {"key": "static", "settle": "eod", "source": "tushare"},
    "trade_cal": {"key": "static", "settle": "eod", "source": "tushare"},

    # ── ② akshare 按取数日快照(内容随取数日变,用 entity@as_of 留底) ──
    "stock_zh_a_gdhs_detail_em": {"key": "as_of", "settle": "eod", "source": "akshare"},      # 股东户数
    "stock_restricted_release_queue_em": {"key": "as_of", "settle": "eod", "source": "akshare"},  # 解禁队列
    "stock_news_em": {"key": "as_of", "settle": "eod", "source": "akshare"},                   # 个股新闻
    "stock_lhb_stock_statistic_em": {"key": "as_of", "settle": "eod", "source": "akshare"},    # 龙虎榜统计
    # 热度快照(Wave12 T2;design docs/research/2026-08-09-hot-rank-probe.md):两端点都是
    # 全市场零 entity 快照(零参数或 symbol=分类选择器,非个股),不带日期列 —— 用 key="date"
    # 会因 params 无 trade_date 类键而退化成字面量 "unkeyed"(每晚覆写同一文件,历史全丢);
    # as_of 模式的原生兜底(entity 缺省 "all",as_of 缺省 today)才是正确的按天分区键。
    # 调用方一律传空 params(见 T1 报告决策②:stock_news_em 先例把 as_of 塞进 params 会原样
    # 透传进真实 akshare 调用并 TypeError,本组端点不重蹈)。
    "stock_hot_rank_em": {"key": "as_of", "settle": "eod", "source": "akshare"},      # 东财人气榜(全市场TOP100快照)
    "stock_hot_follow_xq": {"key": "as_of", "settle": "eod", "source": "akshare"},    # 雪球关注度(全市场快照)
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
