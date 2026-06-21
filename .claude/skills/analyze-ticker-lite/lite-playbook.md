# lite-playbook — 决策卡模板(analyze-ticker-lite)

> 沿用 `analyze-ticker/engine-playbook.md` 的**数据坑 + 铁律 + 五档评级**;本文只定义**压缩后的单张卡**。卡 ≈ 1 屏,目标 ~3–4k token 输出。

## 输入
`context/<ticker>_<date>_slim.md`(`harvest_context.py --slim` 产出)。块清单见 SKILL.md。
**slim 没取的块(宏观/做空/同业全表/期权/资产负债+现金流全表)不得在卡里引用数字**——没取就是没取,不编、不靠记忆补。
**UZI 增量块(A股 slim 已含,可引用)**:`A股原生财报(UZI·tushare)`(5y ROE/毛利/负债率/分红)、`融资余额趋势`、`杀猪盘/派发风险(复用L1)`、**`量价形态/吸筹·多日资金流(复用L1)`**——后两块 slim context 已**直接渲染**(被 scan L4 调用时复用 L1 因子行,零取数)。卡片**风险段优先看 trap 信号**(获利盘满/放量滞涨/过热/浮盈了结)命中即压评级;**量价形态块**:`bias=吸筹`(底部放量/地量企稳/缩量回调/量增价涨)进多头论点(**底部放量须基本面背书,>70% 无支撑会败**)、`bias=派发`(高位放量净出)进风险段压级;**多日 `cmf_20`/`obv_mom_20`**(>0=资金净进侧,IC 实证强于单日量比)与快照位置共振更可信。席位识别/DCF 只在**全量 analyze-ticker**。

## 输出:单张决策卡(两种落点)
独立跑 → `reports/analyze/<YYYYMMDD>_<HHMM>/<名称|TICKER>_lite.md`(目录名=运行时刻;**A股→中文名、其他市场→TICKER**,与 analyze-ticker 落点一致);被 scan L4 研究阶段 调用 → staging `context/scan/<date>/details/<ticker>.md`(`autoresearch.scan.assemble` 发布到 `reports/scan/<YYYYMMDD>_<HHMM>/details/`)。

写成下面这一张卡(`autoresearch.scan.assemble` + `parse_rating` 直接读它):

