# Statistical Estimator Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use inline execution unless the user or applicable repository instructions authorize delegation.

**Goal:** 将隔夜普查的日等权点估计与置信区间收敛到同一共享实现，保留现有 CLI 的正确统计语义与各入口默认随机种子。

**Architecture:** `common.stats` 新增日等权包装函数，底层 `date_cluster_bootstrap` 保持事件行等权语义。`core.cell_stats` 调用新函数；CLI 保留兼容包装器，`_stat_cell` 直接使用 core 的正确结果，不再二次覆盖区间。

**Tech Stack:** Python、pandas、NumPy、现有 `Interval` dataclass、pytest；不新增依赖。

**Design:** [主开发设计：工作包 A](../specs/2026-09-06-research-reliability-and-system-evolution-design.md#5-工作包-a统计口径收敛)。

**Status:** 未实施。文中的代码是实施方案及测试示例，不是已修改的源码。基线为 `cfd1371`。

---

## 0. 范围与验收前提

此次只修正统计入口的一致性。新闻语义、执行评估、交易规则、研究三门、召回、BUY、任务编排均不在该补丁中。

关键事实：CLI 当前已经通过 `_day_series → day_equal_ci` 修正了区间。因此禁止删除上层补偿却忘记修 core；禁止以此次修正为理由宣称全部历史普查报告错误。

`core.DEFAULT_SEED=20260828`，`common.stats.DEFAULT_SEED=20260803`。原 CLI 用公共层 seed，必须保持；相同 seed 的入口才要求严格数值相等。

开发开始时检查工作树。存在用户对 `.claude/skills/scan-market/pinned.jsonc` 的修改，不加入任何本任务提交。源码实施若需要隔离，使用项目认可的独立 worktree，并确保依赖可用；本次文档交付没有创建 worktree。

```bash
export AUTORESEARCH_ENGINE=codex
git status --short
uv run --no-sync python -m pytest -q \
  tests/common/test_stats.py \
  tests/research/test_oc_core.py
```

预期：既有测试通过。若基线失败，先记录失败与本任务的相关性；不要把未通过的基线写成通过。

## 1. 文件职责

| 文件 | 操作 | 职责 |
|---|---|---|
| `autoresearch/common/stats.py` | 修改 | 新增日等权聚合包装，不改旧 bootstrap |
| `autoresearch/research/overnight_census/core.py` | 修改 | `cell_stats` 区间与均值同权重 |
| `autoresearch/research/overnight_census/__main__.py` | 修改 | 复用新原语，保持 CLI seed，移除二次补偿 |
| `tests/common/test_day_equal_stats.py` | 新增 | 数学不变量、缺失与确定性测试 |
| `tests/research/test_oc_statistics_contract.py` | 新增 | core/CLI 同口径与既有 CLI 语义保持 |

## Task 1：新增明确估计对象的统计原语

**Files:**

- Modify: `autoresearch/common/stats.py`，放在 `date_cluster_bootstrap` 后。
- Create: `tests/common/test_day_equal_stats.py`。

- [ ] **Step 1：写入以下失败测试。**

```python
import pandas as pd
import pytest

from autoresearch.common import stats


def unbalanced_frame():
    rows = []
    for i, day in enumerate(pd.bdate_range("2026-01-05", periods=40)):
        value, count = (1.0, 100) if i < 20 else (-1.0, 1)
        rows.extend({"date": day.strftime("%Y%m%d"), "value": value}
                    for _ in range(count))
    return pd.DataFrame(rows)


def test_day_equal_estimator_does_not_weight_busy_days_more():
    result = stats.day_equal_bootstrap(unbalanced_frame(), "value")
    assert result.point == pytest.approx(0.0)
    assert result.lo < 0 < result.hi
    assert result.n == 2020
    assert result.n_clusters == 40


def test_replicating_one_days_complete_population_changes_no_estimate():
    frame = unbalanced_frame()
    duplicated = pd.concat([frame, frame[frame["date"] == "20260105"]])
    a = stats.day_equal_bootstrap(frame, "value", seed=7)
    b = stats.day_equal_bootstrap(duplicated, "value", seed=7)
    assert (a.point, a.lo, a.hi) == (b.point, b.lo, b.hi)
    assert b.n > a.n
    assert a.n_clusters == b.n_clusters


def test_empty_and_single_day_do_not_fabricate_intervals():
    empty = pd.DataFrame({"date": [], "value": []})
    a = stats.day_equal_bootstrap(empty, "value")
    assert (a.point, a.lo, a.hi, a.n, a.n_clusters) == (None, None, None, 0, 0)
    single = pd.DataFrame({"date": ["20260105", "20260105"], "value": [1, 3]})
    b = stats.day_equal_bootstrap(single, "value")
    assert (b.point, b.lo, b.hi, b.n, b.n_clusters) == (2.0, None, None, 2, 1)


def test_invalid_rows_are_excluded_without_inventing_dates():
    frame = pd.DataFrame({
        "date": ["20260105", None, "20260106", ""],
        "value": [1.0, 9.0, "bad", 9.0],
    })
    result = stats.day_equal_bootstrap(frame, "value")
    assert (result.point, result.n, result.n_clusters) == (1.0, 1, 1)
    assert result.lo is None and result.hi is None


def test_missing_columns_and_invalid_bootstrap_parameters_raise():
    frame = unbalanced_frame()
    with pytest.raises(ValueError):
        stats.day_equal_bootstrap(frame.drop(columns="date"), "value")
    with pytest.raises(ValueError):
        stats.day_equal_bootstrap(frame, "missing")
    with pytest.raises(ValueError):
        stats.day_equal_bootstrap(frame, "value", n_boot=0)
    with pytest.raises(ValueError):
        stats.day_equal_bootstrap(frame, "value", alpha=1.0)


def test_fixed_seed_and_row_order_are_stable():
    frame = unbalanced_frame()
    a = stats.day_equal_bootstrap(frame, "value", seed=13)
    b = stats.day_equal_bootstrap(frame.iloc[::-1], "value", seed=13)
    assert a == b


def test_old_event_weighted_primitive_retains_its_meaning():
    frame = unbalanced_frame()
    old = stats.date_cluster_bootstrap(frame, "value")
    assert old.point == pytest.approx(1980 / 2020)
    new = stats.day_equal_bootstrap(frame, "value")
    assert new.point == pytest.approx(0.0)
```

- [ ] **Step 2：确认测试为 RED。**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q tests/common/test_day_equal_stats.py
```

预期：新函数缺失造成失败；不能因命令路径错误、依赖缺失等无关原因将其记为正确 RED。

- [ ] **Step 3：加入完整包装函数。**

复用同文件已有的 `np`、`pd`、`Interval` 和默认常量，无需新 import。

```python
def day_equal_bootstrap(frame: pd.DataFrame, value_col: str, *,
                        date_col: str = "date", n_boot: int = DEFAULT_BOOT,
                        alpha: float = DEFAULT_ALPHA,
                        seed: int = DEFAULT_SEED) -> Interval:
    """日内事件均值再跨日等权；n 为有效事件数，n_clusters 为有效日数。"""
    if frame is None or value_col not in frame or date_col not in frame:
        raise ValueError("day_equal_bootstrap requires value and date columns")
    if isinstance(n_boot, bool) or not isinstance(n_boot, int) or n_boot < 1:
        raise ValueError("n_boot must be a positive integer")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between zero and one")
    values = pd.to_numeric(frame[value_col], errors="coerce")
    dates = frame[date_col].astype(str).str.strip()
    keep = values.notna() & ~dates.isin(["", "nan", "NaT", "None"])
    work = pd.DataFrame({
        "__date": dates[keep].to_numpy(dtype=object),
        "__value": values[keep].to_numpy(dtype=float),
    })
    if not np.isfinite(work["__value"].to_numpy()).all():
        raise ValueError("day_equal_bootstrap requires finite observed values")
    daily = work.groupby("__date", as_index=False, sort=True)["__value"].mean()
    interval = date_cluster_bootstrap(
        daily, "__value", date_col="__date", n_boot=n_boot, alpha=alpha, seed=seed,
    )
    return Interval(
        interval.point, interval.lo, interval.hi,
        len(work), interval.n_clusters,
        f"day_equal/{interval.method}", interval.alpha,
    )
```

金额或收益无穷值是无效输入，显式拒绝；NaN 和不可解析数值沿现有清洗规则排除。此函数不做真实交易日历校验，日期身份与日历由上游数据契约负责；不可把它解释成连续交易日 block bootstrap。

- [ ] **Step 4：验证新旧统计原语。**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q \
  tests/common/test_day_equal_stats.py \
  tests/common/test_stats.py
```

预期：新测试和既有统计测试通过。

- [ ] **Step 5：仅提交本任务文件。**

```bash
git add autoresearch/common/stats.py tests/common/test_day_equal_stats.py
git diff --cached --check
git commit -m "feat(stats): add explicit day-equal bootstrap estimator"
```

执行提交前核对暂存区没有其他任务文件。

## Task 2：将 core 接入正确估计对象

**Files:**

- Modify: `autoresearch/research/overnight_census/core.py::cell_stats`。
- Create: `tests/research/test_oc_statistics_contract.py`。

- [ ] **Step 1：写入入口回归测试。**

```python
import pandas as pd
import pytest

from autoresearch.common import stats
from autoresearch.research.overnight_census import core
from autoresearch.research.overnight_census import __main__ as cli


def uneven_events():
    rows = []
    for i, day in enumerate(pd.bdate_range("2026-01-05", periods=40)):
        value, count = (1.0, 100) if i < 20 else (-1.0, 1)
        rows.extend({"date": day.strftime("%Y%m%d"), "gap_pp": value}
                    for _ in range(count))
    return pd.DataFrame(rows)


def test_core_mean_and_ci_describe_same_estimator():
    frame = uneven_events()
    result = core.cell_stats(frame, value_col="gap_pp", seed=19)
    expected = stats.day_equal_bootstrap(frame, "gap_pp", seed=19)
    assert result["mean_pp"] == pytest.approx(expected.point)
    assert (result["ci_low_pp"], result["ci_high_pp"]) == (expected.lo, expected.hi)
    assert result["ci_low_pp"] < 0 < result["ci_high_pp"]
    assert result["n_events"] == 2020 and result["n_days"] == 40


def test_core_and_existing_cli_agree_with_same_seed():
    frame = uneven_events()
    result = core.cell_stats(frame, value_col="gap_pp", seed=stats.DEFAULT_SEED)
    lo, hi, method = cli.day_equal_ci(frame)
    assert (result["ci_low_pp"], result["ci_high_pp"]) == (lo, hi)
    assert method == "date_cluster_bootstrap(day-equal)"


def test_non_finite_returns_are_rejected():
    frame = pd.DataFrame({"date": ["20260105"], "gap_pp": [float("inf")]})
    with pytest.raises(ValueError):
        core.cell_stats(frame, value_col="gap_pp")
```

- [ ] **Step 2：确认 core 回归测试为 RED。**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q tests/research/test_oc_statistics_contract.py
```

预期：不等日样本的 core 区间与正确日等权区间不一致。CLI 已有的正确行为不应被误写成失败预期。

- [ ] **Step 3：替换 core 的区间调用。**

原调用：

```python
interval = _stats.date_cluster_bootstrap(work, "__v", date_col="__date", seed=seed)
```

替换为：

```python
interval = _stats.day_equal_bootstrap(work, "__v", date_col="__date", seed=seed)
```

将 `cell_stats` docstring 中旧的“已知口径不一致”整段替换为以下说明；其他成本、样本量、逐年与频率定义保持不变。

```text
- 区间走 common.stats.day_equal_bootstrap，与 mean_pp 一样先日内均值、再跨日等权。
  n_events 是有效事件行数，n_days 是有效观测日数；事件数量不改变日权重。
  该区间仍是独立日重采样，不是连续交易日 moving-block 区间。
  seed 由本函数显式传入；单日没有可估计的跨日区间。
```

在 core 的常量区增加统计实现版本，供新研究产物记录；不更改 scan 的产物版本。

```python
STATISTICS_VERSION = "overnight.day_equal.v2"
```

- [ ] **Step 4：验证 core 的现有边界。**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q \
  tests/common/test_day_equal_stats.py \
  tests/research/test_oc_statistics_contract.py \
  tests/research/test_oc_core.py
```

预期：全部通过，包括原有 seed 透传、空样本、成本和年度稳定性测试。

- [ ] **Step 5：提交 core 修正与回归测试。**

```bash
git add autoresearch/research/overnight_census/core.py tests/research/test_oc_statistics_contract.py
git diff --cached --check
git commit -m "fix(research): align overnight core confidence intervals with daily means"
```

## Task 3：收敛 CLI，同时保留既有 seed 与输出语义

**Files:**

- Modify: `autoresearch/research/overnight_census/__main__.py`。
- Modify: `tests/research/test_oc_statistics_contract.py`。

- [ ] **Step 1：追加不依赖新实现计算期望的兼容测试。**

```python
def test_cli_preserves_old_daily_weighted_formula():
    frame = uneven_events()
    daily = frame.groupby("date", as_index=False)["gap_pp"].mean()
    expected = stats.date_cluster_bootstrap(daily, "gap_pp")
    lo, hi, method = cli.day_equal_ci(frame)
    assert (lo, hi) == (expected.lo, expected.hi)
    assert method == "date_cluster_bootstrap(day-equal)"


def test_cli_stat_cell_uses_correct_core_result_without_second_override(monkeypatch):
    frame = uneven_events()
    expected = core.cell_stats(frame, value_col="gap_pp", seed=stats.DEFAULT_SEED)

    def no_compensation(*args, **kwargs):
        raise AssertionError("CLI must not recompute a replacement interval")

    monkeypatch.setattr(cli, "day_equal_ci", no_compensation)
    result = cli._stat_cell({
        "family": "synthetic", "kind": "main", "timing": "R",
        "sample_kind": "event", "rows": frame,
    })
    actual = result["stats"]
    assert (actual["ci_low_pp"], actual["ci_high_pp"]) == (
        expected["ci_low_pp"], expected["ci_high_pp"],
    )
    assert actual["ci_method"] == "date_cluster_bootstrap(day-equal)"


def test_cli_empty_wrapper_remains_compatible():
    assert cli.day_equal_ci(None) == (
        None, None, "date_cluster_bootstrap(day-equal, n_days<2 → 无区间)",
    )


def test_cli_oracle_classification_remains_separate_from_statistics():
    result = cli._stat_cell({
        "family": "synthetic", "kind": "main", "timing": "X",
        "sample_kind": "event", "rows": uneven_events(),
    })
    assert result["actionability"] == cli.X_ORACLE
    assert result["verdict"] != cli.POS_HIST
```

- [ ] **Step 2：执行测试确认重复补偿路径仍被调用。**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q tests/research/test_oc_statistics_contract.py
```

预期：`test_cli_stat_cell_uses_correct_core_result_without_second_override` 失败，其余兼容测试可以通过。

- [ ] **Step 3：用以下完整函数替换 CLI 的两处入口。**

```python
def day_equal_ci(rows: pd.DataFrame, value_col: str = MAIN_VALUE_COL,
                 date_col: str = "date") -> tuple[float | None, float | None, str]:
    """兼容入口：由公共原语提供日等权区间，保留既有返回格式与 seed。"""
    if rows is None or len(rows) == 0 or value_col not in rows.columns:
        return None, None, "date_cluster_bootstrap(day-equal, n_days<2 → 无区间)"
    iv = _stats.day_equal_bootstrap(rows, value_col, date_col=date_col)
    if iv.n_clusters < 2:
        return None, None, "date_cluster_bootstrap(day-equal, n_days<2 → 无区间)"
    return iv.lo, iv.hi, "date_cluster_bootstrap(day-equal)"


def _stat_cell(cell: dict) -> dict:
    """一格一次统计；CLI 保持公共统计层的既有默认 seed。"""
    rows, kind = cell.get("rows"), cell.get("sample_kind", "event")
    source = (rows if rows is not None else
              pd.DataFrame({"date": [], MAIN_VALUE_COL: []}))
    st = core.cell_stats(source, value_col=MAIN_VALUE_COL, sample_kind=kind,
                         seed=_stats.DEFAULT_SEED)
    lo, hi = st["ci_low_pp"], st["ci_high_pp"]
    st["ci_method"] = (
        "date_cluster_bootstrap(day-equal)" if st["n_days"] >= 2 else
        "date_cluster_bootstrap(day-equal, n_days<2 → 无区间)"
    )
    rel = (core.cell_stats(rows, value_col=REL_VALUE_COL, sample_kind=kind,
                          seed=_stats.DEFAULT_SEED)
           if rows is not None and REL_VALUE_COL in rows.columns else None)
    st["rel_mean_pp"] = (rel or {}).get("mean_pp")
    state = five_state(st, ci_low=lo, ci_high=hi)
    act = actionability_of(cell)
    out = dict(cell)
    out["stats"] = st
    out["state"] = state
    out["actionability"] = act
    out["verdict"] = oracle_verdict(state) if act == X_ORACLE else state
    out.pop("rows", None)
    out["_rows"] = rows
    return out
```

删除 CLI 中已经无消费者的 `_day_series` 函数。先运行下列搜索，确认除了本模块和说明文档没有实际调用；若出现新的调用者，保留兼容包装直到调用者迁移完成。

```bash
rg -n '_day_series' autoresearch tests
```

CLI 模块 docstring 第 3 点改为“日等权区间由公共原语计算，core 与 CLI 显式选择各自既有 seed，不再在 CLI 重算覆盖”。保留对五态、ORACLE 和成本假设的现有说明。

在 `run_census` 的 `meta` 字典创建完成后、调用渲染函数之前增加版本字段。现有 `render_json` 完整保留 `meta`，因此无需另建版本文件或改动历史输出。

```python
meta["statistics_version"] = core.STATISTICS_VERSION
```

- [ ] **Step 4：运行相关测试与静态检查。**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q \
  tests/common/test_stats.py \
  tests/common/test_day_equal_stats.py \
  tests/research/test_oc_core.py \
  tests/research/test_oc_families.py \
  tests/research/test_oc_statistics_contract.py \
  tests/contracts/test_layering.py
git diff --check
```

预期：通过。若渲染测试变化，先确认是否只来自被允许的正确 core 区间变化；不修改样本门、成本门或 ORACLE 定义来迁就测试。

- [ ] **Step 5：提交 CLI 收敛。**

```bash
git add autoresearch/research/overnight_census/__main__.py tests/research/test_oc_statistics_contract.py
git diff --cached --check
git commit -m "refactor(research): share daily estimator across overnight entrypoints"
```

## Task 4：完成验证与交付记录

**Files:**

- Modify: 本计划的任务状态与实施验证记录。
- 不修改任何历史研究读数或扫描 run。

- [ ] **Step 1：核对变更范围。**

```bash
git status --short
git log -3 --oneline
```

预期：三个任务提交只涉及统计模块及对应测试；用户持仓清单保留原状。

- [ ] **Step 2：合入前运行全量测试。**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q
```

预期：全量通过；环境、外部服务或既有失败要明确分类。局部通过不能写成全量通过。

- [ ] **Step 3：更新本文验证记录。**

记录实施 commit、测试命令、实际通过数量、是否有未解决失败、是否运行历史重算。默认不运行历史普查，因为当前 `--out` 只控制 Markdown，JSON 仍写固定研究路径，直接执行可能覆盖既有研究结果。

若后续单独开展历史重算，应先实现成对的独立 Markdown/JSON 输出目录，并确认不会覆盖已有文件；该输出功能不在本首期补丁中。不要误把 `--out` 当作所有产物已隔离。

- [ ] **Step 4：提交验证记录并交付。**

```bash
git add docs/superpowers/plans/2026-09-06-statistical-estimator-convergence.md
git diff --cached --check
git commit -m "docs: record statistical estimator convergence validation"
```

## 2. 实施完成后的预期行为

不等日事件数不会扭曲 core 的日等权置信区间。CLI 保持原本正确的日等权数值及默认 seed，且无需在计算后覆盖 core 区间。通用事件加权 bootstrap 继续服务原消费者。

此次不产生投资结论、不更改选股行为、不自动重算历史报告。若有研究代码直接依赖旧 core 的错误区间，其新计算结果会改变；这属于修正，应按代码版本区分并明确记录。

## 3. 文档交付验证记录

当前记录仅针对方案：已检查文件归属、已有 CLI 补偿、两个 seed 和历史输出覆盖风险；未执行上述开发任务。

2026-09-06 文档验证：20 个本地链接有效；方案中的函数、测试代码通过 Python 语法检查，15 段 shell 命令通过 `bash -n`。在独立 Python 进程中加载拟新增函数、对 core 做内存替换并加载拟修改的 CLI 函数，14 个文档回归样例全部通过。该过程没有修改仓库源码，也没有运行真实行情普查，不能替代后续实施时的 RED/GREEN 与全量验证。
