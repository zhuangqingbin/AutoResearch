# 全覆盖研究系统 · P0(接线断裂)+ P1(契约层)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把设计稿 §6 的 P0(10 件接线断裂)与 P1(契约层 A1/A2/A6/A7)落地 —— 让「该有什么」只有一份真身、让已建成的证据腿真的接上、让死掉的数据腿要么活要么诚实缺席。

**Architecture:** 路 A「契约优先·运行器不换」。不新建 pipeline 运行器(06-22 那套已因平行实现零调用被 `174ffd7` 删过一次);新增 `autoresearch/contracts/` 只存**声明**(产物登记表 / 阶段词汇 / agent 产出语法),其余五份登记表改为**从它派生**,JS 与 agent def 消费**生成物**。P0 各件互不依赖,严格文件所有权并发;P1 依赖 P0 与 contracts 核心文件先落地。

**Tech Stack:** Python 3.13(venv-only,**一律 `uv run --no-sync`**)· pytest · pandas · JS(Claude Code workflow,ESM,`node --check` 对它零鉴别力)

**Spec:** `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md`(§1.3 三类接线断裂 · §2.2 K1–K9 · §2.4 A1–A7 · §6 批次)

## Global Constraints

- **一律 `uv run --no-sync`**,仓库根目录运行(venv-only 的 akshare/tushare/lightgbm 会被 sync 删掉)。
- **B 类冻结到 09-中**(08-26 A0):不得改召回配额/门/评级/早停/主尺/BUY 数量/prompt 语义;`tests/scan/test_frozen_b_class_boundary.py` 六条必须继续绿,**不得删改**。
- **主尺** `common.ruler.MAIN_RULER` = `gap_c1_o2`,不动。
- **数据契约**:A 级空/腰斩 → `DataContractError` 阻断且拒绝入湖,**不得被 `except Exception` 吞**;B 级降级但**必须** `record_degradation()` 记账。
- **确定性层零 LLM**:L0/L1/L2/L5 不得引入任何 LLM 调用。
- **双引擎隔离**:只共享 `lake/`;禁止跨读对方 `context_*`/`reports_*`;路径一律经 `autoresearch/common/workspace.py`,不得手拼。
- **新参数三件套**:进 `scan/user_config.py` 白名单 + 有真实消费点 + 测试锁,三缺一不合入。
- **不得写 run 目录 / staging**:研究与回放仪器只写 scratch 或 `$RPT/research/`。
- **每个任务结束必须** `uv run --no-sync python -m pytest -q <自己的测试文件>` 全绿,并跑一次 `uv run --no-sync ruff check <自己碰过的文件>` 无新增错误。
- **提交纪律**:每个任务一个 commit,消息 `fix(<scope>): …` / `feat(<scope>): …`;结尾加
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。**不 push**。
- **绿灯不等于有灯**:每个任务至少有一条测试满足「把实现删掉/改坏它会红」;写完自问一遍。

---

# Wave 1 · P0(7 个任务,严格文件所有权,可完全并发)

**文件所有权表(不得越界写别人的文件;需要别人的产物就按 Interfaces 约定的名字用):**

| 任务 | 独占文件 |
|---|---|
| T1 | `autoresearch/trace/capsule.py` · `autoresearch/scan/run_profile.py` · `tests/trace/test_run_profile_mode.py`(新)· `tests/trace/test_capsule_d5_wiring.py`(新) |
| T2 | `autoresearch/trace/replay.py` · `tests/trace/test_replay.py` |
| T3 | `autoresearch/data/tushare_source.py` · `autoresearch/data/contracts.py` · `tests/data/test_frame_lake_routing.py`(新) |
| T4 | `autoresearch/scan/self_review.py` · `scripts/nightly_close.sh` · `tests/scan/test_web_budget_wiring.py`(新) |
| T5 | `autoresearch/scan/published_days.py`(新)· `autoresearch/sector/reuse.py` · `autoresearch/scan/l3/prompt.py` · `autoresearch/scan/menu.py` · `tests/scan/test_published_days.py`(新) |
| T6 | `autoresearch/scan/prewarm.py` · `scripts/com.tradingagents.scan-prewarm.plist` · `tests/scan/test_prewarm_guards.py`(新) |
| T7 | `.claude/skills/scan-market/SKILL.md` · `.claude/skills/scan-market/STAGES.md` · `.claude/skills/sector-research/SKILL.md` · `.claude/agents/l3-rank.md` · `.gitignore` |

**共享只读(谁都不许改)**:`tests/scan/conftest.py`、`tests/scan/test_frozen_b_class_boundary.py`、`.claude/skills/scan-market/scan_config.jsonc`。

---

### Task 1: capsule 传 mode + D-5 两个 materializer 接线

**Files:**
- Modify: `autoresearch/scan/run_profile.py`(`MODES` 补 `SENTINEL_PINNED`;哨兵跳过集合覆盖两个哨兵模式)
- Modify: `autoresearch/trace/capsule.py:2370`(加两个 materialize 调用)、`:2383-2386`(`scan_profile` 传 mode)
- Test: `tests/trace/test_run_profile_mode.py`(新)、`tests/trace/test_capsule_d5_wiring.py`(新)

**Interfaces:**
- Consumes: `autoresearch.scan.run_mode`(读 `run_mode.json` 的 `mode`)、`autoresearch.trace.web_budget.materialize_web_budget`、`autoresearch.trace.evidence_index.materialize_evidence_index`
- Produces:
  - `run_profile.MODES == ("FULL","FORCED_FULL","SENTINEL_EMPTY","SENTINEL_PINNED")`
  - **`capsule/usage/web_budget.json`**(T4 依赖这个精确路径)
  - `capsule/lineage/external_evidence_index.json`
  - `capsule.finalize` 内部读 `staging/run_mode.json` 得到 mode 并传给 `scan_profile(mode=…)`

