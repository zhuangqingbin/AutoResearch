---
name: sector-research
description: "Single A-share INDUSTRY (申万一级) research — 景气度/产业链/竞争格局/资金地形/龙头映射 (「研究半导体行业」「创新药板块怎么样」). Also owns the LITE sector brief scan-market invokes at Stage 1: a one-段 machine contract (`## 地形段`, terrain-only, feeds L3/L4). NOT for one ticker (→ stock-research), whole-market (→ scan-market), cross-asset (→ macro-research). Project-local."
---

> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`;shell 里 `CTX=context_${AUTORESEARCH_ENGINE:-claude}`,`RPT=reports_${AUTORESEARCH_ENGINE:-claude}`)。数据湖 `lake/` 两引擎共享。Read/Write 工具调用时把 `$CTX`/`$RPT` 代入具体目录名。

# sector-research — 单行业研究:full 深研 / lite 行业 brief(一个 skill,两档)

> session_v1 编排入口(PILOT,默认仍 legacy):见 `docs/session-agent/README.md`;`finish` 后用 `session_agent verify-report --level full` 的机器结果交付。
## 核心原理
中观 = 宏观与微观之间此前缺失的海拔:**macro-research 横向比较所有行业给配置倾向(beta),本 skill 纵向深挖一个行业给结构认知(链/格局/景气位置/龙头映射,alpha 语境)**。数据层零新增端点——确定性 pack 全部聚合 scan staging 既有产物(`autoresearch/sector/pack.py`);判断层 = Claude subagent,零付费 API。(design: `docs/specs/2026-07-03-research-skills-altitude-refactor-design.md` §5.3)

## 档位路由
| 情形 | 档 |
|---|---|
| 被 **scan-market Stage 1** 调用(热点行业批量 brief) | **恒 lite** |
| 用户单独触发("研究 XX 行业/板块") | **full** |
| 用户说"快速 / 一句话 / brief" | **lite** |

## lite 档(行业 brief;模板见 `sector-playbook.md`)
1. **确定性件(零 LLM)**:`uv run --no-sync python -m autoresearch.sector.reuse <date> --apply`(TTL≤5 日♻️复用:regime 同 + 行业中位 60 日动量位移 ≤3pp)→ 剩余行业 `uv run --no-sync python -m autoresearch.sector.pack <date>`(自动选:红榜 top3 ∪ L2 集中度 top3 ∪ 观察单行业,K≤6;→ `$CTX/sector/<date>/<行业>.json`)。
2. **brief subagent(每行业一个,可并发)**:读 pack JSON(数字不可编造),写 `$CTX/scan/<date>/sector_briefs/<行业>.md`——**单段契约**(标题即机器接口,勿改字):`## 地形段(喂 L3/L4 · 描述性)`,**只此一段**。(2026-08-19 D6 用户裁定:`## 研判段` 与其中的 `**行业方向**` keyed 行**整段砍除** —— 分析最重的半段最终只在 summary 值一行字;`sector/brief.py` 现在只认 `TERRAIN_HDR = "## 地形段"`,写了研判段也没有任何读者。行业方向叙事改由确定性 top3 (`scan/market.py` 的 `sector_healthy_top3`/`render_sector_top3`)独扛。)
3. **消费自动发生(零编排)**:L3 表 `sector_terrain=True` 前置全行业地形行;L4 简报注入该行业 brief 的地形段(`sector/brief.render_terrain_block` → `### 🏭 行业地形 — <行业>` 块,唯一调用点 `scan/l4/context.py:269`;无 brief 则整段省略)。**L5 不嵌任何行业研判节** —— assemble 只把 `sector_briefs/` 整目录拷进报告(`publisher.py:265`)并在耗时/字节表里数它的文件数与字节数(`report_sections.py:226,277`);L5 的 `🔗 同链对比` 表是拿 **finalists** 现算的(`report_sections.py:651`),与 brief 无关。

## full 档(单行业深研,standalone;6 节结构见 `sector-playbook.md`)
`python -m autoresearch.sector.pack <date> --industries <行业>` 取包 → 深研(链上下游 WebSearch 产业证据标『实时网查』、格局与龙头映射、景气位置、行业内估值分布)→ 报告落 `$RPT/sector/<date>/<行业>.md`(**§6「研判结论」= standalone 专属**,不进机器契约、不喂 L3/L4 —— lite brief 已无此节,**两档不再同构**)。**无收尾记账**(2026-08-21 learning 层退役:`sector_memo` / `sector_ledger` 两条腿已删)。
pack 的 **`readthrough` 键**(海外读透映射,presence-gated:无有效映射则整键不存在)与可选的 `sector-intel` 活体情报 subagent(cap 6、四面盲搜)**只在本档消费** —— 渲染规则与禁忌(`kind` 决定能否当财报主体、`stale_reason` 处理、**不表因果/传导/评级方向**)见 `sector-playbook.md` 的「海外读透映射(full 专属)」节。

## 铁律(防锚定,违反即作废)
- **三层同律**:地形段只许数字/事实/日历(会喂 L3/L4);方向性判断(看多空/超低配语言)**只允许出现在 full 档 standalone 报告的 §6 研判结论**里 —— lite brief 是纯地形段,一个方向词都不许有(它没有第二段可以放)。**个股评级只由本股 rubric 三门决定。**
- **不设门**:行业弱 ≠ 该行业的票不研究——本 skill 产出不参与 L0–L3 筛选,只增强 L4/L5 判断(每加一条硬门 = 一块永久盲区)。
- 数字全出 pack/staging,缺字段写 —,不编。
- 收尾写明"Claude 推理产出,仅供研究,非投资建议"。

## 常见坑
- `uv run --no-sync` + 仓库根目录;**pack 依赖当日 scan staging**(L2 后才有 L1_scored_full)——standalone 深研若当日无 scan,先跑 universe 或用最近一个 scan 日的 staging(`--scan-dir` 指过去)。
- 行业指数序列(tushare `sw_daily`)未接(权限待核,spec 开放问题 1):TTL 复用以行业中位动量位移代理;叙述别引用不存在的指数数字。
- brief 的**单段**标题勿改字(`## 地形段` = `sector/brief.py` 的 `TERRAIN_HDR`,`extract_terrain` 逐字前缀匹配;改一个字 = L3 表头与 L4 简报同时静默丢掉整段地形)。**没有 ledger 可记了**:`sector_ledger` / `sector_memo` 两条腿随 2026-08-21 learning 层退役一并删除,`**行业方向**` keyed 行也随 08-19 的研判段一起消失 —— 全仓零解析者,别再按 keyed 格式写它。
