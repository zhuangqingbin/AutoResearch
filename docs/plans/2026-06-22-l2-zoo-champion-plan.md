# L2 全特征落湖 + 全 zoo 训练 + 多-horizon champion — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。步骤用 `- [ ]` 勾选。
> 关联 spec:`docs/specs/2026-06-22-l2-zoo-champion-design.md`。

**Goal:** 把 ~2 年历史落 parquet 湖,训全 20 zoo 模型 × 3 horizon,每 horizon 晋升胜线性的 champion,L2 默认用 `l2_fwd5`、缺则回落线性。

**Architecture:** P-A 落湖(种子 migrate + lake-native harvest + handler 保留 3 标签);P-B 训练(Trainer 修 kind + load_champion_any + zoo runner 故障隔离 + champion 门);L2 接线用 champion。

**Tech Stack:** pandas / pyarrow(湖)、lightgbm/xgboost/catboost/torch(zoo)、pytest 合成 fixture。

## Global Constraints(逐条来自 spec)

- 确定性、零 LLM(L2 铁律)。
- **绝不部署比线性差的模型**:champion 门 = oos rank-IC 严格 > 线性基线,否则不晋升、L2 回落线性。
- 标签三 horizon:`fwd_1_oo` / `fwd_5_oc` / `fwd_10_oc`;champion 名 `l2_fwd1` / `l2_fwd5` / `l2_fwd10`。
- 特征不补基本面;core 45 列照旧。
- 单元测试**无网络**(合成 fixture:`tests/models/_synth.py`、`tests/data/test_handler.py::synth`);真 harvest/训练为开发后台任务。
- 每任务 TDD:红 → 绿 → commit。分支 `l2-zoo-champion`。

---

### Task 1: handler 三分支保留 3 个 fwd 标签

**Files:**
- Modify: `autoresearch/data/features.py`(加 `FWD_LABELS`)
- Modify: `autoresearch/data/handler.py`(materialize core/seq/graph 三分支 keep 列)
- Test: `tests/data/test_handler.py`(扩断言)

**Interfaces — Produces:** `features.FWD_LABELS = ["fwd_1_oo","fwd_5_oc","fwd_10_oc"]`;materialize 三视图输出列含三者 + `buyable`。

- [ ] **Step 1: 失败测试** — 在 `tests/data/test_handler.py` 末尾加:

```python
def test_materialize_retains_all_three_fwd_labels(synth):
    from autoresearch.data.features import FWD_LABELS
    P, F = synth
    for fs in ("core", "seq", "graph"):
        panel = DataHandler().materialize([F[0]], feature_set=fs, kind=fs,
                                          price_dates=P, cap_floor=CAP_FLOOR, fwd=FWD)
        assert not panel.empty
        for lab in FWD_LABELS:
            assert lab in panel.columns, f"{fs} 缺标签 {lab}"
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest tests/data/test_handler.py::test_materialize_retains_all_three_fwd_labels -q` → FAIL(ImportError FWD_LABELS / 缺列)。

- [ ] **Step 3: features.py 加常量** — 在 `LABEL` 定义后:

```python
# 训练可用的前瞻收益标签(forward_returns 全算齐;materialize 三视图都保留,供多 horizon 训练)。
FWD_LABELS: list[str] = ["fwd_1_oo", "fwd_5_oc", "fwd_10_oc"]
```

- [ ] **Step 4: handler.py 三分支保留** — import 处加 `FWD_LABELS`;
  - core 分支:`keep_extra = [LABEL, "buyable"]` → `keep_extra = [*FWD_LABELS, "buyable"]`。
  - `_materialize_seq`:merge `fr[["code", "fwd_1_oo", "buyable"]]` → `fr[["code", *FWD_LABELS, "buyable"]]`;末尾 select 与空帧列 `[..., "fwd_1_oo", "buyable"]` → `[..., *FWD_LABELS, "buyable"]`。
  - `_materialize_graph`:`fr[["date","code","fwd_1_oo","buyable"]]` → `fr[["date","code",*FWD_LABELS,"buyable"]]`;末尾 select/空帧同理。

- [ ] **Step 5: 跑测试确认通过** — 上条命令 → PASS;并跑 `uv run --no-sync pytest tests/data/test_handler.py -q`(原 parity/seq/graph 仍绿)。

