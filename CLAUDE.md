# CLAUDE.md

## 在 session 内做交易研究（零付费 LLM API）

本项目支持用 **Claude 当引擎**在 session 内跑完整的多 agent 交易分析，**不依赖付费 LLM API**：

- **数据层**走项目自己的免费工具（yfinance / FRED，keyless + `FRED_API_KEY`）；**LLM 层由 Claude（本 session）替代**框架原本计费的多 agent 调用。
- 研究某个标的时使用 **`analyze-ticker` skill**：说"研究 NVDA" / "分析 600519.SS"即自动触发（可带同业，如 `AMD,AVGO`）。它封装了 6 步流程 + **决策主线 / 证据附录** 报告骨架（v4）。
- 工具脚本：`scripts/harvest_context.py`（取数，零 LLM）、`scripts/assemble_report.py`（组装 + 用项目 `parse_rating` 校验五档评级）。产物落 `reports/`、`context/`（均已 gitignore）。

> 注：原框架的**付费 LLM 多 agent 路径**（LangGraph 编排 `tradingagents/graph`、`tradingagents/llm_clients`、agent 定义、CLI、`scripts/run_analysis.py`）已移除——本项目现在**只**保留 Claude-as-engine 的 `analyze-ticker` 路线 + 其依赖的免费数据层（`tradingagents/dataflows`、`tradingagents/agents/utils`）。
