# W2 · A 股超短(隔夜)研究可用免费/低成本实时信息源普查

- 调研日:2026-08-30 晚 → 2026-08-31 上午(北京时间);探针环境:本机(macOS),akshare **1.18.64**(项目 venv,最新版 1.18.94),tushare 1.4.29 + 用户高权限 token,curl 直连。
- 决策窗口:**T+1 14:00–14:45 盘中**(买入决策)与 **T+1 15:00 → T+2 09:25 隔夜**(决定跳空)。
- **核实方式图例**:`[P]`=本机探针实测(真调用真返回);`[D]`=fetch 到官方接口文档;`[S]`=仅搜索摘要/二手资料;`[U]`=**未核实,需探针**。多标叠加表示多重核实。
- 重要基线:本机对东财 **push2/push2delay 行情族已被频控**(项目已知,本次复现),但 push2ex(涨停池)/np-weblist(快讯)/emappdata(人气榜)/data.eastmoney(数据中心)均正常;tushare **2025-11 起单账号单 IP** 访问限制(官方 changelog)。

---

## ① 一页摘要:对隔夜窗口价值最高的 10 个源

| # | 源 | 一句话获取路径 |
|---|----|----|
| 1 | **tushare `major_news` 长篇通讯聚合(已有权限)** | `pro.major_news(start_date,end_date)`;晚间实测 T 日 15:00→T+1 9:30 共 604 条,来源含同花顺 387/财联社 116/华尔街见闻 53/新浪 26,`pub_time` 到秒——这是我们 token 下**唯一已开通的全市场新闻流**,可当隔夜新闻主干 [P] |
| 2 | **巨潮资讯公告直连**(hisAnnouncement/query + disclosure 最新公告流) | `POST http://www.cninfo.com.cn/new/hisAnnouncement/query`(seDate 窗口,免 key)与 `POST /new/disclosure`(column=szse_latest/sse_latest 最新流);法定披露平台全量;**注意时间戳只有日期(午夜对齐),小时级要靠流序或付费 anns_d** [P] |
| 3 | **tushare `kpl_list` 开盘啦榜单(已有权限)** | `pro.kpl_list(trade_date,tag='涨停'/'炸板'/'竞价'…)`;**含涨停原因 lu_desc、题材 theme、连板 status、封板时间 lu_time**;数据次日 8:30 落 → T+2 盘前复盘 T+1 情绪的首选 [P][D] |
| 4 | **akshare 涨停池族 `stock_zt_pool_em` 系列(盘中实时)** | `ak.stock_zt_pool_em/_zbgc_em/_previous_em/_strong_em/_dtgc_em(date)`;push2ex 主机未被封,T+1 14:00–14:45 可实时拉当日涨停/炸板/昨涨停表现(已在生产用) [P] |
| 5 | **新浪 7x24 快讯 API** | `GET https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=20&zhibo_id=152&tag_id=0` 免 key JSON,秒级 create_time,夜间覆盖美股时段(=ak.stock_info_global_sina) [P] |
| 6 | **华尔街见闻 lives API** | `GET https://api-prod.wallstreetcn.com/apiv1/content/lives?channel=a-stock-channel&client=pc&limit=40`(另有 global/us-stock/forex/commodity 频道),免 key JSON,item 带 symbols 关联股票 [P] |
| 7 | **东方财富快讯双端点** | `GET https://np-weblist.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_724&fastColumn=102&pageSize=200`(=ak.stock_info_global_em)与 `newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html`;两端点均通,晚间 19-20 点条目实测在流 [P] |
| 8 | **同花顺快讯 API** | `GET https://news.10jqka.com.cn/tapp/news/push/stock/?page=1&pagesize=20&track=website` 免 key JSON(=ak.stock_info_global_ths 的裸端点;akshare 包装在本机被断连,裸 curl 通) [P] |
| 9 | **新浪外盘 A50 期指(含夜盘)** | `GET https://hq.sinajs.cn/list=hf_CHA50CFD`(带 Referer: finance.sina.com.cn),秒级报价+日期时间字段,同族 hf_CL/hf_GC 覆盖原油黄金铜夜盘 → T+2 开盘跳空的首要免费代理 [P] |
| 10 | **tushare 盘后结构族(已有权限)** | `limit_list_d`(涨跌停炸板+连板数+封单)/`top_list`+`top_inst`(龙虎榜)/`hm_list`(游资名录)/`moneyflow_ind_dc`(板块资金)/`block_trade`/`margin_detail`/`hsgt_top10`;当晚~次晨陆续可得,拼出 T+1 全天情绪结构 [P] |

**替补三件**(差一步即高价值):`report_rc` 券商盈利预测每晚 19–22 点增量(create_time 实测 21:11,评级/目标价变化=隔夜预期差)[P];akshare `stock_irm_cninfo` 互动易问答(时间戳到秒,董秘晚间答复)[P];`news_cctv` 新闻联播文字稿(政策口径,免费)[P]。

---

## ② 分节普查表

列:名称 | 内容类型 | 获取方式 | 免费? | 延迟 | 结构化 | 稳定性/反爬 | 隔夜价值 | 核实

### 1. 公告与官方口径

