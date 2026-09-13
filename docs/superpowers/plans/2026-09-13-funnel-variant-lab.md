# Funnel Variant Lab — Implementation Plan

> 日期：2026-09-13；关联：[设计](../specs/2026-09-13-funnel-shape-gates-dual-engine-design.md)。

**Goal:** 用冻结日内人口构建 current、composite_only、composite_plus_diversifiers 三种同预算漏斗，并对成熟交易日做 day-equal 配对评估。

**Architecture:** 新模块只读 run artifacts 与 shared lake，纯函数负责成员重建，CLI 负责跨日装配、收益连接和原子写研究报告；复用现有 L2、pass1 与 stage-value 统计原语，不接入生产扫描链。

**Tech Stack:** Python、pandas、现有 recall/l2/l3、forward_returns、stage_value、pytest。

## Task 1：纯成员生成器

- [x] 在 `tests/research/test_funnel_variants.py` 写同预算、稳定 tie-break、80/20 上限、无 provenance floor 归零和原因分列的失败测试。
- [x] 新增 `autoresearch/research/funnel_variants.py` 的 current/composite/hybrid 纯函数。
- [x] 复用 `select_l2` 和 pass1 入口；不复制 production 算法，不写 run 目录。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/research/test_funnel_variants.py -q
```

## Task 2：配对评估

- [x] 先写共同日期、缺失收益不填零、empty/pending 分离、raw/excess 双口径的失败测试。
- [x] 复用 `stage_value.paired_daily_selection` 或其统计原语，按 date 等权汇总。
- [x] 输出 overlap/unique 与 lane/backfill/pinned 等真实原因计数。

## Task 3：CLI 与审计产物

- [x] 写临时目录 fixture，验证只写当前 engine 的 research 根及 manifest 输入摘要。
- [x] 增加模块 CLI，原子写 membership、daily_metrics、paired_summary、manifest。
- [x] 在 scan-market 文档中登记研究命令，但不接 production prelude。

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/research/test_funnel_variants.py tests/research/test_stage_value.py -q
```

**验收：** 任一 challenger 与 current 的比较都来自相同日期、相同预算、相同收益人口；无足够成熟日时明确返回 insufficient_evidence。


## 执行与复核记录(2026-09-13)

Task 1–3 已实施(commit `afec42b`)。复核按「摸产物本身」而非读勾选框:

- `autoresearch/research/funnel_variants.py` 三变体纯函数 + `run()` + CLI 在场;`tests/research/test_funnel_variants.py` 6 条(同预算/80-20 上限/无 provenance 时 floor 归零与 tie-break/raw 与 excess 分列且缺收益不填 0/只用共同成熟日且小样本标 immature/排他 bundle)。
- 复用而非另起:`select_l2`、`l3.triage.triage_l2_for_l3`、`common.stats.day_equal_bootstrap`、`ruler.MAIN_RULER`、`common.atomic`。`out_dir` 已存在即 `FileExistsError`,不覆盖已有读数;`manifest.json` 逐输入记 sha256。
- **补完**:Task 3 第三条「在 scan-market 文档中登记研究命令」原未做 → 已登记在 `.claude/skills/scan-market/STAGES.md` 的「运维细节」节(与 `lowturn_precheck` 同位置),写明只读/手动/不接 prelude/三档 `evidence_status` 的读法。
- **补完**:`membership.csv`、`paired_summary.json` 未进产物登记表 → 全量里 `test_no_unregistered_artifact_literals` 真红。已按「离线研究 bundle,非任何 run 的期望证据」进 `contracts.artifacts.NON_ARTIFACT_LITERALS` 并写明理由。顺带记下守卫的一个盲点:同 bundle 的 `daily_metrics.csv`/`manifest.json` 只是**与别的已登记产物重名**才被基名放行。

**已补接 F1 预注册**(原为待裁项,用户裁「全部开发」后落地):本 lab 此前是唯一不走 `contracts/research_experiment.py` 的研究模块(`w3_grids`/`experiment_io`/`stage_value` 都接了),而设计 §3.3 的晋级门槛通篇是预注册口径。现按与 `stage_value.run` **完全一致**的顺序收口:

- CLI 从 `--experiment-id` + `--hybrid-core-fraction` 改成 `--spec <已冻结方案>`;**命令行上不再有任何研究旋钮** —— 80/20 core、行业帽、style floor 进 `selection_rule`(新增 `registered_selection` 逐项校验),`n_boot`/`seed` 进 `bootstrap`,最小共同日进 `maturity_policy`。留在 CLI 上的旋钮等于「可以试很多个、只报最好看那个」。
- 开跑前逐项验:`verify_engine` / `verify_modes` / `parse_maturity_policy` / `verify_code_provenance`(behavior roots 含被复用的 `select_l2`、`triage_l2_for_l3` —— 它们变了,「同预算重建」就不是同一件事)/ `verify_manifest`(逐输入 sha256)/ `_registered_days`(分析日必须全落在冻结的 test 区间)。任一条不过就拒跑,且**一个字节都不落盘**(与 `stage_value` 同款断言:被拒的跑法不留半个目录)。
- 落点改 `eio.create_experiment_dir` 排他建 + `eio.freeze_spec` 把方案冻进结果目录;**没有 `--force`**,改假设就换 `experiment_id`。manifest 增记 `experiment_id`/`code_identity`/`input_manifest_hash`/`selection_rule`/`min_common_days`/`alpha`/`bootstrap`,`SCHEMA_VERSION` 升 2。
- 家族登记 `docs/research/2026-09-13-funnel-shape-family.spec.json`(过 F1 契约),两条假设各带自足的 `rejection_condition` —— 首版把第二条写成「同 funnel_composite_only」,被 `test_family_registry` 逮到:交叉引用不是可证伪主张,已展开重写。`stop_rule` 写明向前样本外的起算点就是冻结日 2026-09-13。
- 新增 5 组测试(引擎参数化 bundle / 输入清单不符 / 日期出区间 / 旋钮未冻 5 例 / 引擎声明不符)+ 3 条家族登记校验;4 条变异探针逐条实测变红。
