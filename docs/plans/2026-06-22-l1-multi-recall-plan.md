# Phase 2 — L1 多路召回 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** L1 召回从单一 `composite_score` 排序 → 8 路 channel(全复用 `scoring.py`)+ quota union 合并，带 provenance + trace；`recall_mode=composite` 保留 golden 对拍。

**Architecture:** 新子包 `autoresearch/scan/recall/`(registry/channels/merge，镜像 `models/`)。多路逻辑单一来源，被两个编排器调用：`scan/universe.py::run`(live staging，下游 L2/L3 读的就是它)与 `scan/stages/l1_recall.py::L1Recall`(typed trace)。两处都加 `recall_mode` 分支，parity 两侧强制 composite。

**Tech Stack:** Python 3 · pandas · pyarrow(parquet trace) · pytest · ruff。`uv run --no-sync` 跑测试(勿删 venv-only 包)。

## Global Constraints
- 零新因子数学：8 路全部复用 `autoresearch/common/scoring.py` 现成 lens/列；accumulation 路复用 `composite_score` 既有吸筹判据，不重写。
- `recall_mode=composite` 必须逐值复现今天的单复合分召回(golden parity diff=0)。
- 缺列/缺权限的 channel → 返回空帧，merge 不破(与现有「降级置 NaN」一致)。
- 合并 = **pure quota union**(非 RRF、非 score-blend)；`n_channels` 仅作 trim tiebreak + provenance。
- 段间只经 trace 产物通信；staging CSV 列须含下游所需全列 + provenance。
- 所有测试 NO network(合成夹具)。命令一律 `uv run --no-sync python -m pytest ...`。

---

### Task 1: Channel 框架(base + registry + 包骨架)

**Files:**
- Create: `autoresearch/scan/recall/__init__.py`
- Create: `autoresearch/scan/recall/base.py`
- Create: `autoresearch/scan/recall/registry.py`
- Test: `tests/scan/test_recall_registry.py`

**Interfaces:**
- Produces: `gate_rank(frame, mask, score_col, k) -> DataFrame[code, channel_rank, channel_score]`；`@channel(name, quota, floor, desc)` 装饰器；`build(name) -> callable`；`registered_channels() -> list[str]`；`CHANNEL_DEFAULTS: dict[str, ChannelSpec]`(`ChannelSpec.quota/floor/desc`)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_recall_registry.py
"""recall registry + gate_rank:注册副作用 / build / defaults / 排序截断。NO network。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.scan.recall.base import gate_rank
from autoresearch.scan.recall.registry import CHANNEL_DEFAULTS, build, channel, registered_channels


def test_gate_rank_sorts_gates_and_truncates():
    frame = pd.DataFrame({"code": [f"{i:06d}" for i in range(5)],
                          "s": [1.0, 5.0, 3.0, float("nan"), 4.0],
                          "g": [True, True, True, True, False]})
    out = gate_rank(frame, frame["g"], "s", k=2)
    assert list(out.columns) == ["code", "channel_rank", "channel_score"]
    assert out["code"].tolist() == ["000001", "000002"]      # 5.0, 4.0? no: 000004 gated out
    assert out["channel_rank"].tolist() == [1, 2]


def test_gate_rank_missing_col_or_empty_returns_empty():
    frame = pd.DataFrame({"code": ["000001"], "s": [1.0]})
    assert gate_rank(frame, None, "nonexist", k=3).empty
    assert list(gate_rank(frame, None, "nonexist", k=3).columns) == ["code", "channel_rank", "channel_score"]


def test_channel_register_build_defaults():
    @channel("t_dummy", quota=7, floor=2, desc="d")
    def _dummy(frame, date, k):
        return gate_rank(frame, None, "s", k)
    assert "t_dummy" in registered_channels()
    assert build("t_dummy") is _dummy
    assert CHANNEL_DEFAULTS["t_dummy"].quota == 7 and CHANNEL_DEFAULTS["t_dummy"].floor == 2


def test_build_unknown_raises():
    with pytest.raises(KeyError):
        build("no_such_channel")
```

> 注:`test_gate_rank_sorts_gates_and_truncates` 期望排序后是分数降序的前 2 名:gated 集 = {000000:1, 000001:5, 000002:3, 000003:nan(drop)}，000004 被门剔。降序 top2 = 000001(5.0), 000002(3.0)。改断言为 `["000001", "000002"]`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_recall_registry.py -q`
Expected: FAIL(ModuleNotFoundError: autoresearch.scan.recall）

- [ ] **Step 3: 实现 base.py**

```python
# autoresearch/scan/recall/base.py
#!/usr/bin/env python3
"""recall channel 共用原语 —— gate_rank(过门 → 降序 top-k → 标准三列)。

design: docs/specs/2026-06-22-l1-multi-recall-design.md §架构。
每路 channel 都把「过门 + 排序 + 截断」收敛到这里,保证返回列契约一致。
"""
from __future__ import annotations

import pandas as pd

_COLS = ["code", "channel_rank", "channel_score"]


def gate_rank(frame: pd.DataFrame, mask, score_col: str, k: int) -> pd.DataFrame:
    """过 mask(None=不过门)→ 按 score_col 降序 → top-k → DataFrame[code, channel_rank(1..), channel_score]。

    缺 score_col / 空帧 / 过门后为空 → 空帧(仍带三列)。stable 排序保证确定性。
    """
    if score_col not in frame.columns or not len(frame):
        return pd.DataFrame(columns=_COLS)
    sub = frame if mask is None else frame[mask.fillna(False)]
    sub = sub[sub[score_col].notna()]
    if not len(sub):
        return pd.DataFrame(columns=_COLS)
    sub = sub.sort_values(score_col, ascending=False, kind="stable").head(k)
    return pd.DataFrame({
        "code": sub["code"].astype(str).str.zfill(6).to_numpy(),
        "channel_rank": range(1, len(sub) + 1),
        "channel_score": sub[score_col].astype(float).to_numpy(),
    }).reset_index(drop=True)
```

- [ ] **Step 4: 实现 registry.py**

```python
# autoresearch/scan/recall/registry.py
#!/usr/bin/env python3
"""recall channel registry —— `@channel(name, quota, floor)` 注册 + `build(name)` 取函数。