- [ ] **Step 6: commit** — `git add autoresearch/data/features.py autoresearch/data/handler.py tests/data/test_handler.py && git commit -m "feat(handler): materialize 三视图保留 fwd_5/fwd_10 标签(多 horizon 训练)"`

---

### Task 2: lake-native 历史 harvest CLI

**Files:**
- Create: `autoresearch/data/harvest.py`
- Test: `tests/data/test_harvest.py`

**Interfaces — Produces:**
- `plan_harvest(trade_days, start, end, step, back=60, fwd=10) -> tuple[list[str], list[str]]`(F, P;trade_days=升序紧凑日历)。
- `harvest(start, end, step=3, *, today=None, sleep=0.0, fetch=None, trade_days=None) -> dict`(返回 `{"F":n,"P":m,"calls":k}`)。
- 端点:`_FACTOR_EPS = ("daily_basic","stk_factor_pro","cyq_perf","moneyflow","hk_hold","margin_detail","block_trade","top_inst")`;价格面板用 `daily`;`stock_basic` 一次。

- [ ] **Step 1: 失败测试** — `tests/data/test_harvest.py`:

```python
"""lake-native harvest:plan_harvest 区间 + harvest 落湖 + 断点续(NO network,注入 fetch)。"""
from __future__ import annotations
import pandas as pd
from autoresearch.data import cache
from autoresearch.data.harvest import plan_harvest, harvest

_CAL = [d.strftime("%Y%m%d") for d in pd.bdate_range("2024-01-01", periods=200)]

def test_plan_harvest_F_every_step_and_P_covers_back_fwd():
    F, P = plan_harvest(_CAL, _CAL[80], _CAL[120], step=5, back=60, fwd=10)
    assert F == _CAL[80:121:5]
    assert P[0] == _CAL[80 - 60] and P[-1] == _CAL[120 + 10]
    assert set(F) <= set(P)

def test_harvest_writes_lake_and_is_resumable(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    calls = {"n": 0}
    def fake_fetch(endpoint, params):
        calls["n"] += 1
        return pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": [params.get("trade_date", "x")]})
    r1 = harvest(_CAL[80], _CAL[90], step=5, today=_CAL[150], trade_days=_CAL, fetch=fake_fetch)
    assert r1["calls"] > 0 and (cache.LAKE / "daily").exists()
    n_after_first = calls["n"]
    r2 = harvest(_CAL[80], _CAL[90], step=5, today=_CAL[150], trade_days=_CAL, fetch=fake_fetch)
    assert calls["n"] == n_after_first, "断点续:第二次应零取数(湖命中)"
    assert r2["calls"] == 0
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest tests/data/test_harvest.py -q` → FAIL(模块不存在)。

- [ ] **Step 3: 实现 harvest.py**:

```python
#!/usr/bin/env python3
"""lake-native 历史 harvest —— 把 core 所需端点的全市场历史落进 parquet 湖(取一次永不重取)。

design: docs/specs/2026-06-22-l2-zoo-champion-design.md §P-A。
plan_harvest 规划 成型日 F(每 step 交易日)+ 连续价格面板 P(供 60d 回看 + 10d 前瞻);
harvest 对每个 (endpoint, date) 调 get_or_fetch 落湖。断点续 = 湖命中即跳(get_or_fetch 内建)。
"""
from __future__ import annotations

import sys
import time

from autoresearch.data.cache import get_or_fetch

_FACTOR_EPS = ("daily_basic", "stk_factor_pro", "cyq_perf", "moneyflow",
               "hk_hold", "margin_detail", "block_trade", "top_inst")


def plan_harvest(trade_days, start, end, step, back=60, fwd=10):
    """(F 成型日, P 价格面板)。trade_days=升序紧凑(YYYYMMDD)交易日历。"""
    cal = list(trade_days)
    start, end = start.replace("-", ""), end.replace("-", "")
    in_rng = [d for d in cal if start <= d <= end]
    F = in_rng[::step]
    if not F:
        return [], []
    i0, i1 = cal.index(F[0]), cal.index(F[-1])
    P = cal[max(0, i0 - back): min(len(cal), i1 + fwd + 1)]
    return F, P


def _trade_days_live(end):
    from autoresearch.data.tushare_source import _pro, _trade_days
    pro = _pro()
    return _trade_days(pro, "20180101", end.replace("-", ""))


def harvest(start, end, step=3, *, today=None, sleep=0.0, fetch=None, trade_days=None):
    """落湖 [start,end] 的 core 端点历史。today=结算锚(>=today 的盘中日不写)。返回计数。"""
    today = today or end
    cal = trade_days if trade_days is not None else _trade_days_live(end)
    F, P = plan_harvest(cal, start, end, step)
    calls = {"n": 0}

    def _go(ep, params):
        before = calls["n"]
        from autoresearch.data.cache import lake_path
        if lake_path(ep, params, today=today).exists():
            return
        get_or_fetch(ep, params, today=today, fetch=fetch)
        calls["n"] += 1
        if sleep:
            time.sleep(sleep)
        _ = before

    get_or_fetch("stock_basic", {}, today=today, fetch=fetch) if not _exists("stock_basic", {}, today) else None
    for i, d in enumerate(P, 1):
        _go("daily", {"trade_date": d})
        if i % 50 == 0 or i == len(P):
            print(f"[harvest] daily {i}/{len(P)} ({d})", file=sys.stderr, flush=True)
    for i, d in enumerate(F, 1):
        for ep in _FACTOR_EPS:
            _go(ep, {"trade_date": d})
        print(f"[harvest] factors {i}/{len(F)} ({d})", file=sys.stderr, flush=True)
    print(f"[harvest] done F={len(F)} P={len(P)} 新取={calls['n']}", file=sys.stderr)
    return {"F": len(F), "P": len(P), "calls": calls["n"]}


def _exists(ep, params, today):
    from autoresearch.data.cache import lake_path
    return lake_path(ep, params, today=today).exists()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("start"); ap.add_argument("end")
    ap.add_argument("--step", type=int, default=3); ap.add_argument("--sleep", type=float, default=0.35)
    a = ap.parse_args()
    harvest(a.start, a.end, step=a.step, sleep=a.sleep)
```

> 注:`stock_basic` 行用 `_exists` 守护避免重取;`daily`/factor 端点的跳过由 `_go` 内 `lake_path(...).exists()` 处理(与 get_or_fetch 的命中语义一致,但避免计入 calls)。执行时若 `_go` 的 before/`_` 冗余可清理。

- [ ] **Step 4: 跑测试确认通过** — `uv run --no-sync pytest tests/data/test_harvest.py -q` → PASS(2 passed)。

- [ ] **Step 5: commit** — `git add autoresearch/data/harvest.py tests/data/test_harvest.py && git commit -m "feat(data): lake-native 历史 harvest CLI(plan_harvest + 断点续)"`

---

### Task 3: Trainer 修 kind + load_champion_any

**Files:**
- Modify: `autoresearch/models/trainer.py`
- Test: `tests/models/test_framework.py`(扩)或 `tests/models/test_champion_any.py`(新)

**Interfaces — Produces:**
- `Trainer.train` 向 `materialize` 传 `kind=cfg.feature_set`(修 seq/graph 全 NaN)。
- `load_champion_any(name, *, root=STORE_ROOT) -> Model | None`(按 champion.json 的 `kind` 用 `_REGISTRY[kind].load` 反序列化)。

- [ ] **Step 1: 失败测试** — `tests/models/test_champion_any.py`:

