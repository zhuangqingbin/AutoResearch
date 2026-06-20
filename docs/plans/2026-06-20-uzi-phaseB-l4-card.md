# Phase B — UZI 透镜 → 增强 L4 决策卡

**Goal:** 给 analyze-ticker(-lite) 卡片加 UZI 的单票定性透镜,A股优先 tushare、爬虫降级。
**Spec:** `docs/specs/2026-06-20-uzi-integration-design.md` §4。
**前置:** Phase A 无强依赖(并行可)。

## 文件
- 改 `scripts/harvest_context.py`(新 harvest 块)
- 新 `scripts/uzi_lenses.py`(席位识别 seat_db + A股财报 + 融资 + trap;纯函数 + 自测)
- 改 analyze-ticker / lite playbook(卡片新增小节 + 引用规则)

## Task 1 — A股原生财报块(补 yfinance 稀疏)
`uzi_lenses.ashare_fundamentals_ts(code)`:`fina_indicator`(5y ROE/毛利率/负债率/ROIC/EPS)+ `dividend`(近年分红/股息率)。harvest 在 A股分支调用,slim 也保留(决策驱动)。

## Task 2 — 游资席位识别(seat_db)
`uzi_lenses.lhb_seats(code, date)`:`top_inst`+`top_list` → 机构专用净买 vs 非机构(游资)净买;`context/knowledge/seat_db.jsonl` 累积知名游资席位名(冷启动用"机构专用"二分,逐步沉淀)。卡片"谁在买/卖"。

## Task 3 — 融资融券块 + 杀猪盘 trap
- `uzi_lenses.margin_trend_ts(code)`:近 20 日融资余额趋势(杠杆资金进出)。
- `uzi_lenses.trap_signals(l1_row, evidence)`:UZI 8 信号轻量版(放量滞涨 / 股东户数激增 / 高质押 / 龙虎榜游资对倒 / winner_rate 满)→ 风险标。复用 L1 因子行 + L3 证据,**不新增取数**。

## Task 4 — DCF·comps(仅 analyze-ticker 全量,不进 lite)
`uzi_lenses.simple_dcf(fcf, growth, wacc)` + 5×4 敏感性矩阵;行业 PE/PB 分位(tushare daily_basic 行业聚合)。给重注票内在价值参照。

## Task 5 — 舆情热度(弱信号,降级)
akshare 雪球热度 `stock_hot_rank_detail_em`,**标注[弱信号/爬虫],不进硬评级**。

## Task 6 — 接线 + 测试
- harvest_context A股分支按 slim/full 调用上述块;卡片 playbook 加小节 + 数据带 source/降级标注。
- `uzi_lenses.py --selftest`(DCF 数学 / seat 二分 / trap 信号 纯函数);ruff 绿。
- 不碰排除文件;不 commit。

## 验收
A股决策卡含:原生财报 + 席位识别 + 融资趋势 + trap 风险标;全量卡另含 DCF·comps;数据均带来源/降级标注。
