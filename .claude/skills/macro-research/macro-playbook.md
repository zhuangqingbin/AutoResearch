# macro-playbook — agent 蒸馏参考 + 报告骨架(Phase 1)

> 读完这份就不用回翻代码。报告 = 决策主线 + 中观落地 + 证据附录三层。

## 输出文件映射(须与 autoresearch/macro/assemble.py 一致)
```
context/macro/<date>/        # 分节草稿(gitignored);assemble → reports/macro/<YYYYMMDD>/<HHMM>_summary.md
  1_spine/      decision.md  variant.md  crossfire.md  calendar.md  premortem.md  debate.md(opt)
  2_meso/       sector_map.md  flows.md  sentiment.md  themes.md
  3_regional/   us.md  china.md  global.md
  4_crossasset/ rates.md  fx.md  equities.md  commodities.md  crypto.md  credit.md(opt)
  5_sinous/     divergence.md  desync.md  geopolitics.md  relative.md
  6_meso_evidence/  industry_cycle.md(opt)
```
**必需**:decision variant crossfire calendar premortem · sector_map flows sentiment themes · us china global · rates fx equities commodities crypto · divergence desync geopolitics relative。
**optional**:debate credit industry_cycle。

## 两张配置表的机器可读约定(关键)
`decision.md`(跨资产)与 `sector_map.md`(A股行业)在表后,**每行补一行**:
`- <KEY>: **Rating**: <Buy|Overweight|Hold|Underweight|Sell> — <一句依据(落 context 数字)>`
- 跨资产 KEY:`OVERALL 风险档 / 美债 / 美股 / A股·港股 / USD / CNY / JPY / 黄金 / 大宗 / 加密(BTC) / 信用`。
- A股行业 KEY:申万一级行业名(与 context 中观「行业资金流」一致)。
- 5 档语义:Buy=强超配 / Overweight=超配 / Hold=中性 / Underweight=低配 / Sell=强低配。
- assemble 对每行单独跑 `parse_rating`(无"首标签胜出"碰撞,因每行只有一个标签);确保每个 KEY 各一行。

## LangGraph 风格顺序(先明细 → 综合 → 配置封装)
```
区域: 美国 → 中国 → 全球外层(欧/日/EM)
  → 跨资产: 利率&央行 → 外汇 → 权益 → 大宗&黄金 → 加密 → [信用]
  → 中美四专题: 货币分化 → 增长通胀错位 → 贸易关税地缘 → 相对资产&资本流
  → 中观: 行业配置图 → 资金&游资 → 情绪周期&涨停 → 题材&风格 → [景气桥]
  → 综合: 预期差 → 中美对撞&情景 → 催化日历 → 红队&监控 → Risk Debate
  → 配置封装(两张 5 档表: 跨资产 decision.md + A股行业 sector_map.md)
```

## 决策主线 — S1 decision.md(Phase 1 重点)
顶部两张表 + 摘要:
1. **宏观仪表盘**(一行):regime 象限(增长×通胀)/ 美政策档 / 中政策档 / 全球流动性 / 风险偏好档(risk-on·neutral·risk-off)/ 关键假设 / 置信度。
2. **跨资产配置表**(每行:5 档倾向 + 关键驱动 + 主要表达 + 触发/失效位)+ 表后的 keyed `**Rating**` 行(含 `OVERALL 风险档`)。
3. **执行摘要** 2–4 句。

## 中观落地 — M1 sector_map.md(Phase 1 重点)
申万一级行业排名表:相对强度(1/5/20日)+ 主力净流入(context「行业资金净流入」tushare 逐行,**亿**)+ 北向(tushare 官方汇总,日频可靠)+ 估值方向(context「指数估值」沪深300/创业板 **PE 近1年分位**)→ 每行业 5 档倾向 + 表后 keyed `**Rating**` 行。
> M2 flows / M3 sentiment / M4 themes:据 context 中观块写(资金&游资逐条读「拉高出货 vs 吸筹」、涨停情绪档位[tushare 涨停家数/连板/最热行业]、题材/风格一句)。**两融余额趋势(tushare,融资余额↑=加杠杆=risk-on)+ 行业/指数 PE 分位现已在 context**——直接读进 M2/风险偏好与估值锚;ETF/概念可后续补。

## 区域读数 — A 区域宏观(Phase 1 重点)
- `us.md`:增长/通胀/就业/金融条件/政策路径 — 数字出 context 的 US FRED 段;Fed 反应函数判断标『判断』。
- `china.md`:增长/通胀/信用/政策/地产 — 出 context China 段;akshare 缺失项走 WebSearch 标『实时网查』。
- `global.md`:欧/日/EM;**日本重点**(BOJ/JPY/套息)。BOJ/ECB 前瞻走 WebSearch。

## 综合段(Phase 1 可精简,Phase 2 展开)
- `variant.md`(S2):市场 price-in 什么(FedWatch 隐含降息次数/曲线/CNY forward)vs 我们哪里不同 vs 何时收敛。**无差异化 = 跟随 beta,如实说。**
- `crossfire.md`(S3):中美对撞表(货币分化/增长错位/贸易地缘/相对资产 四行,各列 美向·中向·净含义)+ 增长×通胀四象限情景(base/再通胀/滞胀/硬着陆 + 概率,和≈100%)+ 可叠 1–2 自定义中美情景(中国强刺激 vs 失速)。
- `calendar.md`(S4):FOMC/CPI/PCE/NFP · 中国 PMI/社融/LPR/NPC/政治局 · BOJ/ECB · 关税地缘,每条标方向 + 是否=调仓触发。
- `premortem.md`(S5):红队 3–4 死因(政策误判/通胀再加速/地缘黑天鹅/流动性事件/中国超预期或失速)+ 每个早期预警位 + **配置监控 KPI 表**。

## 证据附录 B/C(Phase 1 可精简)
- B 跨资产:`rates/fx/equities/commodities/crypto` 各一段,数字出 context 跨资产 basket;`credit` 可选。
- C 中美四专题:`divergence/desync/geopolitics/relative`,对应 S3 四行的详证。
- D `industry_cycle`(可选):macro regime → 受益/受损产业链(半导体/地产链/出口链/猪周期),部分 WebSearch。

## 全员通用标准
- 每段结尾一行:`置信度: 高/中/低 ｜ 最大不确定项: …`。
- 每个数字出 context;判断/网查显式标注。
- as-of 分析日,无未来数据。
- 中美对撞 / Risk Debate 有真实张力。

## 已知数据坑
1. FRED 国际 series 若 `MACRO_DATA_UNAVAILABLE` → WebSearch,标『实时网查』。
2. akshare 中观端点版本漂/限流 → context 已留 WebSearch 指令,推理阶段补回逐日/逐行颗粒度,别静默跳过。
3. 北向个股实时披露 2024-08 已停 → 用**汇总口径**;现 tushare `moneyflow_hsgt`(north_money)提供**日频官方汇总**(可靠、非 push2),不必再标 staleness——但仍是汇总而非个股口径。
4. 跨资产相关性随 regime 漂移(通胀期股债翻正)→ 配置表声明当前相关性假设。
5. 期货(GC=F/CL=F)盘后可能 n/a → 用现货 ETF 或标注时点。
6. 行业资金流 + 两融 + 涨停 + 北向 + 指数估值:context **tushare 优先**(`tushare_macro`,非 push2 更稳),akshare(Eastmoney→THS)补龙虎榜游资;都失败才 WebSearch『行业净流入排名 / 两融余额 / 涨停家数 / 北向净流入』。