| 名称 | 内容类型 | 获取方式 | 免费? | 延迟 | 结构化 | 稳定性/反爬 | 隔夜价值 | 核实 |
|---|---|---|---|---|---|---|---|---|
| 巨潮 hisAnnouncement | 全市场公告(标题+PDF url) | `POST cninfo.com.cn/new/hisAnnouncement/query`,pageSize≤30,seDate 窗口;免登录 | ✅ | 准实时(落库即查) | 高(JSON) | 中:需 Referer/XHR 头,页深限制;中报季 2 天 1.3 万条 | **高** | [P] 实测返回 totalAnnouncement=13317(0828~0829) |
| 巨潮「最新公告」流 | 沪/深最新公告滚动 | `POST cninfo.com.cn/new/disclosure`,column=szse_latest / sse_latest | ✅ | 准实时 | 高(JSON) | 中(同上) | **高** | [P];**坑:announcementTime 99.9% 为当日 0 点对齐,无小时戳**(实测 1384 条直方图) |
| 巨潮 RSS | — | **不存在公开 RSS**(官网未见,搜索无) | — | — | — | — | — | [S] |
| 上交所公告查询 | 沪市公司公告 | `GET query.sse.com.cn/security/stock/queryCompanyBulletinNew.do?...`(JSONP,需 Referer: sse.com.cn) | ✅ | 盘后/准实时 | 高(JSONP) | 中:需 Referer;字段 SSEDATE 只有日期 | 中 | [P] |
| 深交所公告 annList | 深市公司公告 | `POST szse.cn/api/disc/announcement/annList`(JSON body) | ✅ | 准实时 | 高 | 本机探针**返回空体**,需补头/cookie 微调 | 中 | **[U] 需探针** |
| tushare `anns_d` | 全量公告+PDF url+**rec_time(到分秒)** | `pro.anns_d(ann_date)`,2000 条/次 | 单独权限(¥1000/年档) | 准实时~盘后 | 高 | 高(官方) | **高**(唯一有精确发布时刻的结构化源) | [D] 文档✅;**[P] 实测无权限**(当前 token 未开) |
| tushare `irm_qa_sh` | 上证 e 互动问答(2023-06 起) | `pro.irm_qa_sh(trade_date)`,3000 条/次,pub_time 到秒 | 单独权限/10000 积分 | 准实时 | 高 | 高 | 中 | [D] 本地 skill 文档✅;[P] 实测无权限 |
| tushare `irm_qa_sz` | 深交所互动易问答(2010-10 起) | `pro.irm_qa_sz(trade_date)`,3000 条/次,含 industry | 同上 | 准实时 | 高 | 高 | 中 | [D]✅;[P] 无权限 |
| akshare `stock_irm_cninfo`(+`_ans_`) | 互动易问答(逐票) | `ak.stock_irm_cninfo('002594')`,提问/更新时间到秒 | ✅ | 准实时 | 高 | 中(irm.cninfo,逐票分页稍慢) | 中(隔夜董秘答复) | [P] 实测 237 行,时间到 2026-08-31 09:54 |
| akshare `stock_sns_sseinfo` | 上证 e 互动(逐票) | `ak.stock_sns_sseinfo('603119')` | ✅ | 准实时 | 高 | 慢(72 页分页,探针 30s 超时未跑完) | 中 | [P 部分]/**[U] 需完整探针** |
| akshare `stock_notice_report` | 东财公告一览(按类型/日期) | `ak.stock_notice_report(symbol='全部', date)`(生产已在用) | ✅ | 盘后/准实时 | 高 | 中(68 页分页慢;本机 30s 超时未完;生产可用) | **高** | [P 生产在用+本次部分] |
| akshare `stock_zh_a_disclosure_report_cninfo` | 巨潮公告(逐票) | 生产在用;本版本(1.18.64)探针**列名不匹配报错** | ✅ | 准实时 | 高 | 中:**akshare 版本落后于巨潮改版**,需升级 | 中 | [P 报错] → 升级或改直连 |
| 披露时段制度 | 规则 | 沪深 2015-06-01 起:交易日**早间 7:30–8:30**(限 4 类)/**午间 11:30–12:30**(限 2 类)/**盘后 15:00 起**主时段;非交易日 13:00–17:00(直通车) | — | — | — | — | 背景 | [P fetch SSE 官网通知] |
| 公告发布时间分布 | 实测 | 免费巨潮口径**测不到小时**(0 点对齐);制度+业内共识:主力落在 **T 日 15:00–23:00**,高峰 18:00–22:00;精确分布需 `anns_d.rec_time` | — | — | — | — | 背景 | [P 直方图证伪免费口径]+[U 精确分布需 anns_d 权限] |

### 2. 盘中/盘后情绪与结构

| 名称 | 内容类型 | 获取方式 | 免费? | 延迟 | 结构化 | 稳定性/反爬 | 隔夜价值 | 核实 |
|---|---|---|---|---|---|---|---|---|
| akshare `stock_zt_pool_em` | 当日涨停池(封板时间/封单/连板/炸板次数/行业) | `ak.stock_zt_pool_em('20260828')`;盘中实时 | ✅ | 实时 | 高 | 中高:push2ex 主机,本机未被封;生产在用 | **高**(14:00–14:45 决策核心) | [P] 82 行,与 tushare limit_list_d 82 行互证 |
| akshare `stock_zt_pool_zbgc_em` / `_dtgc_em` | 炸板池/跌停池 | 同上族 | ✅ | 实时 | 高 | 同上 | **高** | [P] 16 行/1 行 |
| akshare `stock_zt_pool_previous_em` | **昨日涨停今日表现**(赚钱效应核心) | `ak.stock_zt_pool_previous_em(date)` | ✅ | 实时 | 高 | 同上 | **高** | [P] 77 行 |
| akshare `stock_zt_pool_strong_em` | 强势股池 | 同上 | ✅ | 实时 | 高 | 同上 | 中 | [P] 252 行 |
| akshare `stock_changes_em` | 盘口异动(60 类:火箭发射/大笔买入…,时间到秒) | `ak.stock_changes_em('火箭发射')` | ✅ | 实时 | 高 | 中(push2ex) | 中(盘中佐证) | [P] 1401 行@10:57 |
| akshare `stock_market_activity_legu` | 赚钱效应仪表(涨/跌/涨停/炸板率等 12 项) | `ak.stock_market_activity_legu()`(乐咕乐股) | ✅ | 实时 | 高 | 中(第三方小站) | 中 | [P] |
| tushare `limit_list_d`(有权限) | 涨跌停+炸板全字段(first/last_time、open_times、连板 limit_times、封单、板上成交) | `pro.limit_list_d(trade_date, limit_type='U/D/Z')`,2020 起,不含 ST | ✅(5000 积分档) | 盘后 | 高 | 高 | **高**(连板高度/情绪) | [P]+[D] |
| tushare `limit_step` / `limit_cpt_list` | 连板天梯/涨停最强板块 | 文档 doc_id=356/357 | 需更高积分 | 盘后 | 高 | 高 | 中 | [D]✅;[P] 无权限 |
| tushare `limit_list_ths` | 同花顺涨跌停榜(**含涨停原因 lu_desc**、tag、N连板),2023-11 起,**约 16:00 更新** | doc_id=355 | 8000 积分 | 盘后 16:00 | 高 | 高 | **高**(若开通:T+1 收盘后 1h 拿到当日原因) | [D]✅;[P] 无权限 |
| tushare `kpl_list`(有权限) | 开盘啦榜单:**涨停原因 lu_desc/题材/连板 status/封板时刻**,tag 含**竞价** | `pro.kpl_list(trade_date, tag)`;**次日 8:30 落数** | ✅ | **T+1 → T+2 8:30** | 高 | 高 | **高**(T+2 盘前) | [P] 样例:渝三峡A 14:44 封板「化工」/誉衡药业「创新药、股权转让」;竞价 tag 164 行 |
| tushare `kpl_concept`(+`_cons`)(有权限) | 开盘啦题材梯队(题材×涨停数 z_t_num)+成分 | `pro.kpl_concept(trade_date)` | ✅ | 盘后/次晨 | 高 | 高 | **高**(题材梯队) | [P] 252 题材/3000 成分 |
| 涨停原因(东财/同花顺/韭研网页) | 涨停揭秘文本 | 东财 zt 池**无原因列**(实测);同花顺「异动观察」网页 yuanchuang.10jqka;韭研公社 Nuxt 页(部分 SSR,深度内容登录墙) | ✅ | 盘中~晚间 | 低(网页) | 中低 | 中 | [P zt 池列名]+[S]+[P 韭研 SSR 探针] |
| 开盘啦 App 直连 | 涨停原因/情绪/人气(50+私有接口) | `apphq.longhuvip.com/w1/api/index.php`(逆向参数) | ✅ | 实时 | 高 | **本机探针空响应,需逆向参数/签名**;无官方 API | 中 | **[U] 需探针**(tushare kpl_* 已覆盖大部分) |
| 概念板块(东财) | 概念行情/成分 | `ak.stock_board_concept_name_em` — **本机 push2 断连**;tushare `moneyflow_ind_dc`(有权限)可替代 | ✅ | 实时/盘后 | 高 | 东财口本机不可靠 | 中 | [P 断连]+[P 替代通] |
| 概念板块(同花顺) | 概念指数/成分 | `ak.stock_board_concept_name_ths` 探针超时(hexin-v cookie 反爬);tushare `ths_index/ths_daily/moneyflow_ind_ths`(生产在用) | ✅ | 盘后 | 高 | THS 网页口不稳 | 中 | [P 超时]+[P 生产替代] |
| 板块资金流 | 行业/概念资金 | tushare `moneyflow_ind_dc`(有权限,[P] 1031 行);`ak.stock_sector_fund_flow_rank` 本机断连 | ✅ | 盘后(DC 口) | 高 | tushare 高 | 中 | [P] |
| 龙虎榜 | 席位明细 | tushare `top_list`(57 只)+`top_inst`(630 席,盘后晚间);ak `stock_lhb_detail_em`(含「解读」列) | ✅ | 盘后(约 17–19 点,精确时点未核) | 高 | 高 | **高**(游资接力预期) | [P]×3 |
| 游资名录 | 席位→游资映射 | tushare `hm_list`(113 名录,有权限);`hm_detail` 每日明细**无权限**(10000 积分) | 部分 | 静态/盘后 | 高 | 高 | 中 | [P]+[D] |
| 集合竞价 | 竞价成交/竞价异动 | tushare `stk_auction`(**9:26–9:29 当日可取**,2025-01 起,**无权限**);`stk_auction_o/_c`(盘后,无权限);替代:腾讯分笔 `ak.stock_zh_a_tick_tx_js`(含 9:25 首笔,[P] 1780 行)、kpl_list tag=竞价(次日) | 部分 | 当日 9:26/盘后 | 高 | tushare 高;腾讯口中 | 中(对 T+2 开盘卖出的竞价观察有用) | [D]✅+[P 无权限]+[P 替代通] |
| 分时/分钟 | 1–60min bar | **tushare `rt_min`(有权限!实时分钟,1000 行/次,单/多代码)** [P 实测 10:55 live];`rt_k` 全市场快照无权限;`stk_mins` 历史分钟单独权限**未探**;`ak.stock_zh_a_hist_min_em`/`stock_intraday_em`/`stock_bid_ask_em` 本机 push2 断连 | 部分 | 实时 | 高 | tushare 高 | **高**(14:00–14:45 持仓/候选盘中确认) | [P];stk_mins **[U] 需探针** |
| 全市场快照 | spot 全 A | `ak.stock_zh_a_spot_em` **本机断连**(频控);低频单发 curl push2delay 可通;tushare `rt_k` 未开 | ✅ | 实时 | 高 | 本机不可靠 | 中 | [P 断连+单发通] |
| 融资融券 | 两融余额/明细 | tushare `margin`/`margin_detail`(有权限,[P] 4436 行);ak `stock_margin_detail_szse`(通);**T+1 披露**(交易所惯例盘前~上午,精确时刻未核) | ✅ | **T+1** | 高 | 高 | 低(对 T+2 开盘略滞后) | [P];披露时刻 **[U]** |
| 北向/南向 | 沪深港通 | **2024-05-13 起盘中实时取消;2024-08-19 起盘后仅:成交总额+笔数+ETF 成交额+前十大活跃(`hsgt_top10`/`ggt_top10` 有权限 [P])**;`moneyflow_hsgt` 总额([P]);⚠️ `hk_hold` 现际上是**南向持股**(repo 已修「southbound 被当 northbound」) | ✅ | 盘后 | 高 | 高 | 中(只剩总量+前十) | [P]+[S 规则多源]+repo 佐证 |
| ETF 份额/申赎 | 份额规模 | tushare `fund_share`(有权限,[P] 1734 行,日频);ak `fund_etf_scale_sse/szse`([P] 593 行);`etf_share_size`(8000 分,无权限);「ETF 实时参考(IOPV/申赎)」doc_id=454 单独权限 | 部分 | T+1 为主 | 高 | 高 | 低中 | [P]+[D] |
| 大宗交易 | 折溢价成交 | tushare `block_trade`([P] 58 行,盘后);ak `stock_dzjy_mrmx`([P] 46 行) | ✅ | 盘后晚间 | 高 | 高 | 低中 | [P] |
| 两市成交额/大盘资金 | 量能 | tushare `moneyflow_mkt_dc`(有权限 [P]);指数实时:tushare「指数实时日线」doc 403 **无权限**;`ak.stock_zh_index_spot_em` 本机断连;替代:新浪 hq.sinajs 指数码 | ✅ | 实时/盘后 | 高 | 混合 | 中 | [P]+[D] |
| 人气/热度榜 | 东财人气榜 | `ak.stock_hot_rank_em`([P] 100 只)/裸端点 `POST emappdata.eastmoney.com/stockrank/getAllCurrentList`([P] 通,免 key);`stock_hot_up_em` 飙升榜([P]);`stock_hot_keyword_em` 个股热门概念([P]) | ✅ | 实时 | 高 | 中高 | 中 | [P]×4 |
| tushare `ths_hot`/`dc_hot` | 同花顺/东财 App 热榜(盘中 4 次+盘后 4 次,**最晚 22:00/22:30**) | doc 320/321 | 6000/8000 积分,**无权限** | 盘中+22:00 | 高 | 高 | 中(22 点情绪定格) | [D]✅+[P 无权限] |

### 3. 快讯与新闻流

| 名称 | 内容类型 | 获取方式 | 免费? | 延迟 | 结构化 | 稳定性/反爬 | 隔夜价值 | 核实 |
|---|---|---|---|---|---|---|---|---|
| 财联社电报 | A股最强快讯(加红分级) | 网页 JS 渲染;**旧 `nodeapi/telegraphList` 已 404(实测)**;现行 `api/cache?name=telegraph`+`sign=md5(sha1(排序参数))`(社区逆向);akshare 1.18.64 的 `stock_info_global_cls` 因此**超时失效** | ✅(逆向) | 实时 | 高(打通后) | **高反爬**(2025 起加签) | **高** | [P 404/超时]+[S sign 方案];**[U] sign 直连需探针** |
| 财联社(经 tushare `news` src=cls) | 同上结构化 | `pro.news(src='cls')` | 单独权限(¥1000/年) | 实时 | 高 | 高 | **高** | [D]✅;[P] 无权限 |
| 新浪 7x24 | 全球快讯 | `zhibo.sina.com.cn/api/zhibo/feed`(免 key JSON;=ak.stock_info_global_sina [P 通]) | ✅ | 实时(秒) | 高 | 中高(需常规 UA) | **高**(夜间美股时段持续更新) | [P]×2 |
| 东财全球快讯 | 7×24 快讯 | np-weblist getFastNewsList / newsapi kuaixun(均 [P] 通);=ak.stock_info_global_em [P 通] | ✅ | 实时 | 高 | 中高 | **高** | [P]×3 |
| 同花顺快讯 | 7×24 推送 | news.10jqka tapp JSON([P] 通);akshare 包装本机断连 | ✅ | 实时 | 高 | 中(裸端点稳,网页口有 hexin-v) | 中高 | [P] |
| 华尔街见闻 lives | 全球+A股快讯(带关联标的) | api-prod.wallstreetcn.com lives([P] 通,channel 可选) | ✅ | 实时 | 高 | 中高 | **高**(海外夜间增量) | [P] |
| 富途快讯 | 港美快讯 | `ak.stock_info_global_futu`([P] 50 行) | ✅ | 实时 | 高 | 中 | 中(美股盘中) | [P] |
| 东财财经早餐 | 每日 6:00 晨报汇总 | `ak.stock_info_cjzc_em`([P] 400 行,发布时间 06:00) | ✅ | T+2 06:00 | 中 | 中高 | 中(盘前一站式回看隔夜) | [P] |
| 财新精选 | 深度财经 | `ak.stock_news_main_cx`([P] 100 行,tag/summary/url) | ✅(摘要) | 小时级 | 中 | 中 | 低中 | [P] |
| 个股新闻(东财搜索口) | 逐票新闻 | `ak.stock_news_em('300059')`(生产已建 as-of lake ~1891 分片) | ✅ | 准实时 | 高 | 中高 | 中(逐票补充,有选择偏差) | [P+生产] |
| tushare `news` 快讯 | 9 源快讯(sina/wallstreetcn/10jqka/eastmoney/yuncaijing/fenghuang/jinrongjie/**cls**/yicai),1500 条/次,6 年+历史 | `pro.news(src, start_date, end_date)` | **单独权限 ¥1000/年** | 实时 | 高 | 高 | **高**(一把钥匙=全部快讯史) | [D]✅(本地 skill 文档);[P] 无权限 |
| tushare `major_news`(**有权限**) | 长篇通讯 9 源(新华网/凤凰/同花顺/新浪/华尔街见闻/中证网/财新/一财/财联社),8 年+ | `pro.major_news(...)`,400 行/次 | ✅ | 准实时(pub_time 到秒) | 高 | 高 | **高**(实测隔夜窗 604 条/晚) | [P]+[D] |
| 新闻联播 | 政策口径 | tushare `cctv_news`(**无权限**);**akshare `news_cctv`([P] 通,12 段/天,免费)**;当晚落地时点未核(联播 19:00–19:30 直播) | ✅(ak 口) | 当晚 | 中 | 中 | 中高(政策夜) | [P]+[D];落地时点 **[U]** |
| 国家政策库 | 国务院系政策原文(110 类,pubtime 到秒,样例多为 17:00/19:00 发布) | tushare `npr`(**无权限,单独开**) | 付费 | 当晚 | 高 | 高 | 中高 | [D]✅;[P] 无权限 |
| 中国政府网/国务院 | 政策发布 | www.gov.cn/zhengce/([P fetch] 服务器渲染,**只有日期无时刻**;无 RSS);国务院客户端无公开 API | ✅ | 当晚~次日 | 低 | 中 | 中 | [P fetch] |
| 新华社/央视 | 通稿 | 新华网/央视网页面抓取(无官方 RSS);替代:**中新网 RSS `chinanews.com.cn/rss/finance.xml`([P] 活)**、国家统计局 RSS 页 | ✅ | 准实时 | 低(RSS 中) | 中 | 中 | [P RSS]+[S] |
| 证监会/交易所发布 | 监管动态 | csrc.gov.cn/csrc/xwfb 列表(**只有日期**,无 RSS,搜索核);交易所官网「本所新闻」 | ✅ | 当晚 | 低 | 中 | 中 | [S];**[U] 页面抓取需探针** |
| 百度财经日历 | 宏观数据/事件日历(带时刻+重要性) | `ak.news_economic_baidu(date)`([P] 109 条,07:00–23:30) | ✅ | 静态日历 | 高 | 中 | 中(排夜间宏观雷) | [P] |