```python
"""Trainer 传 kind=feature_set;load_champion_any 按 kind 自解析(NO network)。"""
from __future__ import annotations
import pandas as pd
from autoresearch.models.registry import ModelConfig
from autoresearch.models.trainer import Trainer, save_champion, load_champion_any, TrainedModel
from autoresearch.models.linear import LinearComposite
from autoresearch.models.base import FitReport
from tests.models._synth import make_panel

class _RecordHandler:
    def __init__(self, panel): self._p = panel; self.kinds = []
    def materialize(self, dates, feature_set="core", kind="core", cap_floor=30.0, *, price_dates=None, fwd=10):
        self.kinds.append(kind); return self._p.copy()

def test_trainer_forwards_kind_equal_feature_set():
    h = _RecordHandler(make_panel())
    Trainer(h, label="fwd_1_oo").train(ModelConfig(kind="linear", feature_set="graph"), ["20260101"])
    assert h.kinds[-1] == "graph", "Trainer 必须把 kind 传成 feature_set"

def test_load_champion_any_resolves_kind(tmp_path):
    store = tmp_path / "store"
    champ = LinearComposite()
    trained = TrainedModel(model=champ, report=FitReport(n_rows=1, n_dates=1, notes={}),
                           oos_rank_ic=0.05, meta={"kind": "linear", "feature_set": "core"})
    save_champion("l2_fwd5", trained, "v1", root=store)
    loaded = load_champion_any("l2_fwd5", root=store)
    assert loaded is not None and hasattr(loaded, "predict")
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest tests/models/test_champion_any.py -q` → FAIL(kind 默认 core / load_champion_any 不存在)。

- [ ] **Step 3: 实现** — trainer.py:
  - `train` 内 materialize 调用:`feature_set=cfg.feature_set` 后加 `kind=cfg.feature_set`。
  - 末尾加:

```python
def load_champion_any(name: str, *, root: Path = STORE_ROOT) -> Model | None:
    """按 champion.json 的 kind 用 registry 解析模型类反序列化(支持任意 zoo kind)。无 → None。"""
    ptr = _champion_pointer(name, root)
    if ptr is None:
        return None
    pkl = _name_dir(name, root) / f"{ptr['version']}.pkl"
    if not pkl.exists():
        return None
    from autoresearch.models.registry import _REGISTRY
    cls = _REGISTRY.get(ptr.get("kind") or "")
    if cls is None:
        return None
    try:
        return cls.load(pkl)
    except Exception:  # noqa: BLE001 — 反序列化失败 → 调用方回落
        return None
```

- [ ] **Step 4: 跑测试确认通过** — `uv run --no-sync pytest tests/models/test_champion_any.py tests/models/test_framework.py -q` → PASS。

- [ ] **Step 5: commit** — `git add autoresearch/models/trainer.py tests/models/test_champion_any.py && git commit -m "fix(trainer): train 传 kind=feature_set(修 seq/graph)+ load_champion_any 按 kind 自解析"`

---

### Task 4: zoo train_zoo runner(故障隔离 + champion 门 + leaderboard)

**Files:**
- Create: `autoresearch/models/zoo.py`
- Modify: `tests/models/_synth.py`(make_panel 加 fwd_5_oc/fwd_10_oc)
- Test: `tests/models/test_zoo.py`

**Interfaces — Consumes:** `Trainer`/`save_champion`/`champion_ic`(trainer)、`catalog.MODELS`/`ported`、`ModelConfig`。
**Produces:** `train_zoo(handler, dates, horizons, model_names=None, *, price_dates=None, cap_floor=30.0, store_root=None, out_csv=None) -> pd.DataFrame`;`_tag(h)`(`fwd_1_oo→l2_fwd1` 等)。

- [ ] **Step 1: _synth.make_panel 加 swing 标签** — 在 `df["fwd_1_oo"] = ...` 后加:

```python
        df["fwd_5_oc"] = signal * df["pct_60d"] + rng.normal(scale=1.2, size=n_stocks)
        df["fwd_10_oc"] = signal * df["pct_60d"] + rng.normal(scale=1.5, size=n_stocks)
```
  并把 `ordered` 里 `"fwd_1_oo"` 处扩成 `"fwd_1_oo", "fwd_5_oc", "fwd_10_oc"`。

- [ ] **Step 2: 失败测试** — `tests/models/test_zoo.py`:

```python
"""zoo train_zoo:多 horizon × 多模型 leaderboard + 故障隔离 + champion 门(NO network)。"""
from __future__ import annotations
import pandas as pd
from autoresearch.models.zoo import train_zoo, _tag
from autoresearch.models.trainer import load_champion_any
from tests.models._synth import make_panel, StubHandler

def test_tag_horizon_to_champion_name():
    assert _tag("fwd_1_oo") == "l2_fwd1" and _tag("fwd_5_oc") == "l2_fwd5" and _tag("fwd_10_oc") == "l2_fwd10"

def test_train_zoo_leaderboard_isolation_and_champion(tmp_path, monkeypatch):
    h = StubHandler(make_panel(n_dates=10, n_stocks=150, signal=0.8))
    # 注入一个会抛错的 kind,验证故障隔离
    import autoresearch.models.zoo as zoo
    monkeypatch.setattr(zoo, "_resolve_models", lambda names: [
        ("linear", "linear", "core"), ("lgbm", "lgbm", "core"), ("boom", "boom", "core")])
    def boom_build(cfg):
        if cfg.kind == "boom":
            raise RuntimeError("intentional")
        from autoresearch.models.registry import build as real
        return real(cfg)
    monkeypatch.setattr(zoo, "build_model", boom_build)
    lb = train_zoo(h, ["20260101"], ["fwd_1_oo", "fwd_5_oc"], store_root=tmp_path / "store",
                   out_csv=tmp_path / "lb.csv")
    assert set(["horizon", "model", "feature_set", "oos_rank_ic", "vs_linear", "status"]) <= set(lb.columns)
    assert (lb["status"] == "error").any(), "坏模型应记 error 不中断"
    assert (lb["status"] == "ok").sum() >= 2, "好模型应照常训练"
    assert (tmp_path / "lb.csv").exists()
    # champion 门:若有人胜线性,store 落 champion;否则回落(None)
    for h_ in ("fwd_1_oo", "fwd_5_oc"):
        beat = lb[(lb.horizon == h_) & (lb.vs_linear > 0) & (lb.status == "ok")]
        champ = load_champion_any(_tag(h_), root=tmp_path / "store")
        assert (champ is not None) == (len(beat) > 0)
```

- [ ] **Step 3: 跑测试确认失败** — `uv run --no-sync pytest tests/models/test_zoo.py -q` → FAIL(模块不存在)。

- [ ] **Step 4: 实现 zoo.py**:

```python
#!/usr/bin/env python3
"""zoo 训练 runner —— horizons × 全 zoo 模型,每 horizon 晋升胜线性的 champion。

design: docs/specs/2026-06-22-l2-zoo-champion-design.md §P-B。
故障隔离:单模型异常 → status=error 跳过。champion 门:oos rank-IC 最高且 > 线性基线 → save_champion。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from autoresearch.models.catalog import MODELS, ported
from autoresearch.models.registry import ModelConfig, build as build_model
from autoresearch.models.trainer import STORE_ROOT, Trainer, save_champion

_TAGS = {"fwd_1_oo": "l2_fwd1", "fwd_5_oc": "l2_fwd5", "fwd_10_oc": "l2_fwd10"}


def _tag(horizon: str) -> str:
    return _TAGS.get(horizon, f"l2_{horizon}")


def _resolve_models(names):
    """[(name, kind, feature_set)];names 缺省 = catalog.ported()。"""
    names = names or ported()
    return [(n, MODELS[n]["kind"], MODELS[n]["feature_set"]) for n in names]


def train_zoo(handler, dates, horizons, model_names=None, *, price_dates=None,
              cap_floor=30.0, store_root=None, out_csv=None) -> pd.DataFrame:
    store_root = Path(store_root) if store_root else STORE_ROOT
    rows = []
    for horizon in horizons:
        trainer = Trainer(handler, label=horizon)
        results = {}   # name -> (trained, ic)
        lin_ic = None
        for name, kind, fset in _resolve_models(model_names):
            cfg = ModelConfig(kind=kind, feature_set=fset)
            try:
                model = build_model(cfg)
                # Trainer.train 用 build();此处复用 trainer.train(cfg) 走统一物化/评估
                trained = trainer.train(cfg, dates, price_dates=price_dates, cap_floor=cap_floor)
                ic = float(trained.oos_rank_ic)
                results[name] = (trained, ic)
                if name == "linear":
                    lin_ic = ic
                rows.append({"horizon": horizon, "model": name, "feature_set": fset,
                             "oos_rank_ic": ic, "status": "ok"})
            except Exception as e:  # noqa: BLE001 — 单模型隔离
                rows.append({"horizon": horizon, "model": name, "feature_set": fset,
                             "oos_rank_ic": float("nan"), "status": f"error:{type(e).__name__}"})
                print(f"[zoo] {horizon}/{name} 失败: {e!r}", file=sys.stderr)
        # champion 门
        base = lin_ic if lin_ic is not None else 0.0
        winners = {n: ic for n, (_, ic) in results.items() if ic == ic and ic > base and n != "linear"}
        if winners:
            best = max(winners, key=winners.get)
            save_champion(_tag(horizon), results[best][0], "v1", root=store_root)
            print(f"[zoo] {horizon} champion = {best} (ic {winners[best]:+.4f} > 线性 {base:+.4f})", file=sys.stderr)
        else:
            print(f"[zoo] {horizon} 无人胜线性({base:+.4f})→ 不晋升,L2 回落线性", file=sys.stderr)
    lb = pd.DataFrame(rows)
    # vs_linear / champion 标注
    lin_by_h = {h: g[g.model == "linear"]["oos_rank_ic"].max() for h, g in lb.groupby("horizon")}
    lb["vs_linear"] = lb.apply(lambda r: r["oos_rank_ic"] - lin_by_h.get(r["horizon"], 0.0)
                               if r["status"] == "ok" else float("nan"), axis=1)
    if out_csv:
        Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
        lb.to_csv(out_csv, index=False)
    return lb


if __name__ == "__main__":
    import argparse
    from autoresearch.data.handler import DataHandler
    from autoresearch.data.harvest import plan_harvest, _trade_days_live
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates-from", required=True); ap.add_argument("--dates-to", required=True)
    ap.add_argument("--step", type=int, default=3)
    ap.add_argument("--horizons", default="fwd_1_oo,fwd_5_oc,fwd_10_oc")
    ap.add_argument("--models", default="")
    ap.add_argument("--out", default="context/factor_lab/zoo_leaderboard.csv")
    a = ap.parse_args()
    cal = _trade_days_live(a.dates_to)
    F, P = plan_harvest(cal, a.dates_from, a.dates_to, a.step)
    names = [m for m in a.models.split(",") if m] or None
    lb = train_zoo(DataHandler(), F, a.horizons.split(","), names, price_dates=P, out_csv=a.out)
    print(lb.to_string(index=False))
```