**病灶(照抄自 spec §2.2 K3):** `run_profile.MODES` 只有 3 个而 `run_mode.MODES` 有 4 个;`capsule.py:2383` 调 `scan_profile(business_status=…, last_stage=…)` **从不传 mode**,于是哨兵趟(没有 L4 腿)的 `l4-card`/`l4-intel` 被标 REQUIRED,完整性结论对哨兵趟恒假。`SENTINEL_PINNED` 是**跑 L4 的**哨兵(持仓票要出卡),所以它**不能**进 `SENTINEL_SKIPPED_*`。

- [ ] **Step 1: 写失败测试(mode 词汇 + 哨兵期望)**

`tests/trace/test_run_profile_mode.py`:

```python
from autoresearch.scan import run_mode
from autoresearch.scan.run_profile import MODES, scan_profile


def test_modes_match_run_mode_vocabulary():
    # 两份 mode 词汇必须同源:run_profile 少一个 SENTINEL_PINNED 会让
    # 「持仓哨兵趟」在完整性里落进未知分支。
    assert set(MODES) == set(run_mode.MODES)


def test_sentinel_empty_does_not_owe_l4_roles():
    prof = scan_profile(mode="SENTINEL_EMPTY", business_status="SUCCEEDED")
    assert "l4" not in prof.expected_stages
    assert "l4-card" not in prof.agent_roles
    assert "l4-intel" not in prof.agent_roles


def test_sentinel_pinned_still_owes_l4_roles():
    # 持仓哨兵**跑** L4(持仓票必须出卡),它不是「跳过 L4」的那种哨兵。
    prof = scan_profile(mode="SENTINEL_PINNED", business_status="SUCCEEDED")
    assert "l4" in prof.expected_stages
    assert "l4-card" in prof.agent_roles
```

- [ ] **Step 2: 跑它,确认红**

Run: `uv run --no-sync python -m pytest -q tests/trace/test_run_profile_mode.py`
Expected: FAIL —— `test_modes_match_run_mode_vocabulary` 报集合不等;`SENTINEL_PINNED` 那条报 `ValueError: unknown run mode`。

- [ ] **Step 3: 改 `run_profile.py`**

`MODES` 改成 `("FULL", "FORCED_FULL", "SENTINEL_EMPTY", "SENTINEL_PINNED")`。
`SENTINEL_SKIPPED_STAGES` / `SENTINEL_SKIPPED_ROLES` 的**触发条件**保持只对 `SENTINEL_EMPTY` 生效(`scan_profile` 里现有的 `if mode == "SENTINEL_EMPTY"` 判断不要改成 `mode.startswith("SENTINEL")`)。在 `MODES` 上方加一行注释说明为什么 `SENTINEL_PINNED` 不跳过 L4(持仓票要出卡,`STAGES.md`「哨兵 vs 持仓」节)。

- [ ] **Step 4: 跑测试,确认绿**

Run: `uv run --no-sync python -m pytest -q tests/trace/test_run_profile_mode.py`
Expected: PASS(3 条)

- [ ] **Step 5: 写 D-5 接线的失败测试**

`tests/trace/test_capsule_d5_wiring.py` —— 断言 `finalize` 之后两份产物存在,且 `finalize` 把 run_mode 传给了 profile。用仓里现有的 capsule 测试夹具(先 `grep -rn "def .*capsule.*fixture\|finalize(" tests/trace/ | head -20` 找到现成的建 run helper 并复用,**不要**自己造一套新的 run 目录夹具)。测试要点三条:

```python
def test_finalize_materializes_web_budget_and_evidence_index(<现成夹具>):
    handle = <按现成 helper 建一个 run 并 finalize>
    assert (handle.capsule / "usage/web_budget.json").is_file()
    assert (handle.capsule / "lineage/external_evidence_index.json").is_file()


def test_finalize_passes_run_mode_to_profile(<现成夹具>, monkeypatch):
    # staging 里写 run_mode.json = SENTINEL_EMPTY,断言 expected 清单里没有 l4-card
    ...
    expected = json.loads((handle.capsule / "verification/expected.json").read_text())
    assert not [r for r in <expected 的规则行> if "l4-card" in <该行的 selector>]


def test_materializer_failure_does_not_break_finalize(<现成夹具>, monkeypatch):
    # D-5 是 B 级证据:它炸了不许把整个 finalize 带走(否则一个索引 bug 毙掉整趟现场)
    monkeypatch.setattr(capsule.web_budget_mod, "materialize_web_budget", _boom)
    handle = <finalize>
    assert (handle.capsule / "capsule.json").is_file()
```

`expected.json` 的真实结构请先 `uv run --no-sync python -c "..."` 或读 `trace/completeness.py:write_expected` 确认后再写断言,**不要猜字段名**。

- [ ] **Step 6: 跑它,确认红**

Run: `uv run --no-sync python -m pytest -q tests/trace/test_capsule_d5_wiring.py`
Expected: FAIL —— 两份文件不存在。

- [ ] **Step 7: 实现接线**

在 `capsule.py:2370` 的 `materialize_agent_index(handle.run_id)` 之后、`_write_usage(handle)` 附近,加两个调用,**各自包在 try/except 里并 `record_degradation` 记账**(B 级证据,失败不阻断 finalize;「降级不留痕才是真病」):

```python
# D-5 留痕(设计稿 2026-08-28 §6.1/§6.2):两份索引在此之前**建成未接线** ——
# 模块有、测试有、生产零调用者,于是真跑既没有 web_budget.json 也没有证据索引。
# 它们是 B 级证据:失败记账、不阻断 finalize(一个索引 bug 不该毙掉整趟现场)。
for _name, _fn in (("web_budget", _materialize_web_budget_safe),
                   ("external_evidence_index", _materialize_evidence_index_safe)):
    ...
```

mode 的传法:从 `handle.staging / "run_mode.json"` 读 `mode`(缺文件/解析失败/不在 `run_profile.MODES` → 回退 `"FULL"` 并 `record_degradation`),传进 `scan_profile(mode=…)`。