### 4. 研报与机构预期

| 名称 | 内容类型 | 获取方式 | 免费? | 延迟 | 结构化 | 稳定性/反爬 | 隔夜价值 | 核实 |
|---|---|---|---|---|---|---|---|---|
| tushare `report_rc`(**有权限**) | 券商盈利预测/评级/目标价(2010 起) | `pro.report_rc(report_date)`,3000 条/次;**每晚 19–22 点更新**(create_time 实测 21:11) | ✅ | **T+1 晚 19–22 点** | 高 | 高 | **高**(评级/目标价隔夜变化=预期差) | [P]+[D] |
| tushare `research_report` | 研报语料(标题+**摘要 abstr**+pdf url,2017 起,**每天两次增量**) | doc_id=415,1000 条/次 | **单独权限** | 半日 | 高 | 高 | 中高 | [D]✅(本地 skill);[P] 无权限 |
| akshare `stock_research_report_em` | 东财研报中心(评级+盈利预测表+pdf 链接) | `ak.stock_research_report_em(symbol)`([P] 227 行) | ✅ | 半日~T+1 | 高 | 中高 | 中 | [P] |
| 慧博 hibor | 研报全文 | 网站可达([P] 200);全文需注册/APP,接口未核 | 部分 | T+1 | 低 | 中 | 低 | **[U] 需探针** |

