# 波 1a · 全套尺子对齐超短(T+1/T+2) · 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把选股校准、retro 归因、全部学习账本的**主口径**从 T+1(`fwd_1_oo`)切到超短主尺 `fwd_2_oc`(D+1 开盘买→D+2 收盘卖),T+1 降副、T+5/T+10 降参考;历史账本用缓存价格回填新列,n 不清零;两个 T+5 提案记 rejected。

**Architecture:** 单一定义源 `factor_lab.forward_returns` 加 `fwd_2_oc`/`hi_2_oc` 两窗 → 校准(calibrate/calibrate_regimes/GBDT label)与 retro 主归因切主尺 → 各账本**加列不改旧列**、主裁决/主排序切 T+2 → 逐日重跑 `retro.attribute` 即回填(账本都是从 attribution.csv 重聚合的,天然幂等)。成熟门不变(fwd_2_oc 与 fwd_1_oo 同在 D+2 收盘成熟),retro 节奏不变。

**Tech Stack:** Python 3 + pandas + pytest(uv);数据走 factor_lab pickle 缓存(`context/factor_lab/cache/daily/`)。

**Spec:** `docs/specs/2026-07-10-ultrashort-t2-inst-progress-design.md` §0/§1(用户 2026-07-10 拍板:持仓 1~2 日)。

## Global Constraints

- 一律 `uv run --no-sync python ...`,仓库根目录跑。
- 每 task 收尾:`uv run --no-sync python -m ruff check .` + `uv run --no-sync python -m pytest tests/ -q` 全绿才 commit。
- **加列不删列**:所有账本/CSV 的 t5/t1 旧列一律保留(参考口径),只新增 t2 列并把**主裁决/主排序**切过去。
- 主尺阈值沿用 spec:winner = T+2 前 10 分位 ∧ ≥3%;十分位价差 clip ±0.30(2 日容 10cm 两连板)。
- L4 卡目标价语义/hi_10 触价校准**本计划不动**(属波 1b 计划);GBDT 模型 pkl 不重训(线上 L2 是 ML-free 分层器,champion 休眠)。
- commit 中文 conventional + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`;进度记 `.superpowers/sdd/progress.md`。

---

### Task 1: `forward_returns` 加 `fwd_2_oc` + `hi_2_oc`,FWDS 收编

**Files:**
- Modify: `autoresearch/research/factor_lab.py:224-252`(`forward_returns`)、`:451`(FWDS)
- Test: `tests/research/test_factor_lab.py`(追加)

**Interfaces:**
- Consumes: 既有 `piv` 价格 pivot(`open/close/high/pct_chg`)。
- Produces: `forward_returns` 结果帧新增两列——`fwd_2_oc = close[D+2]/open[D+1] − 1`、`hi_2_oc = max(high[D+1..D+2])/open[D+1] − 1`;`FWDS = ["fwd_1_cc","fwd_1_oo","fwd_2_oc","fwd_5_oc","fwd_10_oc"]`。后续所有 task 消费这两列。

- [ ] **Step 1: 写失败测试**(追加到 `tests/research/test_factor_lab.py` 末尾)

```python
def test_forward_returns_fwd2_hi2():
    """超短主尺两窗:fwd_2_oc=close[D+2]/open[D+1]−1;hi_2_oc=max(high[D+1..D+2])/open[D+1]−1。"""
    import numpy as np
    import pandas as pd

    import autoresearch.research.factor_lab as fl

    P = ["20260701", "20260702", "20260703", "20260706", "20260707", "20260708"]
    codes = ["000001", "600519"]

    def _piv(rows):
        return pd.DataFrame(rows, index=codes, columns=P, dtype=float)

    piv = {
        "open":    _piv([[10, 10.5, 11.0, 11.5, 12, 12.5], [100, 101, 102, 103, 104, 105]]),
        "close":   _piv([[10.2, 10.8, 11.55, 11.6, 12.1, 12.6], [100.5, 101.5, 103, 103.5, 104.5, 105.5]]),
        "high":    _piv([[10.3, 11.0, 11.9, 11.7, 12.2, 12.7], [101, 102, 104, 104, 105, 106]]),
        "pct_chg": _piv([[1, 2, 3, 1, 1, 1], [1, 1, 1, 1, 1, 1]]),
    }
    fr = fl.forward_returns(piv, P, "20260701", fwd=10)
    # D=07-01 → o1=open[07-02];fwd_2_oc 用 close[07-03];hi_2 用 high[07-02..07-03]
    assert np.isclose(fr.loc["000001", "fwd_2_oc"], 11.55 / 10.5 - 1.0)
    assert np.isclose(fr.loc["000001", "hi_2_oc"], 11.9 / 10.5 - 1.0)
    assert np.isclose(fr.loc["600519", "fwd_2_oc"], 103 / 101 - 1.0)
    assert np.isclose(fr.loc["600519", "hi_2_oc"], 104 / 101 - 1.0)
    assert "fwd_2_oc" in fl.FWDS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/research/test_factor_lab.py::test_forward_returns_fwd2_hi2 -v`
Expected: FAIL,`KeyError: 'fwd_2_oc'`

- [ ] **Step 3: 实现**

`forward_returns` 中 `res["fwd_1_oo"] = ...` 行之后插两行(`h` 已解包在 `c, o, h = ...`):

```python
    res["fwd_2_oc"] = col(c, 2) / o1 - 1.0            # 超短主尺:D+1 开买 → D+2 收卖(成熟同 fwd_1_oo)
    res["hi_2_oc"] = pd.concat([col(h, 1), col(h, 2)], axis=1).max(axis=1) / o1 - 1.0   # 2 日触价 MFE
