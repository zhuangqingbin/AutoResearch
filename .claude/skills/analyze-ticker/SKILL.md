---
name: analyze-ticker
description: Use when the user wants to research or analyze a stock/crypto ticker in THIS TradingAgents project without paying for an LLM API — e.g. "研究 NVDA", "分析 600519.SS", "run the trading analysis for X", "给我一份 NVDA 的 BUY/HOLD/SELL 报告", "在 session 里跑一下这个项目". Project-local skill.
---

# analyze-ticker — 在 session 内零付费 API 跑 TradingAgents

## 核心原理
TradingAgents = `确定性数据工具(免费)` + `LLM 多 agent 推理(要钱)`。本 skill 保留前者（调项目自己的数据工具取真数据），把后者**换成你自己(Claude)**——于是零 LLM API、用真实数据 + 真实 prompt 产出完整的多 agent 交易研究报告。

## 何时用 / 不用
- ✅ 用户想对某个 ticker（美股/A股/港股/加密）出一份在 session 内、不花 API 钱的研究报告 + 五档 BUY/HOLD/SELL。
- ❌ 想要**批量回测 / 完全无人值守**：那是自动化路（`scripts/run_analysis.py` 走 DeepSeek/Ollama，需要 API key），不是本 skill。
- ❌ 想跑**真实 LangGraph 引擎**（非我推理）：同样走 `run_analysis.py` + API key。

## 前置
- 在**项目根目录**运行；`.env` 里需有 `FRED_API_KEY`（宏观数据，免费申请）。行情/技术/财报/新闻走 yfinance（免费、无 key）。
- 默认报告语言**中文**（用户要英文就改）。

## 流程（6 步）

1. **取数（零 LLM）**：
   ```bash
   uv run python scripts/harvest_context.py TICKER [YYYY-MM-DD] [stock|crypto] [PEER1,PEER2,...]
   ```
   → 真实数据落到 `context/<TICKER>_<DATE>.md`（~90KB，含 v2 期权/分析师/财报/同业 + v3 持仓做空/盈利质量 + **v4 可交易性·涨跌停 / 偿付再融资 / (A股)股东户数·解禁**）。TICKER 带交易所后缀（**A股可只传 6 位代码、自动补 .SS/.SZ/.BJ**；见 playbook）。日期默认今天。第 4 参=同业(可选,逗号分隔)，缺省用内置映射或仅基准。
2. **读 context**：分页读 `context/<TICKER>_<DATE>.md`（文件大，用 offset/limit 或 Grep 定位）。锁定：验证快照的最新收盘+指标、个股/全球新闻、8 个 FRED 宏观、4 张财报。
3. **读 playbook**：读本目录的 `engine-playbook.md`，拿到 **报告骨架（决策主线 + 证据附录）+ 各 agent** 的角色/顺序/输出格式/五档评级，**不要回去重读 60 个源文件**。v4：正文按**决策论证**重排——**决策主线**=PM仪表盘/评分卡 → 预期差 → 多空对撞 → 催化日历 → 风险·认错·监控；**证据附录**=7+1 分析师明细 + 研究验证。新增 **预期差·催化日历·持仓监控·偿付** 四维度 + (A股)股东户数。
4. **扮演各 agent**：按真实 LangGraph 顺序逐段产出。报告目录命名 **`reports/<分析日YYYYMMDD>/<TICKER>/`**（按日期分组，无此日期文件夹则新建——用 Write 写嵌套文件时会自动建；分析日 = 第 1 步用的那个日期、去连字符，如 `2026-06-19` + `NVDA` → `reports/20260619/NVDA/`），子结构见 playbook（`1_analysts/ 2_research/ 3_risk/ 4_portfolio/`，**v4 新文件**：`2_research/variant.md`·`2_research/faceoff.md`·`4_portfolio/calendar.md`·`1_analysts/solvency.md`；`decision.md` 顶部加仪表盘+评分卡、`premortem.md` 末尾加监控 KPI 表）。**写齐核心文件**（每段结尾带 `置信度:` 行；附录分析师段首行加「→ 对决策的影响」；optional lens 缺了 assemble 自动跳过，但应尽量写齐）；每段的**每个数字必须来自第 2 步的 context，禁止凭记忆/编造**。
5. **组装+校验**：
   ```bash
   uv run python scripts/assemble_report.py reports/<分析日YYYYMMDD>/<TICKER>
   ```
   → 生成 `complete_report.md`（**两层结构：目录 → 决策主线(读它就能下单) → 证据附录(按需核实)**；PM 仪表盘/评分卡/决策置顶），并用项目自己的 `parse_rating` 打印五档信号（校验你的 PM 决策能被框架原生解析）。若提示 `[MISSING]`，说明第 4 步漏写了某个必需文件（含 v4 新增 variant/faceoff/calendar），补齐再跑。