- [ ] **Step 8: 跑全部相关测试**

Run: `uv run --no-sync python -m pytest -q tests/trace/ tests/scan/test_stage_result.py`
Expected: PASS,无回归。

- [ ] **Step 9: 提交**

```bash
git add autoresearch/scan/run_profile.py autoresearch/trace/capsule.py tests/trace/test_run_profile_mode.py tests/trace/test_capsule_d5_wiring.py
git commit -m "fix(trace): pass run mode to evidence profile and wire D-5 indexes"
```

---

### Task 2: 修 replay 的死 argv(l2 阶段从未真正重放)

**Files:**
- Modify: `autoresearch/trace/replay.py:176-181`
- Test: `tests/trace/test_replay.py`

**Interfaces:**
- Consumes: 无
- Produces: `default_stage_specs()` 的 l2 项 argv 指向一个**真实存在且有 `main`** 的模块

**病灶:** `default_stage_specs` 的 l2 项是 `("autoresearch.scan.l2_stratify", analysis_date)` —— **该模块不存在**(真身是 `autoresearch/scan/recall/l2_stratify.py`,而且它没有 `main`)。测试全程注入假 runner,所以这条死 argv 从未变红:一个「永不变红的绿灯」。

- [ ] **Step 1: 先确认事实**

Run:
```bash
uv run --no-sync python -c "import autoresearch.scan.l2_stratify" ; echo "exit=$?"
uv run --no-sync python -c "import autoresearch.scan.recall.l2_stratify as m; print(hasattr(m,'main'))"
```
Expected: 第一条 `ModuleNotFoundError`;第二条 `False`。

- [ ] **Step 2: 写失败测试(每个 spec 的 argv 必须可导入且可执行)**

在 `tests/trace/test_replay.py` 追加:

```python
import importlib.util

from autoresearch.trace.replay import default_stage_specs


def test_every_stage_spec_module_is_importable_and_runnable():
    """死 argv 是永不变红的绿灯:测试注入假 runner,于是模块不存在也一路绿。"""
    for spec in default_stage_specs("2026-08-26"):
        mod_name = spec.argv[0]
        found = importlib.util.find_spec(mod_name)
        assert found is not None, f"{spec.stage}: 模块不存在 {mod_name}"
        mod = importlib.import_module(mod_name)
        assert hasattr(mod, "main"), f"{spec.stage}: {mod_name} 没有 main(),python -m 跑不起来"
```

- [ ] **Step 3: 跑它,确认红**

Run: `uv run --no-sync python -m pytest -q tests/trace/test_replay.py::test_every_stage_spec_module_is_importable_and_runnable`
Expected: FAIL —— `l2: 模块不存在 autoresearch.scan.l2_stratify`。

- [ ] **Step 4: 决定修法并实现**

L2 的真实生产者是 `autoresearch.scan.universe`(它内部调 `recall.l2_stratify.select_l2` 并写 `L2_gbdt_top200.csv`)。**先跑一次确认**:
```bash
uv run --no-sync python -c "import autoresearch.scan.universe as u; print(hasattr(u,'main'))"
```
`True` → 把 l2 spec 的 argv 改成 `("autoresearch.scan.universe", analysis_date)`。

⚠️ **随之而来的语义问题必须处理**:l1 与 l2 现在是同一条命令。两个 spec 跑同一个 argv 会把 universe 跑两遍(浪费且可能因湖状态不同产生噪音)。正确形状是**一个 spec 声明多产物**:把 l1/l2 合成 `StageSpec("l1l2", ("autoresearch.scan.universe", date), ("L1_scored_full.csv","L1_recall_top1000.csv","L2_gbdt_top200.csv"))`,并在 `REPLAYABLE_STAGES`(`scan/run_profile.py`)侧保持兼容——**但 `run_profile.py` 归 T1 独占,不要改它**;改为在 `replay.py` 内做名字映射(`l1`/`l2` 两个旧名都映到同一个 spec 的结果),并在模块 docstring 记一行为什么。若映射会让 `replay` 的返回结构变化,保持对外 dict 的 `l1`/`l2` 两个键都在(值相同),这样 `capsule`/`completeness` 的读侧逐字节不变。

- [ ] **Step 5: 跑测试**

Run: `uv run --no-sync python -m pytest -q tests/trace/test_replay.py`
Expected: PASS(含既有全部用例,结构未变)

- [ ] **Step 6: 提交**

```bash
git add autoresearch/trace/replay.py tests/trace/test_replay.py
git commit -m "fix(trace): replay l2 through the real universe entrypoint"
```

---

### Task 3: 生产帧走湖 + 半载快照守卫 + north/rz 死腿

**Files:**
- Modify: `autoresearch/data/tushare_source.py`(`fetch_universe_tushare` 的 9 条取数、`_fetch_factors` 的静默 print、`margin_detail` 的裸 print)
- Modify: `autoresearch/data/contracts.py`(`check_market_frame` 加覆盖率判据)
- Test: `tests/data/test_frame_lake_routing.py`(新)

**Interfaces:**
- Consumes: `autoresearch.data.cache.get_or_fetch`(现成的入湖读写口;**入湖一律全字段**,不得传窄 `fields`)
- Produces: 生产帧的 tushare 取数经 `get_or_fetch`;`contracts.check_market_frame` 新增列覆盖率判据

**病灶(spec §1.3 第一行,本波最重要的一件):** `fetch_universe_tushare`(`tushare_source.py:354-454`)裸调 `pro.daily_basic / daily×3 / stock_basic / moneyflow / margin_detail / stk_factor_pro / cyq_perf / hk_hold`,只有 60 日 `daily` 面板走湖。于是 A 级契约(`contracts.py:113-120`)**只在 prewarm/backfill/doctor 里跑过,生产路径上一次都没执行**。后果三条:① `north`/`rz` 两个 composite 组自 07-13 起每次扫描非空率 **0.0**,`margin_detail` 失败只 `print` 零记账;② 08-26/07-29 `chip`/`tech` 非空率 **0.547**(21:xx 的 tushare 半载快照),`check_market_frame` 不覆盖这些列 → composite 静默重归一;③ 08-28 普查回填对同样的 `hk_hold 20260825/26/27` 拿到 958 行 —— 生产的「空返回」是 T 晚撞上发布滞后。

