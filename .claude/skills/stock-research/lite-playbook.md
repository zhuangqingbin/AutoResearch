# lite-playbook — stock-research lite 档(决策卡)

> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`)。数据湖 `lake/` 两引擎共享。

**卡片契约的唯一真身是 `.claude/agents/l4-card.md`**(流程 P0–P5、早停点、评分卡与五档映射、入场行、执行线、两张卡模板、机读口径、压缩纪律)。写卡时读它,不读本文;本文只记 lite 档在 **standalone**(单票直查 / 持仓复核)路径上与 scan L4 不同的几件事。数据坑、铁律、五档评级沿用同目录 `engine-playbook.md`。

## standalone 与 scan L4 的差异
- **输入**:`$CTX/<ticker>_<date>_slim.md`(`autoresearch.analyze.harvest --slim`);standalone 顶部**没有**漏斗简报,P0 改为「读 slim 快照建立假设」,「L3 论点裁决」表写「standalone:无 L3 前提」。
- **落点**:`$RPT/analyze/<YYYYMMDD>_<HHMM>/<名称|TICKER>_lite.md`(A股→中文名);scan L4 的落点由任务包指定。
- **档案节**:standalone 无「📚 覆盖档案摘要」注入时,研报体 / 微研报写一行「档案未建」。
- **入场行 lint 只在 scan 路生效**:`card_contract_lint` 的唯一调用点在 `scan/report_sections.py`;standalone 卡缺入场行不会被挡下——它是约定不是硬门,也不影响任何决策(lite 卡从不被 E6 读取)。
- **UZI 增量块(A股 slim 已含,可引用)**:`A股原生财报`(5y ROE/毛利/负债率/分红)、`融资余额趋势`、`龙虎榜席位`(presence-gated via seats.csv)、`杀猪盘/派发风险`、`量价形态/吸筹·多日资金流`。`bias=吸筹` 进多头(底部放量须基本面背书)、`bias=派发` 进风险压级;机构上榜净买默认反指、游资净买作接力信号,席位只作技术·资金维校准。更深的席位叙事 DD 与 DCF 只在 full 档(`engine-playbook.md`)。

## 与 scan-market 的衔接
scan-market L4 对每只 finalist 派 `l4-card` agent(model/effort 由 `scan_config.jsonc` 的 `agents.l4_card` 经 `user_config.resolve_agent_config` 解释,本文不写档位),产物落任务包指定的 staging 路径,由 `autoresearch.scan.assemble` 发布;≥OW 的卡在发布前由两次独立复核取中位、只向下折回。
