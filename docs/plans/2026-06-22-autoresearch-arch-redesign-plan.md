# AutoResearch 架构重构 Implementation Plan (Phase 1 骨架)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) or superpowers:subagent-driven-development to implement task-by-task. Steps use `- [ ]` checkboxes.

**Goal:** 把扁平 `scripts/` 重构成 `autoresearch/` 包,所有 skill 走统一 Stage 契约 + 统一数据层(Parquet 湖,取一次永不重取)+ 可插拔模型框架(统一 Trainer + champion 门),trace 现场全留。Behavior-preserving:确定性段 golden 对拍一致。

**Architecture:** 见 `docs/specs/2026-06-22-autoresearch-arch-redesign-design.md`(A 包结构 / B 数据层 / C 模型框架 / D trace / E 迁移)。本计划 = Section E 的 6 步,每步独立可测。

**Tech Stack:** Python 3.13 · pandas · pyarrow(parquet+zstd)· lightgbm/xgboost/catboost · pytest · tushare/akshare/fred(取数)· uv(`--no-sync`)。

## Global Constraints
- 命令一律 `uv run --no-sync`(venv-only akshare/tushare/lightgbm,勿误删)。仓库根目录运行。
- 包名 `autoresearch`(import 小写,对齐 pyproject `name` + repo)。
- `context/`、`reports/` 已 gitignore;数据湖落 `context/lake/`(gitignore),代码不提交大文件。
- 确定性层零 LLM、不编数;每段产物 typed(parquet/md/json)。
- 改 `weights.json` 前 `feedback_store.snapshot_weights()` 留快照。
- 每步:测试绿 → commit。commit message 结尾 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。
- 分支 `autoresearch-redesign`(已建,baseline 已提)。

## File Structure(决策锁定)
```
autoresearch/                  # ← git mv tradingagents/
  common/{__init__,sw_sector_map,uzi_lenses,vol_series}.py
  data/{__init__,cache,sources,endpoints,features,handler}.py
  models/{__init__,base,registry,trainer,catalog,linear,gbdt,xgb,cat,dbl}.py
  trace/{__init__,store,schema}.py
  learning/{__init__,feedback_store,self_review,retro,stage_eval}.py
  scan/{__init__,context,config,pipeline,cli,parity}.py
  scan/stages/{__init__,base,l0_universe,l1_recall,l2_rank,l3_select,l4_research,l5_assemble}.py
  scan/agents/{__init__,l3_select,l4_card,verify}.py
  analyze/{__init__,...}.py   macro/{__init__,...}.py   # Phase E5
tests/  → 镜像 autoresearch/(pytest)
```

---

## Phase E1 — 脚手架 + 改名(基础,先让一切能 import)

### Task 1.1: 改名 tradingagents → autoresearch + 修 27 处 import
**Files:** `git mv tradingagents autoresearch`;改全仓 `import tradingagents`→`autoresearch`(27 文件);`pyproject.toml` packages.find 确认含 autoresearch。
- [ ] Step 1: `git mv tradingagents autoresearch`
- [ ] Step 2: `grep -rl "tradingagents" --include=*.py . | xargs sed -i '' 's/tradingagents/autoresearch/g'`(macOS sed)
- [ ] Step 3: `uv run --no-sync python -c "import autoresearch; from autoresearch.agents.utils.rating import parse_rating; print('ok')"` → Expected: ok
- [ ] Step 4: `uv run --no-sync pytest -q` → Expected: 现有测试绿(import 通)
- [ ] Step 5: commit `refactor: rename tradingagents package to autoresearch`

### Task 1.2: 建空子包 + 迁 common/learning
**Files:** Create `autoresearch/{common,data,models,trace,learning,scan,scan/stages,scan/agents}/__init__.py`;`git mv scripts/{sw_sector_map,uzi_lenses,vol_series}.py autoresearch/common/`;`git mv scripts/{feedback_store,self_review,retro,stage_eval}.py autoresearch/learning/`;修 import(扁平→包)。
- [ ] Step 1: 建 __init__.py 占位
- [ ] Step 2: git mv common/learning 模块 + 改其内部 import(`from sw_sector_map`→`from autoresearch.common.sw_sector_map` 等)
- [ ] Step 3: 把各模块 `--selftest` 包成 pytest:`tests/common/test_uzi_lenses.py` 等,调 `_selftest()` 断言返回 0
- [ ] Step 4: `uv run --no-sync pytest -q tests/common tests/learning` → Expected: 绿
- [ ] Step 5: commit `refactor: move common + learning into autoresearch package`

---

## Phase E2 — 数据层(Parquet 湖 + DataHandler)