design: docs/specs/2026-06-22-l1-multi-recall-design.md §架构(镜像 models/registry)。
加一路召回 = 写函数 + `@channel(...)`,不动 stage/merge。CHANNEL_DEFAULTS 存每路默认配额/保底。
"""
from __future__ import annotations

from dataclasses import dataclass

_REGISTRY: dict[str, object] = {}
_DEFAULTS: dict[str, "ChannelSpec"] = {}


@dataclass(frozen=True)
class ChannelSpec:
    """一路 channel 的默认元数据:配额 quota(取 top-k)+ 保底 floor(top-floor 无条件保留)+ 描述。"""

    name: str
    quota: int
    floor: int
    desc: str = ""


def channel(name: str, quota: int, floor: int, desc: str = ""):
    """函数装饰器:把一路 channel 函数登记进 registry(重名报错)+ 记默认配额/保底。"""

    def deco(fn):
        if name in _REGISTRY:
            raise KeyError(f"channel {name!r} already registered to {_REGISTRY[name]!r}")
        _REGISTRY[name] = fn
        _DEFAULTS[name] = ChannelSpec(name=name, quota=quota, floor=floor, desc=desc)
        return fn

    return deco


def build(name: str):
    """取一路 channel 函数(签名 fn(frame, date, k) -> DataFrame[code, channel_rank, channel_score])。"""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown channel {name!r}: registered={sorted(_REGISTRY)}") from None


def registered_channels() -> list[str]:
    """已注册的全部 channel 名(导入 channels 模块后才齐)。"""
    return sorted(_REGISTRY)


CHANNEL_DEFAULTS = _DEFAULTS   # name -> ChannelSpec(随 @channel 注册增长;同一 dict 引用)
```

- [ ] **Step 5: 写 __init__.py(暂不导入 channels，Task 2 再补)**

```python
# autoresearch/scan/recall/__init__.py
"""autoresearch.scan.recall —— L1 多路策略召回(channel registry + quota union merge)。

design: docs/specs/2026-06-22-l1-multi-recall-design.md。
导入本包即触发 channels 的 @channel 注册副作用(Task 2 起);公共 API 见 __all__。
"""
from __future__ import annotations

from autoresearch.scan.recall.base import gate_rank
from autoresearch.scan.recall.registry import (
    CHANNEL_DEFAULTS,
    ChannelSpec,
    build,
    channel,
    registered_channels,
)

__all__ = ["gate_rank", "channel", "build", "registered_channels",
           "CHANNEL_DEFAULTS", "ChannelSpec"]
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_recall_registry.py -q`
Expected: PASS(4 passed）

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/recall/__init__.py autoresearch/scan/recall/base.py autoresearch/scan/recall/registry.py tests/scan/test_recall_registry.py
git commit -m "feat(recall): channel registry + gate_rank 原语(Phase 2 Task 1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 8 路内置 channel

**Files:**
- Create: `autoresearch/scan/recall/channels.py`
- Modify: `autoresearch/scan/recall/__init__.py`(导入 channels 触发注册)
- Test: `tests/scan/test_recall_channels.py`

**Interfaces:**
- Consumes: `gate_rank` · `channel`(Task 1)；`composite_score / lens_* / _num / _load_weights`(`common/scoring`)。
- Produces: 注册 8 路 channel:`composite, momentum, reversal, growth, value, main_fund, northbound, accumulation`。每路 `fn(frame, date, k) -> DataFrame[code, channel_rank, channel_score]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_recall_channels.py
"""8 路 channel:返回列契约 / 过门 / top-k / 缺列降级。NO network(合成 universe)。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.scan.recall import build, registered_channels
from tests.scan._synth_universe import synth_universe

_EIGHT = {"composite", "momentum", "reversal", "growth", "value",
          "main_fund", "northbound", "accumulation"}


def test_eight_channels_registered():
    assert _EIGHT <= set(registered_channels())


def test_each_channel_returns_contract_and_respects_k():
    uni = synth_universe(n=400, seed=1)
    from autoresearch.common.scoring import _load_weights, composite_score
    scored = composite_score(uni, _load_weights())
    for name in _EIGHT:
        out = build(name)(scored, "2026-06-20", 50)
        assert list(out.columns) == ["code", "channel_rank", "channel_score"], f"{name} 列契约破"
        assert len(out) <= 50, f"{name} 超 k"
        if len(out):
            assert out["channel_rank"].tolist() == list(range(1, len(out) + 1)), f"{name} rank 非连续"
            assert np.isfinite(out["channel_score"].to_numpy()).all(), f"{name} score 非有限"


def test_channel_missing_column_degrades_to_empty():
    uni = pd.DataFrame({"code": [f"{i:06d}" for i in range(10)], "composite": range(10)})
    # 缺 hk_ratio/main_inflow 等 → northbound/main_fund/accumulation 空帧不抛
    for name in ("northbound", "accumulation"):
        out = build(name)(uni, "2026-06-20", 50)
        assert out.empty and list(out.columns) == ["code", "channel_rank", "channel_score"]