### 5. 社区情绪(可获取性判断)

| 名称 | 内容类型 | 获取方式 | 免费? | 延迟 | 结构化 | 稳定性/反爬 | 隔夜价值 | 核实 |
|---|---|---|---|---|---|---|---|---|
| 雪球 | 讨论/关注热度 | `ak.stock_hot_tweet_xq`(**[P] 通,5631 只全市场讨论热度,免登录**);其余 xueqiu API 需 cookie(acw_sc__v2 加密,1.18.92 起 akshare 加「登录引导」) | 部分 | 准实时 | 中 | **中高反爬** | 中(热度尚可,正文噪声大) | [P]+[S] |
| 东财股吧 | 帖子/评论 | 网页爬取(guba.eastmoney);**反爬=封 IP 后返回假数据/404**;人气榜裸端点 emappdata 免 key([P] 通) | ✅ | 实时 | 低 | **高风险(假数据型反爬)** | 低(帖子);中(人气榜) | [S]+[P 榜单] |
| 淘股吧 | 游资社区 | 本机 curl **连接失败(code 000)**,疑云防护 | ✅ | — | 低 | **高** | 低 | **[U] 需探针** |
| 韭研公社 | 涨停逻辑/异动解析/晚间前瞻 | Nuxt SSR 首屏可抓([P] 页面含涨停/异动字样);深度正文+盘前热点**登录墙**;无公开 API | 部分 | 晚间~盘前 | 低 | 中高 | 中(逻辑文本质量高) | [P 部分]+**[U] 登录态抓取需探针** |
| 同花顺问财 | 自然语言选股 | 社区库 `pywencai`(非官方):需**浏览器 cookie+hexin-v(node 计算)**,接口策略常变,高频封禁 | ✅(低频) | 实时 | 高(选股结果表) | **高(cookie+js 反爬,常失效)** | 低中 | [S 多源];**[U] 需探针** |
| 微博热搜 | 舆情爆点 | `weibo.com/ajax/side/hotSearch` 本机 **403(需 cookie)**;第三方镜像 API 众多 | 部分 | 实时 | 中 | 高 | 低 | [P 403];**[U] cookie 方案需探针** |
| 百度指数 | 搜索热度 | **必须登录+加密参数**;第三方付费 API | ❌ 实质不免费 | T+1 | 中 | 高 | 低 | [S] |
| 百度股市热搜 | 股票热搜榜 | `ak.stock_hot_search_baidu`([P] 12 行) | ✅ | 实时 | 高 | 中 | 低中 | [P] |

