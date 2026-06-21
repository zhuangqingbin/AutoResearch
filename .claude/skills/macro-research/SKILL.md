---
name: macro-research
description: Use when the user wants top-down GLOBAL + 中美 macro research that ends in cross-asset allocation tilts AND A股 sector/中观 read — e.g. "研究全球宏观", "中美宏观现在怎么看", "现在该超配什么资产", "A股哪些行业值得配", "give me a macro regime + asset allocation view". NOT for one named ticker (use analyze-ticker) or a full A-share stock screen (use scan-market). Project-local skill.
---

# macro-research — 在 session 内零付费 API 跑全球+中美宏观 + A股中观 → 配置

## 核心原理
宏观研究 = `确定性数据(免费)` + `多 agent 推理(本来要钱)`。本 skill 调项目数据工具取真宏观/中观数据(FRED/akshare/yfinance),把推理换成你(Claude,本 session)——零 LLM API,产出 regime 判断 + 跨资产配置表 + A股行业配置表。

## 何时用 / 不用
- ✅ 自上而下的宏观/中美/中观研究,收在跨资产 + A股行业的超-中-低配。
- ❌ 单只票 → analyze-ticker;❌ 全 A股选股 → scan-market。

## 前置
- 仓库根目录运行;`.env` 需 `FRED_API_KEY`(免费)。A股中观需 `uv add akshare`。报告默认中文。

## 流程(6 步)
1. **取数(零 LLM)**:`uv run python scripts/harvest_macro.py [YYYY-MM-DD]` → `context/macro/<date>/data.md`(区域宏观 US/China/Global + 跨资产 basket + A股中观骨架)。日期默认今天。
2. **读 context**:分页读 `context/macro/<date>/data.md`(文件较大,用 offset/limit 或 grep 定位),锁定 US/China/Global 宏观、跨资产价(含 USD/CNY/JPY/黄金/大宗/BTC)、A股中观(tushare 优先:北向官方汇总/**两融余额**/行业资金净流入(亿)/涨停情绪/**指数估值分位** + akshare 补游资龙虎榜)。
3. **读 playbook**:读本目录 `macro-playbook.md` 拿报告骨架 + 各 agent 角色/输出格式 + 两张配置表的机器可读约定 + 数据坑,**不要回翻代码**。
4. **扮演各 agent**:按 playbook 顺序逐段产出到 `context/macro/<date>/`(分节草稿,gitignored;目录结构见 playbook)。**每个数字必出 context;判断性内容(情景概率/政策路径/央行反应函数)显式标『判断』或『实时网查』。** 两张配置表(跨资产 `decision.md`、A股行业 `sector_map.md`)每行带 keyed `**Rating**` 行。
5. **组装+校验**:`uv run python scripts/assemble_macro.py context/macro/<date>` → `reports/macro/<YYYYMMDD>/<HHMM>_summary.md`,并对跨资产表 + A股行业表逐行打印 `parse_rating` 信号(校验你的配置能被框架原生解析)。若 `[MISSING]`,补齐缺的必需分段再跑。
6. **汇报**:regime 判断 + 两张配置表(关键超/低配 + 表达 + 触发位)+ 诚实局限。

## 铁律(防幻觉,违反即作废重来)
- 每个价格/宏观/中观数字都出自 context;实时网查数标来源/日期。
- 宏观判断性内容(情景概率、政策路径、央行反应函数)显式标注,不冒充确定性数据。
- 分析窗口钉死分析日,绝不用未来数据。
- 中美对撞 / Risk Debate 必须有真实张力(不许橡皮图章一边倒)。
- 北向个股实时披露 2024-08 已停 → 中观北向用 tushare `moneyflow_hsgt` 官方**日频汇总**(可靠);仍是汇总非个股口径。
- 跨资产相关性随 regime 漂移(通胀期股债翻正)→ 配置表声明当前相关性假设。
- 收尾写明:这是 Claude 的推理产出、非自动引擎;仅供研究,非投资建议。

## 常见坑
- 必须 `uv run` + 仓库根目录,否则 `.env`/依赖加载不到。
- akshare 版本漂/限流 → harvester 已防御降级 + WebSearch 兜底;context 出现『取数失败 → WebSearch』时,推理阶段务必网查补回逐日/逐行颗粒度,**别静默跳过或塌缩成一个累计数**。
- FRED 国际 series 若 `MACRO_DATA_UNAVAILABLE` → 该指标走 WebSearch,标『实时网查』。
- A股中观 **tushare 优先**(`tushare_macro`:北向/两融/行业资金/涨停/指数估值,非 push2 更稳),akshare(Eastmoney→THS)补龙虎榜游资;都失败才 WebSearch。