```

- [ ] **Step 2: 写合成 universe 夹具**

```python
# tests/scan/_synth_universe.py
"""合成 post-gate universe —— composite_score + 8 channel 所需全列。NO network。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def synth_universe(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    inds = rng.choice(["半导体", "光模块", "白酒", "煤炭", "医药", "电力"], n)
    df = pd.DataFrame({
        "code": [f"{600000 + i:06d}" for i in range(n)],
        "name": [f"股票{i}" for i in range(n)], "industry": inds,
        "close": rng.uniform(5, 300, n), "mktcap_yi": rng.uniform(40, 4000, n),
        "amount_yi": rng.uniform(0.5, 200, n), "pct_60d": rng.uniform(-50, 300, n),
        "pct_ytd": rng.uniform(-60, 400, n), "vol_ratio": rng.uniform(0.3, 5, n),
        "turnover": rng.uniform(0.1, 30, n), "pe": rng.uniform(-50, 200, n),
        "pb": rng.uniform(0.5, 30, n), "rev": rng.uniform(1e8, 5e10, n),
        "rev_yoy": rng.uniform(-40, 120, n), "np_yoy": rng.uniform(-100, 300, n),
        "np_qoq": rng.uniform(-50, 80, n), "roe": rng.uniform(-10, 35, n),
        "gross_margin": rng.uniform(5, 70, n), "cfo_ps": rng.uniform(-1, 3, n),
        "np_yoy_prev": rng.uniform(-100, 200, n), "main_inflow_yi": rng.uniform(-5, 8, n),
        "dv_ratio": rng.uniform(0, 6, n), "ma_bull": rng.integers(0, 2, n).astype(float),
        "above_ma60": rng.integers(0, 2, n).astype(float), "rsi6": rng.uniform(10, 95, n),
        "rsi12": rng.uniform(10, 95, n), "winner_rate": rng.uniform(0, 100, n),
        "main_net_ratio": rng.uniform(-0.1, 0.1, n), "retail_net_yi": rng.uniform(-2, 2, n),
        "chip_concentration": rng.uniform(0.1, 2.0, n), "price_to_cost": rng.uniform(0.7, 1.5, n),
        "hk_ratio": rng.uniform(0, 30, n), "cmf_20": rng.uniform(-0.5, 0.5, n),
        "obv_mom_20": rng.uniform(-1, 1, n), "is_st": False,
    })
    return df
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_recall_channels.py -q`
Expected: FAIL(ImportError: cannot import build channel 'composite' 未注册 / channels 模块不存在）

- [ ] **Step 4: 实现 channels.py**

```python
# autoresearch/scan/recall/channels.py
#!/usr/bin/env python3
"""8 路内置 channel —— 全复用 common.scoring(零新因子数学)。

design: docs/specs/2026-06-22-l1-multi-recall-design.md §8 路 channel 表。
每路:对 scored 帧(已含 composite + 因子列)过门 + 按策略信号降序 + 截 top-k。
accumulation 复用 composite_score 既有吸筹判据(底部放量 + 主力未撤),不重写。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.scoring import (
    _num,
    lens_growth,
    lens_momentum,
    lens_reversal,
    lens_value,
)
from autoresearch.scan.recall.base import gate_rank
from autoresearch.scan.recall.registry import channel


@channel("composite", quota=500, floor=100, desc="IC 校准复合分(=今天)")
def composite(frame, date, k):
    return gate_rank(frame, None, "composite", k)


@channel("momentum", quota=250, floor=50, desc="趋势龙头(lens_momentum 过门)")
def momentum(frame, date, k):
    g = lens_momentum(frame)
    return gate_rank(g, g["momentum_gate"], "momentum_score", k)


@channel("reversal", quota=200, floor=50, desc="困境反转(lens_reversal 过门)")
def reversal(frame, date, k):
    g = lens_reversal(frame)
    return gate_rank(g, g["reversal_gate"], "reversal_score", k)


@channel("growth", quota=150, floor=40, desc="成长加速(lens_growth 过门)")
def growth(frame, date, k):
    g = lens_growth(frame)
    return gate_rank(g, g["growth_gate"], "growth_score", k)


@channel("value", quota=200, floor=50, desc="行业内低估(lens_value 过门)")
def value(frame, date, k):
    g = lens_value(frame)
    return gate_rank(g, g["value_gate"], "value_score", k)


@channel("main_fund", quota=200, floor=50, desc="主力净流入")
def main_fund(frame, date, k):
    score_col = "main_net_ratio" if "main_net_ratio" in frame.columns else "main_inflow_yi"
    mask = (_num(frame["main_inflow_yi"]) > 0) if "main_inflow_yi" in frame.columns else None
    return gate_rank(frame, mask, score_col, k)


@channel("northbound", quota=120, floor=30, desc="北向(hk_ratio)")
def northbound(frame, date, k):
    mask = (_num(frame["hk_ratio"]) > 0) if "hk_ratio" in frame.columns else None
    return gate_rank(frame, mask, "hk_ratio", k)


@channel("accumulation", quota=120, floor=30, desc="底部吸筹(投机高召回,交下游证伪)")
def accumulation(frame, date, k):
    if "vol_ratio" not in frame.columns:
        return gate_rank(frame, None, "vol_ratio", k)   # -> 空帧
    low_pos = pd.Series(False, index=frame.index)
    if "winner_rate" in frame.columns:
        low_pos = low_pos | (_num(frame["winner_rate"]) < 40)
    if "price_to_cost" in frame.columns:
        low_pos = low_pos | (_num(frame["price_to_cost"]) < 1.0)
    not_high = (_num(frame["pct_60d"]) < 20) if "pct_60d" in frame.columns else pd.Series(True, index=frame.index)
    main_ok = (_num(frame["main_net_ratio"]) >= 0) if "main_net_ratio" in frame.columns else pd.Series(True, index=frame.index)
    mask = (_num(frame["vol_ratio"]) >= 1.5) & low_pos & not_high & main_ok
    return gate_rank(frame, mask, "vol_ratio", k)
