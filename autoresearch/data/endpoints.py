"""端点 policy registry —— 决定每个取数端点怎么 key、是否入湖、今天是否取新。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §B。

每条 policy:
  key    : "date" | "period" | "as_of" | "static" | None
           lake 文件名怎么取——date→交易日串;period→报告期;as_of→f"{entity}@{取数日}"
           的按取数日快照;static→单文件 "static";None→不入湖(live)。
  settle : "eod" | "live"
           eod → 收盘后结算、可入湖永不重取(过去日);live → 盘中实时、总取新不缓存。
  source : "tushare" | "akshare" | "eastmoney" | "fred" | "yfinance" | "cboe" | "official" | "sec"
           —— 取数后端("eastmoney" = 本仓自采,不经 akshare 封装)。前五个由 sources.fetch
           路由;后三个(2026-08-28 外源扩面)**只是出处标签**,没有 sources.fetch 分支 ——
           它们的 load_* 总是注入 fetch=(理由见 ENDPOINTS 里那段注释)。
  snapshot(可选,默认 False):**不可回填的观测型端点**(接口只有"此刻",没有历史参数)。
           cache 层据此 ① 强制 as-of 键 == 真实今天(防 PIT 错标)② 落盘打观测戳。
  freshness_slo / max_stale / stale_on_error(可选三键,秒 / 秒 / bool):**时效契约**
           (design 2026-08-28 §9)。语义与三态判定见本文件末尾的 freshness_state()。

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

    # ── ① tushare 指数 / 基金(design 2026-09-25 指数调样事件源 §2.1;全部 B 级,消费者 presence-gated)──
    # index_weight:月末成分快照,一指数一月一份 —— 实体 index_code + as_of(调用方把 today 传成月末),
    # 键形如 000300_SH@20260630;用于普查 / 对账(公告名单 vs 月末实现),**不是**前瞻源。
    "index_weight": {"key": "as_of", "settle": "eod", "source": "tushare"},
    "index_basic": {"key": "static", "settle": "eod", "source": "tushare"},   # 指数元数据(单一 market=CSI 参数集)
    "fund_basic": {"key": "static", "settle": "eod", "source": "tushare"},    # ETF 元数据(基准含指数名;market=E,status=L)
    "fund_share": {"key": "date", "settle": "eod", "source": "tushare"},      # ETF 份额(trade_date 全基金一日一份)
    "fund_nav": {"key": "date", "settle": "eod", "source": "tushare"},        # ETF 净值(nav_date 键,见 cache._DATE_PARAM_KEYS)

    # ── ② 中证指数公司公告(source=csindex 自采;akshare 无对应函数)──
    # list:queryAnnouncementByType 只给最新 5 条、不分页 → 每取数日一份快照(all@today),历史靠湖累积;
    #      snapshot=True:接口只返回「此刻」,补跑不得写成过去某天的假历史(SnapshotDateError 守门)。
    # detail:queryAnnouncementById + 附件 xlsx 解析后的长表,内容不可变 → 实体 ann_id,取一次永久留底
    #      (取数方先找湖里任一 <ann_id>@*.parquet,见 scan/index_events._load_detail)。
    "csindex_rebalance_list": {"key": "as_of", "settle": "eod", "source": "csindex", "snapshot": True},
    "csindex_rebalance_detail": {"key": "as_of", "settle": "eod", "source": "csindex"},

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
    #
    # `snapshot: True` = **不可回填的观测型端点**(接口只返回"此刻",没有历史参数)。两条后果
    # 由 cache 层强制(2026-08-09 复核 I1/I3):
    #   ① as-of 键**必须等于真实今天** —— 补跑/节假日 launchd/手工触发若传过去某个交易日,
    #      会把今天的快照写成那天的"历史",且事后不可甄别(工作树里那个 08-09 03:03 写成
    #      `all@20260807.parquet` 的分区就是这么来的)→ 取数前 `SnapshotDateError` 拒绝;
    #   ② 落盘前打观测戳(`first_seen_ts` + `first_seen_basis="observed"`)。
    # 东财走**自采第一跳**(source=eastmoney):akshare 封装 `stock_hot_rank_em` 的第二跳是
    # push2(记忆判例点名的被封主机,实测 502),会把完好的榜单本体连坐丢掉。
    "eastmoney_hot_rank": {"key": "as_of", "settle": "eod", "source": "eastmoney",
                           "snapshot": True},                                          # 东财人气榜(TOP100 快照)
    "stock_hot_follow_xq": {"key": "as_of", "settle": "eod", "source": "akshare",
                            "snapshot": True},                                         # 雪球关注度(全市场快照)
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

    # ── ① 外源实时信息扩面(design 2026-08-28-external-evidence-expansion §9;**全部 B 级**)──
    #
    # I 类基建:**先登记、后接消费**(D-1 要求源模块先跑 7 天无消费者 canary)。所有消费者
    # presence-gated —— 这些键缺席时漏斗/报告照常成立,只是相应块整块不出现。
    #
    # 两处与本仓既有形态不同,先写清楚免得下一个人踩:
    #
    # 1. **`source` 是出处标签,不等于 `sources.fetch` 的可用路由**。`cboe` / `official` / `sec`
    #    在 `sources/__init__.py` 没有分支(真调会 ValueError)—— 这是**刻意**的:它们的取数
    #    形态(整表 csv / 官方年度页 / 官方 UA + CIK 解析)与"端点名 == 后端函数名"的路由假设
    #    对不上,故各自的 `load_*` **总是注入 `fetch=`**(见 `yf_tape.load_global_tape` 与
    #    `cboe_vix.load_vix_history` 的 docstring)。三个 `yfinance` 键同理(逐 symbol 取数 ≠
    #    一次取全表)——**不要指望 `sources.fetch("global_tape", ...)` 能跑通**。
    # 2. **时效三键**(§9 逐字要求「每个 endpoint 还要声明 `freshness_slo`、`max_stale`、
    #    `stale_on_error`」):语义与三态判定见本文件末尾的 `freshness()` / `freshness_state()`。
    #    单位=秒。`max_stale` 是**整块省略线**,不是"打个陈旧标记继续用"。

    # 隔夜 tape:分区键 = **美股参考日**(A 股日另列,不混)。整表取数失败时消费端应整块省略而
    # 不是沿用昨夜 —— 昨夜的 tape 是"上一个隔夜窗"的已知事实,不是今晚的 → `stale_on_error=False`。
    "global_tape": {"key": "date", "settle": "eod", "source": "yfinance",
                    "freshness_slo": 86400, "max_stale": 345600, "stale_on_error": False},

    # 美股期权链:**快照**(接口只有"此刻",没有历史参数)。`freshness_slo` = 6h,与
    # `yf_options.MAX_QUOTE_AGE_SEC` 同一个数(单个交易时段内的报价 age 上限);`max_stale` = 1 天
    # (再往前只是"昨天的 IV",不是今天的定价)。`stale_on_error=False`:报价是易腐品,取数失败时
    # 宁可缺席也不拿上一份冒充 —— 即 `yf_options` 纪律 2「陈货冒充报价」的端点侧同款。
    "us_options": {"key": "as_of", "settle": "eod", "source": "yfinance", "snapshot": True,
                   "freshness_slo": 21600, "max_stale": 86400, "stale_on_error": False},

    # 美股票面(news / earnings_dates / upgrades_downgrades / holders / info)。按需取,
    # 内容随取数日增长 → as_of 留底。
    "us_ticker": {"key": "as_of", "settle": "eod", "source": "yfinance",
                  "freshness_slo": 86400, "max_stale": 604800, "stale_on_error": True},

    # CBOE VIX 全历史 csv:VIX 1 年分位的**备源**。全历史累积表,少几天不改变分位量级 →
    # 允许陈旧兜底(但消费端必须打印 age)。
    "cboe_vix": {"key": "date", "settle": "eod", "source": "cboe",
                 "freshness_slo": 86400, "max_stale": 604800, "stale_on_error": True},

    # FRED releases/dates:未来 14 日海外发布日历。几天前的日历仍列着未来发布,可陈旧兜底。
    "fred_calendar": {"key": "date", "settle": "eod", "source": "fred",
                      "freshness_slo": 86400, "max_stale": 604800, "stale_on_error": True},

    # FOMC 年度日程:官方年度页物化,周检 + 跨年预热(§9)。**快照**:年度页只有"此刻"的版本,
    # 改期靠 revision 记录,不能补出历史版本。
    "fomc_calendar": {"key": "as_of", "settle": "eod", "source": "official", "snapshot": True,
                      "freshness_slo": 604800, "max_stale": 2592000, "stale_on_error": True},

    # 官方事件时刻(Fed / BLS / BEA 官方日历 + 发行人 IR):只为 FRED / yfinance 候选补准确时刻,
    # 能确认才升 `TIMED`,否则保持 `DATE_ONLY`。同为**快照**(官方页只有当前版本)。
    "official_event_calendar": {"key": "as_of", "settle": "eod", "source": "official",
                                "snapshot": True, "freshness_slo": 86400, "max_stale": 604800,
                                "stale_on_error": True},

    # SEC EDGAR submissions(需官方 UA;CIK 由官方 ticker→CIK 表解析)。申报是追加型:陈旧副本
    # 会漏掉最新申报,故 age 必须打印;但漏新 ≠ 读错,7 日内仍可用。
    "edgar": {"key": "as_of", "settle": "eod", "source": "sec",
              "freshness_slo": 86400, "max_stale": 604800, "stale_on_error": True},

    # 美股财报日历(yfinance `earnings_dates`):既供未来日期,也供历史 8 次实际波动对齐。
    "us_earnings_dates": {"key": "as_of", "settle": "eod", "source": "yfinance",
                          "freshness_slo": 86400, "max_stale": 604800, "stale_on_error": True},
}

# 时效契约三键的登记名(§9)。**改名 = 破坏 `freshness()` 契约**,先读它的 docstring。
FRESHNESS_KEYS: tuple[str, ...] = ("freshness_slo", "max_stale", "stale_on_error")

# `freshness_state` 的四种状态。
FRESH = "fresh"              # age ≤ freshness_slo
STALE = "stale"              # freshness_slo < age ≤ max_stale —— 可用,但消费端**必须打印 age**
EXPIRED = "expired"          # age > max_stale —— 消费端**整块省略**,不得沿用旧值
UNDECLARED = "undeclared"    # 该端点没声明时效契约(既有 40 个端点),不判定


def policy(endpoint: str) -> dict:
    """返回端点的 policy；未登记端点抛 KeyError（逼显式登记，不静默漏缓存）。"""
    try:
        return ENDPOINTS[endpoint]
    except KeyError:
        raise KeyError(
            f"unknown endpoint {endpoint!r}: register it in autoresearch.data.endpoints.ENDPOINTS"
        ) from None


# ───────────────────────── 时效契约(design 2026-08-28 §9) ─────────────────────────
#
# §9 逐字:「每个 endpoint 还要声明 `freshness_slo`、`max_stale`、`stale_on_error`;消费端打印
# `age / stale_reason`,**超过 `max_stale` 整块省略而不是静默沿用旧值**。」
#
# 三键都是**秒**(`stale_on_error` 除外,它是 bool):
#
#   freshness_slo   期望刷新周期。age ≤ 它 = 新鲜,消费端不必解释。
#   max_stale       硬上限。age > 它 → **整块省略**(不是"标个陈旧继续用")。
#   stale_on_error  这次取数**失败**时,能否退回上一份仍在 `max_stale` 内的副本:
#                   True = 可以,但仍要打印 age / stale_reason;
#                   False = 不可以,整块省略 —— 易腐数据(报价/隔夜 tape)专用。
#
# 为什么 `stale_on_error` 必须被真消费而不是只登记:一个没人读的字段等于没写(本仓家训
# 「记进 lessons ≠ 会生效」)。故 `freshness_state(..., after_error=True)` 是它唯一的消费点。


def freshness(endpoint: str) -> dict:
    """端点的时效契约三键;未声明 → 三个 None(既有端点向后兼容,不强行补默认值)。"""
    pol = policy(endpoint)
    return {k: pol.get(k) for k in FRESHNESS_KEYS}


def declares_freshness(endpoint: str) -> bool:
    """该端点是否声明了完整的时效契约(三键齐全)。"""
    f = freshness(endpoint)
    return f["freshness_slo"] is not None and f["max_stale"] is not None \
        and f["stale_on_error"] is not None


def freshness_state(endpoint: str, age_sec: float | None, *, after_error: bool = False) -> dict:
    """`age` → 三态 + `usable` + `stale_reason`(消费端照抄进 pack/报告的那一行)。

    `age_sec`  这份数据的年龄(秒)= 现在 − `fetched_at`。**`None` 不当作新鲜**:age 不可判
               (缺 `fetched_at`)与"很新"是两回事,前者按 `EXPIRED` 处理 —— 与
               `yf_options._quote_ages`「无 lastTradeDate → age 不可判,不当作新鲜」同一条纪律。
    `after_error` 这次取数失败、正打算退回上一份副本时置 True:`stale_on_error=False` 的端点
               在 `STALE` 带里也不可用(易腐数据宁可缺席,不可冒充今天)。

    返回 {"state", "usable", "age_sec", "stale_reason", **三键}。
    `state == UNDECLARED`(未声明契约的既有端点)恒 `usable=True`:本函数不给它们凭空立规矩。
    """
    f = freshness(endpoint)
    out = {"state": UNDECLARED, "usable": True, "age_sec": age_sec, "stale_reason": "", **f}
    if not declares_freshness(endpoint):
        return out
    slo, mx, on_err = f["freshness_slo"], f["max_stale"], f["stale_on_error"]
    if age_sec is None:
        return {**out, "state": EXPIRED, "usable": False,
                "stale_reason": f"age 不可判(缺 fetched_at)→ {endpoint} 整块省略;"
                                f"「不可判」不是「新鲜」"}
    age = float(age_sec)
    if age <= slo:
        return {**out, "state": FRESH, "usable": True, "age_sec": age}
    if age > mx:
        return {**out, "state": EXPIRED, "usable": False, "age_sec": age,
                "stale_reason": f"age {int(age)}s > max_stale {mx}s → {endpoint} **整块省略**,"
                                f"不得静默沿用旧值"}
    reason = (f"age {int(age)}s > freshness_slo {slo}s(仍在 max_stale {mx}s 内)"
              f"—— 可用但必须随块打印 age")
    if after_error and not on_err:
        return {**out, "state": STALE, "usable": False, "age_sec": age,
                "stale_reason": f"{reason};但 stale_on_error=False → 本次取数失败,"
                                f"{endpoint} 整块省略,不拿上一份冒充"}
    return {**out, "state": STALE, "usable": True, "age_sec": age, "stale_reason": reason}