### Task 2.1: data/sources.py + endpoints.py(取数入口 + policy registry)
**Interfaces produces:** `sources.fetch(endpoint:str, params:dict) -> pd.DataFrame`;`endpoints.policy(endpoint) -> {key, settle}`。
- [ ] Step 1: 写 `tests/data/test_endpoints.py`:`policy("daily")["settle"]=="eod"`,未知端点抛 KeyError
- [ ] Step 2: 运行测试看失败
- [ ] Step 3: 实现 endpoints.py(ENDPOINTS dict,从现 factor_lab `_FIELDS` + screen_market/tushare_source 端点搬全)+ sources.py(包现 tushare_source/akshare 取数)
- [ ] Step 4: 测试绿
- [ ] Step 5: commit `feat(data): sources + endpoint policy registry`

### Task 2.2: data/cache.py(Parquet 湖 + 存在即命中 + 原子写)
**Interfaces produces:** `cache.get_or_fetch(endpoint, params, today=None) -> pd.DataFrame`;`cache.lake_path(endpoint, params) -> Path`。
- [ ] Step 1: 写 `tests/data/test_cache.py`:第一次 fetch 写 parquet、第二次命中不调 sources(用 monkeypatch 计数);空结果写空 parquet 仍算命中;date==today+settle=live 不缓存。
- [ ] Step 2: 跑测试失败
- [ ] Step 3: 实现 cache.py(pyarrow zstd 写、临时文件 rename 原子、读 parquet;policy 决定 key/是否缓存)
- [ ] Step 4: 测试绿
- [ ] Step 5: commit `feat(data): parquet lake cache (exists=hit, atomic write)`

### Task 2.3: pkl→parquet 迁移现有 factor_lab cache
**Files:** `autoresearch/data/migrate_cache.py`(一次性脚本)。
- [ ] Step 1: 写 `tests/data/test_migrate.py`:造 1 个 pkl → 迁移 → 读回 parquet 值/行数/dtype 一致
- [ ] Step 2: 失败
- [ ] Step 3: 实现迁移(遍历 `context/factor_lab/cache/<ep>/<date>.pkl` → `context/lake/<ep>/<key>.parquet`)
- [ ] Step 4: 测试绿;跑真迁移 + 抽样校验(行数/几列 sum 对比)
- [ ] Step 5: commit `feat(data): migrate factor_lab pkl cache to parquet lake`

### Task 2.4: data/features.py + handler.py(特征 registry + DataHandler.core)
**Interfaces produces:** `@feature(name, source, deps)`;`DataHandler.materialize(dates, feature_set, kind="core") -> pd.DataFrame`(含 label fwd_1_oo)。
- [ ] Step 1: 写 `tests/data/test_handler.py`:core feature_set 物化出含声明特征 + label 的帧;复用 cache(不重取)。
- [ ] Step 2: 失败
- [ ] Step 3: 实现 features registry(把 screen_market `_factor_groups` + factor_lab `factor_frame` 的特征声明化)+ handler.core(从 lake 拼横截面帧 + 前瞻收益)
- [ ] Step 4: 测试绿
- [ ] Step 5: commit `feat(data): feature registry + DataHandler core feature_set`

---

## Phase E3 — 模型框架 + 确定性段 L0/L1/L2 + golden 对拍

### Task 3.1: models 框架(base + registry + trainer + catalog)
**Interfaces produces:** `class Model(ABC){feature_set,kind,fit,predict,save,load}`;`@register(key)`;`build(cfg)`;`class Trainer{train,promote_if_better}`;`catalog.MODELS`(全 zoo 在册 + status)。
- [ ] Step 1: 写 `tests/models/test_framework.py`:注册一个 dummy Model、build 出实例、Trainer.train 走通(用合成 panel)、champion 门(challenger oos 高才晋升)、catalog 列出 lgbm 等。
- [ ] Step 2: 失败
- [ ] Step 3: 实现 base/registry/trainer(label+时序切+oos rank-IC+champion 全在 Trainer)/catalog
- [ ] Step 4: 测试绿
- [ ] Step 5: commit `feat(models): Model interface + registry + unified Trainer + catalog`

### Task 3.2: 迁 linear + gbdt(+ xgb/cat/dbl)成原生 Model
**Files:** `models/{linear,gbdt,xgb,cat,dbl}.py`(从现 factor_lab `train_gbdt`/`predict_scores` + screen_market `composite_score` 重构成 Model 子类)。
- [ ] Step 1: 写 `tests/models/test_rankers.py`:GBDTRanker.fit/predict 形状对、LinearComposite.predict 与现 `composite_score` 数值一致(golden)、五个都注册进 registry。
- [ ] Step 2: 失败
- [ ] Step 3: 实现五个 ranker(linear 包 weights.json;gbdt 搬 lightgbm 逻辑 + beats_linear→champion 门通用化;xgb/cat/dbl 同构)
- [ ] Step 4: 测试绿;`train` 一遍确认 oos 口径与现一致
- [ ] Step 5: commit `feat(models): native linear/gbdt/xgb/cat/dbl rankers`