```

- [ ] **Step 5: __init__.py 导入 channels 触发注册**

在 `autoresearch/scan/recall/__init__.py` 顶部 import 块**后**加:

```python
from autoresearch.scan.recall import channels  # noqa: F401  (registration side-effects)
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_recall_channels.py -q`
Expected: PASS(3 passed）

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/recall/channels.py autoresearch/scan/recall/__init__.py tests/scan/test_recall_channels.py tests/scan/_synth_universe.py
git commit -m "feat(recall): 8 路内置 channel(全复用 scoring,零新因子)(Phase 2 Task 2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: quota_union 合并

**Files:**
- Create: `autoresearch/scan/recall/merge.py`
- Modify: `autoresearch/scan/recall/__init__.py`(导出 quota_union)
- Test: `tests/scan/test_recall_merge.py`

**Interfaces:**
- Consumes: `ChannelSpec`(`.floor`)；channel 帧 `[code, channel_rank, channel_score]`。
- Produces: `quota_union(channel_frames: dict[str,DataFrame], defaults: dict[str,ChannelSpec], recall_n: int, base_frame: DataFrame) -> tuple[merged_df, per_channel_long]`。`merged_df` = 恰 `recall_n` 行,base 全列 + `recall_channels/n_channels/best_rank`,按 `(n_channels desc, composite desc)` 排序。`per_channel_long` = `[channel, code, channel_rank, channel_score]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_recall_merge.py
"""quota_union:并集去重 / floor 保底 / 恰 recall_n / provenance / backfill / 确定性。NO network。"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.recall.merge import quota_union
from autoresearch.scan.recall.registry import ChannelSpec


def _base(n=300):
    return pd.DataFrame({"code": [f"{i:06d}" for i in range(n)],
                         "composite": [n - i for i in range(n)], "name": [f"s{i}" for i in range(n)]})


def _cf(codes, scores):
    return pd.DataFrame({"code": [f"{c:06d}" for c in codes], "channel_rank": range(1, len(codes) + 1),
                         "channel_score": scores})


def test_union_dedup_and_provenance():
    base = _base()
    frames = {"a": _cf([0, 1, 2], [9, 8, 7]), "b": _cf([2, 3, 4], [9, 8, 7])}
    defs = {"a": ChannelSpec("a", 3, 1), "b": ChannelSpec("b", 3, 1)}
    merged, longf = quota_union(frames, defs, recall_n=5, base_frame=base)
    assert set(merged["code"]) == {"000000", "000001", "000002", "000003", "000004"}
    row2 = merged[merged["code"] == "000002"].iloc[0]
    assert row2["n_channels"] == 2 and row2["recall_channels"] == "a|b"
    assert len(longf) == 6   # 3 + 3 长表行


def test_floor_protects_each_channel_top():
    base = _base()
    # a 配额大但 floor=2;b floor=2。recall_n 小到必须靠 floor 保住各路 top
    frames = {"a": _cf(list(range(10)), list(range(10, 0, -1))),
              "b": _cf([100, 101, 102, 103], [9, 8, 7, 6])}
    defs = {"a": ChannelSpec("a", 10, 2), "b": ChannelSpec("b", 4, 2)}
    merged, _ = quota_union(frames, defs, recall_n=4, base_frame=_base(200))
    # b 的 top-2(000100,000101)必须在(floor 保底),即便 composite 低
    assert {"000100", "000101"} <= set(merged["code"])
    assert len(merged) == 4


def test_exactly_recall_n_with_backfill():
    base = _base(300)
    frames = {"a": _cf([0, 1, 2], [9, 8, 7])}            # 并集仅 3
    defs = {"a": ChannelSpec("a", 3, 1)}
    merged, _ = quota_union(frames, defs, recall_n=10, base_frame=base)
    assert len(merged) == 10                              # backfill 到 10
    assert (merged["recall_channels"] == "(backfill)").sum() == 7


def test_deterministic():
    base = _base()
    frames = {"a": _cf([0, 1, 2], [9, 8, 7]), "b": _cf([2, 3, 4], [9, 8, 7])}
    defs = {"a": ChannelSpec("a", 3, 1), "b": ChannelSpec("b", 3, 1)}
    m1, _ = quota_union(frames, defs, 5, base)
    m2, _ = quota_union(frames, defs, 5, base)
    pd.testing.assert_frame_equal(m1, m2)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_recall_merge.py -q`
Expected: FAIL(ModuleNotFoundError: merge）

- [ ] **Step 3: 实现 merge.py**

```python
# autoresearch/scan/recall/merge.py
#!/usr/bin/env python3
"""quota_union —— 多路 channel 名单的 pure quota union 合并(非 RRF)。