### 6. 海外与跨市场(隔夜跳空核心输入)

| 名称 | 内容类型 | 获取方式 | 免费? | 延迟 | 结构化 | 稳定性/反爬 | 隔夜价值 | 核实 |
|---|---|---|---|---|---|---|---|---|
| 新浪外盘 hf_* | **A50 期指 CFD(含夜盘)**/原油/黄金/铜等 | `hq.sinajs.cn/list=hf_CHA50CFD,hf_CL,hf_GC`+Referer;时间字段到秒 | ✅ | 实时 | 高(定长串) | 中高(需 Referer) | **高**(T+2 开盘方向第一代理) | [P] |
| 东财 XIN9 | 富时中国 A50 指数 | `push2delay.eastmoney.com/api/qt/stock/get?secid=100.XIN9`(单发通;高频防断连) | ✅ | 延迟约 15min | 高 | 中(push2delay 频控) | 中 | [P] |
| SGX A50 官方 | 期指(T session 09:00–16:35,T+1 夜盘约 17:00–次日 05:15 新加坡时间) | 无免费官方 API;investing 网页 | ❌/网页 | 实时 | 低 | 中 | 背景 | [S];**时段分钟级 [U]** |
| yfinance | 美股/中概 ADR/美债ETF/美元/商品/^GSPC/^IXIC | 项目生产在用(数据层现有) | ✅ | 15min 内 | 高 | 中(偶发限流) | **高**(21:30–04:00 美股窗) | [P 生产] |
| akshare `index_global_spot_em` | 全球指数快照(56 个,带行情时间) | `ak.index_global_spot_em()` | ✅ | 准实时 | 高 | 中 | 中高 | [P] |
| 港股/ADR 映射 | 港股日线/实时 | tushare `hk_daily`(**有权限** [P]);`hk_rt` 族单独权限;ak `stock_hk_spot_em`(未探,东财口) | 部分 | 盘后/延迟 | 高 | 中 | 中 | [P]+[U ak 口] |
| FRED | 美债/宏观 | 项目生产在用(FRED_API_KEY) | ✅ | T+1 | 高 | 高 | 低中 | [P 生产] |
| 商品夜盘(境内) | 黑色/有色/能化 21:00–次日 2:30 | 新浪期货 `futures_zh_realtime`/`hq.sinajs`(nf_ 码);akshare `futures_foreign_commodity_realtime` **参数名与文档不符(探针 TypeError)** | ✅ | 实时 | 高 | 中 | 中(行业价格映射) | [P hf 码通];**[U] akshare 包装需对签名** |

