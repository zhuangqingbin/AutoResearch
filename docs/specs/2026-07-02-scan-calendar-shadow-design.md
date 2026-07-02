# scan 日历(解禁/预约披露)+ 影子漏斗(确定性 A/B)+ 因子待验清单 设计

日期:2026-07-02 ｜ 分支:feat/scan-calendar-shadow ｜ 状态:实施中

## 1. 解禁 + 预约披露日历(`autoresearch/scan/calendar.py`,新)

**端点已冒烟**(2026-07-02):`share_float(start_date,end_date)`(按 float_date 窗;单查 6000 行
分页上限 → **≤14 天分块拉**)与 `disclosure_date(end_date=上一日历季末)`(全市场预约披露
`pre_date`——观察单"8 月中报"类触发从此有**确切日期锚**)。

- `harvest_calendar(date, codes, horizon_days=35)`(网络,CLI):解禁窗 [date, date+horizon]
  分块 bulk → 按 (code, float_date) 聚合 ratio;披露 = 上一日历季末期次的 pre_date(取未来的);
  过滤到 codes(L2∪finalists)→ `<scan_dir>/calendar.csv`(code,kind,event_date,detail,ratio)。
- `calendar_flags(scan_dir, code, within_days=30, min_ratio=2.0)`:该票风险/催化行 →
  **L4 简报注入**(compose_funnel_brief,⚠️ 解禁在 within 天内且占比≥min_ratio / 📅 预约披露)。
- `calendar_section(scan_dir, horizon_days=14)`:未来两周日历块(finalists 披露日 + 全 L2 大解禁
  ratio≥5%)→ assemble 嵌 summary(presence-gated)。
- `_last_quarter_end(date)`:上一**日历**季末(≠ latest_reported_quarter 的"已披露期"语义)。
- 铁律:日历是**事实日期**,不是方向;解禁旗提示 L4 深核 P4 必核,不自动降级。

## 2. 影子漏斗(universe.py `--no-shadow` 关;默认开,纯增量)

主漏斗不动;同一份 recall 帧上再产 2 套变体 L2(**零额外取数、零 LLM**):
- `nostrat`:纯 composite top-N(不分层不 cap)——"分层到底救了还是害了"的对照;
- `nocap`:分层但 sector cap 关(sector_cap_frac=1.0)——"行业 cap 挡了多少赢家"。
→ `<scan_dir>/shadow/L2_<variant>.csv`,**只落 staging 不喂 L3**。整块 try 包裹(影子失败不阻主漏斗)。
retro:`shadow_compare(attr, scan_dir)` 逐变体算 T+1/T+5 赢家捕获数 vs 主 L2 → retro_input
"影子漏斗对照"节。**把"召回线/采样器错配"从事后验尸变成常态化前向实验,而且免费。**
样本纪律:单日读数不下结论,≥10 日累计才提 proposal。

## 3. 因子待验清单(docs/research/factor-backlog.md,新)

制度化"新因子先过 IC 门再上线":候选(两融变化/股东户数/52周高距离/开盘gap侵蚀/一致预期修正〔已排队〕)
+ 统一验收标准(≥60 成型日、|IC|≥0.02 且稳定、regime 分桶复核、事件类先跑事件研究)+ 负结果归档义务。

## 测试
test_calendar.py(quarter 端点/flags/section/缺文件降级/brief 注入)、test_shadow.py
(变体写盘/nostrat 排序/nocap 无上限/shadow_compare 捕获数)、assemble 嵌入。合成,无网络。