⚠️ **边界(必须守)**:本任务**不改任何打分口径、权重、通道、门**。它只改「数据从哪来」与「坏了要不要说话」。改完 L1/L2 的产物在**同一份湖数据**下必须逐字节不变 —— 这是本任务的主验收(Step 6)。

- [ ] **Step 1: 写失败测试**

`tests/data/test_frame_lake_routing.py`。三组:

```python
def test_universe_fetch_goes_through_lake(monkeypatch):
    """生产帧的每条 tushare 取数都必须经 get_or_fetch —— 否则 A 级契约在生产路径上从不执行。"""
    seen_lake, seen_raw = [], []
    # monkeypatch cache.get_or_fetch 记录 endpoint;monkeypatch 底层 pro 客户端记录裸调
    ...
    tushare_source.fetch_universe_tushare("2026-08-26")
    assert seen_raw == [], f"这些端点绕过了湖:{seen_raw}"
    assert {"daily", "daily_basic", "moneyflow", "cyq_perf", "stk_factor_pro", "stock_basic"} <= set(seen_lake)


def test_half_loaded_snapshot_is_recorded_not_silent():
    """21:xx 的半载快照(A 级列非空率 0.547)此前静默通过 —— composite 会悄悄重归一。"""
    frame = <构造:mktcap/close 齐全,但 rsi6/winner_rate 只有 50% 非空>
    with pytest.raises(DataContractError):
        contracts.check_market_frame(frame)


def test_b_tier_leg_failure_is_recorded(monkeypatch):
    """margin_detail 失败此前只 print:rz 组从此恒 NaN 而没有任何一行降级记账。"""
    <让 margin_detail 抛>
    tushare_source.fetch_universe_tushare("2026-08-26")
    assert any(d["endpoint"] == "margin_detail" for d in contracts.degradations())
```

具体 monkeypatch 目标(`cache.get_or_fetch` 的确切模块路径、`pro` 客户端句柄名、`degradations()` 的返回结构)**先读源码确认**,不要照抄伪代码。

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest -q tests/data/test_frame_lake_routing.py`
Expected: 三条全 FAIL。

- [ ] **Step 3: 改取数路由**

`fetch_universe_tushare` 里每条 `pro.X(...)` 改成 `cache.get_or_fetch(endpoint=..., key=<交易日或 static>, fetch=lambda: pro.X(...))`(照 `frame.py:93` 那条已经走湖的 60 日 `daily` 的现成写法)。**铁律:入湖一律全字段** —— 不得把窄 `fields` 传进 cache key(`lake` 窄表毒化,`STAGES.md` 数据层节)。`stock_basic` 是 static 键(现湖副本 mtime 停在 06-22),给它加**每周刷新**:key 用 ISO 周(`YYYY-Www`),这样一周一份、不会每天重拉也不会两个月不动。

- [ ] **Step 4: 加半载守卫**

`contracts.check_market_frame` 现在只查 `code/close/mktcap_yi/pct_60d/main_net_ratio/cmf_20/obv_mom_20` 的存在性。加一条**覆盖率**判据:对 A 级来源的列(至少 `close`、`pct_60d`、`main_net_ratio` + 新增 `rsi6`、`winner_rate` 两个代表 `stk_factor_pro`/`cyq_perf` 的列),非空率 < 0.90 → `DataContractError`(A 级);对 B 级来源列(`hk_ratio`、`rz_buy_intensity`)非空率 < 0.90 → `record_degradation`。阈值 0.90 与判据写进函数 docstring,并说明 08-26 实测 0.547 是怎么漏过去的。

- [ ] **Step 5: 修 B 级静默腿**

`_fetch_factors`(`:203-221`)与 `margin_detail`(`:345-351`)的 `print` 改成 `record_degradation(endpoint, reason, key=...)` + 保留 print。`hk_hold` **不改取数日**(改 T−1 是 Q8,用户未裁)—— 只确保失败/空返回落 `record_degradation`(现在已有,复核一遍即可)。

- [ ] **Step 6: 主验收 —— 同湖数据下 L1/L2 逐字节不变**

对 `lake/` 里已有的一个数据日跑改前/改后各一次 universe,比对产物:
```bash
D=2026-08-26   # 用 lake 里确实齐全的一天
uv run --no-sync python -m autoresearch.scan.universe $D
md5 <(sort context_claude/scan/$D/L1_recall_top1000.csv) <(sort context_claude/scan/$D/L2_gbdt_top200.csv)
```
改前先存一份 md5(可用 `git stash` 拿改前版本),改后比对。**不一致就是回归**,先查是不是窄 fields 或 key 口径变了。若本机 `lake/` 那天数据不全导致跑不动,如实记录在 commit message 里并把该验收标为**未执行**(不要伪装成通过)。

- [ ] **Step 7: 跑测试**

Run: `uv run --no-sync python -m pytest -q tests/data/ tests/scan/test_frame*.py`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add autoresearch/data/tushare_source.py autoresearch/data/contracts.py tests/data/test_frame_lake_routing.py
git commit -m "fix(data): route the production frame through the lake and stop silent half-loads"
```

---

### Task 4: web_budget 真值路接进生产 lint + stage_rulers 进夜跑

**Files:**
- Modify: `autoresearch/scan/self_review.py`(给 `intel_query_cap_lint` 传 `web_budget_path`)
- Modify: `scripts/nightly_close.sh`(加 `populations rulers` 一步)
- Test: `tests/scan/test_web_budget_wiring.py`(新)

