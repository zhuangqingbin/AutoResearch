# Funnel Variant Lab — Implementation Plan

> 日期：2026-09-13；关联：[设计](../specs/2026-09-13-funnel-shape-gates-dual-engine-design.md)。

**Goal:** 用冻结日内人口构建 current、composite_only、composite_plus_diversifiers 三种同预算漏斗，并对成熟交易日做 day-equal 配对评估。

**Architecture:** 新模块只读 run artifacts 与 shared lake，纯函数负责成员重建，CLI 负责跨日装配、收益连接和原子写研究报告；复用现有 L2、pass1 与 stage-value 统计原语，不接入生产扫描链。

**Tech Stack:** Python、pandas、现有 recall/l2/l3、forward_returns、stage_value、pytest。

## Task 1：纯成员生成器

- [ ] 在 `tests/research/test_funnel_variants.py` 写同预算、稳定 tie-break、80/20 上限、无 provenance floor 归零和原因分列的失败测试。
- [ ] 新增 `autoresearch/research/funnel_variants.py` 的 current/composite/hybrid 纯函数。
- [ ] 复用 `select_l2` 和 pass1 入口；不复制 production 算法，不写 run 目录。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/research/test_funnel_variants.py -q
```

## Task 2：配对评估

- [ ] 先写共同日期、缺失收益不填零、empty/pending 分离、raw/excess 双口径的失败测试。
- [ ] 复用 `stage_value.paired_daily_selection` 或其统计原语，按 date 等权汇总。
- [ ] 输出 overlap/unique 与 lane/backfill/pinned 等真实原因计数。

## Task 3：CLI 与审计产物

- [ ] 写临时目录 fixture，验证只写当前 engine 的 research 根及 manifest 输入摘要。
- [ ] 增加模块 CLI，原子写 membership、daily_metrics、paired_summary、manifest。
- [ ] 在 scan-market 文档中登记研究命令，但不接 production prelude。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/research/test_funnel_variants.py tests/research/test_stage_value.py -q
```

**验收：** 任一 challenger 与 current 的比较都来自相同日期、相同预算、相同收益人口；无足够成熟日时明确返回 insufficient_evidence。
