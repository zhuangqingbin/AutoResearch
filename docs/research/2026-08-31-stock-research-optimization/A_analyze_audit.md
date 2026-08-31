# stock-research 确定性取数/组装路径 · 只读审计(2026-08-30)

> 范围:`autoresearch/analyze/{harvest,assemble}.py`、`.claude/skills/stock-research/*`、`.claude/agents/{company-intel,us-intel}.md`、harvest 顺 import 触到的数据层、08-29 未提交 diff。**未改任何仓库文件。**
> 证据格式 `file:line`;样本产物 = `context_claude/300308.SZ_2026-08-30.md`(69,065B,full,A股)与 `context_claude/300857.SZ_2026-08-26_slim.md`(8,517B)+ `_slim_deep.md`(5,682B)。

---

## 1. 取数清单表

派发顺序 = `harvest.main()`(`autoresearch/analyze/harvest.py:1909-2054`)。「档」列:**both** = full 与 slim 都拉;**full** = 仅 `not slim`;**slim+L1** = 仅 scan L4 调用且当日 `L1_scored_full.csv` 命中时。「lake」列判据 = 是否经 `autoresearch.data.cache.get_or_fetch`(`data/cache.py:257`)—— **结论先说:36 行里 0 行走 lake**,全部直连或走 `~/.autoresearch/cache` 的 CSV 私缓存。「失败」列:A = 抛异常阻断;B = 降级 + `record_degradation` 记账;**T = 只输出降级文本、不记账、不阻断**(harvest 自己的老路,`_section` 统一 try/except → `_ERROR fetching this section_`,`harvest.py:1643-1651`)。