**Interfaces:**
- Consumes: **T1 产出的 `capsule/usage/web_budget.json`**(路径逐字一致,别自己另起名)
- Produces: 生产 lint 在 `web_budget.json` 存在时走真值路;`nightly_close.sh` 产 `stage_rulers.csv`

**病灶:** `intel_query_cap_lint(scan_dir, cap=15, web_budget_path=None)` 早就写好了真值路(「一行 tool call ≠ 一次查询」),但**生产从不传路径** → 至今仍是稿件自报路,而自报正是 08-26 八稿超限没被发现的原因。另一半:`stage_rulers.csv` 只有 prelude 里一个包在 `contextlib.suppress` 的生产者,`nightly_close.sh` 跑的是 `populations build`(不写 stage_rulers,写它的是 `rulers` 子命令)→ 没扫描的日子四把阶段尺不更新。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_web_budget_wiring.py`:

```python
def test_lint_prefers_web_budget_when_present(tmp_path):
    """有 capsule 真值就不许再信稿件自报 —— 自报是 08-26 超限没被发现的原因。"""
    <造一个 scan_dir + 一份 web_budget.json,里面某 role 的真实 query 数 > cap>
    rows = self_review.intel_query_cap_lint(scan_dir, cap=20)   # 注意:不显式传 path
    assert any(r.get("source") == "web_budget" for r in rows)


def test_lint_falls_back_to_self_report_without_capsule(tmp_path):
    <没有 web_budget.json>
    rows = self_review.intel_query_cap_lint(scan_dir, cap=20)
    assert all(r.get("source") != "web_budget" for r in rows)   # 逐字节旧行为
```

先读 `self_review.py:82-160` 与 `trace/web_budget.py:628 materialize_web_budget` 确认真值路返回行的**真实字段名**再写断言。

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_web_budget_wiring.py`
Expected: FAIL

- [ ] **Step 3: 实现自动定位**

在 `self_review.py` 里加一个私有 `_default_web_budget_path(scan_dir)`:由 `AUTORESEARCH_RUN_ID` / `workspace` 解析出本 run 的 `capsule/usage/web_budget.json`,存在则返回,否则 `None`;`intel_query_cap_lint` 的 `web_budget_path=None` 时调它。**签名不变**(显式传 path 仍优先),旧行为在文件缺席时逐字节保留。

- [ ] **Step 4: 跑测试**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_web_budget_wiring.py tests/scan/test_self_review*.py`
Expected: PASS

- [ ] **Step 5: nightly 加一步**

`scripts/nightly_close.sh` 在 `populations build` 之后加:
```bash
step "populations rulers"  autoresearch.scan.populations   rulers
```
并把文件头注释里的「只跑…两步 / 三步」改成四步、逐条列名(注释与实际步数不符是本仓反复复发的病)。

- [ ] **Step 6: 冒烟**

Run: `AUTORESEARCH_ENGINE=claude uv run --no-sync python -m autoresearch.scan.populations rulers`
Expected: 退出码 0,`reports_claude/scan/_ledger/views/stage_rulers.csv` 的 mtime 更新。把打印的 pooled 行贴进 commit message。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/self_review.py scripts/nightly_close.sh tests/scan/test_web_budget_wiring.py
git commit -m "fix(scan): use capsule web budget as the query-cap truth and refresh stage rulers nightly"
```

---

### Task 5: 跨日读者改走「已发布日」解析器(run 分区回归)

**Files:**
- Create: `autoresearch/scan/published_days.py`
- Modify: `autoresearch/sector/reuse.py:52-70`(`find_reusable`)、`autoresearch/scan/l3/prompt.py:126-136`(`_prev_l3_day`)、`autoresearch/scan/menu.py:92-105`(0 买连败)
- Test: `tests/scan/test_published_days.py`(新)

**Interfaces:**
- Consumes: `reports_<engine>/scan/_ledger/views/runs.csv`(G2 已产,60 行)
- Produces:
  ```python
  def previous_scan_days(date: str, *, limit: int = 30) -> list[str]        # 比 date 早的数据日,新→旧
  def previous_staging_dirs(date: str, *, limit: int = 30) -> list[Path]    # 这些日子的 staging 目录(优先本 run,回落历史根)
  ```

**病灶(spec §2.2 K4):** capsule 波把 staging 改成按 run 分区(`scan_runs/<run_id>/staging/<date>/`),而三个跨日读者仍在**遍历 `scan_root()` 的兄弟目录** —— 在 run 分区下它们只看得见**本 run 自己的日期**:行业 brief 的 TTL 复用永远找不到昨天的 brief(白付 6 个 opus)、L3 的 Δ 模式永远退化成全量、0 买连败永远数成 0(它是 L4 预算五面旗之一)。三处都**静默**降级。

⚠️ 本任务**不改任何判据**(TTL 5 日、Δ 容差、连败阈值全不动),只换「去哪找昨天」。

- [ ] **Step 1: 先证明病存在**

Run:
```bash
uv run --no-sync python -c "
from autoresearch.common import workspace as ws
import os
os.environ['AUTORESEARCH_RUN_ID']='20260826T120000000000Z'
print('run-partitioned scan_root:', ws.scan_root())
print('siblings:', sorted(p.name for p in ws.scan_root().iterdir() if p.is_dir())[:5] if ws.scan_root().exists() else 'MISSING')
"
```
把输出贴进 commit message —— 这是「兄弟目录在 run 分区下是空的」的现场证据。若本机现象与预期不符,**如实记录并停下来问**,不要硬改。

- [ ] **Step 2: 写失败测试**

`tests/scan/test_published_days.py`:

