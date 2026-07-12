# AGENTS.md — 给 Codex 等非 Claude agent 的项目操作手册

本仓的完整操作手册在 `CLAUDE.md`(先读它)+ `.claude/skills/*/SKILL.md`(六个项目技能,每个是一份可执行的流程说明书)。本文件只做两件事:告诉你技能在哪、以及非 Claude harness 下怎么适配。

## 项目技能(trigger → 说明书)

| 技能 | 何时用 | 说明书 |
|---|---|---|
| scan-market | 扫描全 A 股 / 全市场选股 / 哪些板块值得买 | `.claude/skills/scan-market/SKILL.md`(流程细节 `STAGES.md`) |
| stock-research | 研究/分析单一股票(full 全量报告;"快速看一眼/出张卡"= lite 决策卡) | `.claude/skills/stock-research/SKILL.md` |
| macro-research | 全球宏观 / 资产配置 / "今天大盘怎么看"(lite=市场研判) | `.claude/skills/macro-research/SKILL.md` |
| sector-research | 研究单个申万行业(景气/格局/龙头映射) | `.claude/skills/sector-research/SKILL.md` |
| scan-retro | 复盘某日扫描(/retro;漏斗归因+权重再校准) | `.claude/skills/scan-retro/SKILL.md` |
| feedback | 用户对报告的纠错/表扬/"记住X" → 闭环知识库 | `.claude/skills/feedback/SKILL.md` |

这六个技能已软链进 `~/.codex/skills/`(codex 原生 skill 发现同构于 `<name>/SKILL.md`)。换机重建:

```bash
for s in feedback macro-research scan-market scan-retro sector-research stock-research; do
  ln -sfn "$PWD/.claude/skills/$s" ~/.codex/skills/$s
done
```

## 非 Claude harness 的适配规则

1. **确定性层原样可用**:所有 `uv run --no-sync python -m autoresearch.<...>` 命令(取数/漏斗/组装/校验门/预热)与 harness 无关,照 SKILL.md 跑即可。产物落 `reports/`、`context/`(已 gitignore)。
2. **LLM 编排层自行代偿**:SKILL.md 里的 `Workflow`/`Agent(subagent_type=...)` 派发是 Claude Code 专有。codex 等价做法 = 按同一顺序**自己在会话内**完成各角色的判断(策略师 market_view → 行业 brief → L3 精排 `_l3_judged.json` → 每票 L4 决策卡 → assemble),每步的输入文件、输出契约、校验门(`python -m autoresearch.scan.gates gate1/2/4`)与 SKILL.md 完全一致——**门必须跑,产物契约不许改**。
3. **不变量(与 harness 无关,一律遵守)**:持仓尺度=超短 1~2 日(fwd_2_oc 主尺,勿推 swing);0 买日 ≠ 门过严,勿松门凑单;喂 L3/L4 的只能是描述性地形(market_pack 里的 `sector_healthy_top3` 是 L5 专用,不得写进地形/卡片);评级只由本股 rubric 三门定。
4. **数据源**:A 股走 tushare(东财 push2 被封),需 `TUSHARE_TOKEN`;湖(`context/lake`)命中即零网络,数据契约层(A 级空帧抛异常拒入湖)不得绕过。

## 常用入口速查

```bash
uv run --no-sync python -m autoresearch.scan.prewarm            # 夜间预热(launchd 19:30 亦可手动)
uv run --no-sync python -m autoresearch.scan.prelude <date>     # 确定性前奏一键(L0-L2 等)
uv run --no-sync python -m autoresearch.scan.frame <date> --json # market_pack(策略师输入)
uv run --no-sync python -m autoresearch.scan.assemble <date>    # L5 整合(内含 self_review 硬门)
uv run --no-sync python -m autoresearch.learning.retro pending  # 待复盘日
uv run --no-sync python -m pytest -q                            # 全量测试
```