design: docs/specs/2026-06-22-l1-multi-recall-design.md §merge。
并集去重 → 每路 top-floor 无条件保留(多样性保证)→ 裁到恰 recall_n
(优先级 = n_channels desc, composite desc;非加权融合,只是 trim tiebreak)→
不足则从 base 按 composite backfill。provenance 列:recall_channels/n_channels/best_rank。
"""
from __future__ import annotations

import pandas as pd

_BIG = 10**9
_PROV = ["recall_channels", "n_channels", "best_rank"]


def quota_union(channel_frames, defaults, recall_n, base_frame):
    """见模块 docstring。channel_frames: {name: [code,channel_rank,channel_score]}。"""
    base = base_frame.copy()
    base["code"] = base["code"].astype(str).str.zfill(6)

    chan_of: dict[str, set] = {}
    best_rank: dict[str, int] = {}
    protected: set[str] = set()
    long_rows = []
    for name, cf in channel_frames.items():
        if cf is None or not len(cf):
            continue
        floor = defaults[name].floor if name in defaults else 0
        for i, r in enumerate(cf.itertuples(index=False)):
            code = str(r.code).zfill(6)
            chan_of.setdefault(code, set()).add(name)
            best_rank[code] = min(best_rank.get(code, _BIG), int(r.channel_rank))
            long_rows.append({"channel": name, "code": code,
                              "channel_rank": int(r.channel_rank), "channel_score": float(r.channel_score)})
            if i < floor:
                protected.add(code)
    per_channel_long = pd.DataFrame(long_rows, columns=["channel", "code", "channel_rank", "channel_score"])

    union_codes = set(chan_of) & set(base["code"])
    protected &= union_codes
    prov = pd.DataFrame({"code": sorted(union_codes)})
    prov["recall_channels"] = prov["code"].map(lambda c: "|".join(sorted(chan_of[c])))
    prov["n_channels"] = prov["code"].map(lambda c: len(chan_of[c]))
    prov["best_rank"] = prov["code"].map(lambda c: best_rank[c])
    merged = base.merge(prov, on="code", how="inner")

    def _ranked(df):
        return df.sort_values(["n_channels", "composite"], ascending=[False, False], kind="stable")

    is_prot = merged["code"].isin(protected)
    prot_df, rest_df = _ranked(merged[is_prot]), _ranked(merged[~is_prot])
    chosen = pd.concat([prot_df, rest_df], ignore_index=True)

    if len(chosen) > recall_n:
        if len(prot_df) >= recall_n:
            chosen = prot_df.head(recall_n)
        else:
            chosen = pd.concat([prot_df, rest_df.head(recall_n - len(prot_df))], ignore_index=True)
    elif len(chosen) < recall_n:
        have = set(chosen["code"])
        extra = (base[~base["code"].isin(have)]
                 .sort_values("composite", ascending=False, kind="stable").head(recall_n - len(chosen)))
        extra = extra.assign(recall_channels="(backfill)", n_channels=0, best_rank=_BIG)
        chosen = pd.concat([chosen, extra], ignore_index=True)

    out = _ranked(chosen).reset_index(drop=True)
    return out, per_channel_long
```

- [ ] **Step 4: __init__.py 导出 quota_union**

在 `autoresearch/scan/recall/__init__.py`:加 `from autoresearch.scan.recall.merge import quota_union`，并把 `"quota_union"` 加进 `__all__`。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_recall_merge.py -q`
Expected: PASS(4 passed）

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/recall/merge.py autoresearch/scan/recall/__init__.py tests/scan/test_recall_merge.py
git commit -m "feat(recall): quota_union 合并(floor 保底 + provenance + backfill)(Phase 2 Task 3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: trace schema(L1_CHANNELS + provenance 列)

**Files:**
- Modify: `autoresearch/trace/schema.py:19-23`(加 `L1_CHANNELS`)、`:60-71`(L1_RECALL/L1_SCORED_FULL optional 加 provenance)、`:52`(SCHEMAS 加 L1_CHANNELS)
- Test: `tests/trace/test_schema.py`(若无则新建该断言）

**Interfaces:**
- Produces: `schema.L1_CHANNELS = "L1_channels"`；`L1_RECALL` optional 列含 `recall_channels/n_channels/best_rank`。

- [ ] **Step 1: 写失败测试**

```python
# tests/trace/test_schema_recall.py
"""Phase 2: trace schema 含 L1_CHANNELS + L1_RECALL provenance 列。"""
from __future__ import annotations

from autoresearch.trace import schema


def test_l1_channels_schema_registered():
    assert schema.L1_CHANNELS == "L1_channels"
    sch = schema.get_schema(schema.L1_CHANNELS)
    assert sch is not None
    assert set(sch.required) == {"channel", "code"}


def test_l1_recall_has_provenance_optional_cols():
    sch = schema.get_schema(schema.L1_RECALL)
    assert {"recall_channels", "n_channels", "best_rank"} <= set(sch.optional)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/trace/test_schema_recall.py -q`
Expected: FAIL(AttributeError: L1_CHANNELS）

- [ ] **Step 3: 实现 schema 改动**

`autoresearch/trace/schema.py`:在 key 常量区(`L2_RANK = "L2_rank"` 后)加:
```python
L1_CHANNELS = "L1_channels"   # Phase 2:各路 channel 召回名单(长表)
```
把 `_PROV_COLS` 定义加在 `_DISPLAY_COLS` 后:
```python
# Phase 2 多路召回 provenance(L1_RECALL 追加;有则带、无则不强求)
_PROV_COLS = ("recall_channels", "n_channels", "best_rank")
```
`L1_RECALL` 与 `L1_SCORED_FULL` 的 `optional` 末尾加 `*_PROV_COLS`。`SCHEMAS` dict 加:
```python
    L1_CHANNELS: ArtifactSchema(
        name=L1_CHANNELS, required=("channel", "code"),
        optional=("channel_rank", "channel_score"),
    ),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/trace/test_schema_recall.py -q`
Expected: PASS(2 passed）

- [ ] **Step 5: 提交**

```bash
git add autoresearch/trace/schema.py tests/trace/test_schema_recall.py
git commit -m "feat(trace): L1_CHANNELS schema + L1_RECALL provenance 列(Phase 2 Task 4)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: ScanConfig + CLI flags

**Files:**
- Modify: `autoresearch/scan/config.py:15-29`(加 4 字段 + to_dict 已 asdict 自动含)
- Modify: `autoresearch/scan/cli.py:33-41`(_config_from_args)、`:98-106`(_add_common_funnel_flags)
- Test: `tests/scan/test_config_recall.py`

**Interfaces:**
- Produces: `ScanConfig.recall_mode: str="multi"`、`recall_channels: list[str]|None=None`、`channel_quotas: dict[str,int]|None=None`、`channel_floors: dict[str,int]|None=None`。CLI `--recall-mode {multi,composite}`、`--recall-channels a,b,c`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_config_recall.py
"""Phase 2: ScanConfig 多路召回字段 + CLI 解析。"""
from __future__ import annotations

from autoresearch.scan.cli import build_parser
from autoresearch.scan.config import ScanConfig


def test_config_defaults_multi():
    cfg = ScanConfig()
    assert cfg.recall_mode == "multi"
    assert cfg.recall_channels is None
    assert "recall_mode" in cfg.to_dict()


def test_cli_parses_recall_mode_and_channels():
    args = build_parser().parse_args(["run", "2026-06-20", "--recall-mode", "composite",
                                      "--recall-channels", "composite,momentum,value"])
    assert args.recall_mode == "composite"
    assert args.recall_channels == "composite,momentum,value"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_config_recall.py -q`
Expected: FAIL(AttributeError: recall_mode / unrecognized arg）

- [ ] **Step 3: 实现 config 改动**

`autoresearch/scan/config.py` 在 `l2_model` 字段后加:
```python
    recall_mode: str = "multi"                       # L1 召回:multi(多路)| composite(单复合分,对拍)
    recall_channels: list[str] | None = None         # 启用的 channel 子集(None=全注册)
    channel_quotas: dict[str, int] | None = None     # 覆盖各路 quota(None=CHANNEL_DEFAULTS)
    channel_floors: dict[str, int] | None = None     # 覆盖各路 floor(None=CHANNEL_DEFAULTS)
```

- [ ] **Step 4: 实现 CLI 改动**

`autoresearch/scan/cli.py::_add_common_funnel_flags` 末尾加:
```python
    p.add_argument("--recall-mode", choices=["multi", "composite"], default="multi",
                   help="L1 召回:multi=多路策略召回(默认)| composite=单复合分(对拍/回退)")
    p.add_argument("--recall-channels", default=None,
                   help="启用的 channel 子集(逗号分隔;缺省=全 8 路)")
```
`_config_from_args` 的 return 加:
```python
        recall_mode=args.recall_mode,
        recall_channels=(args.recall_channels.split(",") if args.recall_channels else None),
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_config_recall.py -q`
Expected: PASS(2 passed）

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/config.py autoresearch/scan/cli.py tests/scan/test_config_recall.py
git commit -m "feat(scan): ScanConfig 多路召回字段 + CLI flags(Phase 2 Task 5)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 接入 universe.run(live staging) + L1Recall Stage(trace)

**Files:**
- Modify: `autoresearch/scan/universe.py:208-251`(run 签名加 `recall_mode`/`recall_channels`;recall 块分支;keep 列加 provenance;multi 时写 `L1_channels.csv`)
- Modify: `autoresearch/scan/cli.py:55-57`(cmd_run 传 recall_mode/recall_channels 给 universe.run)
- Modify: `autoresearch/scan/stages/l1_recall.py:43-75`(run 加 recall_mode 分支 + 写 L1_CHANNELS)
- Test: `tests/scan/test_recall_wiring.py`

**Interfaces:**
- Consumes: `build/registered_channels/CHANNEL_DEFAULTS/quota_union`(recall 包)；`composite_score`(scoring)。
- Produces: `universe.run(..., recall_mode="multi", recall_channels=None)` 写多路 staging + provenance 列;`L1Recall.run` multi 分支写 `L1_RECALL`(provenance)+ `L1_CHANNELS`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_recall_wiring.py
"""Phase 2 接线:universe.run multi 产 provenance + L1_channels;composite 走旧路径;
L1Recall stage multi 写 3 产物。NO network(patch universe 取数 + vol_series)。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import tushare_source
from autoresearch.scan import universe as smu
from autoresearch.scan.config import ScanConfig
from autoresearch.scan.context import RunContext
from autoresearch.scan.pipeline import Pipeline
from autoresearch.trace import schema
from autoresearch.trace.store import TraceStore
from tests.scan._synth_universe import synth_universe