```python
def test_previous_scan_days_survives_run_partition(tmp_path, monkeypatch):
    """run 分区下 scan_root() 的兄弟目录只有本 run 的日期 —— 昨天必须从已发布账本找。"""
    <造 runs.csv:三个数据日 08-24/08-25/08-26>
    <设 AUTORESEARCH_RUN_ID,使 scan_root() 指向只含 08-26 的 run 目录>
    assert published_days.previous_scan_days("2026-08-26") == ["2026-08-25", "2026-08-24"]


def test_falls_back_to_legacy_root_without_ledger(tmp_path, monkeypatch):
    <没有 runs.csv,但历史根 context_claude/scan/<date>/ 有三天>
    assert published_days.previous_scan_days("2026-08-26") == ["2026-08-25", "2026-08-24"]
```

- [ ] **Step 3: 跑,确认红** — `uv run --no-sync python -m pytest -q tests/scan/test_published_days.py`

- [ ] **Step 4: 实现 `published_days.py`**

三级解析,逐级回落并在返回值里标 `source`(便于 prelude 端口行显示):① `_ledger/views/runs.csv` 的 `analysis_date` 列(去重、新→旧、排除 date 本身及更晚);② `reports_<engine>/scan/<run 目录>/manifest.json` 的数据日;③ 历史根 `context_<engine>/scan/<date>/` 的兄弟目录(= 今天的行为,兼容老树)。模块 docstring 写清病灶与三级顺序。

- [ ] **Step 5: 三处改接**

- `sector/reuse.find_reusable`:`prev_dirs` 改用 `published_days.previous_staging_dirs(date)`;**保留 `root` 形参**(测试在用),`root` 显式传入时仍走旧逻辑。
- `l3/prompt._prev_l3_day`:候选目录来源换成同一个解析器,`L3_judged_full.csv` + `L2_gbdt_top200.csv` 存在性判据不变。
- `menu` 的连败:`root = scan_dir.parent` 换成解析器;`lookback` 语义不变。

- [ ] **Step 6: 跑测试**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_published_days.py tests/sector/ tests/scan/test_calendar.py tests/scan/test_prelude.py -q`
再跑一次 L3/menu 相关:`uv run --no-sync python -m pytest -q tests/scan/ -k "menu or prompt or reuse or l3"`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/published_days.py autoresearch/sector/reuse.py autoresearch/scan/l3/prompt.py autoresearch/scan/menu.py tests/scan/test_published_days.py
git commit -m "fix(scan): resolve previous scan days from published runs, not staging siblings"
```

---

### Task 6: prewarm 守卫(一次 DNS 瞬断 = 整晚白过)

**Files:**
- Modify: `autoresearch/scan/prewarm.py:143-180`
- Modify: `scripts/com.tradingagents.scan-prewarm.plist`
- Test: `tests/scan/test_prewarm_guards.py`(新)

**Interfaces:**
- Consumes: 无
- Produces: `run_prewarm` 在日历解析失败时返回 `{"ok": False, "steps": [{"step": "resolve_date", "ok": False, ...}]}` 而不是抛栈

**病灶:** `date = date or latest_settled_trade_date(now)`(`:149`)在 `_step` **之外**,`trade_cal` 一次 DNS 瞬断就把整晚预热带走(08-22 起、08-28 20:13 `NameResolutionError api.waditu.com` ×3 又死一次),plist 无 `KeepAlive`/重试。08-26 稿 B1 提过,一直没做。

- [ ] **Step 1: 写失败测试**

```python
def test_calendar_failure_is_a_recorded_step_not_a_crash(monkeypatch):
    monkeypatch.setattr(prewarm, "latest_settled_trade_date", _raise_dns)
    res = prewarm.run_prewarm(None)          # 不传 date → 走日历解析
    assert res["ok"] is False
    assert any(s["step"] == "resolve_date" and not s["ok"] for s in res["steps"])


def test_explicit_date_skips_calendar(monkeypatch):
    monkeypatch.setattr(prewarm, "latest_settled_trade_date", _raise_dns)
    res = prewarm.run_prewarm("2026-08-26")  # 显式日期不该碰日历
    assert all(s["step"] != "resolve_date" for s in res["steps"])
```

- [ ] **Step 2: 跑,确认红(第一条会抛而不是返回)**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_prewarm_guards.py`

- [ ] **Step 3: 实现**

把日期解析搬进 `_step("resolve_date", …)` 之内(注意 `_step` 现在的签名是 `(name, fn)` 且 `fn(date)`,日期解析发生在 date 存在之前 —— 需要一个小变体或先建 steps 列表再解析)。解析失败 → 记一步 `ok:False`、**不再往下跑取数步**(没有日期,后面每一步都会拿 `None` 去取数)、`_prewarm.json` 仍要落盘(否则汇总屏那行「预热 ✗」也没有素材)。注意 `scan_dir` 的建立依赖 date,失败路径要能在无 date 时落盘(落到 `ws.scan_root()/"_prewarm_failed.json"` 或等价位置,自行选并在 docstring 说明)。

- [ ] **Step 4: plist 加重试**

`scripts/com.tradingagents.scan-prewarm.plist` 加 `<key>KeepAlive</key>` 的 `SuccessfulExit=false` 变体(失败才重启)**或**追加一个 21:00 的 `StartCalendarInterval` 二次尝试。二选一,在文件里写一行 XML 注释说明选了哪种、为什么。⚠️ **不要**给 `KeepAlive` 用无条件 `true`(会让它失败后疯狂重启)。

- [ ] **Step 5: 跑测试 + 冒烟**

Run: `uv run --no-sync python -m pytest -q tests/scan/test_prewarm_guards.py tests/scan/ -k prewarm`
Run: `uv run --no-sync python -m autoresearch.scan.prewarm 2026-08-26`(有网就真跑一次;没网正好验证失败路径不抛栈)

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/prewarm.py scripts/com.tradingagents.scan-prewarm.plist tests/scan/test_prewarm_guards.py
git commit -m "fix(scan): survive calendar outages in the nightly prewarm"
```

---