```

docstring 首行补口径:`oc/ocN=开盘到第N日收盘` 后加 `;fwd_2_oc=超短主尺(2026-07-10 用户裁定持仓 1~2 日)`。
FWDS 改为:

```python
FWDS = ["fwd_1_cc", "fwd_1_oo", "fwd_2_oc", "fwd_5_oc", "fwd_10_oc"]
```

- [ ] **Step 4: 跑测试 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/research/test_factor_lab.py -v && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿(加列不影响既有断言;若某测试断言了 FWDS 全序列,按新序列更新)。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/research/factor_lab.py tests/research/test_factor_lab.py
git commit -m "feat(research): forward_returns 加 fwd_2_oc/hi_2_oc 超短主尺两窗(成熟仍 D+2)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 选股校准切主尺 + `calibrate-regimes` CLI + weights 重算

**Files:**
- Modify: `autoresearch/research/factor_lab.py`:`_build_calib_panel`(:589)、`calibrate`(:655)、`calibrate_regimes`(:685)、`GBDT_LABEL`(:738)、`evaluate` 主排序/十分位/半样本块(:494/:521/:543)、CLI `main`(:964+)
- Test: `tests/research/test_factor_lab.py`(追加契约测试)

**Interfaces:**
- Consumes: Task 1 的 `fwd_2_oc` 列。
- Produces: `calibrate()/calibrate_regimes()/_build_calib_panel()` 的 `label_col` 默认 = `"fwd_2_oc"`;`GBDT_LABEL = "fwd_2_oc"`;`context/factor_lab/weights.json` 重算(meta.horizon 标超短);CLI 新 mode `calibrate-regimes`。线上 `scoring._load_weights` 零改动直接吃新 weights。

- [ ] **Step 1: 写失败契约测试**

```python
def test_ultrashort_label_defaults():
    """主尺契约:校准/GBDT label 默认 fwd_2_oc(2026-07-10 用户裁定);IC 表主排序同尺。"""
    import inspect

    import autoresearch.research.factor_lab as fl

    assert inspect.signature(fl.calibrate).parameters["label_col"].default == "fwd_2_oc"
    assert inspect.signature(fl.calibrate_regimes).parameters["label_col"].default == "fwd_2_oc"
    assert inspect.signature(fl._build_calib_panel).parameters["label_col"].default == "fwd_2_oc"
    assert fl.GBDT_LABEL == "fwd_2_oc"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/research/test_factor_lab.py::test_ultrashort_label_defaults -v`
Expected: FAIL(默认仍 fwd_1_oo)。

- [ ] **Step 3: 实现(六处)**

① `_build_calib_panel(frames, label_col: str = "fwd_2_oc")`;docstring `(fwd_2_oc 超短主尺默认 / fwd_1_oo / fwd_5_oc / fwd_10_oc 多 horizon)`。
② `calibrate(cap_floor=30.0, k=200.0, label_col="fwd_2_oc", ...)`;函数内 horizon 行改:

```python
    horizon = "fwd_2_oc(超短:D+1开→D+2收)" if label_col == "fwd_2_oc" else label_col
```

docstring 尾句 `label_col 默认 T+1 开到开(parity);可换多 horizon` → `label_col 默认超短主尺 fwd_2_oc(2026-07-10 裁定);可换多 horizon`。
③ `calibrate_regimes(..., label_col="fwd_2_oc", ...)` + 同款 horizon 行。
④ `GBDT_LABEL = "fwd_2_oc"                          # 超短主尺,与 calibrate 同口径(可交易、无前视)`。
⑤ `evaluate`:半样本/t/hit 块条件 `if fwdcol == "fwd_1_cc":` → `if fwdcol == "fwd_2_oc":`;十分位段注释 `(T+1 cc,买得到的)` → `(超短主尺 fwd_2_oc,买得到的)`,`r = sub["fwd_1_cc"].clip(-0.21, 0.21)` → `r = sub["fwd_2_oc"].clip(-0.30, 0.30)`(2 日容 10cm 两连板);排序 `sortcol = "ICIR_fwd_2_oc"`,其注释 `(按超短主尺 ICIR 降序)`。
⑥ CLI:`choices=["harvest", "eval", "calibrate", "calibrate-regimes", "train"]`,help 里 `calibrate=主尺(fwd_2_oc)IC→weights.json;calibrate-regimes=同尺+regime分块`;分发处加:

```python
    elif args.mode == "calibrate-regimes":
        calibrate_regimes(args.cap_floor, k=args.k)
```

- [ ] **Step 4: 跑测试 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/research/ -q && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿(若既有测试冻结了旧默认/旧排序列,按主尺更新断言——这是**有意破坏**,勿放宽回旧值)。

