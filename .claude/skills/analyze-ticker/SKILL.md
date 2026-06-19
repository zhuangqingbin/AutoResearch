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
   uv run python scripts/harvest_context.py TICKER [YYYY-MM-DD] [stock|crypto]
   ```
   → 真实数据落到 `context/<TICKER>_<DATE>.md`（~80KB）。TICKER 必带交易所后缀（见 playbook）。日期默认今天。
2. **读 context**：分页读 `context/<TICKER>_<DATE>.md`（文件大，用 offset/limit 或 Grep 定位）。锁定：验证快照的最新收盘+指标、个股/全球新闻、8 个 FRED 宏观、4 张财报。
3. **读 playbook**：读本目录的 `engine-playbook.md`，拿到 12 个 agent 的角色/顺序/输出格式/五档评级，**不要回去重读 60 个源文件**。
4. **扮演 12 个 agent**：按真实 LangGraph 顺序逐段产出。报告目录命名 **`reports/<TICKER>_<分析日YYYYMMDD>/`**（分析日 = 第 1 步用的那个日期；如 `2026-06-19` → 目录 `NVDA_20260619`，去掉连字符），子结构见 playbook。**必须把 12 个文件全部写齐**；每段的**每个数字必须来自第 2 步的 context，禁止凭记忆/编造**。
5. **组装+校验**：
   ```bash
   uv run python scripts/assemble_report.py reports/<TICKER>_<分析日YYYYMMDD>
   ```
   → 生成 `complete_report.md`，并用项目自己的 `parse_rating` 打印五档信号（校验你的 PM 决策能被框架原生解析）。若提示 `[MISSING]`，说明第 4 步漏写了某个 agent 文件，补齐再跑。
6. **汇报**：给用户最终评级 + 目标价/持有期/仓位/止损 + 诚实局限。

## 铁律（防幻觉，违反即作废重来）
- **每个价格/指标/财务数字都必须出自 context 文件**；不准凭记忆或训练知识填数。
- 以 `get_verified_market_snapshot` 为价格/指标的**唯一真值**；其他来源与之冲突时**标注冲突**，不要私自调和。
- **已知数据坑必须如实标注**（详见 playbook）：①头条净利可能含非经营投资收益→拆出营业利润；②yfinance FCF 字段常与现金流量表打架→以报表为准；③内部人卖出多为 10b5-1 预设→点明别过度解读；④Polymarket/StockTwits/Reddit 常取不到→降级处理、下调情绪置信度。
- 分析窗口**钉死在分析日**，绝不使用未来数据。
- 多空/风控辩论必须有**真实张力**（不许橡皮图章式一边倒）。
- 收尾必须写明：**这是我(Claude)的推理产出、非 LangGraph 自动运行；仅供研究，非投资建议。**

## 常见坑
- 必须 `uv run`（用项目锁定环境）且在仓库根目录，否则 .env / 依赖加载不到。
- `context/` 与 `reports/` 已在 .gitignore 之外的话注意别误提交大文件（按需）。
- 非美标的：英文新闻/社交薄、宏观只有美国(FRED)，情绪/新闻段会偏薄——属数据源限制，照实说明。