### Task 7: 文档勘误 + 仓库卫生

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`、`.claude/skills/scan-market/STAGES.md`、`.claude/skills/sector-research/SKILL.md`、`.claude/agents/l3-rank.md`、`.gitignore`

**Interfaces:** 无代码接口。**只改散文,不改任何机器契约字符串**(`## 地形段`、`**Rating**`、`FINAL TRANSACTION PROPOSAL` 等一个字都不许动 —— 它们是解析器契约)。

逐条(每条都先 grep 现状,确认后再改;改完把「改前 → 改后」写进 commit message):

- [ ] **Step 1: prelude 步数** —— `SKILL.md` 写「**9 步**」,真身 `prelude.STEP_NAMES` 是 **12**(`ledger_views`、`overseas` 是 08-29 新加的)。
  Run: `uv run --no-sync python -c "from autoresearch.scan.prelude import STEP_NAMES; print(len(STEP_NAMES), STEP_NAMES)"` → 按真实值改,并把步名逐个列出。

- [ ] **Step 2: `_l3_calibration.md` 死引用** —— `.claude/agents/l3-rank.md:16` 把它写成「硬约束,逐条遵守」,而**全仓没有任何生产者**(`grep -rn "_l3_calibration" autoresearch/ tests/` 为空)。删掉这条必读项;在同处留一行注释说明它随 2026-08-21 learning 层退役消失。⚠️ 改 agent def **下个 session 才生效**(会话启动装载),在 commit message 里注明。

- [ ] **Step 3: 通道数** —— `scan_config.jsonc` 注释与 `STAGES.md:47` 说「12 路」,`registered_channels()` 实为 **14**、启用 **10**。`scan_config.jsonc` 归共享只读**不许改**,只改 `STAGES.md`,并把「⚠️ 该 key 缺省 = 用全部 12 路」改成正确的 14 路且加粗警告。

- [ ] **Step 4: 行业分类学** —— `STAGES.md` L2 节与 `l2_stratify` 文档说「申万一级」,真身是 akshare `stock_yjbb_em` 的**东财**「所处行业」(~110 标签,`tushare_source.py:154`、`common/sw_sector_map.py:4-5`)。改 `STAGES.md` 的说法为「东财所处行业(~110 类)」并注明 `sector_cap` 因此很少触发。

- [ ] **Step 5: sector SKILL 陈述过期** —— `.claude/skills/sector-research/SKILL.md:22-23,38` 仍写 `## 研判段` / `**行业方向**` / ledger / 「L5 嵌 🏭 行业研判节」,而研判段已于 2026-08-19 整段砍除、L5 并不嵌(只数字节)。按 `sector-playbook.md:147-150` 的现状改写。

- [ ] **Step 6: `_token_usage.json` 路径分歧** —— `SKILL.md` 步骤 5 让 `usage_harvest --json-out $CTX/scan/<date>/_token_usage.json`(日期键),而 `post_run` 读 `ws.scan_dir(date)`(run 分区)。
  Run: `grep -n "_token_usage" autoresearch/scan/post_run.py autoresearch/trace/usage_harvest.py` 确认读侧真身,把 SKILL 的命令改成与读侧同一个解析(推荐写成 `$(uv run --no-sync python -m autoresearch.common.workspace scan-dir <date>)` 之类的**单一事实源**取法;若 workspace 没有这样的 CLI,就在 SKILL 里明确写 run 分区路径并注明由 `AUTORESEARCH_RUN_ID` 决定)。

- [ ] **Step 7: 仓库卫生** —— `docs/research/2026-08-26-ledger-first-readout/` 里有 `__pycache__/*.pyc`;仓库根有 `task_plan.md` / `findings.md` / `progress.md`(planning-with-files 的临时件)。把 `__pycache__/` 与那三个根文件加进 `.gitignore`(若已有 `__pycache__` 规则则只补根文件),并 `git rm --cached` 掉已被跟踪的 `.pyc`(**只用 `--cached`,不删磁盘文件**)。先 `git status --short docs/research/2026-08-26-ledger-first-readout/` 确认它们到底有没有被跟踪。

- [ ] **Step 8: 验收**

Run: `uv run --no-sync python -m pytest -q tests/test_skill_docs_refs.py tests/test_agent_defs.py`
Expected: PASS(doc-lint 与 def 锚都不许因这次改动变红)

- [ ] **Step 9: 提交**

```bash
git add -A .claude docs .gitignore
git commit -m "docs(scan): correct step counts, channel counts, taxonomy and dead references"
```

---

# Wave 2 · P1(契约层;Wave 1 全绿后开工)

> Wave 2 的**前置**是 `autoresearch/contracts/` 的核心声明文件先落地(由主会话写,见 Task 8),之后 Task 9–12 可并发。

### Task 8: `autoresearch/contracts/` 核心声明(**串行,先做**)

**Files:**
- Create: `autoresearch/contracts/__init__.py`、`autoresearch/contracts/artifacts.py`、`autoresearch/contracts/stages.py`、`autoresearch/contracts/agent_output.py`
- Test: `tests/contracts/test_registry_parity.py`

**Interfaces:**
- Produces(Task 9–12 全部依赖这些精确名字):
  ```python
  # stages.py
  STAGES: tuple[str, ...]              # frame prelude gate1 sector l3 gate2 l4_prep l4 l5 observe gate4 finalize
  MODES: tuple[str, ...]               # 与 run_mode.MODES 同源(4 个)
  REPLAYABLE_STAGES: tuple[str, ...]
  # artifacts.py
  @dataclass(frozen=True)
  class Artifact:
      name: str; path: str; root: str; stage: str; producer: str
      kind: str            # csv | json | md | dir
      presence: str        # always | gated | conditional
      required_when: str | None
      replayable: bool
  ARTIFACTS: tuple[Artifact, ...]
  def by_name(name: str) -> Artifact
  def for_stage(stage: str) -> tuple[Artifact, ...]
  # agent_output.py
  @dataclass(frozen=True)
  class Field: key: str; pattern: str; required: bool
  @dataclass(frozen=True)
  class OutputContract: role: str; fields: tuple[Field, ...]
  CONTRACTS: dict[str, OutputContract]   # l4-card / l3-rank / sector-brief / macro-brief / l4-intel
  RATING_ORDER: tuple[str, ...]          # 单一事实源:Buy, Overweight, Hold, Underweight, Sell
  ```

