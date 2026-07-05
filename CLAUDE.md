# CLAUDE.md

## 在 session 内做交易研究（零付费 LLM API）

本项目支持用 **Claude 当引擎**在 session 内跑完整的多 agent 交易分析，**不依赖付费 LLM API**：

- **数据层**走项目自己的免费工具（yfinance / FRED / akshare / tushare，keyless + `FRED_API_KEY` / `TUSHARE_TOKEN`）；**LLM 层由 Claude（本 session）替代**框架原本计费的多 agent 调用。
- 所有取数/组装是确定性脚本（零 LLM），统一收进 `autoresearch` 包，用 `uv run --no-sync python -m autoresearch.<...>` 调用。产物落 `reports/`、`context/`（均已 gitignore）。

### 研究入口（skill 自动触发）

- **单标的**：`stock-research` skill（原 analyze-ticker + analyze-ticker-lite 合并，**full/lite 两档 prompt 路由**）—— 说"研究 NVDA" / "分析 600519.SS"即触发 full 全量报告（可带同业，如 `AMD,AVGO`；6 步流程 + **决策主线 / 证据附录** 骨架 v4）；"快速看一眼 / 出张卡"或被 scan-market L4 调用 → lite 决策卡。
  - 取数：`python -m autoresearch.analyze.harvest <ticker> [date] [stock|crypto] [PEER1,PEER2]`（`--slim` = lite 档轻量取数）。
  - 组装：`python -m autoresearch.analyze.assemble context/analyze/<TICKER>_<date>`（用项目 `parse_rating` 校验五档评级）。
- **全 A 扫描**：`scan-market` skill —— "扫描全 A 股 / 全市场选股 / 哪些板块值得买"。确定性漏斗 L0→L1→L2 + Claude 在 L3/L4/L5 做研究/辩论/整合。
  - 漏斗：`python -m autoresearch.scan run <date> [--recall-n 1000 --l2-n 200 --cap-floor 30 --source tushare --exclude-bj]`（旧 staging `context/scan/<date>/*.csv` + typed trace `reports/scan/<run_id>/`）。
  - 整合：`python -m autoresearch.scan.assemble <date>`。复盘：`python -m autoresearch.learning.retro pending`（`scan-retro` skill）。
- **宏观**：`macro-research` skill（**full/lite 两档**）—— full："研究全球宏观 / 现在该超配什么资产 / A股哪些行业值得配";lite = **市场研判**(原首席策略师,scan-market Stage 0 调用或"今天大盘怎么看",读 `python -m autoresearch.scan.frame <date> --json` 的湖派生 market_pack 写 market_view.md)。
  - 取数：`python -m autoresearch.macro.harvest [date]`；组装：`python -m autoresearch.macro.assemble context/macro/<date>`。

### 包结构（`autoresearch/`）

- `autoresearch/data`、`autoresearch/dataflows`、`autoresearch/agents/utils` —— 免费数据层（lake + DataHandler + sources；yfinance/FRED/akshare/tushare）+ `rating.py` 等工具。
- `autoresearch/common`、`autoresearch/models`、`autoresearch/trace`、`autoresearch/learning` —— 打分原语 / 模型框架（registry+Trainer+champion）/ 现场存储 / 闭环学习（feedback·retro·self_review·stage_eval）。
- `autoresearch/scan`、`autoresearch/analyze`、`autoresearch/macro` —— 三个 skill 的 stage 管道 + agents + CLI。

> 注：原框架的**付费 LLM 多 agent 路径**（LangGraph 编排、provider clients、CLI、批量 runner）已移除——本项目现在**只**保留 Claude-as-engine 的 scan / analyze / macro 路线 + 其依赖的免费数据层（`autoresearch/data`、`autoresearch/dataflows`、`autoresearch/agents/utils`）。架构详见 `docs/specs/2026-06-22-autoresearch-arch-redesign-design.md` 与 README 的 **架构** 节。