- [ ] **Step 5: 真重算 weights(107+ 成型日面板,零等待)**

Run: `uv run --no-sync python -m autoresearch.research.factor_lab calibrate-regimes`
Expected: `[calibrate] weights → context/factor_lab/weights.json`;随后
`uv run --no-sync python -c "import json;m=json.load(open('context/factor_lab/weights.json'))['meta'];print(m['horizon'],m['regime_calib'],m['regimes_present'],m['ic_global'])"`
Expected: horizon=`fwd_2_oc(超短:D+1开→D+2收)`、regimes 三块在;把 `ic_global`(尤其 momentum/north 新读数)原文记入 progress.md。若成型日缓存缺 → 先 `factor_lab harvest`(见其 CLI help)再重算。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/research/factor_lab.py tests/research/test_factor_lab.py context/factor_lab/weights.json
git commit -m "feat(research): 校准/GBDT/IC表主尺切 fwd_2_oc + calibrate-regimes CLI + weights 重算

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(若 `context/factor_lab/` 在 .gitignore 内则不 add weights.json,只提交代码——先 `git check-ignore context/factor_lab/weights.json` 确认。)

---

### Task 3: retro 主归因切 T+2(winner/bucket/验尸/配对/机判/门审计/day_ic/输入表)

**Files:**
- Modify: `autoresearch/learning/retro.py`:主归因(:57-108)、`l3_miss_autopsy`(:145-165)、`build_retro_pairs`(:168-220)、`mtm_check_guards`(:231-263)、`gate_audit`(:266-286)、`stage_stats`(:300-328)、`realized_returns`(:371-403)、`write_retro_input`(:518-572)
- Modify: `build_retro_pairs` 产出键的下游消费者(先 `grep -rn "fail_fwd5\|win_fwd5\|win_bucket5" autoresearch/ tests/` 找全,预计 M1/M2 蒸馏侧 + `tests/learning/test_retro_pairs.py`)
- Test: `tests/learning/test_retro.py`、`test_retro_depth.py`、`test_retro_pairs.py`(fixtures 加 `fwd_2_oc`/`hi_2_oc` 列 + 断言切主尺)

**Interfaces:**
- Consumes: Task 1 的两新列(经 `realized_returns`)。
- Produces: `attribution.csv` 新增 `fwd_2_oc`/`hi_2_oc` 列;`winner/bucket/false_positive/tradable` 全按 fwd_2_oc 判;`winner_5/bucket_5` 保留(参考);`gate_audit` 增 `ex2`;pairs 键改 `fail_fwd2/win_fwd2/win_bucket`。**全部账本(Task 5)与回填(Task 6)依赖本任务的 attribution 新列。**

- [ ] **Step 1: 写失败测试**(核心断言,并入 `tests/learning/test_retro.py`;fixtures 构帧时同时给 fwd_1_oo 与 fwd_2_oc,两列故意选出**不同**赢家)

```python
def test_winner_follows_fwd2_not_fwd1():
    """主归因主尺=fwd_2_oc:T+1 大涨但 T+2 回吐的票不是赢家;反之才是。"""
    import numpy as np
    import pandas as pd

    from autoresearch.learning import retro

    n = 40
    realized = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "fwd_1_oo": [0.08] + [0.0] * (n - 1),            # 000000 只赢在 T+1
        "fwd_2_oc": [0.0] + [0.06] + [0.001] * (n - 2),  # 000001 赢在 T+2(主尺)
        "fwd_5_oc": [np.nan] * n,
        "buyable": [True] * n,
    })
    l1 = pd.DataFrame({"code": realized["code"], "composite": 0.5, "recalled": False})
    attr = retro.attribute_frame(realized, l1, buylist={})   # 若实际函数名不同,按 grep 结果替换
    w = attr[attr["winner"]]
    assert set(w["code"]) == {"000001"}
```

> 落笔前先 `grep -n "def attribute" autoresearch/learning/retro.py` 确认纯函数名(excerpt 显示主归因逻辑在一个接收 `realized/l1/buylist/top_q/bot_q/abs_thresh` 的函数内;测试调那个纯函数,不走网络)。既有测试里已有它的调用范例,沿用其调用姿势。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_retro.py -v -k fwd2`
Expected: FAIL(winner 仍按 fwd_1_oo 选出 000000)。

- [ ] **Step 3: 实现(八处,主尺替换)**

① 主归因:`m["tradable"] = m["buyable"].fillna(True) & m["fwd_2_oc"].notna()`;`hi/lo` 分位改在 `trad["fwd_2_oc"]` 上取;`m["winner"] = m["tradable"] & (m["fwd_2_oc"] >= hi) & (m["fwd_2_oc"] >= abs_thresh)`;`false_positive` 条件 `r["fwd_2_oc"] <= lo`。`winner_5/bucket_5` 块**原样保留**(注释改「T+5 参考口径(降级保留)」)。
② `realized_returns`:cols 列表改

```python
    cols = ["code", "fwd_1_oo", "fwd_2_oc", "fwd_5_oc", "fwd_10_oc", "hi_2_oc", "hi_10_oc", "buyable", "gap_d1"]