### Task 3.3: scan/stages L0/L1/L2 上 Stage 契约 + trace 写产物
**Files:** `scan/stages/{base,l0_universe,l1_recall,l2_rank}.py`、`scan/{context,pipeline,config}.py`、`trace/{store,schema}.py`。
- [ ] Step 1: 写 `tests/scan/test_stages_det.py`:L0→L1→L2 跑通(用小 fixture lake),产物落 typed trace,L2 调 models.build(champion).predict。
- [ ] Step 2: 失败
- [ ] Step 3: 实现 Stage base + 三段(逻辑搬现 screen_market.run)+ trace store/schema + pipeline runner(断点续跑)
- [ ] Step 4: 测试绿
- [ ] Step 5: commit `feat(scan): L0/L1/L2 stages on contract + typed trace`

### Task 3.4: golden 对拍(scan/parity.py)
**Files:** `scan/parity.py`。
- [ ] Step 1: 重构前已在 baseline;`parity capture <date>` 用 **baseline commit** 的 screen_market 跑出 L1_recall/L2 快照存 `context/golden/<date>/`(若无网络用已有 `context/scan/2026-06-18` 产物当 golden）
- [ ] Step 2: 写 `tests/scan/test_parity.py`:新 L0/L1/L2 vs golden,**召回集合 + L2 top200 + 名次一致**(1e-9 容差)
- [ ] Step 3: 跑 `parity check 2026-06-18` → Expected: 召回集/排名一致(diff 空)
- [ ] Step 4: 若 diff 非空 → 定位重构引入的偏差并 fix 到一致
- [ ] Step 5: commit `test(scan): golden parity for deterministic L0/L1/L2`

---

## Phase E4 — L3/L4/L5 + agents（结构校验,非内容对拍）

### Task 4.1: L3/L4/L5 stages + agents prompt 构造
**Files:** `scan/stages/{l3_select,l4_research,l5_assemble}.py`、`scan/agents/{l3_select,l4_card,verify}.py`(搬现 scan_pipeline 的 `l3_table_md`/`batch_finalists`/`pick_*`/`rubric_rating` + assemble_scan 的 summary/token/trace 发布)。
- [ ] Step 1: 写 `tests/scan/test_stages_llm.py`:L3 表渲染、finalists schema、L4 评级解析、L5 summary 三段 + token 段 + 逐阶段表(搬现 assemble_scan selftest 断言)。
- [ ] Step 2: 失败
- [ ] Step 3: 实现三段 + agents(LLM 现场写 `agents/`)
- [ ] Step 4: 测试绿
- [ ] Step 5: commit `feat(scan): L3/L4/L5 stages + agents + trace publish`

### Task 4.2: cli + 端到端 smoke
**Files:** `scan/cli.py`(`python -m autoresearch.scan run/capture/check`)。
- [ ] Step 1: 写 `tests/scan/test_cli.py`:`run` dispatch 到 pipeline(mock 取数),产物目录结构对。
- [ ] Step 2-4: 实现 + 绿
- [ ] Step 5: commit `feat(scan): cli + e2e wiring`

---

## Phase E5 — analyze / macro 进包上契约
### Task 5.1: analyze 管道进包(harvest_context/assemble_report → autoresearch/analyze/stages + 接共享 data 层)
- [ ] TDD 同构:搬逻辑 → 接 DataHandler(去重取数)→ 现有 analyze selftest 迁 pytest → 绿 → commit
### Task 5.2: macro 管道进包(harvest_macro/assemble_macro/tushare_macro → autoresearch/macro/)
- [ ] TDD 同构 → 绿 → commit

---

## Phase E6 — 文档 + CLI + 清理
### Task 6.1: 更新 skill 文档命令到 `python -m autoresearch.*`;删旧 scripts(留薄 shim 或删)
- [ ] 改 5 个 skill 的命令 → 全 pytest 绿 → commit
### Task 6.2: pyproject extras（[torch] for MLP/TabNet;[qlib-ref] 可选）+ README 结构说明
- [ ] commit

---

## 后续 phase(不在本计划,catalog 已占位)
- torch 表格(MLP/TabNet)→ Phase 1.5;序列层 + LSTM/Transformer/TFT/TRA → Phase 2;图层 + GATs/HIST → Phase 2+。
- L1 多路召回(独立 spec)、L3 Opus-high 精修 + FinGPT 情感特征(独立 spec)。

## Self-Review 记录
- Spec 覆盖:A→Task1.x/3.3;B→Task2.x;C→Task3.1-3.2;D→Task3.3+4.1;E→Task3.4+全程。✓
- 类型一致:Model.{fit,predict,feature_set,kind} / DataHandler.materialize / Trainer.{train,promote_if_better} 跨任务一致。✓
- 占位扫描:later phase(E5/E6/后续)按"同构 TDD"压缩——属**已知模式重复**,执行时照 E1-E4 模板展开,非占位失败。
