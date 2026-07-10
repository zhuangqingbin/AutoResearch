# Plan A2:L3.5 可插拔闸 + 回测 harness(L4 出量 6~10)

spec: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §3。
门/纪律同 A1。执行顺序:T1/T2 独立可先行;T4 接线的配置选择依赖 A3-T1;T4 的豁免集契约与 A3-T4(pinned)对齐。

### Task 1: gate registry + 三策略(纯函数层)

**Files:** Create `autoresearch/scan/l35_gate.py`;Test `tests/scan/test_l35_gate.py`。

- 镜像 `autoresearch/scan/recall/registry.py` 模式:`@gate(name)` 注册 + `build(name)` 取函数 + `registered_gates()`。
- 统一签名:`gate_fn(judged: pd.DataFrame, *, regime: str, exempt: set[str], params: dict) -> GateResult`;`GateResult={picked: list[str], cut: list[dict(code, rank, conviction, lane, reason)]}`。**exempt 集(pinned/carryover/watchlist直通车)恒直通且不占配额**——这是与 A3-T4 的契约。
- 三策略:
  - `passthrough`:全放行(**默认=parity**,配置未指定时行为与现状完全一致);
  - `topk_simple`:按 L3 排名截 params.k(对照基线);
  - `conviction_floor_quota`:conviction≥params.floor + lane 多样性(每主 lane ≥params.lane_min,默认1)+ regime 分档上限(params.caps 默认 {trend:10, range:8, risk_off:6})+ 上限再与 `menu.l4_budget`(:101,base/floor 机制保留)取 min——预算旗收编为输入,一个机制。
- Steps:失败测试(合成 judged 帧:多样性被挤压时低分 lane 代表仍入选;exempt 票 conviction 低于地板仍直通;regime 分档数值)→ RED → 实现 → GREEN → commit `feat(scan): L3.5 可插拔闸 registry+三策略(exempt直通契约·预算旗收编)`。

### Task 2: gate_backtest 回测 CLI(零 LLM)

**Files:** Create `autoresearch/research/gate_backtest.py`;Test `tests/research/test_gate_backtest.py`。

- 重放:历史各日 `L3_judged_full.csv`(grep 定位列名:code/rank/conviction/lane)× `retro/attribution.csv` fwd_2_oc → 对每个注册 gate(及 params 网格,CLI --params-json)输出:picked 集 mean_fwd2/hit/n、**落选赢家清单**(cut 且 fwd_2>params.win_thr 默认 +3%,=错杀审计)、分 regime 小节;落 `reports/gate_backtest_<date>.md`。机制镜像 `l2_eval.forward_compare` 先例。
- Steps:失败测试(合成两日 judged+attribution,断言 picked 均值与错杀清单)→ RED → 实现 → GREEN → commit `feat(research): gate_backtest 历史重放 CLI(入选收益+落选赢家错杀审计)`。

### Task 3: 影子反事实账本(被闸掉票 fwd_2)

**Files:** Modify gate 接线点落 `_l35_cut.csv`(scan_dir,列=GateResult.cut);retro 侧补算其 fwd_2(grep `pre_healthy` 影子反事实实现,抄同款姿势接入 attribution/独立小节);Test 追加。

- 报告渲染:L5 加「L3.5 闸影子」一行(presence-gated:无 cut 文件=不加节):cut n、cut 集 fwd_2 均值 vs picked 均值——闸的日常体检读数。
- commit `feat(learning): L3.5 闸影子反事实(被闸票 fwd_2 记账+L5 体检行)`。

### Task 4: 接线(merge 后插闸 + workflow 提示行)

**Files:** Modify finalists 合流点(grep `merge_l3_finalists_v2` 定位,插在其后)、`.claude/workflows/scan-market.js`(L3 段 l4_budget 提示行改读闸后数)、`autoresearch/scan/gates.py:34-39`(g1 包加 gate 名+闸后 n);Test:合流测试追加 + `node --check` + `tests/scan/test_l4_prompt_cache_prefix.py` 必绿。

- gate 选择读 scan_config `l4_gate:{name,params}`(依赖 A3-T1;文件缺/键缺=passthrough=parity)。
- cut 落 `_l35_cut.csv`(T3 契约);exempt 集来源:pinned(A3)/carryover/watchlist 直通车 lane 标记。
- commit `feat(scan): L3.5 闸接线(merge后插闸·配置选策略·默认passthrough=parity)`。

### Task 5: 首份真实回测 + 切换裁决(跑动型,controller)

- 真跑 `gate_backtest`(现有 ≥14 日历史)出首份报告;依据报告(含错杀审计)在 scan_config 把 `l4_gate` 切 `conviction_floor_quota`(**人批点**:报告贴给用户,批后才切);STAGES/SKILL 文档更新一段(闸位置+影子行说明+回测迭代法)。结论记 progress.md。