DATE = "2026-06-20"


@pytest.fixture
def patched(monkeypatch):
    uni = synth_universe(n=600, seed=7)
    monkeypatch.setattr(tushare_source, "fetch_universe_tushare",
                        lambda *a, **k: uni.copy(), raising=True)
    monkeypatch.setattr(smu, "_harvest_vol_series",
                        lambda codes, d, lookback=20: pd.DataFrame(columns=["code"]), raising=True)
    import autoresearch.research.factor_lab as fl
    monkeypatch.setattr(fl, "GBDT_MODEL", "/nonexistent/x.pkl", raising=False)
    return uni


def test_universe_run_multi_writes_provenance_and_channels(patched, tmp_path):
    out = tmp_path / "scan"
    smu.run(DATE, recall_n=300, l2_n=100, outdir=out, recall_mode="multi")
    l1 = pd.read_csv(out / "L1_recall_top1000.csv", dtype={"code": str})
    assert "recall_channels" in l1.columns and "n_channels" in l1.columns
    assert len(l1) == 300
    assert (out / "L1_channels.csv").exists()


def test_universe_run_composite_mode_no_provenance(patched, tmp_path):
    out = tmp_path / "scan_c"
    smu.run(DATE, recall_n=300, l2_n=100, outdir=out, recall_mode="composite")
    l1 = pd.read_csv(out / "L1_recall_top1000.csv", dtype={"code": str})
    assert "recall_channels" not in l1.columns        # 旧路径不带 provenance
    assert l1["composite"].is_monotonic_decreasing    # 纯 composite 降序