---

## ③ 隔夜信息时间线:T+1 14:00 → T+2 09:25(北京时间)

> 括号内=本次实测/文档依据。★=已有权限/免费探针通,可直接接。

| 时段 | 落地的增量信息 | 源(状态) |
|---|---|---|
| **T+1 14:00–14:45(决策时刻)** | 当日涨停/炸板/昨涨停表现快照;盘口异动;分钟线确认;快讯扫描;板块梯队(实时口) | ★ak zt_pool 族[P] · ★tushare rt_min[P] · ★stock_changes_em[P] · ★新浪/东财/华尔街见闻/同花顺快讯[P] · ★东财人气榜[P] · ★market_activity_legu[P] |
| 14:45–15:00 | 尾盘集合竞价(14:57–15:00 深市);当日封板终局 | ★zt_pool 最终态;竞价明细次日见 kpl_list tag=竞价 |
| **15:00–16:00** | **公告主披露时段开启**(制度:盘后 15:00 起);快讯进入公司公告转述高峰(major_news 实测 15 点档 92 条=全窗最高);两市收盘统计;北向盘后总额+前十活跃 | ★巨潮最新流[P] · ★major_news[P] · ★moneyflow_hsgt/hsgt_top10[P] |
| **16:00–17:00** | 同花顺涨跌停榜(约 16:00,含涨停原因;无权限档)落地;交易所盘后统计;龙虎榜开始出 | limit_list_ths[D 无权限] · ★top_list/top_inst(17 点后陆续,[P 数据完整,时点未核[U]]) |
| **17:00–19:00** | 公告持续密集;A50 夜盘开盘(约 17:00 SGT);major_news 17 点档 75 条;政策文件(npr 样例常见 17:00/19:00 发布) | ★巨潮流 · ★hf_CHA50CFD[P] · npr[D 无权限] |
| **19:00–20:00** | **新闻联播**(19:00–19:30,政策口径);国常会通稿多在晚间(精确时点[U]);公告继续 | ★news_cctv(次日抓稳妥,当晚时点[U]) · ★major_news 19 点档 46 条 |
| **20:00–22:00** | **公告晚高峰**(业内 18:00–22:00;免费口径无小时戳,以 major_news 20/21 点档 61/56 条为代理);**券商盈利预测/评级增量落地(report_rc create_time 实测 21:11)**;热榜终榜(ths_hot/dc_hot 22:00 档,无权限);美股开盘(21:30 夏令时) | ★巨潮流 · ★report_rc[P] · ★新浪 7x24/华尔街见闻(美股头条)[P] |
| **22:00–24:00** | 美股盘初方向;A50 夜盘随美股波动;公告长尾(23 点档 19 条);east/sina 快讯持续 | ★hf_CHA50CFD · ★快讯族 · ★major_news 22/23 点档 31/19 条 |
| **00:00–04:00/05:00** | 美股午盘~收盘(04:00 夏令时收盘);美债/美元/商品定型;major_news 深夜档 0–5 点合计约 33 条 | ★yfinance(收盘价) · ★hf_* 夜盘 · ★快讯族 |
| **05:00–07:00** | A50 夜盘收盘(约 05:15 SGT[U]);美股收盘数据完整可取;隔夜要闻沉淀 | ★yfinance · ★index_global_spot_em |
| **06:00–08:00** | **东财财经早餐 06:00**(实测 06:00:37);中新网 RSS 晨间更新;早间披露时段 7:30–8:30(停复牌/澄清 4 类) | ★stock_info_cjzc_em[P] · ★巨潮流 · ★major_news 7 点档 21 条 |
| **08:00–09:15** | **开盘啦 T+1 全量榜单落地(8:30:涨停原因/题材/竞价)**;8 点档 major_news 23 条;两融 T+1 数据(时点[U]) | ★kpl_list/kpl_concept[P] · ★margin_detail[P] |
| **09:15–09:25(T+2 竞价=卖出执行窗)** | 集合竞价撮合;9:25 出开盘价;竞价异动(stk_auction 9:26 可取但无权限;腾讯分笔 9:25 首笔可即时抓) | ★stock_zh_a_tick_tx_js[P] · ★rt_min(9:30 起)[P] |

**工程要点**:对 gap_c1_o2 主尺,真正改变 T+2 开盘价的是 15:00–23:00 公告+21:30–04:00 美股+A50 夜盘三条线;我们 token 现状下**已经免费在手**的覆盖=major_news(新闻)+巨潮流(公告,无小时戳)+report_rc(预期)+kpl(次晨情绪)+hf_A50/yfinance(外盘);**付费才有**的关键缺口=公告精确时刻(anns_d)+财联社结构化(news src=cls)+当日 16:00 涨停原因(limit_list_ths)。

---

## ④ LLM 消费这些信息的已有做法

### 论文(2023–2026,与 A 股新闻/事件相关)
| 工作 | 数据源→LLM 用法 | 对我们的启示 |
|---|---|---|
| **CN-Buzz2Portfolio** (arXiv 2603.22305) | 中国市场**每日热榜新闻**(2024→2025 中)→ 三段 CPA 工作流(压缩→感知→配置),输出宏观/行业 ETF 配置;9 个 LLM 差异显著 | 「高曝光叙事→行业配置」与我们 scan L3 行业透镜同构;压缩段=我们的确定性预处理 [P fetch 摘要] |
| **Janus-Q** (arXiv 2602.19919) | 62,400 篇金融新闻,标注 10 事件类型+关联股票+情绪+**事件驱动 CAR** → SFT+分层门控奖励 RL;Sharpe +102% vs 基线 | **事件为决策单元**而非新闻原文;给事件挂 CAR 标签=把「新闻→隔夜收益」先验数值化 [P fetch 摘要] |
| **LLMFactor** (arXiv 2406.10811) | 提示词从新闻中抽「影响股价的因子」,美+中市场 | 抽因子而非直接问涨跌,可复用到 L4 情报站 [S] |
| 中文金融新闻情绪因子 (arXiv 2306.14222) | 3 个 LLM 抽中文新闻情绪→预测涨跌 | 情绪因子基线做法 [S] |
| **FinTruthQA** (arXiv 2406.12009) | **互动易/e 互动问答**披露质量评估基准 | 互动易文本可信度参差,LLM 需先判「答没答」再用 [S] |
| **AlphaSchema** (arXiv 2607.26642) | LLM agent 在 A 股生成交易因子(结构化语义探索) | 因子生成线,与新闻消费正交 [S] |
| TradingAgents (arXiv 2412.20138) / survey 2408.06361 / FinRobot 2405.14767 / Agentic Trading 2605.19337 | 多 agent 框架综述与原型 | 我们即 TradingAgents 改造项目,略 [S] |