| # | 名称 | 市场 | 档 | 数据源(调用点) | lake | 新鲜度 / PIT | 失败策略 | 输出块标题 |
|---|---|---|---|---|---|---|---|---|
| 1 | Instrument identity | A/US/crypto | both | yfinance `.info` longName/sector/industry(`agents/utils/agent_utils.py:78-119`,`lru_cache`) | 否 | 实时(info=此刻) | fail-open 返回 `{}`,不记账 | `## Instrument identity` |
| 2 | Price history OHLCV 400天 | 全部(**A股也走 yfinance**) | full | `get_stock_data`→`route_to_vendor`→`get_YFin_data_online`(`core_stock_tools.py:24`;`dataflows/y_finance.py:18-70`;vendor 配置 `default_config.py:105`) | 否 | 日频;PIT ✓(end=trade_date 含);>10 日陈旧→拒(`stockstats_utils.py:88-122`) | 空/陈旧→`NoMarketDataError`→路由层返回 `NO_DATA_AVAILABLE` 文本(`interface.py:213-233`);其它→T | `## Price history (OHLCV) …`(样本 15,099B,**最大块**) |
| 3 | Technical indicators 12×30天 | 全部 | full | stockstats over `load_ohlcv`(`y_finance.py:72-209`;`stockstats_utils.py:166-239`);**A股 = tushare `pro_bar qfq`**(`:142-163`),其它 yfinance | 否;CSV 私缓存 `~/.autoresearch/cache/<sym>-<vendor>-data-<5y起>-<明日>.csv`(文件名含今日→**每天一份新文件**,同日复用) | 日频;PIT ✓(`≤curr_date` 过滤 `:233`) | 同 2 | `## Technical indicators (full menu)` + 12 个 `## <ind> values…`(合计 ≈17KB) |
| 4 | Verified market snapshot | 全部 | both | `build_verified_market_snapshot`→`load_ohlcv`(`market_data_validator.py:62-123`) | 同 3 | 日频;PIT ✓ | 无数据→`ValueError`→T;**scan 下 GATE3 `_slim_defect` 硬拒**(锚 `### Latest verified OHLCV row` + Close 数值,`scan/l4/producers.py:11-19,296-336`) | `## Verified market snapshot (source of truth)` |
| 5a | Market context A股(复用 L1) | A | slim+L1 | 读 scan staging `L1_scored_full.csv`/`L1_recall_top1000.csv` 该票行(`harvest.py:1678-1696`),渲染 `ashare_market_context_from_l1`(`:1699-1774`) | 零取数(读 CSV) | = 当日 L1 帧(tushare EOD) | 无行/坏文件→回落 5b | `## Market context — A股 (…· 复用L1召回)` |
| 5b | Market context A股(live) | A | full / 无 L1 的 slim | tushare `moneyflow`(近10日)/`stk_factor_pro`/`cyq_perf`/`hk_hold` **直调** `_ts_call(pro.*)`(`data/tushare_enrich.py:47-118`)→失败回退 akshare `stock_individual_fund_flow`(live)/`stock_lhb_stock_statistic_em(近三月)`/`stock_zt_pool_em`(`harvest.py:1305-1361`)→再回退 WebSearch 指令(`:1364-1371`) | 否(这 4 个 tushare 端点在 `data/endpoints.py:37-40` 已登记 eod/date,**harvest 绕湖直调**) | 日频;锚 = `resolve_momentum_dates(curr_date)` 最近交易日 ✓;tushare 盘后灌数 19:30 后才齐 | 分块 try/except → `_tushare … 取数失败_` 文本,**不记账**(T) | `## Market context — A股 (主力/技术/筹码/北向)` |
| 5c | Market context US | US | both | yfinance SPY 400d / RSP / 板块ETF(仅 `SECTOR_ETF` 8 只硬编码 `:106`)/ `^VIX`(`harvest.py:1374-1411`) | 否 | 日频;**⚠️ `^VIX` 用 `period="5d"`,不锚 curr_date(`:1403`)= 回填日期时前视** | T | `## Market context — US (regime/breadth/sector/VIX)` |
| 6 | Tradeability & 涨跌停 | 全部 | both | yfinance history 120d(`harvest.py:1429-1463`;板块规则 `_board_limit :1416-1426`,ST 未识别) | 否 | 日频;PIT ✓ | T | `## Tradeability & price-limit reality (v4)` |
| 7 | Ticker news 14天 | 全部 | both | yfinance `stock.get_news(count=20)`(`dataflows/yfinance_news.py:73-125`,窗内过滤 ✓)+ A股 akshare `stock_news_em` `head(12)`(`harvest.py:1217-1238`)→两者皆空→WebSearch 指令(`:1262-1267`) | 否(`stock_news_em` 在 `endpoints.py:85` 登记 as_of,**绕湖**) | 实时;**⚠️ akshare 路不按 14 日窗过滤、不去重、不排序**(样本 `300308…:838-850` 含 07-21/07-28 条与两条重复) | T | `## Ticker news …` |
| 8 | Global / macro news | 全部 | full | yfinance Search ×5 查询、7 日、10 条(`yfinance_news.py:128-`;`default_config.py:87-98`) | 否 | 实时 | T | `## Global / macro news` |
| 9 | Insider transactions | 全部 | full | yfinance `insider_transactions`(`y_finance.py:446-471`) | 否 | 实时;A股金额单位坑(样本 `:868` "Sale at price 58.33") | T | `## Insider transactions` |
| 10 | Ownership & short | 全部 | full | yfinance `.info` 做空/流通/机构 + `institutional_holders` + `major_holders`(`harvest.py:1064-1121`) | 否 | 实时(做空双月滞后) | T | `## Ownership & short interest (v3)` |
| 11 | 股东户数 / 质押 | A | both | tushare `stk_holdernumber` + `pledge_stat` 直调(`tushare_enrich.py:124-155`)→回退 akshare `stock_zh_a_gdhs_detail_em`(`harvest.py:1531-1591`) | 否(`endpoints.py:63-64,83` 已登记 as_of) | 定期披露;**取最新期,无 `≤curr_date` 过滤**(`:135,148`) | T | `## 股东户数 / 质押 (A股, v4)` |
| 12 | A股原生财报 UZI | A | both | tushare `fina_indicator`(全史)+ `dividend` 直调(`common/uzi_lenses.py:255-290`) | 否 | 季频;**取 `iloc[-1]` 无 PIT 过滤**(`:273`) | None→`_暂不可用_`(T) | `## A股原生财报 (UZI·tushare)` |
| 13 | 融资余额趋势 UZI | A | both | tushare `margin_detail` 近 20 交易日直调(`uzi_lenses.py:293-317`) | 否(`endpoints.py:41` 已登记) | 日频(T-1 披露);**⚠️ `end=datetime.now()` 而非 curr_date(`:301-302`)** | None→`_非两融标的_`(T,无法区分"非标的"与"取数炸") | `## 融资余额趋势 (UZI·tushare)` |
| 14 | 杀猪盘/派发 + 量价形态·CMF/OBV | A | slim+L1 | 纯函数读 L1 行 `trap_signals`/`volume_price_signals`(`uzi_lenses.py:61-146`;`harvest.py:1839-1864`) | 零取数 | = 当日 L1 帧 | n/a | `## 杀猪盘/派发风险 (UZI·复用L1)` / `## 量价形态/吸筹·多日资金流 (UZI·复用L1)` |
| 15 | 龙虎榜席位识别 UZI | A | **full only** | tushare `top_inst(trade_date=d)` **逐日直调 ≤15 次**(`uzi_lenses.py:320-359`) | 否(`endpoints.py:43` 已登记 eod/date) | 日频;T 日榜单盘后 ~18:00 才出,17:10 跑取不到当日 | None→`_暂不可用_`(T) | `## 龙虎榜席位识别 (UZI·tushare)` |
| 16 | Macro ×8(FRED) | 全部 | full | FRED API `get_macro_data`(`dataflows/fred.py:141`;需 `FRED_API_KEY`) | 否(`endpoints.py:126` 登记的 `fred` as_of 键是 macro 包在用,dataflows 不走) | 官方发布频率;1 年窗 ≤curr_date ✓ | 缺 key/失败→"series unavailable" 文本(T) | `## Macro: <series>` ×8(≈6KB) |
| 17 | China backdrop | A | full | yfinance 000300/000001/159915/^HSI/CNY=X 1/3/6 月(`harvest.py:1271-1283`) | 否 | 日频 | T | `## China market backdrop (A-share)` |
| 18 | Prediction markets | 全部 | full | Polymarket `public-search`(`dataflows/polymarket.py:68-`) | 否 | 实时;常被 RST → WebSearch 指令(`harvest.py:1193-1214`) | T | `## Prediction markets …` |
| 19 | Fundamentals overview | 全部 | both | yfinance `.info` 28 字段(`y_finance.py:274-338`) | 否 | **实时非 as-of**(PE/市值 = 此刻) | stub→`NoMarketDataError`→NO_DATA 文本 | `## Fundamentals overview`(scan GATE3 锚之一) |
| 20 | Income statement 季度 | 全部 | both(slim→**deep** 文件) | yfinance `quarterly_income_stmt` + `filter_financials_by_date ≤curr_date`(`y_finance.py:411-`;`stockstats_utils.py:242-253`) | 否 | 季频;PIT ✓ | T | `## Income statement (quarterly)` |
| 21 | Balance sheet 季度 | 全部 | full | 同 20(`y_finance.py:341-`) | 否 | 季频 PIT ✓ | T | `## Balance sheet (quarterly)`(6,153B) |
| 22 | Cash flow 季度 | 全部 | full | 同 20(`y_finance.py:376-`) | 否 | 季频 PIT ✓ | T | `## Cash flow (quarterly)` |
| 23 | Earnings quality | 全部 | both(deep) | yfinance 三张 quarterly 表 `_latest` 取第 0 列(`harvest.py:1124-1190`) | 否 | 季频;**⚠️ 未过 `filter_financials_by_date`,回填日前视** | T | `## Earnings quality / forensics (v3)` |
| 24 | Solvency | 全部 | both(deep) | yfinance quarterly_balance_sheet/income(`harvest.py:1466-1528`) | 否 | 同 23 | T;A股质押改由 11 给,块内仍印 WebSearch 提示(`:1524-1526`,与 11 重复) | `## Solvency & refinancing (v4)` |
| 25 | 期权与仓位地形 options v2 | US(A股/港股→固定降级句) | **full only**(`external_sections` 二道门 `:944-966`) | `yf_options.full_chain`(yfinance option_chain ≤8 到期 + `earnings_dates` + 900 日日线,`harvest.py:407-538,561-570`;`data/sources/yf_options.py:482-593`) | **否**(`us_options` snapshot 端点 `endpoints.py:155` 已登记;harvest 直调,不写湖;`iv_history=None` → **1 年分位恒「样本不足(0)」**,`:466,541-558`,且全仓无写湖者) | 实时快照(报价 age ≤6h) | **B**:`_degrade_note("us_options")` + 文本,不阻断 | `## 期权与仓位地形 (options v2)` |
| 26 | EDGAR 近 90 日 | US | full | `edgar.fetch_submissions_safe`(`sources/edgar.py:245-271`;**需 env `SEC_EDGAR_USER_AGENT`**,缺→FAILED) | 否(`edgar` as_of 已登记 `endpoints.py:185`) | 官方申报流;`as_of=curr_date` PIT ✓ | **B** `record_degradation("edgar")`;NO_CIK→整节省略;EMPTY 与 FAILED 分开渲染(`harvest.py:593-598`) | `## 外源事件账 · EDGAR 近90日 (T1官方)` |
| 27 | 分析师行动近 30 日 | US(无字段则省略) | full | yfinance `upgrades_downgrades` + `.info` 目标价(`harvest.py:609-659`) | 否(`us_ticker` 已登记 `:160`) | 实时;窗内 as-of ≤ 分析日 ✓;**`prev_targets` 恒 None → 区间变化恒 UNMEASURED**(`:649-656`,调用点 `:964` 不传) | **B** `_degrade_note("us_ticker")`;presence-gated | `## 外源事件账 · 分析师行动 近30日` |
| 28 | Google News en/zh 14 日候选 | US(en)/A(zh) | full | `gnews_rss.search(query, lang, limit=60)`(`sources/gnews_rss.py:172-228`);query = yfinance **英文 longName**(`harvest.py:957,2034`) | 否;**不入 `news_catalog`**(`to_catalog_rows` 存在但 harvest 不调) | 实时;窗内 PIT 过滤 ✓(`:726-750`) | **B**(源模块 `record_degradation(via)`);0 条→整节省略 | `## 外源事件账 · Google News … (T4发现源·UNVERIFIED)`(样本 A股用英文名 zh 搜,仅 2 条、1 条日文) |
| 29 | 海外映射 readthrough | A | full | `readthrough.load_map(yaml)` → `yf_tape.fetch_global_tape` **直调**(非 `load_global_tape`)+ `yf_options.narrow_snapshot` + `earnings_dates`(`harvest.py:789-917`) | 否 | 日频 tape / 实时 IV | **B** 记账;**当前 `readthrough_map.yaml` 全 `pending_evidence` → `load_map` 返回空 → 整块恒省略**(`data/readthrough_map.yaml:8-17`;样本无此节) | `## 海外映射 (readthrough·≤4名·事实行)` |
| 30 | Analyst consensus | 全部 | both | yfinance `.info` 目标价/评级 + `recommendations` 尾 8 行(`harvest.py:983-1013`) | 否 | 实时 | T | `## Analyst consensus & price targets (v2)` |
| 31 | Earnings & events calendar | 全部 | both | yfinance `calendar` + `earnings_dates.head(8)`(`harvest.py:1016-1035`) | 否(`us_earnings_dates` 已登记 `:189`) | 实时 | T | `## Earnings & events calendar (v2)` |
| 32 | Corporate calendar A股 | A | both | tushare `forecast`/`express` 直调(`tushare_enrich.py:161-210`)+ akshare `stock_restricted_release_queue_em`(`harvest.py:1594-1638`) | 否(`endpoints.py:49-50,84` 已登记) | 公告日频;forecast 取最新 ann_date **无 ≤curr_date**;express 有 15 月时效守卫 ✓;解禁 `≥curr_date` ✓ | T(各自降级) | `## Corporate calendar — A股 业绩预告·快报/解禁 (v4)` |
| 33 | 同花顺一致预期 EPS / fwd-PE | A | both | keyless GET `basic.10jqka.com.cn/new/<code>/worth.html`(`data/keyless.py:92-135`,串行限流 1s) | 否(docstring 说"整合层包 get_or_fetch",harvest 没包) | 实时 | 空帧→文本(T) | `## A股卖方一致预期 EPS / fwd-PE`;**⚠️ full 档 `price=None`(`harvest.py:2042`)→ 不算 fwd-PE**(样本 `:1527-1534` 无 fwd-PE 列),只有 slim+L1 才算 |
| 34 | Peer-relative | 全部 | full | yfinance history 220d ×(自身+peers+基准)+ `.info.forwardPE`(`harvest.py:1038-1059`;peers 来自第 4 参或 `PEER_MAP` 6 只硬编码 `:97-104`) | 否 | 日频 | T | `## Peer-relative valuation & strength (v2)` |