def test_l1recall_stage_multi_writes_channels(patched, tmp_path):
    store = TraceStore(tmp_path / "trace")
    ctx = RunContext(analysis_date=DATE, trace=store,
                     config=ScanConfig(recall_n=300, l2_n=100, recall_mode="multi"))
    Pipeline().run(ctx)
    l1 = store.get_df(ctx.run_id, schema.L1_RECALL)
    assert "n_channels" in l1.columns and len(l1) == 300
    chans = store.get_df(ctx.run_id, schema.L1_CHANNELS)
    assert set(chans["channel"].unique()) and "code" in chans.columns
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_recall_wiring.py -q`
Expected: FAIL(universe.run 无 recall_mode 参数 / L1_channels.csv 不存在）

- [ ] **Step 3: 抽出共用 recall helper(给两个编排器复用)**

在 `autoresearch/scan/universe.py`，于 `_harvest_vol_series` 后加(单一来源,L1Recall 也 import):
```python
def recall_select(scored: pd.DataFrame, analysis_date: str, recall_n: int,
                  recall_mode: str = "multi", recall_channels=None):
    """L1 召回:multi=多路 quota_union(provenance)| composite=单复合分降序 top-n。

    返回 (recall_df, per_channel_long|None)。composite 模式逐值复现今天(parity 锚点)。
    """
    if recall_mode == "composite":
        recall = scored.sort_values("composite", ascending=False).head(recall_n).reset_index(drop=True)
        return recall, None
    from autoresearch.scan.recall import CHANNEL_DEFAULTS, build, quota_union, registered_channels
    names = recall_channels or registered_channels()
    frames = {n: build(n)(scored, analysis_date, CHANNEL_DEFAULTS[n].quota) for n in names}
    recall, per_channel = quota_union(frames, CHANNEL_DEFAULTS, recall_n, scored)
    return recall, per_channel
```

- [ ] **Step 4: universe.run 用 helper + 写 provenance/channels**

`autoresearch/scan/universe.py::run`:签名加 `recall_mode: str = "multi", recall_channels=None`。把第 232 行
```python
    recall = scored.sort_values("composite", ascending=False).head(recall_n).reset_index(drop=True)
```
换成:
```python
    recall, per_channel = recall_select(scored, analysis_date, recall_n, recall_mode, recall_channels)
```
`keep` 列表(第 238 行起)末尾追加 provenance(有则带):
```python
    keep = keep + [c for c in ("recall_channels", "n_channels", "best_rank") if c in recall.columns]
```
在写 `L1_recall_top1000.csv` 后加(multi 才有 per_channel):
```python
    if per_channel is not None and len(per_channel):
        per_channel.to_csv(outdir / "L1_channels.csv", index=False)
```

- [ ] **Step 5: cmd_run 传参**

`autoresearch/scan/cli.py::cmd_run` 的 `smu.run(...)` 调用加:
```python
                  recall_mode=cfg.recall_mode, recall_channels=cfg.recall_channels,
```

- [ ] **Step 6: L1Recall Stage 用 helper + 写 L1_CHANNELS**

`autoresearch/scan/stages/l1_recall.py::run`:把第 56 行
```python
        recall = scored.sort_values("composite", ascending=False).head(recall_n).reset_index(drop=True)
```
换成:
```python
        from autoresearch.scan.universe import recall_select
        recall, per_channel = recall_select(
            scored, ctx.analysis_date, recall_n,
            ctx.config.recall_mode, ctx.config.recall_channels)
```
`_KEEP`-based `recall_full` 已含全列(provenance 列经 rest 自动带上,因 `rest = [c for c in recall.columns if c not in keep_first]`)。在 `ctx.trace.put_df(... L1_RECALL ...)` 后加:
```python
        if per_channel is not None and len(per_channel):
            ctx.trace.put_df(ctx.run_id, schema.L1_CHANNELS, per_channel)