### 开源项目的 A 股新闻接法(实查)
| 项目 | 新闻/数据接法 | 备注 |
|---|---|---|
| **TradingAgents-CN**(28k★,v1.1.0 2026-07) | 数据:Tushare/AKShare/BaoStock 统一管理;新闻:`dataflows/news/{google_news, realtime_news, chinese_finance, reddit}` + `realtime_news_utils.RealtimeNewsAggregator`(Finnhub/AlphaVantage/NewsAPI + **akshare 东财个股新闻自动用于 A股**);情绪源提及微博/雪球;多级新闻过滤+质量评估;MongoDB+Redis 缓存 | 与我们最大差异:它靠**通用搜索/海外 NewsAPI**兜底,无财联社/公告直连;质量评估层值得抄 [P fetch 目录+S] |
| **A_Share_investment_Agent**(24mlight) | akshare 行情+新浪/网易/东财新闻;情绪 agent 默认**每票 5 条新闻**打 -1..1 分;SQLite 缓存,**新闻 TTL 2h** | 「少而新」的逐票新闻喂法+TTL 缓存与我们 L4 slim 同构 [P fetch] |
| **daily_stock_analysis**(ZhuLinsen) | 行情 akshare/baostock/yfinance/tushare;**新闻全部走搜索 API**(Anspire/SerpAPI/Tavily/Bocha/Brave/SearXNG 降级链);工作日 18:00 定时跑 | 「搜索引擎当新闻层」是零 API 成本的另一路;18:00 定时=瞄准公告晚高峰前沿 [P fetch] |
| **FinGPT**(AI4Finance) | 开源金融 LLM,含中文情绪权重与新闻情绪因子论文 | 自训模型路线,与零付费 LLM 约束不合,仅供对照 [S] |
| **ai_quant_trade**(charliedream1) | 汇总 FinGPT/FinMem/TradingAgents/FinRobot 等大模型金融应用 | 索引型资源 [S] |
| **akshare 官方 LLM 姿势** | 1.18.85 起内置 `ak.search`/`ak.interface_info` **离线接口注册表**(专为 LLM 解析接口名设计);AKTools 起 HTTP;社区多个 akshare-MCP | 我们 endpoints.py 的「接口名→函数」映射可用 ak.search 做防漂移探针 [S+D changelog] |

### 消费模式小结(可移植结论)
1. **事件化优先**:领先工作把新闻先变「事件类型+关联标的+方向」,再进决策(Janus-Q/LLMFactor);对齐我们 news_catalog 的 canonical_event 设计。
2. **少而新**胜过多而全:逐票 3–5 条+TTL,几乎是开源共识(A_Share_investment_Agent;我们 L4 intel 同构)。
3. **没有人真正打通财联社/公告直连**——开源项目全部绕道搜索 API 或东财个股新闻;我们若接 major_news+巨潮流,信息面即超过这些项目的默认档。
4. **PIT 纪律在开源项目中普遍缺失**(抓到即用),我们的 first_seen/available_stage 是差异化优势,普查里所有「无小时戳」的源(巨潮免费口径、gov.cn)必须靠自记 first_seen 补时间轴。

---

## ⑤ 风险与稳定性