6. **汇报**：给用户最终评级 + 目标价/持有期/仓位/止损 + 诚实局限。

## 铁律（防幻觉，违反即作废重来）
- **每个价格/指标/财务数字都必须出自 context 文件**；不准凭记忆或训练知识填数。
- 以 `get_verified_market_snapshot` 为价格/指标的**唯一真值**；其他来源与之冲突时**标注冲突**，不要私自调和。
- **已知数据坑必须如实标注**（详见 playbook）：①头条净利可能含非经营投资收益→拆出营业利润；②yfinance FCF 字段常与现金流量表打架→以报表为准；③内部人卖出多为 10b5-1 预设→点明别过度解读；④Polymarket（美区/SNI 被网络层 RST 封锁）/StockTwits/Reddit 常取不到→**预测市场失败时用 WebSearch 取前瞻赔率(FedWatch/衰退)+催化、标注『实时网查』不当确定性数据**；社交缺失则降级、下调情绪置信度。
- 分析窗口**钉死在分析日**，绝不使用未来数据。
- **(v2/v3)** 全员每份报告结尾带 `置信度: 高/中/低 ｜ 最大不确定项: …`；PM 产出含**三档情景(目标+概率)+期望值+触发位+执行段**，末行 `FINAL TRANSACTION PROPOSAL: **<BUY|HOLD|SELL>**`。新增数据坑见 playbook #6–9（Reported EPS≠GAAP摊薄、期权/卖方/做空美股为主、同业选择、做空数据滞后）。
- **(v4)** PM `decision.md` 顶部先放**决策仪表盘(含 R:R)+维度评分卡**两张表；**执行段必须消化「可交易性」**——A股涨跌停为硬封板、连续跌停可能卖不出、名义止损会被跳空穿越，别承诺一个打不出的价 + 写**组合相关性**(同类暴露需降配)。新增数据坑见 playbook #14–16（涨跌停止损现实性、股东户数、偿付/质押）。
- 多空/风控辩论必须有**真实张力**（不许橡皮图章式一边倒）。
- 收尾必须写明：**这是我(Claude)的推理产出、非 LangGraph 自动运行；仅供研究，非投资建议。**

## 常见坑
- 必须 `uv run`（用项目锁定环境）且在仓库根目录，否则 .env / 依赖加载不到。
- `context/` 与 `reports/` 已在 .gitignore 之外的话注意别误提交大文件（按需）。
- 非美/A股标的：yfinance 英文新闻/社交近乎空 → **个股新闻走 akshare 东财(需 `uv add akshare`)或 WebSearch 兜底**；同业基准自动换沪深300/创业板指、另给 China backdrop(人民币+中港股指)。**A股 insider 金额是 yfinance 单位 bug、只看方向不引金额**；A股无期权/做空数据。照实降级说明。
- **(v4 A股)** **股东户数=散户数量**：户数↓=筹码集中(偏多)、↑=分散(高位放量增加=派发嫌疑)，**看趋势而非单期**；解禁日历只列**未来**供给压力(过去的已消化)；akshare 列名跨版本会变，已防御性取列，缺则 WebSearch 兜底；股权质押率走 WebSearch。