```
`outputs()` 返回值加 `schema.L1_CHANNELS`(条件输出;pipeline 续跑判定对 multi 才有——简单起见恒列出,multi 必写、composite 不写则续跑不跳过该段,可接受)。

> 注:`L1Recall.outputs()` 列出 `L1_CHANNELS` 后,composite 模式不写它 → `_can_skip` 永 False(无害,只是 composite 模式不参与续跑跳过)。

- [ ] **Step 7: 跑测试确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_recall_wiring.py -q`
Expected: PASS(3 passed）

- [ ] **Step 8: 提交**

```bash
git add autoresearch/scan/universe.py autoresearch/scan/cli.py autoresearch/scan/stages/l1_recall.py tests/scan/test_recall_wiring.py
git commit -m "feat(scan): 多路召回接入 universe.run(staging) + L1Recall(trace)(Phase 2 Task 6)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: golden parity 保持(composite 模式 diff=0)

**Files:**
- Modify: `autoresearch/scan/parity.py:50-59`(capture 强制 composite)
- Modify: `tests/scan/test_parity.py:128-131,166-177`(check 侧 config 加 recall_mode="composite")
- Test: `tests/scan/test_parity.py`(既有 4 测试 + 新 multi 行为测试）

**Interfaces:**
- Consumes: `recall_select`(composite 分支)。
- Produces: parity capture/check 两侧均 composite → diff=0 不变;新增 multi 模式行为断言(多样性/provenance)。

- [ ] **Step 1: capture 强制 composite**

`autoresearch/scan/parity.py::capture` 的 `smu.run(...)` 调用加 `recall_mode="composite"`(golden 锚定旧单复合分行为):
```python
    smu.run(date, cap_floor_yi=cfg.cap_floor, include_bj=cfg.include_bj,
            recall_n=cfg.recall_n, l2_n=cfg.l2_n, outdir=out, source=cfg.source,
            recall_mode="composite")
```

- [ ] **Step 2: check 侧测试 config 加 composite**

`tests/scan/test_parity.py`:
- `_run_new`:`ScanConfig(recall_n=1000, l2_n=200)` → `ScanConfig(recall_n=1000, l2_n=200, recall_mode="composite")`。
- `test_golden_parity_via_parity_module`:两处 `ScanConfig(recall_n=1000, l2_n=200)` → 加 `recall_mode="composite"`。

- [ ] **Step 3: 跑既有 parity 测试确认仍绿**

Run: `uv run --no-sync python -m pytest tests/scan/test_parity.py -q`
Expected: PASS(4 passed;composite 模式逐值复现旧路径）

- [ ] **Step 4: 写 multi 模式行为测试(并确认失败再补)**

在 `tests/scan/test_parity.py` 末尾加:
```python
def test_multi_mode_differs_and_has_provenance(patched_universe, tmp_path):
    """multi 模式:产 provenance、多样性(并集含 composite 未必 top 的票),且仍恰 recall_n。"""
    from autoresearch.scan.context import RunContext
    from autoresearch.scan.pipeline import Pipeline
    from autoresearch.trace.store import TraceStore

    store = TraceStore(tmp_path / "trace_multi")
    ctx = RunContext(analysis_date=DATE, trace=store,
                     config=ScanConfig(recall_n=300, l2_n=100, recall_mode="multi"))
    Pipeline().run(ctx)
    l1 = store.get_df(ctx.run_id, schema.L1_RECALL)
    assert len(l1) == 300 and "n_channels" in l1.columns
    assert (l1["n_channels"] >= 1).all()        # 每只至少被一路召回(或 backfill=0)或回填
    chans = store.get_df(ctx.run_id, schema.L1_CHANNELS)
    assert chans["channel"].nunique() >= 5      # 多路确实跑了
```

> 若 `(l1["n_channels"] >= 1).all()` 因 backfill(n_channels=0)失败 → 改断言为 `(l1["n_channels"] >= 0).all()` 且 `(l1["n_channels"] >= 1).sum() >= 200`(主体来自 channel,尾部 backfill 容许)。

Run: `uv run --no-sync python -m pytest tests/scan/test_parity.py::test_multi_mode_differs_and_has_provenance -q`
Expected: PASS

- [ ] **Step 5: 全 scan 测试回归**

Run: `uv run --no-sync python -m pytest tests/scan/ -q`
Expected: PASS（全绿）

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/parity.py tests/scan/test_parity.py
git commit -m "test(scan): golden parity 锚定 composite 模式 + multi 行为测试(Phase 2 Task 7)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 文档(SKILL.md + screening-playbook.md)

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`(L1 行 → 多路召回 + provenance)
- Modify: `.claude/skills/scan-market/screening-playbook.md`(L1 段叙述 + provenance 给 L3)

- [ ] **Step 1: 更新 SKILL.md 的 L1 描述**

把六段表的 L1 行从「富因子复合分(T+1 IC 校准)」改为「**多路策略召回**(8 channel quota union;composite/momentum/reversal/growth/value/main_fund/northbound/accumulation)→ provenance(recall_channels/n_channels)」;流程 1 的命令加 `[--recall-mode multi|composite] [--recall-channels ...]`,并注明产物多了 `L1_channels.csv` + L1 带 provenance 列。

- [ ] **Step 2: 更新 screening-playbook.md**

L1 段说明:召回不再单复合分排序,而是多路 channel 各取 top-Kᶜ → quota union(floor 保底多样性)→ recall_n;每只带 `recall_channels`/`n_channels`(共识路数)。提示 L3 holistic 可用 `n_channels` 作「几路共振」信号(Phase 3 会正式接)。注明 `recall_mode=composite` 为对拍/回退口径。

- [ ] **Step 3: 提交**

```bash
git add .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/screening-playbook.md
git commit -m "docs(scan): 多路召回 skill 文档(Phase 2 Task 8)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 收尾验证(全部 task 后)
- [ ] `uv run --no-sync python -m pytest tests/ -q` → 全绿(含原 328 + 新增）。
- [ ] `uv run --no-sync ruff check autoresearch/scan/recall autoresearch/scan/universe.py autoresearch/scan/stages/l1_recall.py` → All checks passed。
- [ ] `uv run --no-sync python -m autoresearch.scan check 2026-06-20 --golden <已有 golden>` → parity ok(若本地有 golden;否则跳过,靠 pytest parity）。

## Self-Review(写完即查)
- **Spec 覆盖**:8 channel(Task 2)✓ · quota union(Task 3)✓ · provenance/trace(Task 3/4/6)✓ · recall_mode composite 对拍(Task 7)✓ · config/CLI(Task 5)✓ · 接入 staging+trace 两路(Task 6)✓ · L2 自由重排(不动 L2,✓ 默认)· 文档(Task 8)✓。
- **类型一致**:`gate_rank(frame, mask, score_col, k)` / `quota_union(channel_frames, defaults, recall_n, base_frame)` / `recall_select(scored, date, recall_n, mode, channels) -> (recall, per_channel|None)` / `CHANNEL_DEFAULTS[name].quota|floor` —— 全文一致。
- **placeholder 扫描**:无 TBD;每步含完整代码/命令/期望。
