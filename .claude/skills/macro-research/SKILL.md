---
name: macro-research
description: "Top-down GLOBAL + 中美 macro → cross-asset tilts AND A股行业配置 read (「研究全球宏观」「现在该超配什么资产」). Also owns the LITE 市场研判 daily brief: invoked by scan-market Stage 0 or 「今天大盘怎么看」, writes market_view.md from the deterministic market_pack. NOT for one ticker (→ stock-research), a full A-share screen (→ scan-market), or single-industry depth (→ sector-research). Project-local."
---

> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`;shell 里 `CTX=context_${AUTORESEARCH_ENGINE:-claude}`,`RPT=reports_${AUTORESEARCH_ENGINE:-claude}`)。数据湖 `lake/` 两引擎共享。Read/Write 工具调用时把 `$CTX`/`$RPT` 代入具体目录名。

# macro-research — 在 session 内零付费 API 跑全球+中美宏观 + A股中观 → 配置

## session_v1 编排入口

开发/验收期显式选择新编排时使用
`python -m autoresearch.session_agent begin --orchestration session_v1 --request-file <request.json>`，执行
`begin → next → claim → execute/宿主研究 → submit → finish`。FULL/LITE、macro_state 与市场研判
仍走原领域契约。宿主能力不足会在创建 run 前返回 `HOST_CAPABILITY_REQUIRED`。
`session_agent --orchestration legacy` 只返回 `LEGACY_ENTRYPOINT_REQUIRED`，绝不代跑旧流程；
回退必须显式进入标为 `LEGACY_ORCHESTRATION_FALLBACK` 的旧入口并单独记录原因。
当前双宿主真实验收为 `INCOMPLETE`，新入口仅作显式 PILOT，默认仍保留 legacy fallback。
`finish` 后必须对机器返回的 canonical 报告路径运行
`uv run --no-sync python -m autoresearch.session_agent verify-report --report-path <PATH> --expected-run-id <RUN_ID> --level full`；
最终答复按 `VerificationResult` 分开报告编排、发布、完整性与重放状态，不能把合成 PASS 或
`MANIFEST` 通过写成“完全可复现”。

## 核心原理
宏观研究 = `确定性数据(免费)` + `多 agent 推理(本来要钱)`。本 skill 调项目数据工具取真宏观/中观数据(FRED/akshare/yfinance),把推理换成你(Claude,本 session)——零 LLM API,产出 regime 判断 + 跨资产配置表 + A股行业配置表。

## 档位路由(一个 skill 两档)
- **full(默认,用户触发)**:全球宏观 6 步流程(下节)→ 两张配置表报告。
- **lite = 市场研判(首席策略师)**:被 **scan-market 调用**(Stage 0,与 universe 并行跑)或用户要日频大盘 brief。输入 = 确定性 `market_pack`(盘前帧入口 `uv run --no-sync python -m autoresearch.scan.frame <date> --json`;或 L2 后 `autoresearch.scan.market.market_pack(scan_dir)`,两口径同字段)+ `macro_state.json`(full 档机读产物,presence-gated:缺/过期只用 pack);产出 `$CTX/scan/<date>/market_view.md`。**prompt 模板与防锚定铁律见 `macro-playbook.md` 末节「lite 档:市场研判」**(自 scan-market 迁入,2026-07-03 海拔重构:市场层 = 宏观能力的 lite 档)。

## 何时用 / 不用
- ✅ 自上而下的宏观/中美/中观研究,收在跨资产 + A股行业的超-中-低配(full)。
- ✅ scan-market Stage 0 的市场研判 / "今天大盘怎么看"(lite)。
- ❌ 单只票 → stock-research;❌ 全 A股选股 → scan-market。

## 前置
- 仓库根目录运行;`.env` 需 `FRED_API_KEY`(免费)。A股中观需 `uv add akshare`。报告默认中文。

## 流程(6 步)
1. **取数(零 LLM)**:`uv run python -m autoresearch.macro.harvest [YYYY-MM-DD]` → `$CTX/macro/<date>/data.md`(区域宏观 US/China/Global + 跨资产 basket + A股中观骨架)。日期默认今天。
2. **读 context**:分页读 `$CTX/macro/<date>/data.md`(文件较大,用 offset/limit 或 grep 定位),锁定 US/China/Global 宏观、跨资产价(含 USD/CNY/JPY/黄金/大宗/BTC)、A股中观(tushare 优先:北向官方汇总/**两融余额**/行业资金净流入(亿)/涨停情绪/**指数估值分位** + akshare 补游资龙虎榜)。
3. **读 playbook**:读本目录 `macro-playbook.md` 拿报告骨架 + 各 agent 角色/输出格式 + 两张配置表的机器可读约定 + 数据坑,**不要回翻代码**。
4. **扮演各 agent**:按 playbook 顺序逐段产出到 `$CTX/macro/<date>/`(分节草稿,gitignored;目录结构见 playbook)。**每个数字必出 context;判断性内容(情景概率/政策路径/央行反应函数)显式标『判断』或『实时网查』。** 两张配置表(跨资产 `decision.md`、A股行业 `sector_map.md`)每行带 keyed `**Rating**` 行。
5. **组装+校验**:`uv run python -m autoresearch.macro.assemble $CTX/macro/<date>` → `$RPT/macro/<YYYYMMDD>/<HHMM>_summary.md`,并对跨资产表 + A股行业表逐行打印 `parse_rating` 信号(校验你的配置能被框架原生解析)。若 `[MISSING]`,补齐缺的必需分段再跑。
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
