---
name: stock-research
description: Use when the user wants to research or analyze a SINGLE stock/crypto ticker in THIS TradingAgents project without paying for an LLM API — full deep-dive report by default (e.g. "研究 NVDA", "分析 600519.SS", "给我一份 BUY/HOLD/SELL 报告"), or a FAST low-token decision card (5-tier rating + 3-scenario R:R + tripwires) when speed is asked ("快速看一眼", "出张决策卡") or when invoked by scan-market L4 as its workhorse over the ~30 finalists. Merges former analyze-ticker (full) + analyze-ticker-lite (lite). NOT for whole-market scans (use scan-market) or macro (use macro-research). Project-local skill.
---

# stock-research — 单标的研究:full 全量报告 / lite 决策卡(一个 skill,两档)

## 核心原理
同一免费数据层(yfinance/FRED/akshare/tushare)+ Claude(本 session)当引擎,零 LLM API。
- **full 档** = v4 全量报告(决策主线+证据附录;`harvest` 全量 ~90KB context)。
- **lite 档** = 一张决策卡(`harvest --slim` 只取决策驱动块;渐进深度 DD + 早停;~20–30% token)。
原 analyze-ticker(full)/ analyze-ticker-lite(lite)合并于此(design: `docs/specs/2026-07-03-research-skills-altitude-refactor-design.md` §5.4)。

## 档位路由(先定档,再进对应 playbook)
| 情形 | 档 | playbook |
|---|---|---|
| **被 scan-market L4 调用**(finalists 批量出卡) | **恒 lite** | `lite-playbook.md` |
| 用户单独触发(默认) | **full** | `engine-playbook.md` |
| 用户说"快速 / 看一眼 / 出张卡 / lite / 不用全量" | **lite** | `lite-playbook.md` |
| lite 结论想下重注 | 对该票再跑 **full**(live 重取最全) | `engine-playbook.md` |

## 前置(两档同)
在**项目根目录**运行;`.env` 有 `FRED_API_KEY`;A股需 akshare/tushare(venv-only,**务必 `uv run --no-sync`**)。默认报告语言中文。TICKER 带交易所后缀(**A股可只传 6 位代码**;规则见 engine-playbook 末节)。

## full 档流程(6 步;报告骨架/各 agent 角色/数据坑全在 `engine-playbook.md`,不回读源码)
1. **取数(零 LLM)**:`uv run --no-sync python -m autoresearch.analyze.harvest TICKER [YYYY-MM-DD] [stock|crypto] [PEER1,PEER2,...]` → `context/<TICKER>_<DATE>.md`(~90KB;v4 含 可交易性·涨跌停/偿付再融资/(A股)股东户数·解禁)。日期默认今天;第 4 参=同业(可选)。
2. **读 context**:分页读(offset/limit 或 Grep 定位);锁定 验证快照/新闻/8×FRED/4 张财报。
3. **读 `engine-playbook.md`**:拿 **决策主线/证据附录** 报告骨架 + 各 agent 顺序/输出格式/五档评级。
4. **扮演各 agent**:按 LangGraph 顺序逐段产出到 `context/analyze/<TICKER>_<分析日YYYYMMDD>/`(子结构/必需文件清单见 playbook;每段结尾 `置信度:` 行)。
5. **组装+校验**:`uv run --no-sync python -m autoresearch.analyze.assemble context/analyze/<TICKER>_<分析日YYYYMMDD> [--name <A股中文简称>]` → `reports/analyze/<YYYYMMDD_HHMM>/<名称|TICKER>.md` + `parse_rating` 校验五档。**A股务必带 `--name`**;`[MISSING]` = 第 4 步漏写,补齐再跑。
6. **汇报**:评级 + 目标价/持有期/仓位/止损 + 诚实局限。

## lite 档流程(3 步;卡模板/早停规则全在 `lite-playbook.md`)
1. **slim 取数(零 LLM)**:`uv run --no-sync python -m autoresearch.analyze.harvest <ticker> <date> --slim` → `context/<ticker>_<date>_slim.md`(技术快照/指标、市场资金、可交易性、个股新闻、(A股)股东户数、估值概况、利润表、盈利质量、偿付、卖方目标、财报/解禁日历;已重排「表面块前 / 深核块后 + `<!-- P4 深核分界 -->`」;被 scan L4 调用时顶部前置漏斗简报)。
2. **渐进 DD + 早停**:P0 简报定向 → P1–P3 表面 4 维 →【主早停②:非买点 → 早停卡止】→ survivor P4 陷阱核 →【③击杀】→ P5 满卡(三档 EV/R:R + 多空自压)。**早停只向下,≥OW 必走 P4+P5**。落点:独立跑 → `reports/analyze/<YYYYMMDD>_<HHMM>/<名称|TICKER>_lite.md`;被 scan L4 调用 → staging `context/scan/<date>/details/<ticker>.md`。
3. **(可选)校验**:`autoresearch.scan.assemble` / `parse_rating` 直接读卡。

## 铁律(两档共;违反即作废重来)
- **每个价格/指标/财务数字出自本档 context**(full=全量 md / lite=slim);不凭记忆/训练知识填数。lite 不得引用 slim 没取的块(全球宏观/做空/同业全表/期权/资产负债+现金流全表)——要它们 → full。
- 以 `get_verified_market_snapshot` 为价格/指标**唯一真值**;冲突标注、不私自调和。
- 分析窗口**钉死分析日**,绝不用未来数据;已知数据坑如实标注(清单 = engine-playbook「已知数据坑」#1–16,两档通用)。
- **产出契约**:`**Rating**`(五档)+ `FINAL TRANSACTION PROPOSAL` 行必须在(`parse_rating`/assemble 依赖)。full 的 PM 含 决策仪表盘+评分卡+三档情景/EV/触发位+执行段(消化可交易性+组合相关性);lite 满卡含三档 EV/R:R+认错位,早停卡陷阱维标「未核」。
- 多空/风控辩论必须有**真实张力**;lite **不水化也不补全**(卡就是卡)。
- 收尾写明:**Claude 推理产出、非自动引擎;仅供研究,非投资建议。**

## 常见坑
- 必须 `uv run --no-sync` + 仓库根目录,否则 .env/依赖加载不到;`context/`、`reports/` 已 gitignore。
- **A股**:个股新闻走 akshare 东财/WebSearch 兜底;insider 金额是 yfinance 单位 bug 只看方向;OHLCV 价格真值走 tushare 前复权(含北交所);主力资金流要落**逐日表**读模式(拉高出货);股东户数看趋势;质押 >40% 爆雷红旗——细则全在 engine-playbook 数据坑 #10–16。
- 非美/A股标的:英文新闻/社交近乎空 → 降级照实说明;同业基准自动换沪深300/创业板指。