> 注:测试 monkeypatch `zoo.build_model` 与 `zoo._resolve_models`,故二者须是模块级可替换名。

- [ ] **Step 5: 跑测试确认通过** — `uv run --no-sync pytest tests/models/test_zoo.py -q` → PASS。

- [ ] **Step 6: commit** — `git add autoresearch/models/zoo.py tests/models/_synth.py tests/models/test_zoo.py && git commit -m "feat(models): zoo train_zoo runner(多 horizon + 故障隔离 + champion 门 + leaderboard)"`

---

### Task 5: L2 接线用 champion(默认 l2_fwd5,缺则回落线性)

**Files:**
- Modify: `autoresearch/scan/config.py`(`l2_model` 默认 `l2_fwd5`)
- Modify: `autoresearch/scan/stages/l2_rank.py`(`_champion` 用 `load_champion_any`)
- Test: `tests/scan/test_l2_champion.py`

**Interfaces — Consumes:** `load_champion_any`(Task 3)。

- [ ] **Step 1: 失败测试** — `tests/scan/test_l2_champion.py`:

```python
"""L2 加载 l2_fwd5 champion 重排;缺则回落线性(NO network)。"""
from __future__ import annotations
import pandas as pd
from autoresearch.models.linear import LinearComposite
from autoresearch.models.base import FitReport
from autoresearch.models.trainer import TrainedModel, save_champion
from autoresearch.scan.config import ScanConfig

def test_default_l2_model_is_swing():
    assert ScanConfig().l2_model == "l2_fwd5"

def test_l2_champion_loaded_then_fallback(tmp_path, monkeypatch):
    import autoresearch.models.trainer as tr
    monkeypatch.setattr(tr, "STORE_ROOT", tmp_path / "store")
    # 无 champion → load_champion_any None(回落由 L2 处理)
    from autoresearch.models.trainer import load_champion_any
    assert load_champion_any("l2_fwd5", root=tmp_path / "store") is None
    # 存一个 linear champion → 可加载
    trained = TrainedModel(model=LinearComposite(), report=FitReport(n_rows=1, n_dates=1, notes={}),
                           oos_rank_ic=0.05, meta={"kind": "linear", "feature_set": "core"})
    save_champion("l2_fwd5", trained, "v1", root=tmp_path / "store")
    assert load_champion_any("l2_fwd5", root=tmp_path / "store") is not None
```