```

(forward_returns 已产两新列;hi_2_oc 走 forward_returns,原 hi_10 手工块不动;docstring `fwd_1_oo/fwd_5_oc` → `fwd_1_oo/fwd_2_oc/fwd_5_oc`。)
③ `stage_stats`:`day_ic_composite` 相关列 `fwd_1_oo` → `fwd_2_oc`(rank corr 两处)。
④ `mtm_check_guards`:`mkt = pd.to_numeric(attr.get("fwd_2_oc"), errors="coerce")`;docstring「当日 fwd_1」→「当日 fwd_2(超短主尺)」。
⑤ `gate_audit`:cols=`["code","check","severity","fwd_1_oo","ex1","fwd_2_oc","ex2","fwd_5_oc","ex5"]`;加 `m2 = pd.to_numeric(a.get("fwd_2_oc"), errors="coerce").mean() if "fwd_2_oc" in a.columns else float("nan")`;merge 列表加 `fwd_2_oc`;`out["ex2"] = (pd.to_numeric(out.get("fwd_2_oc"), errors="coerce") - m2) if "fwd_2_oc" in out.columns else None`。
⑥ `l3_miss_autopsy`:cols 里 `fwd_5_oc` → `fwd_2_oc`;`w5 = a.get("winner", ...)`(主尺赢家,T+2)——变量名顺手改 `w2`;排序列 `fwd_2_oc`;docstring「∧ winner_5」→「∧ winner(T+2 主尺)」。
⑦ `build_retro_pairs`:`"fwd_5_oc"` → `"fwd_2_oc"`、`_fwd5` → `_fwd2`、`bucket_5` → `bucket`、`winner_5` → `winner`,产出键 `fail_fwd5→fail_fwd2`、`win_fwd5→win_fwd2`、`win_bucket5→win_bucket`;docstring 里三处 T+5 → T+2(「fwd_2 与主归因同尺、D+2 即成熟 → 配对当日可产,不再等 T+5」)。**随后按 Step 1 的 grep 结果同步全部下游消费者**(M1 蒸馏 prompt 字段名/M2 adjudicate/测试)。
⑧ `write_retro_input`:开头行 `当日 composite IC(vs fwd_1_oo)` → `(vs fwd_2_oc)`;`fcols` 里 `fwd_1_oo` → `fwd_2_oc`;三处 `sort_values("fwd_1_oo"...)` → `"fwd_2_oc"`;「赢家(前10%∧≥3%)」→「赢家(T+2 前10%∧≥3%)」。T+5 盲区节**不动**。

- [ ] **Step 4: 更新既有测试 fixtures 并跑绿**

`tests/learning/test_retro*.py` 全部构帧处补 `fwd_2_oc`(数值直接复用原 fwd_1_oo 的设定即可保住原断言语义)、必要处补 `hi_2_oc=NaN`;pairs 测试键名同步。
Run: `uv run --no-sync python -m pytest tests/learning/ -q && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/learning/retro.py tests/learning/
git commit -m "feat(learning): retro 主归因/验尸/配对/机判/门审计切 T+2 主尺(winner_5 降参考)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(若 grep 出 retro 之外的 pairs 消费者文件,一并 add。)

---

### Task 4: stage_eval / l2_eval 主口径切换(channel_eval 生产端加 t2 列)

**Files:**
- Modify: `autoresearch/learning/stage_eval.py`(常量段 :29-30 + 全文件主口径使用处;`channel_edge` 是 `channel_eval.csv` 的生产者,加 t2 列)
- Modify: `autoresearch/research/l2_eval.py:37`(`forward_compare` label 默认)
- Test: `tests/learning/test_stage_eval.py`、`tests/learning/test_channel_eval.py`、l2_eval 对应测试(`grep -rln forward_compare tests/`)

**Interfaces:**
- Consumes: attribution 的 `fwd_2_oc`(Task 3)。
- Produces: 常量 `_RET_MAIN = "fwd_2_oc"`(新增,主口径);`channel_eval.csv` 新增 `unique_excess_t2/mean_excess_t2/hit_rate_t2` 列(t5 列保留);`forward_compare(label_col="fwd_2_oc")` 默认。Task 5 的 channel_ledger 依赖 t2 列。

- [ ] **Step 1: 写失败测试**(并入对应测试文件;channel_eval fixtures 补 fwd_2_oc)

```python
def test_stage_eval_main_horizon_is_t2():
    from autoresearch.learning import stage_eval
    assert stage_eval._RET_MAIN == "fwd_2_oc"


def test_channel_eval_emits_t2_columns(...):   # 沿用该文件既有 fixture 构造姿势
    ev = <既有构造> 
    assert {"unique_excess_t2", "hit_rate_t2"} <= set(ev.columns)
```

```python
def test_forward_compare_default_label_t2():
    import inspect

    from autoresearch.research.l2_eval import forward_compare
    assert inspect.signature(forward_compare).parameters["label_col"].default == "fwd_2_oc"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_stage_eval.py tests/learning/test_channel_eval.py -q`
Expected: FAIL。

- [ ] **Step 3: 实现**

stage_eval 常量段改三行制:

```python
_RET_MAIN = "fwd_2_oc"  # 超短主尺:D+1开→D+2收(2026-07-10 用户裁定持仓 1~2 日)
_RET_T5 = "fwd_5_oc"    # 参考口径(降级保留,列名带 t5 的输出继续产)
_RET_T1 = "fwd_1_oo"    # 副口径(更快、噪声大)
```

全文件检索 `_RET_T5` 的使用处:凡作**主判定/主排序**处换 `_RET_MAIN`,同时把带 `_t5` 后缀的输出列保留、**镜像新增 `_t2` 列**(用 `_RET_MAIN` 算,列名 `unique_excess_t2/mean_excess_t2/hit_rate_t2`,与 t5 同式);`channel_edge` 排序主列切 t2。文件头 docstring「L3/L4 推的是 1–2 周 swing」→「持仓口径=超短 1~2 日(2026-07-10 裁定)」。
l2_eval:`forward_compare(..., label_col: str = "fwd_2_oc", ...)`,docstring 同步。

- [ ] **Step 4: 跑绿 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/learning/ tests/research/ -q && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/learning/stage_eval.py autoresearch/research/l2_eval.py tests/
git commit -m "feat(learning): stage_eval/l2_eval 主口径切 T+2,channel_eval 生产端镜像 t2 列

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 七本账主裁决切 T+2(加列不删列)

**Files:**
- Modify: `autoresearch/learning/channel_ledger.py`、`zero_buy_ledger.py`、`gate_ledger.py`、`catalyst_ledger.py`、`sector_ledger.py`、`watchlist_ledger.py`、`buy_ledger.py`
- Test: `tests/learning/test_channel_ledger.py`、`test_zero_buy_ledger.py`、`test_gate_ledger.py`、`test_catalyst_ledger.py`、`test_sector_ledger.py`、`test_watchlist_ledger.py`、`test_buy_ledger.py`

**Interfaces:**
- Consumes: attribution/channel_eval 的 t2 列(Task 3/4)。
- Produces: 各账本 `_COLS` 增 t2 列、主裁决/主排序按 t2;渲染表头同步。列约定:`channel: mean_unique_excess_t2(主排序)`、`zero_buy: mkt_fwd2(主裁决)`、`gate: mean_ex2 + hit_rate 按 ex2`、`catalyst: f2_flag/f2_unflag(成熟门从 fwd_5 提前到 fwd_2)`、`sector: horizon 默认 "fwd_2"(成熟帧 D+2)`、`watchlist: fwd_2 列`、`buy: fwd_2 列 + 基率 win2/mean2(win5 保留)`。

- [ ] **Step 1: 写失败测试**(每本账并入既有测试文件;fixtures 的 attribution/channel_eval 构造补 t2 列。核心断言样例)

```python
# test_channel_ledger.py 追加
def test_roll_sorts_by_t2(...):
    led = channel_ledger.roll(scan_root)
    assert "mean_unique_excess_t2" in led.columns
    assert led.iloc[0]["channel"] == <fixture 中 t2 最高的那路>   # 主排序=t2,即便其 t5 不是第一

# test_zero_buy_ledger.py 追加
def test_verdict_uses_fwd2(...):
    lines = zero_buy_ledger.render(led)
    assert any("mkt_fwd2" in c for c in led.columns) or "fwd_2" in "\n".join(lines)
    # 构造 fwd_2<0 而 fwd_5>0 的 0 买日 → verdict 仍应判「空仓方向正确」

# test_gate_ledger.py 追加:mean_ex2 在列 + hit_rate 由 ex2 计
# test_catalyst_ledger.py 追加:attr 只有 fwd_2_oc(无 fwd_5)也出行(成熟提前),f2_flag 数值对
# test_sector_ledger.py 追加:backfill(call, f0, f1) 默认 horizon=="fwd_2"
# test_watchlist_ledger.py 追加:行含 fwd_2
# test_buy_ledger.py 追加:rating_base_rates 输出含 win2/mean2 且按 fwd_2 计
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/ -q`
Expected: 新断言 FAIL。

- [ ] **Step 3: 实现(逐本,均为同型小改)**

① **channel_ledger**:`_COLS` 在 t5 三列前插 `mean_unique_excess_t2/mean_excess_t2/mean_hit_rate_t2`;数值化循环加 `("unique_excess_t2","mean_excess_t2","hit_rate_t2")`;agg 加三项(同式);round 循环加三列;`sort_values("mean_unique_excess_t2", ...)`;render 表头改 `| 路 | 天数 | Σunique | 边际超额T2 | 命中率T2 | 边际超额T5(参考) | ... |` 并输出对应列。旧 csv 无 t2 列 → `pd.to_numeric(alld.get(c))` 天然出 NaN,mean 跳过——**不炸旧数据**(Task 6 回填后有值)。
② **zero_buy_ledger**:`_COLS=["date","n_bought","n_stocks","mkt_fwd1","mkt_fwd2","mkt_fwd5"]`;`f2 = pd.to_numeric(df.get("fwd_2_oc"), errors="coerce") if "fwd_2_oc" in df.columns else pd.Series(dtype=float)`,行 dict 加 `mkt_fwd2`;render 表头加列;verdict 改主看 fwd_2:

```python
        v1, v2, v5 = zero["mkt_fwd1"].mean(), zero["mkt_fwd2"].mean(), zero["mkt_fwd5"].mean()
        verdict = "空仓方向正确" if (pd.notna(v2) and v2 < 0) or (pd.isna(v2) and pd.notna(v1) and v1 < 0) \
            else "⚠️ 0买日后市为正——查召回/门(失明预警),别只归因纪律"
        out.append(f"- **0买日**({len(zero)} 日):市场 fwd_1 {f(v1)}、**fwd_2 {f(v2)}(主尺)**、fwd_5 {f(v5)}(参考)→ {verdict}")
```

有买日行同步加 fwd_2。
③ **gate_ledger**:`_COLS=["check","n_days","n_fires","mean_ex1","mean_ex2","mean_ex5","hit_rate"]`;roll 内加 `f2/m2/j["ex2"]`(与 f1/f5 同式,merge 列表加 `fwd_2_oc`);agg 加 `mean_ex2=("ex2","mean")`,`hit_rate=("ex2", lambda s: ...)`(主尺拦对率);无 fwd_2 的旧行照旧 NaN。docstring「已实现 fwd」注明主尺 T+2。
④ **catalyst_ledger**:`_COLS=["date","n_flag","n_unflag","f2_flag","f2_unflag","f5_flag","f5_unflag"]`;`_day` 的硬门 `if "fwd_5_oc" not in attr.columns` → `if "fwd_2_oc" not in attr.columns and "fwd_5_oc" not in attr.columns`;merge 列取二者交集在的列;f2_* 用 fwd_2_oc 均值(主),f5_* 保留(该日无 fwd_5 列则 None)。docstring「fwd_5 对照」→「fwd_2 主对照(+fwd_5 参考)」。
⑤ **sector_ledger**:`backfill(call, frame0, frame1, horizon: str = "fwd_2")`;`render_report` 尾行 `horizon=fwd_5` → `horizon=fwd_2`。**成熟帧选择在调用侧**:`grep -rn "sector_ledger" autoresearch/ | grep -v test` 找 backfill 调用点,把「取 D 与 D+5 成分帧」改「D 与 D+2 成分帧」(交易日下标 +5 → +2),注释注明主尺。
⑥ **watchlist_ledger**:`_COLS` 加 `fwd_2`(fwd_1 后);roll 内 `f2` 读 `attr.at[code, "fwd_2_oc"]`(同 f5 式);render 表头加列。
⑦ **buy_ledger**:`_COLS` 在 fwd_1 后插 `fwd_2`;roll 行 dict 加 `"fwd_2": _a("fwd_2_oc")`;`rating_base_rates` 用 `g["fwd_2"]` 计 `win2/mean2`(键名改,`win5/mean5` 继续用 `g["fwd_5"]` 算并保留输出);render 基率行改「T+2 胜率 …(主)/ T+5 …(参考)」;买单表头 fwd_1 后加 fwd_2 列。**`hi_10/target_hit/target_calibration` 本任务不动**(波 1b)。

- [ ] **Step 4: 跑绿 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/learning/ -q && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/learning/ tests/learning/
git commit -m "feat(learning): 七本账加 t2 列并把主裁决/主排序切 T+2(t1/t5 降参考,加列不删列)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 历史回填 —— 重写全部成熟日 attribution(n 不清零)

**Files:**
- 无新代码(用既有 `retro.attribute` 幂等重写;若该入口名不同以 grep 为准)
- 产物: `context/scan/<每日>/retro/attribution.csv`(新增 fwd_2_oc/hi_2_oc 列)+ 各账本 md 重刷

**Interfaces:**
- Consumes: Task 1-5 全部落地;factor_lab daily 缓存(拉数幂等)。
- Produces: 历史账本 t2 列有值,`n_days` 不清零;momentum 等各路的 **t2 读数首度可见**(记录进 progress.md,供波 2 重审对照)。

- [ ] **Step 1: 确认回填入口**

Run: `grep -n "def attribute\|def refresh_attributions" autoresearch/learning/retro.py`
Expected: 找到按日重算并落 `retro/attribution.csv` 的入口(buy_ledger 渲染注释明示为 `retro.attribute('<date>')`;以 grep 实际签名为准)。

- [ ] **Step 2: 逐日重写(幂等,拉数走 factor_lab 缓存)**

```bash
uv run --no-sync python - <<'PY'
from pathlib import Path
from autoresearch.learning import retro
for p in sorted(Path("context/scan").iterdir()):
    if p.is_dir() and p.name[:2] == "20":
        try:
            retro.attribute(p.name)          # 未成熟日自动跳过/空返
            print("✓", p.name)
        except Exception as e:
            print("✗", p.name, str(e)[:120])
PY
```

Expected: 各成熟日 ✓;spot check:`head -1 context/scan/2026-07-06/retro/attribution.csv` 含 `fwd_2_oc`。

- [ ] **Step 3: 重刷账本并验 n 不清零**

