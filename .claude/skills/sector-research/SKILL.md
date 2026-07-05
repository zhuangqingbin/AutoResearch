---
name: sector-research
description: Use when the user wants to research a single A-share INDUSTRY/sector (申万一级) — 景气度、产业链结构、竞争格局、行业内估值/资金地形、龙头映射 — e.g. "研究一下半导体行业", "创新药板块现在怎么样", "军工景气到哪了", "光伏产业链值不值得配". Also owns the LITE tier 行业 brief that scan-market invokes at Stage 1 (hot sectors, TTL-cached, ~K≤6/day) feeding L3/L4 terrain and the L5 行业研判 section. NOT for one named ticker (use stock-research), whole-market screens (use scan-market), or cross-asset macro allocation (use macro-research). Project-local skill.
---

# sector-research — 单行业研究:full 深研 / lite 行业 brief(一个 skill,两档)

## 核心原理
中观 = 宏观与微观之间此前缺失的海拔:**macro-research 横向比较所有行业给配置倾向(beta),本 skill 纵向深挖一个行业给结构认知(链/格局/景气位置/龙头映射,alpha 语境)**。数据层零新增端点——确定性 pack 全部聚合 scan staging 既有产物(`autoresearch/sector/pack.py`);判断层 = Claude subagent,零付费 API。(design: `docs/specs/2026-07-03-research-skills-altitude-refactor-design.md` §5.3)

## 档位路由
| 情形 | 档 |
|---|---|
| 被 **scan-market Stage 1** 调用(热点行业批量 brief) | **恒 lite** |
| 用户单独触发("研究 XX 行业/板块") | **full** |
| 用户说"快速 / 一句话 / brief" | **lite** |

## lite 档(行业 brief;模板见 `sector-playbook.md`)
1. **确定性件(零 LLM)**:`uv run --no-sync python -m autoresearch.sector.reuse <date> --apply`(TTL≤5 日♻️复用:regime 同 + 行业中位 60 日动量位移 ≤3pp)→ 剩余行业 `uv run --no-sync python -m autoresearch.sector.pack <date>`(自动选:红榜 top3 ∪ L2 集中度 top3 ∪ 观察单行业,K≤6;→ `context/sector/<date>/<行业>.json`)。
2. **brief subagent(每行业一个,可并发)**:读 pack JSON(数字不可编造)+ sector_memo 行(若有),写 `context/scan/<date>/sector_briefs/<行业>.md`——**两段契约**(标题即机器接口,勿改字):`## 地形段(喂 L3/L4 · 描述性)` + `## 研判段(仅 L5)`(内含 `**行业方向**: 看多|中性|看空` keyed 行)。
3. **消费自动发生(零编排)**:L3 表 `sector_terrain=True` 前置全行业地形行;L4 简报注入该行业 brief 地形段(无 brief 回退 memo 行);L5 assemble 自动嵌 🏭 行业研判节 + 🔗 同链对比表;发布时 `sector_ledger.record_calls` 自动记方向。

## full 档(单行业深研,standalone;6 节结构见 `sector-playbook.md`)
`python -m autoresearch.sector.pack <date> --industries <行业>` 取包 → 深研(链上下游 WebSearch 产业证据标『实时网查』、格局与龙头映射、景气位置、行业内估值分布)→ 报告落 `reports/sector/<date>/<行业>.md`(两段结构同 lite,研判段更厚)→ 收尾 **`sector_memo.upsert_memo` 回写**(记忆从"卡片共性蒸馏"升级为"研究结论")+ `sector_ledger.record_calls`。

## 铁律(防锚定,违反即作废)
- **三层同律**:地形段只许数字/事实/日历(会喂 L3/L4);方向性判断(看多空/超低配语言)只在研判段 = 只进 L5/standalone 报告。**个股评级只由本股 rubric 三门决定。**
- **不设门**:行业弱 ≠ 该行业的票不研究——本 skill 产出不参与 L0–L3 筛选,只增强 L4/L5 判断(每加一条硬门 = 一块永久盲区)。
- 数字全出 pack/staging,缺字段写 —,不编;**行业嘴也被 MTM**(方向行进 `sector_ledger` 对行业已实现收益记账,已成熟 n<10 ⚠只记账)。
- 收尾写明"Claude 推理产出,仅供研究,非投资建议"。

## 常见坑
- `uv run --no-sync` + 仓库根目录;**pack 依赖当日 scan staging**(L2 后才有 L1_scored_full)——standalone 深研若当日无 scan,先跑 universe 或用最近一个 scan 日的 staging(`--scan-dir` 指过去)。
- 行业指数序列(tushare `sw_daily`)未接(权限待核,spec 开放问题 1):TTL 复用以行业中位动量位移代理;叙述别引用不存在的指数数字。
- brief 两段标题勿改字(`## 地形段` / `## 研判段` 是 `sector/brief.py` 抽取器的机器契约);`**行业方向**` 行必须是 keyed 格式否则 ledger 记不上。