**Steps(TDD;每步都要能红):**

- [ ] **Step 1** 写 `tests/contracts/test_registry_parity.py` 的**第一条**:`ARTIFACTS` 的名字集合 ⊇ `scan/artifacts.CRITICAL_ARTIFACTS` 的名字集合,且同名项的 `path` 逐字相等。跑 → 红(模块不存在)。
- [ ] **Step 2** 建包 + 用 `CRITICAL_ARTIFACTS`(`scan/artifacts.py:35-73`)与 spec §2.1 的 I/O 表把 `ARTIFACTS` 填出来。跑 → 绿。
- [ ] **Step 3** 加第二条:`stages.MODES == run_mode.MODES`、`set(stages.STAGES) ⊇ set(run_profile.SCAN_STAGES)`、且 JS 里出现的阶段串(`'l4-prep'`、`'finalize'`)都在 `STAGES` 里(把 JS 阶段串在测试里写成显式清单)。跑 → 红 → 补 → 绿。
- [ ] **Step 4** 加第三条 **drift 守卫**(本任务最有价值的一条):grep 全仓生产 `.py`/`.js` 里形如 `"<name>.csv"` / `"<name>.json"` / `"details/"` 的产物字面量,断言每个都能在 `ARTIFACTS` 里找到(允许清单显式列出例外,初始清单 = 今天的例外,**只许减不许增**)。跑 → 红(会捞出一堆)→ 把真产物补进登记表、把非产物加进允许清单 → 绿。
- [ ] **Step 5** `RATING_ORDER` 与 `rating.RATINGS_5_TIER` 一致性断言;`CONTRACTS` 里 `l4-card` 的每个 `pattern` 用 `l4/parsers.py` 的现成正则**逐字搬**,并加一条「用同一份样本卡,`agent_output` 与 `l4/parsers` 解析结果相同」的对拍测试。
- [ ] **Step 6** 提交:`feat(contracts): single source of truth for artifacts, stages and agent output`

### Task 9: 五份登记表改为派生(依赖 Task 8)
`scan/run_profile.py`、`trace/completeness.py`、`scan/health.py:33-36`、`scan/brief.py:81-93`、`scan/publisher.py:247,439`、`trace/replay.py` 的阶段清单全部改为从 `contracts` 派生;每改一处加一条 parity 断言(派生结果 == 今天的硬编码值)。**顺带**:`verify.csv` 无生产者 → 从 `health._ARTIFACTS` 移除并在 `decision_finalize.py:46` 的读侧加显式「已退役」注释(或按 spec §4.3 二选一)。

### Task 10: JS 生成物(依赖 Task 8)
`python -m autoresearch.contracts emit-js` → `.claude/workflows/_contracts.generated.js`(`ARTIFACTS/STAGES/TASK_ACTIONS/RATING_ORDER/TRANSIENT_ERRORS`);两个 workflow 改为 import 它;测试断言「生成物与登记表 hash 一致」且「JS 里不再出现未登记的产物名」。⚠️ `node --check` 对 ESM+顶层 return 零鉴别力,验收改用 `AsyncFunction` 探针(见 `wave35-mutation-testing` 教训)。

### Task 11: 评级序 / conviction 归一单源(依赖 Task 8)
删 JS 的 `RANK` 字面量与 python 侧 4 份评级序副本(`rating.py`、`decision_finalize.py:22`、`self_review.py:26-27`、`brief.py:108`),全部指向 `contracts.RATING_ORDER`;JS 侧用 Task 10 的生成物。**注意方向相反**(JS sell=0…buy=4,python Buy=0…Sell=4)—— 生成物里显式给两个视图并各配一条测试。

### Task 12: 分层测试(依赖 Task 8)
`tests/test_layering.py`:用 AST 算 import 矩阵,断言无「下层 import 上层」;允许清单初始 = 今日矩阵(`trace→scan` 8 行、`research→scan` 6 行、`data→trace` 2 行等,逐条列出并注明「待 Task 9 后收敛」),**只许减不许增**。再加「每个登记产物恰一个 writer 调用点」的探针(第二写者守卫:`run_health.json` 现 5 处、`summary.md` 现 3 处 → 先记为已知例外并标 TODO 引用 spec §2.4 A7)。

---

## Self-Review

**Spec 覆盖**:P0 十件 → ②③ = T1/T2,④ = T3,⑤ = T1+T4,⑥ = T5,⑦⑨⑩ = T7,⑧ = T6。**① (壳成本 A5) 不在本计划** —— spec §6 P0 ① 自己写着「先量(Q12)」,而量它需要一次真跑,用户未裁,故不做。P1 四构件 → Task 8–12(A1=T8/T9/T10、A2=T8/T11、A7=T12);**A6(配置按引用 + run 分区唯一可写根)不在本计划** —— 它与 T5 的路径解析强耦合,等 T5 落地后单独立一个任务,避免两个 agent 抢同一批路径代码。

**占位扫描**:无 TBD/TODO;T1/T3/T4/T5 的测试体里有 `<…>` 尖括号占位,它们是**故意的** —— 那几处的真实字段名必须由实施者读源码确认(计划里写死会变成「参考实现自带缺陷被抄 N 遍」,本仓 Wave11 的原话)。每处都写了「先读哪个文件确认」。

**类型一致性**:`published_days.previous_scan_days/previous_staging_dirs` 在 T5 定义、T5 内部消费,无跨任务引用。`capsule/usage/web_budget.json` 由 T1 产、T4 消费,两处路径逐字一致。`contracts.*` 的名字在 Task 8 定义、9–12 消费,签名已写全。