```bash
uv run --no-sync python -m autoresearch.learning.channel_ledger
uv run --no-sync python -m autoresearch.learning.zero_buy_ledger
uv run --no-sync python -m autoresearch.learning.gate_ledger
uv run --no-sync python -m autoresearch.learning.catalyst_ledger
uv run --no-sync python -m autoresearch.learning.buy_ledger
uv run --no-sync python -m autoresearch.learning.watchlist_ledger
```

Expected: `reports/learning/channel_ledger.md` 里各路 `n_days` ≥ 回填前(不清零)且 t2 列有值;`zero_buy_ledger.md` 0 买日 fwd_2 主裁决行出现。**把 momentum 路的 t2 边际超额、0 买日 mkt_fwd2 均值抄进 progress.md**(超短口径下的第一批真读数,直接回答「0 买是纪律还是失明」的新答案)。
注:channel_eval.csv 若由 retro 诊断流产出(非 attribute 单步),对历史日补 t2 列的完整重算随下次 `retro pending`/refresh 自然发生——本步只要求 attribution 层回填完成 + 账本不炸。

- [ ] **Step 4: 无代码变更,不 commit(产物区 gitignored);progress.md 记读数**

---

### Task 7: paper NAV 持仓 10 日 → 2 日主表(10 日降副表)

**Files:**
- Modify: `autoresearch/learning/paper_nav.py`(`simulate` 默认 `hold` + `main` 双跑 + 报告双节 + 文件头 docstring)
- Test: `tests/learning/test_paper_nav.py`、`test_paper_nav_risk.py`(fixtures/断言同步)

**Interfaces:**
- Consumes: 既有 signals/prices/days 装配(不动)。
- Produces: `simulate(..., hold: int = 2)` 默认超短;`main()` 产主表(hold=2)+ 副表(hold=10,连续性对照);报告标题注明口径。X3 风险调整 NAV 若直接调 simulate,跟随默认切 2。

- [ ] **Step 1: 写失败测试**

```python
def test_simulate_default_hold_is_2():
    import inspect

    from autoresearch.learning.paper_nav import simulate
    assert inspect.signature(simulate).parameters["hold"].default == 2
```

另在既有 simulate 行为测试上补一条:同一 signals/prices 下 `hold=2` 的平仓早于 `hold=10`(校验 exit_i 语义未被改坏)。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_paper_nav.py -q`
Expected: FAIL(默认 10)。

- [ ] **Step 3: 实现**

`simulate(signals, prices, days, slot: float = 0.10, hold: int = 2)`;文件头 docstring「持有 10 个交易日后次日开盘平仓」→「**持有 2 个交易日**开盘平仓(超短主口径,2026-07-10 裁定);另出 hold=10 副表做连续性对照」。`main()` 找到 simulate 调用处改为双跑:

```python
    nav2, skipped = simulate(signals, prices, days, hold=2)
    nav10, _ = simulate(signals, prices, days, hold=10)
```

真实/影子/市场三条线均以 nav2 版为主节,末尾追加「## 副表:hold=10(旧口径连续性对照)」一节复用同一渲染(市场线与 hold 无关,可共用)。`grep -rn "simulate(" autoresearch/learning/ | grep -v test` 找到 X3 风险调整版调用处,确认其跟随主口径(显式传 `hold=2` 或吃默认)。

- [ ] **Step 4: 跑绿 + 重刷报告**

Run: `uv run --no-sync python -m pytest tests/learning/ -q && uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q && uv run --no-sync python -m autoresearch.learning.paper_nav`
Expected: 测试全绿;`reports/learning/paper_nav.md` 出双节,**把 hold=2 的 真实/影子/市场 三读数记 progress.md**(门的价值在超短口径下的首读)。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/learning/paper_nav.py tests/learning/
git commit -m "feat(learning): paper NAV 主口径持仓 10日→2日(hold=10 降副表对照)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 提案裁决 + 全仓自述文案清扫

**Files:**
- Modify: `context/knowledge/proposals.jsonl:5,7`(status open→rejected)
- Modify: `autoresearch/learning/feedback_store.py:426-449`(`_BASELINE_BODY` 主力句)、`autoresearch/common/scoring.py:92-117`(lens_momentum docstring)、`.claude/skills/scan-market/SKILL.md`(铁律「T+1 单 horizon IC 校准」句)
- Test: 既有全量(文档/文案改动靠 doc-lint 与 grep 验证)

**Interfaces:**
- Consumes: 无。
- Produces: 两提案终态 rejected(带 resolution);全仓不再自述「1–2 周 swing」为持仓意图。

- [ ] **Step 1: 确认 proposals 读端不挑 status 枚举**

Run: `grep -rn "proposals.jsonl\|\"status\"" autoresearch/learning/feedback_store.py | head -20`
Expected: 找到看板/读端;确认渲染对未知 status 是透传(现枚举只有 open/resolved)。若读端按枚举分支,给 `rejected` 加一档(与 resolved 同渲染,标记 ✗)。

- [ ] **Step 2: 打 rejected(幂等脚本)**

```bash
uv run --no-sync python - <<'PY'
import json
from pathlib import Path
p = Path("context/knowledge/proposals.jsonl")
lines = p.read_text(encoding="utf-8").splitlines()
out = []
for ln in lines:
    d = json.loads(ln)
    if d.get("id") in ("pr_20260702_001", "pr_20260709_001") and d.get("status") == "open":
        d["status"] = "rejected"
        d["resolution"] = ("2026-07-10 用户裁定持仓意图=超短1~2日,T+5尺不适用;"
                           "由超短口径波取代(docs/specs/2026-07-10-ultrashort-t2-inst-progress-design.md)")
    out.append(json.dumps(d, ensure_ascii=False))
