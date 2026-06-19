# lite-playbook — 决策卡模板(analyze-ticker-lite)

> 沿用 `analyze-ticker/engine-playbook.md` 的**数据坑 + 铁律 + 五档评级**;本文只定义**压缩后的单张卡**。卡 ≈ 1 屏,目标 ~3–4k token 输出。

## 输入
`context/<ticker>_<date>_slim.md`(`harvest_context.py --slim` 产出)。块清单见 SKILL.md。
**slim 没取的块(宏观/做空/同业全表/期权/资产负债+现金流全表)不得在卡里引用数字**——没取就是没取,不编、不靠记忆补。

## 输出:单文件 `reports/<date>/<ticker>/complete_report.md`

写成下面这一张卡(`assemble_scan.py` + `parse_rating` 直接读它):

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
| 技术·资金 | 强/中/弱 | snapshot + 主力净流入 |
| 盈利质量 | 强/中/弱 | CFO/NI、FCF |
| 偿付(爆雷) | 强/中/弱 | 净债务/利息覆盖/商誉(+A股质押) |
| 催化 | 强/中/弱 | 下一闸门 |

**Rating**: <Buy|Overweight|Hold|Underweight|Sell>

**三档情景**: Bull <价>(P%) / Base <价>(P%) / Bear <价>(P%) → **EV <价>**(对现价 <±%>);**R:R <比>**(至 base 也算一遍)

**预期差(2–3 行)**: 市场 price-in 什么 / 我哪里不同 / 何时收敛。无差异化观点就直说"跟随共识,无 alpha,仅 beta/趋势"。

**多空对撞(各 2–3 bullet,不写散文)**:
- 多:… ;… ;…
- 空:… ;… ;…

**催化 & 认错位**: <1–2 个关键日期> ｜ 失效:<价/指标/事件 → 减仓>

**(A股)一行**: 股东户数 <↑↓ 含义> ｜ 主力 <净流入/出> ｜ 涨跌停可交易性 <提示> ｜ 偿付红旗 <有则标,无则略>

FINAL TRANSACTION PROPOSAL: **<BUY|HOLD|SELL>**

置信度: <高/中/低> ｜ 最大不确定项: …
_Claude 推理产出,非全量报告;仅供研究,非投资建议。要完整证据附录请对本票跑 analyze-ticker 全量。_
```

## 压缩纪律(命中即省 token)
- **只写这张卡**:不写 8 段分析师附录、不写多空散文、不写 reality-check 全表 / 红队 / risk-debate——那些是全量 analyze-ticker 的活。
- **三档情景 / R:R / 预期差是卡的核心**,务必落实数字(出自 slim context 的估值/财务)。
- **偿付红旗、股东户数、可交易性保留压缩版一行**(用户看重的爆雷/筹码/执行,不能丢,但只一行)。
- 卡内每个数字可回溯到 slim context;slim 没有的(宏观/同业/做空)不引、不编。
- 评级用项目五档(Buy/Overweight/Hold/Underweight/Sell),`**Rating**` 行 + `FINAL TRANSACTION PROPOSAL` 行必须在,否则 `parse_rating`/`assemble_scan` 读不到。

## 与 scan-market 的衔接
scan-market L3b 对 `finalists.csv` 每只调本 skill;产物 `reports/<date>/<ticker>/complete_report.md` 直接喂 `assemble_scan.py` 汇成 buy-list。建议在 subagent 里逐只跑(每只独立 context,只回传评级/目标/R:R),避免主线上下文堆叠。