- [ ] **Step 2: 跑测试确认失败** — `uv run --no-sync pytest tests/scan/test_l2_champion.py -q` → FAIL(`l2_model` 非 `l2_fwd5`)。

- [ ] **Step 3: 实现**:
  - `scan/config.py`:`ScanConfig` 的 `l2_model` 默认改 `"l2_fwd5"`(无则新增字段 `l2_model: str = "l2_fwd5"`)。
  - `scan/stages/l2_rank.py` `_champion`:把 `load_champion(name, LinearComposite)` 换成 `load_champion_any(name)`;返回 None → `LinearComposite(), "composite-linear(default)"`;成功 → `(champ, f"{getattr(champ,'kind','core')}:{name}")`。import 改 `from autoresearch.models import load_champion_any`(在 `models/__init__.py` 的 `__all__` 加 `load_champion_any`)。

- [ ] **Step 4: 跑测试确认通过** — `uv run --no-sync pytest tests/scan/test_l2_champion.py tests/scan/test_parity.py -q` → PASS(parity composite 仍绿)。

- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(scan): L2 默认 l2_fwd5 champion(load_champion_any),缺则回落线性"`

---

### Task 6: 文档(SKILL.md + screening-playbook.md)

**Files:** `.claude/skills/scan-market/SKILL.md`、`.claude/skills/scan-market/screening-playbook.md`

- [ ] **Step 1: 更新 L2 段** — SKILL.md L2 行 + screening-playbook L2 段:champion = zoo 训练(`zoo train`)、多 horizon(`l2_fwd1/5/10`)、L2 默认 `l2_fwd5`、`zoo_leaderboard.csv`、未胜线性 → 回落;harvest 命令 `python -m autoresearch.data.harvest`。
- [ ] **Step 2: commit** — `git add -A && git commit -m "docs(scan): L2 champion 改 zoo 多 horizon 训练 + harvest 命令"`

---

### Task 7: 真数据运行(无新代码;开发后台,代码全绿后)

- [ ] **Step 1: 种子** — `uv run --no-sync python -c "from autoresearch.data.migrate_cache import migrate; migrate()"`(84 日 pkl → 湖)。
- [ ] **Step 2: 扩历史(后台,长)** — `uv run --no-sync python -m autoresearch.data.harvest 2024-06-01 2026-06-01 --step 3`(断点续;后台跑)。
- [ ] **Step 3: 冒烟(core-only 小训)** — `... -m autoresearch.models.zoo train --dates-from 2026-01-01 --dates-to 2026-06-01 --models linear,lgbm --horizons fwd_1_oo` → 端到端通、leaderboard 出。
- [ ] **Step 4: 全 zoo 训练(后台,长)** — `... zoo train --dates-from 2024-06-01 --dates-to 2026-06-01`(全 20 × 3 horizon;坏模型隔离)。
- [ ] **Step 5: 验收** — 读 `context/factor_lab/zoo_leaderboard.csv`:有模型胜线性 → champion 落 `models/store/l2_fwd5/`;否则诚实标注、L2 回落线性。`uv run --no-sync pytest -q` 全绿。

---

## Self-Review(plan ↔ spec)

- **Spec 覆盖**:§P-A 落湖=Task 2、handler 3 标签=Task 1;§P-B 训练=Task 4、Trainer 修 kind + load_champion_any=Task 3;L2 接线=Task 5;文档=Task 6;真跑/验收=Task 7。全覆盖。
- **类型一致**:`train_zoo`/`plan_harvest`/`harvest`/`load_champion_any`/`_tag` 签名在定义任务与消费任务一致;champion 名 `l2_fwd1/5/10` 全程统一;`FWD_LABELS` 三标签全程一致。
- **无占位**:每实现步含真代码;测试步含真断言。
- **诚实**:Task 7 验收明确"不胜线性则回落",不假设一定胜。