p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("done")
PY
```

Expected: `grep -c '"rejected"' context/knowledge/proposals.jsonl` → 2。

- [ ] **Step 3: 自述清扫(三处 + 全仓扫尾)**

① feedback_store `_BASELINE_BODY` 主力句改(保留事实,改结论口径):

```python
    "- **主力**看 `main_net_ratio`(大单+特大单净占比),**散户**看 `retail_net_yi`(小单);"
    "主力净流入对 T+1/T+2 近中性、T+5/10 才最强 —— **超短主尺下不作核心多头论点,仅作共振确认**。",
```

② scoring `lens_momentum` docstring 末句「主力净流入对 T+1 近中性、对 T+5/10 最强 → 作为 swing 信号保留高权重。」→「主力净流入对 T+1/T+2 近中性、对 T+5/10 最强;超短主尺(fwd_2_oc)下其权重由 calibrate 重定,不再预设 swing 高权重。」
③ SKILL.md 铁律「诚实收尾」句「T+1 单 horizon IC 校准/训练」→「fwd_2_oc 超短主尺 IC 校准/训练(2026-07-10 裁定)」。
④ 扫尾:`grep -rn "1–2 周\|1-2 周\|1~2 周" autoresearch/ .claude/skills/scan-market/ | grep -i "swing\|持仓\|推的"` ——凡**自述持仓意图**处改「超短 1~2 日」;凡**陈述因子事实**(如"主力是 1-2 周尺度的信号")保留事实但补超短结论(同①句式)。
⑤ 「T+1 口径」旧自述点名清单(T2 复审 I-1/I-2/M-1/M-3 移交,全为文本/展示级,行号为 T2 时点近似,以内容锚定位):
- `autoresearch/scan/assemble.py:749` 诚实局限行「T+1 单 horizon IC 校准/训练」→「fwd_2_oc 超短主尺 IC 校准/训练(T+1/T+5 参考)」——**用户可见报告文本,最高优先**;
- `autoresearch/research/factor_lab.py` train_gbdt 族 4 处:`:774` 标签句 fwd_1_oo→fwd_2_oc、`:824` oos 注释同、`:572` 段横幅「calibrate(T+1 IC→…)」、`:725` GBDT 块注释「T+1(开到开)收益」;
- `factor_lab.py:552` evaluate 打印 `cols` 把 `IC_fwd_1_cc/ICIR_fwd_1_cc` 换成 `IC_fwd_2_oc/ICIR_fwd_2_oc`(表按它排序却不显示它);`:556` 十分位 header「T+1 收到收」→「超短主尺 fwd_2_oc(开→D+2收,±0.30 clip)」;
- `autoresearch/common/regime.py:6` docstring「单 horizon T+1 IC 校准」同步;
- `autoresearch/data/features.py:99`「与 factor_lab.GBDT_LABEL 同」已解耦,改「(model-zoo 自有口径,已与 factor_lab.GBDT_LABEL 解耦,后者现为 fwd_2_oc)」;
- `.claude/skills/scan-retro/retro-playbook.md:36`(T3 移交)`_retro_pairs.csv` 描述「fail(评级最高档但 T+5 跌) vs win(…但 T+5 涨)」→ T+2 口径(D+2 即产出,不再等 T+5);顺带 grep 该 playbook 其余 T+5/fwd_5 自述一并同步。

- [ ] **Step 4: 全量回归**

Run: `uv run --no-sync python -m ruff check . && uv run --no-sync python -m pytest tests/ -q`
Expected: 全绿(feedback_store 校准块若有 golden 测试,按新文案更新)。

- [ ] **Step 5: Commit**

```bash
git add context/knowledge/proposals.jsonl autoresearch/learning/feedback_store.py autoresearch/common/scoring.py .claude/skills/scan-market/SKILL.md
git commit -m "chore(learning): T+5 两提案记 rejected(用户裁定超短)+ 全仓 swing 自述清扫

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(若 proposals.jsonl 在 gitignore 内则从 add 列表去掉,裁决靠文件本身生效。)

---

## 执行顺序与依赖

严格 Task 1 → 2 → 3 → 4 → 5 → 6(回填跑动)→ 7 → 8。Task 6 无代码、纯跑动,产出**超短口径首批真读数**(momentum t2 边际超额/0 买日 mkt_fwd2/门拦对率 ex2)——这些数字是下一份计划(波 1b+波 2 机构面)重审的对照基线,务必记 progress.md。

## 不做(边界)

- L4 卡目标价/三情景/tripwires 语义与 hi_10 触价校准 → 波 1b 计划。
- 机构因子重审/接线、report_rc 消费 → 波 2 计划。
- GBDT champion 重训、召回 quota 调整 → 等 t2 读数积累后由 retro/channel_ledger 数据再提案。