**横向结论**
- **A 级阻断 = 0 处**。harvest 从不因数据失败非零退出(`main()` 只要能写文件就 `[saved]`,`:2053`);唯一硬门是 scan 侧 GATE3(`producers.py:296-336`),只管 slim 且只查 4 个锚 + Close 数值。
- **B 级记账 = 仅 D-3 新块(25–29)**;其余 29 行是「T」—— 降级只留在 markdown 正文里,`degradations()` 账本看不见(与 `data/contracts.py:251-264` 立 `record_degradation` 的初衷正相反:那段注释讲的正是"降级不留痕"事故)。
- **两套 OHLCV 同帧**:A股 400 天块走 yfinance(#2),快照/指标走 tushare qfq(#3/#4)。样本价格一致(858.35)但 **成交量单位差 100×**(yfinance `31719580` 股 `300308…:280` vs tushare `317195.80` 手 `:741`),playbook 坑 #11 只讲价格未讲量。
- **PIT 只对"今天"成立**:#5c/#13/#19/#23/#24/#11/#12/#32 取"最新"不锚 curr_date;回填历史日期会前视。`trace/replay` 若重放 analyze,这些块不可复现。

---

## 2. 实时信息覆盖盲区(对「T+1 尾盘买 → T+2 开盘卖」窗口)

标注:**完全没有** = 确定性 harvest 与两个 full 情报员都不覆盖;**只在 full 有**;**只靠 company-intel/us-intel 网查**(full 档、cap 12、盲搜,`.claude/agents/company-intel.md:42-49` 六面 / `us-intel.md:40-47` 六面)。lite/slim 档没有任何情报员(`company-intel.md:16-18`;`us-intel.md:16-19`),scan 下的 `l4-intel` 属 scan 包、不属本 skill。

| # | 信息类型 | 覆盖状态 | 证据 |
|---|---|---|---|
| 1 | 盘后公告**正文**(重组/中标/问询回复/停复牌) | **完全没有(确定性)**;full 只靠 company-intel 面① WebFetch 巨潮 | `anns_d`/cninfo 兜底只接在 scan L3(`anns_fallback.py` 未被 analyze import);harvest 只有 akshare **标题**(`harvest.py:1232-1237`) |
| 2 | 互动易 / e 互动官方回复 | **完全没有**;full 只靠 company-intel 面① | 无任何确定性源;`company-intel.md:44` |
| 3 | 龙虎榜席位(席位名/游资名单/当日榜) | slim **没有**(L1 路径无 LHB 块;仅 tushare 失败的 akshare 回退才有近三月统计 `:1329-1340`);full 只有 `top_inst` 机构 vs 非机构净额聚合(≤15 日,无席位名、无 `top_list`)| `uzi_lenses.py:339-352`;`harvest.py:2002-2003` full only;T 日榜单取决于运行时刻 |
| 4 | 涨停池 / 炸板池 / 连板梯队 / 晋级率 | **完全没有**(`stock_zt_pool_em` 只在 akshare 回退路且只给全市场家数+最高连板 `:1341-1358`;炸板/晋级从未取) | tushare `limit_list_d` 已登记(`endpoints.py:45`)只给 macro 中观 |
| 5 | 题材梯队 / 概念归属 / 同花顺概念 | **完全没有**;company-intel **没有题材面**(六面 = 公告/产业价格/政策/机构/负面/海外映射) | `company-intel.md:42-49`;只有 yfinance sector/industry(`agent_utils.py:110-118`) |
| 6 | 分时 / 集合竞价 / 盘口 / 尾盘 30 分钟 | **完全没有**(全日线);`pos_in_range` 执行线要的"收盘在区间上 30%"只能由 OHLC 日线算 | `lite-playbook.md:155-160` 执行线;harvest 无分钟数据源 |
| 7 | 融资融券**当日** / 融券余额 | 只有 20 日融资余额趋势(T-1 频,both 档);当日与融券**完全没有** | `uzi_lenses.py:304-315`(只取 `rzye`) |
| 8 | 北向**当日净流入** | **完全没有**;只有 `hk_hold` 持股占比(2024-08 后港交所改季度披露,样本印"非标的/无持股记录") | `tushare_enrich.py:107-116`;`stock_hsgt_fund_flow_summary_em` 已登记 live(`endpoints.py:122`)未接 analyze |
| 9 | 行业 / 板块实时价格与资金流 | **完全没有**;full 仅 `china_backdrop` 沪深300/上证/创业板ETF 1/3/6 月 | `harvest.py:1271-1283`;`moneyflow_ind_ths`/`stock_sector_fund_flow_rank`(`endpoints.py:46,119`)未接 |
| 10 | 美股隔夜 / 全球 tape / 同业 ADR | A股 **实际没有**:只在 full 且 `readthrough_map.yaml` 有 active 条目时出现,现 0 条 active → 块恒省略;company-intel 面⑥ 因无名单恒写「不适用」 | `readthrough_map.yaml:8-17`;`harvest.py:862-865`;`company-intel.md:49-51` |
| 11 | 研报观点 / 盈利预测变化(逐条) | full:yfinance 月度计数表 + 目标价区间(A股也有,`300308…:1477-1497`);逐条 upgrades **仅美股**(#27);A股逐条 = 只靠 company-intel 面④;目标价"变化"恒 UNMEASURED(无上期快照) | `harvest.py:649-656,964` |
| 12 | 股吧 / 雪球情绪 / 人气榜 | **完全没有**(设计上排除:`company-intel.md:67` "股吧观点不是事实不入表");`stock_hot_follow_xq`/`eastmoney_hot_rank` 已登记只供 prewarm | `endpoints.py:102-105` |
| 13 | 当日(盘中)主力资金流 | 日频 tushare `moneyflow`(盘后灌数,19:30 后才齐,memory 判例);盘中只在 akshare 回退路 `stock_individual_fund_flow`(live) | `tushare_enrich.py:57-71`;`harvest.py:1316-1327` |
| 14 | 大宗交易 / 股东增减持 / 回购 / 机构调研公告 | **完全没有(确定性)**;`block_trade`/`stk_holdertrade`/`repurchase`/`stk_surv` 全登记未接 analyze;只可能从 akshare 标题偶然带出或 company-intel | `endpoints.py:42,52-54` |
| 15 | 停复牌 / ST / 风险警示 / 退市风险状态 | **完全没有**(`_board_limit` 明写 "ST ±5% 未自动识别") | `harvest.py:1425` |
| 16 | 新闻**正文** | **完全没有**:yfinance 摘要句、akshare 标题、gnews ≤200 字摘要(版权纪律不抓正文) | `gnews_rss.py:18-19,99-105` |
| 17 | 美股盘前/盘后报价与成交 | **完全没有**(options v2 有 `market_state` 字段但无 pre/post 价);us-intel 铁律禁自报行情 | `yf_options.py:49`;`us-intel.md:58` |
| 18 | 公司事件日历(股东大会/发布会/招标/投产) | 只有 yfinance `calendar` + 解禁;其余在块尾印 WebSearch 指令;full 靠 company-intel「催化挂」行 | `harvest.py:1636-1637`;`company-intel.md:36` |
| 19 | 汇率 / 商品 / 利率**当日** | full A股仅 USD/CNY 与指数 1/3/6 月;FRED 8 序列为美国宏观、非当日 | `harvest.py:1274-1275`,`:2007-2011` |
| 20 | 期权 / 隐含波动 | 美股 **只在 full**(#25);A股无挂牌(合理);lite 全关(`external_sections(slim=True)` 返回空 `:955-956`) | `harvest.py:944-966` |

小结:超短窗口真正需要的「**T 日盘后增量**」(公告正文/互动易/龙虎榜席位/涨停梯队/北向当日/两融当日/题材)在确定性层几乎全空;full 档把这些交给 cap 12 的盲搜情报员,lite 档一条都没有。确定性层现有的 36 行里,**没有一行是 T 日盘后才产生的信息**(除非 19:30 后跑且 tushare 已灌数)。

---

## 3. 产物布局

### 3.1 harvest 落点
- **full**:`$CTX/<TICKER>_<YYYY-MM-DD>.md` 单文件(`harvest.py:1899-1906,2051-2052`;`ws.context_root()`=`context_claude/`,`common/workspace.py:65-67`)。实测 60–90KB:NVDA 89,483B、300308.SZ 08-30 69,065B、002384.SZ 73,639B(`ls -laS context_claude/*.md`)。样本 300308 有 60 个 `##` 节;按体积前五:OHLCV 400 天 15.1KB、资产负债表 6.2KB、利润表 4.3KB、现金流 3.1KB、akshare/yf 新闻 1.9KB;12 个指标节各 ~1.4KB(合计 17KB)。**≈45% 字节是 400 天 OHLCV + 30 天指标序列**,决策相关密度最低的两块。
- **slim**:`$CTX/<ticker>_<date>_slim.md`(表面块)+ `$CTX/<ticker>_<date>_slim_deep.md`(深核块:利润表/盈利质量/偿付,`_P4_DEEP_TITLES :1867`),表面块尾插 `<!-- P4 深核分界 … -->` 指针(`:1868-1896`)。实测 slim 8.5–10KB、deep 4–6KB(07-12 拆分前单文件 14–20KB)。scan 下 `--out-dir` 指向 `scan_input_dir` = 有 run_id 时 `<scan_dir>/_external_inputs/`,否则 `context_claude/`(`workspace.py:125-130`;`producers.py:290-294`)。
- slim 与 full 的差(`main()` 各 `if not slim`):slim **不拉** #2 OHLCV 400d、#3 指标序列、#8 全球新闻、#9 内部人、#10 做空、#15 龙虎榜席位、#16–18 宏观/中国底色/预测市场、#21–22 资产负债/现金流、#25–29 全部外源块、#34 同业;slim **额外**有 #5a/#14(仅 L1 命中时)。

### 3.2 分段草稿目录(Claude 写)
`$CTX/analyze/<TICKER>_<YYYYMMDD>/`(注意:**目录名日期无横线**,与 harvest 文件名 `YYYY-MM-DD` 不同):
```
1_analysts/  market.md news.md fundamentals.md [quality] [valuation] [positioning] [peer] [solvency]
2_research/  [reality_check] variant.md faceoff.md bull.md bear.md manager.md
3_risk/      premortem.md [debate]
4_portfolio/ decision.md calendar.md
_company_intel.md | _us_intel.md   ← 情报员写(assemble 不读)
```
样本 `context_claude/analyze/300308.SZ_20260830/`:19 个 md,1.7–8.7KB/个,`_company_intel.md` 8.6KB;另有一个 `.omc/state/last-tool-error.json` 落进了 `1_analysts/`(工具残留)。

### 3.3 assemble(`autoresearch/analyze/assemble.py`)
- **读**:`DECISION_REL="4_portfolio/decision.md"`(`:38`)+ `SPINE`(`:44-58`)+ `APPENDIX`(`:61-78`);必需 11 个(decision/variant/faceoff/calendar/premortem/market/news/fundamentals/bull/bear/manager),可选 7 个(quality/valuation/positioning/peer/solvency/reality_check/debate);缺必需→打印 `[MISSING]` 并 `return 1`(`:160-168`)。**不读** harvest context、不读 `_company_intel.md`/`_us_intel.md`。
- **写**:`$RPT/analyze/<YYYYMMDD_HHMM>/<名称|TICKER>.md`(目录 = 组装时刻,`:213-217`)+ `manifest.json{ticker,name,market,analysis_date,generated_at,hhmm}`(`:221-226`)。样本 `reports_claude/analyze/20260830_1738/中际旭创.md` 100,136B。
- **校验 = 只有两件**:文件存在性 + `parse_rating(decision.md)`(`:228`)。`parse_rating`(`agents/utils/rating.py:28-48`)两趟启发式:先找 `rating…[:-] X` 行,再找任一五档词;**找不到返回默认 "Hold" 而不报错** → 「校验」是软的,assemble 退出码永远 0。**不检查** `FINAL TRANSACTION PROPOSAL` 行、`置信度:` 行、数字是否出自 context、价格断言对账、intel 时效——full 档没有任何等价于 scan `self_review`/`brief_lint` 的发布前 lint(grep `autoresearch/` 无 analyze 侧消费者)。
- **格式契约**(来自 `engine-playbook.md:138-161` + `SKILL.md:46`):`decision.md` 顶部 决策仪表盘表 + 维度评分卡表,随后 `**Rating**: <Buy|Overweight|Hold|Underweight|Sell>`、Executive Summary、Investment Thesis、Scenarios(三档概率≈100%)、Expected Value/R:R、Tripwires、Execution、Time Horizon、`FINAL TRANSACTION PROPOSAL: **<BUY|HOLD|SELL>**`;每段结尾 `置信度: 高/中/低 ｜ 最大不确定项`。lite 卡契约见 `lite-playbook.md:59-170`(`**Rating**` 必须 = `**Rubric建议**`,早停卡 `**早停**: 停于 P<1|3> ｜ 停因:<词表>` 机读行,执行线两行照抄)。
- **潜在缺陷**:`_ashare_name_from_context` 找 `root.parent.parent / f"{root.name}.md"` = `context_claude/300308.SZ_20260830.md`(`:127`),而 harvest 写的是 `300308.SZ_2026-08-30.md`(`harvest.py:2051`)→ 永远找不到 → A股无 `--name` 时文件名退化为 6 位码。`SKILL.md:34` 的「A股务必带 --name」是在绕这个 bug。

---

## 4. 与 scan-market 的耦合

- **import 方向**:`autoresearch/analyze/*.py` **零** `import autoresearch.scan`(grep 无命中);`autoresearch/scan` 也不 import analyze —— scan 只**shell 出**:`scan/l4/producers.py:286-294 _default_harvest_slim` → `[sys.executable, "-m", "autoresearch.analyze.harvest", ticker, date, "stock", "--slim", "--out-dir", str(ctx_root)]`,`check=False`。
- **调用链**:
  - 批量(legacy GATE3):`.claude/workflows/scan-market.js:537` → `python -m autoresearch.scan.agents.l4_card harvest-slim <date>`(`scan/agents/l4_card.py:85`)→ `harvest_slim_batch`(`producers.py:338-379`,读 `_harvest_list.txt`,ThreadPool 4 并发,每票 `retries=1`,`min_bytes=4096`)。
  - 流式(现行):`.claude/workflows/l4-stock.js:298-399` 每股一 workflow 的 `prepare`/preflight → `scan/l4_tasks.py:1221-1249,1441-1444` → `_default_harvest_slim(symbol, date, slim_path.parent)`。
  - `_harvest_list.txt` 与每股 `_l4_prompt_<code>.md` 由 `scan/l4/prompts.py:133-268 write_dispatch_pack` 落稿,prompt 末尾指路 `slim_path`/`deep_path`/`_l4_intel_<code>.md`/卡落点(`:240-262`),并注明「>8KB 才可信,≈4.8KB=NO_DATA」。
- **参数**:位置参 `<ticker> <date> stock` + `--slim --out-dir <ctx_root>`;不传 peers(slim 不用);ticker 已被 scan 归一为 yfinance 后缀(`.SS/.SZ/.BJ`,`prompts.py:206`),harvest 入口再 `normalize_symbol` 一次(`harvest.py:1927`)。
- **数据级耦合(无契约)**:slim 在 scan 下会去读 scan staging 的 `L1_scored_full.csv`/`L1_recall_top1000.csv`(`harvest.py:1683-1696`,路径 `ws.scan_root()/<date>/`),依赖 L1 列名 `composite, score_*, main_inflow_yi, main_net_ratio, retail_net_yi, ma_bull, above_ma60, rsi6, rsi12, winner_rate, chip_concentration, price_to_cost, close, hk_ratio, cmf_20, obv_mom_20`(`:1710-1770,1852-1853`)。L1 改列名 → slim 静默退化为 live tushare 路(不是报错),两边数字不再同源。这条耦合没有登记在 `autoresearch/contracts/`(08-29 契约层)里——它是「消费者读别人产物」的 FN-1 形态。
- **反向契约(scan 施加给 slim)**:`_SLIM_ANCHORS` 四个标题 + `_SLIM_CLOSE_RE`(`producers.py:11-19`)。harvest 改这四个标题会让 GATE3 全红,但 harvest 侧无测试锁这四个字符串(`tests/analyze/test_harvest_slim_split.py` 只锁拆分)。
- **重复**:`autoresearch/macro/harvest.py:27-29,90-92,121-123` 三个函数「Verbatim from autoresearch.analyze.harvest」(`_load_env`/`_ak_call`/`_section`)复制而非共享;`_load_env` 又与 `autoresearch/__init__.py:9-14` 的 `load_dotenv` 功能重叠。
- **lite 独立跑时**没有 assemble 步(`SKILL.md:40` 「(可选)校验」),卡直接落 `$RPT/analyze/<YYYYMMDD>_<HHMM>/<名>_lite.md`;这条路径与 full 的 `<YYYYMMDD_HHMM>` 目录名格式不同(`lite-playbook.md:55` vs `assemble.py:214`)。

---

## 5. 08-29 未提交改动摘要(对照 D-3)

`git diff HEAD --stat`:`harvest.py +865/−33`,`engine-playbook.md +46/−? (46 行变动)`。设计稿 `docs/specs/2026-08-28-external-evidence-expansion-design.md` §10 表 D-3 行(`:397`):「stock-research full:options v2 + EDGAR + 分析师行动 + 财报历史波动 + gnews en(美股);海外映射块 + gnews zh(A 股);us-intel / company-intel agent def + 契约;engine-playbook 消费纪律与数据坑 #17–20」。

**harvest.py 改了什么**
- **删** `options_iv_summary` v1(29 行):只取最近一个到期、跨式用 `lastPrice`、PCR 直接渲染成"偏防御/看空"——正是 §5.2(`spec:177-200`)点名的三处病。
- **加** ≈830 行 D-3 块(`harvest.py:152-980`):
  - 财报时点/时段:`_et_session`(00:00 占位 → None 不猜,`:244-269`)、`earnings_events`、`next_earnings_spec`、`earnings_realized_moves`(AMC=D close→D+1;BMO=D−1 close→D;未知不纳入,`:329-386`)。
  - `options_block`(`:407-538`):渲染 `yf_options.full_chain`,铁律「隐含 vs 历史成对写」「UNMEASURED≠0」「PCR 无方向」「明确不做 max-pain/OI 磁吸/异动」;A股保留 v1 原句 `NO_OPTIONS_DEGRADE`(`:176-179`)。
  - `edgar_block`(`:576-606`)、`analyst_actions_block`(`:609-659`)、`gnews_block`(`:694-723`,T4 注脚硬编码)、`readthrough_block`(`:851-917`,消费者侧独立准入 `_rt_valid :761-786`,禁因果措辞)。
  - `external_sections`(`:944-966`)= 派发清单单一事实源,`slim=True` 返回空(第二道门);`_opt_section`(`:969-980`)presence-gated:返回 None 整节省略。
  - `_degrade_note`(`:200-206`)懒导入 `record_degradation` —— **harvest 史上第一次记账**。
- **main() 变化**(diff `@@ -1228,8 +2028,11`):`if not slim: Options & implied volatility (v2)` 一行 → `for … in external_sections(ticker, end, slim=slim, company_name=identity.get("company_name")): parts.append(_opt_section(...))`(`:2031-2035`)。

**engine-playbook.md 改了什么**:lens ⑥ 改指 options v2(`:133`);「数据采集规格」v2 行去掉旧期权、加 D-3 外源块行(`:165-166`);新增「## 外源扩面 (D-3)」①–④ 节(`:170-206`,含两个情报员表);数据坑 #17–20(`:230-233`)。

**同批未追踪文件**(`git status`):`data/sources/{yf_options,edgar,gnews_rss,yf_tape,cboe_vix,fomc_calendar,fred_calendar,official_event_calendar}.py`(D-1)、`data/readthrough.py`+`readthrough_map.yaml`(D-1/§8)、`.claude/agents/{company-intel,us-intel}.md`(D-3 §7.2/7.3)、`tests/analyze/{test_options_v2(21),test_readthrough_block(14)}.py`;`data/endpoints.py`/`contracts.py` 修改 = 9 个 B 级外源端点登记(`endpoints.py:129-191`)。

**为什么**:§1 病 1「面窄」(美股只在 full 里以一个到期的期权摘要出现;A股与海外链映射不存在,`spec:48`)+ §5.1 阶段裁定「期权只进研究/描述,永不进筛选/评级/regime」+ §5.2「slim 关」。

**D-3 相对设计稿的缺口(读码可见)**
1. IV 1 年分位:§5.2 要求读 `lake/us_options/<sym>@<as_of>`;harvest 传 `iv_history=None`(`:408,466`)且全仓无写湖者 → 永远「样本不足(0)」。
2. 分析师目标价「变化需历史快照」:`prev_targets` 无人传 → 永远 UNMEASURED;`us_ticker` as_of 湖键登记了但没用。
3. gnews → `news_catalog`:§9 说「`news_catalog`(source=gnews_rss)」,harvest 只渲染不入账(`to_catalog_rows` 零调用)。
4. EDGAR/tape/options 全部绕 `get_or_fetch`,与 §9「所有新键进 endpoints + contracts + 记账」只做到登记与记账,未做到入湖 → 法证 `source_lineage`(钩在 cache 层,`cache.py:62-72`)对 analyze 的外源取数零可见。
5. readthrough:映射表 0 条 active,块与 company-intel 面⑥ 双双恒空;设计稿 §11 D-3 验收「映射渲染不出现因果措辞」只能靠 fixture 测试(`test_readthrough_block.py`)证明。
6. gnews 查询词用 yfinance 英文 `longName` 做 zh 搜(`:957`),A股召回差(样本 2 条)。
7. `_spot`(`:128-135`)成了孤儿(唯一调用者 v1 已删)。

---

## 6. 代码健康

**结构**:单文件 2,058 行 / 99,673B,`grep ^def` 得 71 个顶层函数,按注释分隔线可辨 11 个关注点:env 加载(`:33-54`)/ 常量表(`:80-106`)/ v2 yfinance 富化(`:126-149`)/ **D-3 外源 830 行 ≈40%**(`:152-980`)/ v2 分析师·日历·同业(`:983-1059`)/ v3 做空·盈利质量(`:1062-1190`)/ 新闻·预测市场·中国底色(`:1193-1283`)/ akshare 老路市场上下文(`:1291-1371`)/ US 市场上下文(`:1374-1411`)/ v4 可交易性·偿付·户数·解禁(`:1414-1638`)/ `_section`(`:1643`)/ L1 复用渲染(`:1654-1774`)/ tushare-first 包装(`:1777-1819`)/ UZI 包装(`:1822-1864`)/ slim 拆分落盘(`:1867-1906`)/ CLI(`:1909-2058`)。

**可拆边界(建议,未动)**
- `analyze/external.py`:`:152-980`(D-3 全部;自带常量、helper、六个 block、`external_sections`)—— 已是自洽子模块,只被 `main()` 一处引用。
- `analyze/ashare.py`:`:1217-1238,1291-1371,1531-1638,1777-1864`(akshare 老路 + tushare-first 包装 + UZI 包装 + L1 复用渲染)。
- `analyze/us.py`:`:983-1121,1374-1411`(分析师/日历/同业/做空/US regime)。
- `analyze/statements.py`:`:1124-1190,1466-1528`(盈利质量/偿付,共享 `_latest`)。
- `analyze/slim.py`:`:1654-1696,1867-1906`(L1 行读取、二段式落盘)。
- `main()` 本身应变成「按 (market, tier) 生成派发清单」+ 一个执行器 —— 现在 full/slim/A/US 四象限散在 12 个 `if` 里(`:1956,1967,1987,1991,1999,2002,2005,2010,2021,2031,2038,2045`),没有一处能回答「slim+A股 到底拉哪些块」,SKILL.md:38 手抄了一份清单(会漂)。

**重复 / 遮蔽 / 死路**
- 嵌套 `_num`(`:1562`)与 `_cell`(`:1553`)**遮蔽**模块级同名(`:209,218`)且签名不同 —— D-3 加了模块级后形成的陷阱。
- `_spot` 死码(`:128-135`)。
- `ashare_market_context`(akshare,`:1305-1361`)与 `ashare_shareholder_count`(`:1531-1591`)只在 tushare `_pro()` 失败时可达;两者与 tushare 版各自维护同一段语义(户数↓集中/↑分散)。
- `solvency_block` A股尾巴仍印「质押 WebSearch 兜底」(`:1524-1526`),而 #11 已给 tushare 质押 —— 同帧两处口径。
- `macro/harvest.py` 三处「Verbatim」复制;`_load_env` vs `autoresearch/__init__.py` 双重 .env 加载。
- `PEER_MAP`/`SECTOR_ETF` 硬编码 6+8 只(`:97-106`)。
- `_ak_call` 线性退避重试封装只用于 akshare;tushare 直调走 `_ts_call`(4 次)—— 两套重试。

**契约 / 留痕**
- 29/36 行「T 级」降级不记账(见 §1);`degradations()` 对 analyze 几乎为空 → 发布报告无法附「数据降级」行。
- 全路径绕湖:`endpoints.py` 登记了 `stk_holdernumber/pledge_stat/forecast/express/top_inst/margin_detail/stock_news_em/stock_zh_a_gdhs_detail_em/stock_restricted_release_queue_em/stock_lhb_stock_statistic_em/us_options/us_ticker/edgar/us_earnings_dates`,harvest 一个都不经 `get_or_fetch` → 每次全量重拉、无 PIT 回放、无法证 lineage(memory「生产帧 8/9 取数绕湖」在 analyze 侧是 36/36)。
- PIT 漏洞清单:`^VIX period=5d`(`:1403`)、`margin_trend_ts end=now`(`uzi_lenses.py:301`)、`fina_indicator/stk_holdernumber/pledge_stat/forecast` 取最新行、`earnings_quality/solvency` 未过 `filter_financials_by_date`、`.info` 类全部"此刻"。
- 失败语义混装:`margin_trend_ts` 把「非两融标的」与「取数异常」都返回 None(`uzi_lenses.py:306-317`);`lhb_seats` 同(`:353-354`)—— 与 `edgar.FetchOutcome` 四态(`edgar.py:63-72`)、`yf_tape` 三态(`yf_tape.py:27-29`)的新纪律不一致。
- `consensus_eps_block` 在 full 档拿不到价格(`:2042`)→ 不算 fwd-PE;playbook 让分析师"自算",但 snapshot close 就在同一进程里。
- A股新闻块不过滤窗口/不去重(`:1232-1238`);gnews 用英文名搜中文。
- `assemble._ashare_name_from_context` 路径日期格式错配(§3.3)。
- `parse_rating` 默认 "Hold" 吞掉缺失评级;assemble 不校验 `FINAL TRANSACTION PROPOSAL`。
- 测试:`tests/analyze/` 55 个用例(slim 拆分 11 / assemble 4 / harvest helper 5 / readthrough 14 / options 21);**无**用例锁 `main()` 的四象限派发清单、无用例锁 scan 依赖的 `_SLIM_ANCHORS` 四个标题、无用例覆盖 akshare 新闻窗口过滤。
- 无 `AUTORESEARCH_OFFLINE` 处理(`yf_options`/`yf_tape` 有,harvest 老路没有)→ 离线跑 harvest 会逐块超时后写出一份全 `_ERROR_` 的 "成功" 文件。
- 性能:full 档 A股一趟 ≈ yfinance 20+ 次调用 + tushare 直调 ≈14 次(含 `top_inst` ×15 日)+ akshare ≤4 + FRED 8 + Polymarket 2 + gnews 1;无并发、无缓存(除 OHLCV CSV),样本 300308 从 17:10:37 到 17:11:14 的 `Data retrieved on` 戳看 ≈40s+。

---

## 7. 一句话结论(按重要性)

1. **T 日盘后增量在确定性层为零**:公告正文 / 互动易 / 龙虎榜席位 / 涨停·炸板梯队 / 北向·两融当日 / 题材归属一条都不取(§2 #1–#8),lite 档连情报员都没有;36 个取数行里没有一行是「今天 15:00 之后才出现的信息」——超短窗口最需要的那 3 小时是空白。
2. **取数层没有账本也没有湖**:36/36 绕 `get_or_fetch`,29/36 降级不记账,8 处不锚 curr_date;失败只变成 markdown 里的一句斜体,发布报告永远看不见「哪块数据其实没取到」,法证 capsule 也钩不到 analyze 的任何一次读点。
3. **full 的 45% 字节是 400 天 OHLCV + 30 天指标序列**(决策密度最低),而真正带增量的 D-3 外源块在 A股上实际恒空(映射表 0 active、IV 分位恒样本不足、gnews 英文名搜中文只 2 条)—— 体积花在了历史上,增量没落到今天。