1. **东财 push2 家族(本机实测复现)**:`stock_zh_a_spot_em`/`stock_bid_ask_em`/`stock_intraday_em`/`stock_zh_a_hist_min_em`/`stock_zh_index_spot_em`/`stock_board_concept_name_em`/`stock_sector_fund_flow_rank` 全部 RemoteDisconnected(30s);但**单发 curl push2delay 可通**→ 频控型封禁而非域名死。2026-04 akshare 曾把 39 个文件从 push2 切到 push2delay(issue #7230);东财另有强制分页 200 变更。**结论:盘中行情面别押 akshare 东财口,用 tushare rt_min(已有权限)+push2ex 涨停池。**
2. **akshare 版本风险(本项目 1.18.64 落后)**:实测两处已烂——`stock_info_global_cls`(财联社 nodeapi 404)与 `stock_zh_a_disclosure_report_cninfo`(巨潮改版列名不匹配)。akshare 近月维护节奏很快(1.18.85 注册表/1.18.88–94 连环修;**1.18.89 起要求 Python≥3.11**)。升级需过一遍我们 20 个在用 ak.* 调用的回归探针。
3. **财联社反爬升级(2025 起)**:旧 `nodeapi/telegraphList` 弃用,现行 `api/cache?name=telegraph` + `sign=md5(sha1(sorted_params))`,参数含 app/os/sv 版本号,变版即断;第三方镜像(觅知等)可用但引入二手依赖。结构化拿财联社的**正路是 tushare news 权限(¥1000/年)**。
4. **tushare 侧约束**:2025-11-10 起**单账号单 IP**(changelog);新闻/公告/研报语料/分钟历史都是**积分外单独权限**(¥200–¥2000/年档,权限表 doc_id=290);本 token 实测已开:major_news/report_rc/rt_min/limit_list_d/kpl_*/top_*/hm_list/moneyflow_*/fund_share/block_trade/margin/hk_daily 等;未开:news/anns_d/npr/research_report/irm_qa_*/cctv_news/stk_auction*/rt_k/limit_list_ths/ths_hot/dc_hot/hm_detail/etf_share_size/us_daily。
5. **假数据型反爬**(最危险一类):东财股吧封 IP 后**返回假数据/重定向到别的吧**而非报错——社区帖爬取不建议进决策链。淘股吧本机直接连不通;微博需 cookie;百度指数需登录;问财 cookie+hexin-v 常失效。
6. **6 个月内接口变动清单**:①北向披露规则(2024-05 盘中取消/2024-08-19 盘后仅总额+前十;所有「北向实时」接口史料失效,`hk_hold` 语义已变成南向——本 repo 已修);②巨潮页面改版(akshare 巨潮公告接口列名断);③财联社加签;④东财 push2→push2delay+分页 200;⑤tushare 上新:20260706 日线加盘后量额、20260328 ETF 实时参考、20260128 申万实时、20260122 research_report、20251226 ETF 份额、20251225 npr;⑥akshare 1.18.89 Python≥3.11、1.18.92 雪球登录引导(=雪球 cookie 收紧信号)。
7. **抓取礼仪**:巨潮 pageSize 固定 30、加 0.3–0.5s 间隔与真实 UA/Referer;交易所 JSONP 需 Referer;kpl/东财榜单声明「仅限个人研究」;新闻正文有版权,只存哈希+摘要+URL 合规面更稳(与 news_catalog 的 blob 设计一致)。
8. **本普查自身的坑**:探针跑在**周一盘中+中报季末**,公告量(2 天 1.3 万条)与快讯节奏偏峰值;push2 断连有本机 IP 前科成分,换 IP 结论可能更松;巨潮「最新流」时间戳 0 点对齐是**口径事实**(1384 条实测 99.9%),不是探针噪声。

---

## ⑥ 参考 URL 表

| 主题 | URL |
|---|---|
| tushare 权限/积分表 | https://tushare.pro/document/1?doc_id=290 |
| tushare 官方 changelog | https://tushare.pro/document/1?doc_id=9 |
| tushare news/major_news/anns_d/npr/research_report/cctv_news | doc_id=143 / 195 / 176 / 406 / 415 / 154(https://tushare.pro/document/2?doc_id=…) |
| tushare irm_qa_sh / irm_qa_sz | doc_id=366 / 367 |
| tushare stk_auction(当日/开盘/收盘) | doc_id=369 / 353 / 354 |
| tushare rt_k / rt_min / 历史分钟 | doc_id=372 / 374 / 370 |
| tushare limit_list_d / limit_step / limit_cpt_list / limit_list_ths | doc_id=298 / 356 / 357 / 355 |
| tushare kpl_list / kpl_concept / ths_hot / dc_hot | doc_id=347 / (概念族) / 320 / 321 |
| tushare top_list / top_inst / hm_list / hm_detail | doc_id=106 / 107 / 311 / 312 |
| tushare report_rc / moneyflow_hsgt / ETF份额 | doc_id=292 / 47 / 408 |
| akshare 文档/changelog | https://akshare.akfamily.xyz/data/stock/stock.html · /changelog.html |
| akshare push2 封禁 issue | https://github.com/akfamily/akshare/issues/7230 · /issues/6986 · /issues/6061 |
| 巨潮公告接口(社区封装) | https://github.com/rollysys/use_cninfo · https://github.com/tr1s7an/CnInfoReports |
| 巨潮直连端点 | http://www.cninfo.com.cn/new/hisAnnouncement/query · /new/disclosure |
| 上交所公告 JSONP | https://query.sse.com.cn/security/stock/queryCompanyBulletinNew.do |
| 上交所披露时段通知(2015) | https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20150912_3988858.shtml |
| 新浪 7x24 API / 外盘 hq | https://zhibo.sina.com.cn/api/zhibo/feed · https://hq.sinajs.cn/list=hf_CHA50CFD |
| 华尔街见闻 lives | https://api-prod.wallstreetcn.com/apiv1/content/lives?channel=a-stock-channel |
| 东财快讯 | https://np-weblist.eastmoney.com/comm/web/getFastNewsList · https://newsapi.eastmoney.com/kuaixun/v1/getlist_102_ajaxResult_50_1_.html |
| 同花顺快讯 | https://news.10jqka.com.cn/tapp/news/push/stock/ |
| 财联社(页面/逆向线索) | https://www.cls.cn/telegraph · https://github.com/InphinitiZ/cls-telegraph · akshare issue #4992 |
| 东财人气榜裸端点 | https://emappdata.eastmoney.com/stockrank/getAllCurrentList |
| 开盘啦爬虫(社区) | https://github.com/jinhao2003/kaipanla-crawler |
| pywencai | https://github.com/zsrl/pywencai |
| 北向披露规则报道 | https://www.yicai.com/news/102108074.html · https://finance.sina.com.cn/money/fund/jjzl/2024-08-23/doc-inckrpne2715147.shtml |
| 中新网财经 RSS | https://www.chinanews.com.cn/rss/finance.xml |
| 论文 | arXiv 2603.22305(CN-Buzz2Portfolio) · 2602.19919(Janus-Q) · 2406.10811(LLMFactor) · 2306.14222 · 2406.12009(FinTruthQA) · 2607.26642(AlphaSchema) · 2412.20138(TradingAgents) · 2408.06361(survey) |
| 开源项目 | https://github.com/hsliuping/TradingAgents-CN · https://github.com/24mlight/A_Share_investment_Agent · https://github.com/ZhuLinsen/daily_stock_analysis · https://github.com/AI4Finance-Foundation/FinGPT · https://github.com/charliedream1/ai_quant_trade |

---

### 附:本次「需探针」清单(K=14)
1. 深交所 annList(空响应,补头/cookie);2. 财联社 api/cache sign 直连;3. 开盘啦 App 私有接口;4. akshare `stock_sns_sseinfo` 完整跑通;5. `stock_notice_report` 全量分页耗时评估(生产在用,超时阈值);6. `stock_board_concept_name_ths`(hexin-v);7. `futures_foreign_commodity_realtime` 正确参数签名;8. 慧博研报可及性;9. tushare `stk_mins` 权限;10. tushare 指数/申万/ETF 实时接口真名(doc 403/417/454,`rt_idx_k` 名对但无权限、`sw_rt`/`rt_etf_ref` 名不存在);11. 微博热搜 cookie 方案;12. pywencai 当前可用性;13. 韭研公社登录态抓取;14. 证监会/gov.cn 页面抓取器(只有日期,需自记 first_seen)。另:两融披露精确时刻、龙虎榜落地时刻、A50 夜盘精确时段、新闻联播文字稿当晚落地时点为「待量测时点」类。