```
# 决策卡 — <代码> <名称> @ <date>

## 决策仪表盘
| 评级 | 现价 | EV目标(+%) | 上行/下行 | R:R | 时间框架 | 建议仓位 | 触发位 | 置信度 |
|---|---|---|---|---|---|---|---|---|
| **<五档>** | <价> | <EV>(<±%>) | +x% / −y% | <r:r> | <月> | <仓> | <减/清条件> | <高/中/低> |

## 维度评分卡
| 维度 | 评分 | 一句话依据(context 数字) |
|---|---|---|
| 基本面 | 强/中/弱 | 营收/净利 YoY、ROE |
| 估值 | 强/中/弱 | fwd PE / 三档情景 |
| 技术·资金 | 强/中/弱 | snapshot + 主力净流入 + 量价形态(吸筹/派发) |
| 盈利质量 | 强/中/弱 | CFO/NI、FCF |
| 偿付(爆雷) | 强/中/弱 | 净债务/利息覆盖/商誉(+A股质押) |
| 催化 | 强/中/弱 | 下一闸门 |

**Rubric建议**(评分卡派生,防拍脑袋): 维度净分 <±n>/6(强+1·中0·弱−1) ｜ OW三门 <主力真在 ✓/✗·业绩真兑现 ✓/✗·估值不透支 ✓/✗> → **建议 <Rating>**(净分 ≥+4 Buy／≥+2 OW／−1~+1 Hold／≤−2 UW／≤−4 Sell;**任一 OW 门未过 → ≥OW 一律压 Hold**)

**Rating**: <Buy|Overweight|Hold|Underweight|Sell> ← **必须 = Rubric建议**;不同则下一行必写 `**偏离**:<≤20字硬理由(如 解禁已落地/在手订单锁定全年)>`(发布层 `self_review` 会抓『评级超 rubric』)

**三档情景**: Bull <价>(P%) / Base <价>(P%) / Bear <价>(P%) → **EV <价>**(对现价 <±%>);**R:R <比>**(至 base 也算一遍)

**预期差(2–3 行)**: 市场 price-in 什么 / 我哪里不同 / 何时收敛。无差异化观点就直说"跟随共识,无 alpha,仅 beta/趋势"。

**多空对撞(各 2–3 bullet,不写散文)**:
- 多:… ;… ;…
- 空:… ;… ;…

**催化 & 认错位(先扫 slim 近 14 天个股新闻,落到具体事件+日期,不靠记忆/不编)**: 催化 <事件→时点,如「中报预约 8/28」「中标公告 7/x」「定增过会」> ｜ 风险事件 <诉讼/减持/问询/解禁/商誉,带时点> ｜ 失效:<价/指标/事件 → 减仓>;近 14 天无重大事件就写「近 14 天无重大事件」

**(A股,tushare 富化)**: 主力净流入(10日)<净流入/出;1–2周 swing 信号非次日> ｜ 筹码获利比例 <高=惜售/低=套牢压反弹;**非超跌买点**> ｜ 多头排列·RSI·MACD <真技术位置> ｜ 北向持股占比 <聪明钱,看趋势> ｜ 股东户数 <↑↓ 集中度> ｜ 业绩预告/快报 <预增减,前瞻催化> ｜ 质押红旗 <>40%标爆雷> ｜ 涨跌停可交易性 <提示>

FINAL TRANSACTION PROPOSAL: **<BUY|HOLD|SELL>**

置信度: <高/中/低> ｜ 最大不确定项: …
_Claude 推理产出,非全量报告;仅供研究,非投资建议。要完整证据附录请对本票跑 analyze-ticker 全量。_
```

## 压缩纪律(命中即省 token)
- **只写这张卡**:不写 8 段分析师附录、不写多空散文、不写 reality-check 全表 / 红队 / risk-debate——那些是全量 analyze-ticker 的活。
- **三档情景 / R:R / 预期差是卡的核心**,务必落实数字(出自 slim context 的估值/财务)。
- **偿付红旗、股东户数、可交易性保留压缩版一行**(用户看重的爆雷/筹码/执行,不能丢,但只一行)。
- 卡内每个数字可回溯到 slim context;slim 没有的(宏观/同业/做空)不引、不编。
- 评级用项目五档(Buy/Overweight/Hold/Underweight/Sell),`**Rating**` 行 + `FINAL TRANSACTION PROPOSAL` 行必须在,否则 `parse_rating`/`autoresearch.scan.assemble` 读不到。
- **评级由评分卡派生,非 gestalt**:先填 6 维评分卡算净分 + 过 3 道 OW 门 → `**Rubric建议**` 行,`**Rating**` 必须等于它(否则写 `**偏离**`)。这条直接压制『拍脑袋给 OW』(实测 Sonnet 比 Opus 多报 3 倍 OW)——别让乐观情绪绕过评分卡。
- **新闻必扫(slim 已取近 14 天个股新闻,免费)**:写催化前**先读一遍 slim 的新闻块**,把具体催化/风险事件落到**日期**(预约披露/中标/定增/诉讼/减持/解禁),不靠记忆、不编;事件严格 ≤ 分析日(无未来泄漏);无事件就明说。新闻是 slim 里**唯一的前瞻事件源**,别浪费。

## 与 scan-market 的衔接
scan-market L4 研究阶段 对 `finalists.csv` 每只调本 skill;产物写到 staging `context/scan/<date>/details/<ticker>.md`,由 `autoresearch.scan.assemble` 发布到 `reports/scan/<YYYYMMDD_HHMM>/details/〈名称〉.md`(发布层按**股票名称**命名)并汇成 buy-list。建议在 subagent 里逐只跑(每只独立 context,只回传评级/目标/R:R),避免主线上下文堆叠。
