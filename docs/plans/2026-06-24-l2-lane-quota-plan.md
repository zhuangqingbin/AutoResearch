# L2 lane 配额 实现 plan

> Spec:`docs/specs/2026-06-24-l2-lane-quota-design.md`。分支 `l2-lane-quota`。TDD,合成 fixture 无网络。
> 全局约束:`l2_lane_quota` 默认 **0**(parity 锚);两条 L2 路径(`universe.run` staging / `L2Rank` trace)共用 helper;`uv run --no-sync python -m pytest`;ruff 干净。

---

## Task 1:共享 helper `apply_l2_lane_quota`(核心,确定性)

**Files**:Create `autoresearch/scan/recall/l2_quota.py`;Test `tests/scan/test_l2_quota.py`。
**Produces**:`apply_l2_lane_quota(ranked, l2_n, quota, lane_channels) -> pd.DataFrame`(+`l2_lane_reserved` 列)。

- [ ] **Step 1**:写失败测试 `tests/scan/test_l2_quota.py`:

```python
import pandas as pd
from autoresearch.scan.recall.l2_quota import apply_l2_lane_quota

def _ranked(n=10):
    # l2_score 降序;前若干无 lane,后段塞 momentum/heat 票 + 一个高 pct_60d
    rows = []
    for i in range(n):
        rows.append({"code": f"{i:06d}", "l2_score": 100 - i,
                     "recall_channels": "composite" if i < 6 else ("momentum" if i % 2 else "value"),
                     "pct_60d": 5.0 + (50.0 if i == 9 else 0.0)})
    return pd.DataFrame(rows)

def test_quota_zero_is_parity():
    r = _ranked(10)
    out = apply_l2_lane_quota(r, l2_n=5, quota=0, lane_channels=("momentum",))
    assert list(out["code"]) == [f"{i:06d}" for i in range(5)]
    assert out["l2_lane_reserved"].eq(False).all()

def test_quota_reserves_lane_below_cut():
    r = _ranked(10)                       # core_cut = 5-2 = 3
    out = apply_l2_lane_quota(r, l2_n=5, quota=2, lane_channels=("momentum",))
    assert len(out) == 5
    res = set(out[out["l2_lane_reserved"]]["code"])
    assert res, "应有被救回的 momentum 票"
    assert res <= set(out["code"])
    # 被救回的必是 momentum 通道、且在 core_cut 之下(rank>=3)
    for c in res:
        assert "momentum" in r.set_index("code").loc[c, "recall_channels"]

def test_hybrid_half_momentum_picks_high_pct60d():
    r = _ranked(10)                       # 009 momentum + pct_60d 巨高 → 动量半应入选
    out = apply_l2_lane_quota(r, l2_n=5, quota=2, lane_channels=("momentum",))
    assert "000009" in set(out[out["l2_lane_reserved"]]["code"])

def test_output_exactly_l2n_backfill_when_few_eligible():
    r = _ranked(10)
    out = apply_l2_lane_quota(r, l2_n=5, quota=2, lane_channels=("northbound",))  # 无 eligible
    assert len(out) == 5                  # 回填到 l2_n
    assert out["l2_lane_reserved"].eq(False).all()

def test_missing_recall_channels_degrades():
    r = _ranked(10).drop(columns=["recall_channels"])
    out = apply_l2_lane_quota(r, l2_n=5, quota=2, lane_channels=("momentum",))
    assert len(out) == 5 and out["l2_lane_reserved"].eq(False).all()
```

- [ ] **Step 2**:跑测试确认失败(`ModuleNotFoundError`)。
- [ ] **Step 3**:实现 `autoresearch/scan/recall/l2_quota.py`:

```python
#!/usr/bin/env python3
"""L2 lane 配额 —— 在 champion 单分排序后,给多样性 lane(momentum/heat/growth/accumulation)
保留 quota 席穿过 L2,使其到达 L3/L4 Claude 判断。design: docs/specs/2026-06-24-l2-lane-quota-design.md。
quota<=0 → 逐值复现 head(l2_n)(parity 锚)。"""
from __future__ import annotations

import pandas as pd


def _has_lane(s, lane_channels) -> bool:
    if not isinstance(s, str) or not s or s == "(backfill)":
        return False
    return bool(set(s.split("|")) & set(lane_channels))


def apply_l2_lane_quota(ranked: pd.DataFrame, l2_n: int, quota: int, lane_channels) -> pd.DataFrame:
    """ranked 已按 L2 分降序。返回恰 min(l2_n,len) 行 + l2_lane_reserved(bool)。见 spec §4.1。"""
    r = ranked.reset_index(drop=True).copy()
    if quota <= 0 or len(r) <= l2_n or "recall_channels" not in r.columns:
        out = r.head(l2_n).copy()
        out["l2_lane_reserved"] = False
        return out.reset_index(drop=True)
    core_cut = max(0, l2_n - quota)
    core, below = r.iloc[:core_cut], r.iloc[core_cut:]
    eligible = below[below["recall_channels"].map(lambda s: _has_lane(s, lane_channels))]
    n_score = quota // 2
    by_score = eligible.head(n_score)                                   # below 已降序 → 前 n_score = 分最高
    rest = eligible.drop(by_score.index)
    by_mom = (rest.sort_values("pct_60d", ascending=False, kind="stable")
              if "pct_60d" in rest.columns else rest).head(quota - n_score)
    reserve = pd.concat([by_score, by_mom])
    reserve_codes = set(reserve["code"])
    need = l2_n - len(core) - len(reserve)
    filler = below[~below["code"].isin(reserve_codes)].head(max(0, need))
    out = pd.concat([core, reserve, filler]).head(l2_n).copy()
    out["l2_lane_reserved"] = out["code"].isin(reserve_codes)
    return out.reset_index(drop=True)
```

- [ ] **Step 4**:跑测试确认通过。
- [ ] **Step 5**:`ruff check autoresearch/scan/recall/l2_quota.py tests/scan/test_l2_quota.py` 干净。
- [ ] **Step 6**:commit `feat(scan-l2): apply_l2_lane_quota helper(多样性 lane 穿过 L2)+ 单测`。

---

## Task 2:config + CLI 暴露 `l2_lane_quota` / `l2_lane_channels`

**Files**:Modify `autoresearch/scan/config.py`、`autoresearch/scan/cli.py`。
**Consumes**:无。**Produces**:`ScanConfig.l2_lane_quota:int=0`、`.l2_lane_channels:tuple=(...)`;CLI `--l2-lane-quota`/`--l2-lane-channels`。

- [ ] **Step 1**:`config.py` 在 `channel_floors` 后加:

```python
    l2_lane_quota: int = 0                            # L2 给多样性 lane 保留席(0=关=parity;建议 30)
    l2_lane_channels: tuple[str, ...] = ("momentum", "heat", "growth", "accumulation")
```

- [ ] **Step 2**:`cli.py` 的 run 子命令 argparse 加(对齐现有 `--recall-channels` 风格):

```python
    p.add_argument("--l2-lane-quota", type=int, default=0,
                   help="L2 给多样性 lane 保留席(0=关;建议 30)")
    p.add_argument("--l2-lane-channels", default=None,
                   help="逗号分隔;默认 momentum,heat,growth,accumulation")
```

并在构造 `ScanConfig`(cli.py:~41)处传:

```python
        l2_lane_quota=args.l2_lane_quota,
        l2_lane_channels=(tuple(args.l2_lane_channels.split(",")) if args.l2_lane_channels
                          else ("momentum", "heat", "growth", "accumulation")),
```

- [ ] **Step 3**:跑 `python -m autoresearch.scan run --help` 确认 flag 出现、无报错。
- [ ] **Step 4**:commit `feat(scan-l2): config+CLI 暴露 l2_lane_quota/channels(默认关)`。

---

## Task 3:接入 `universe.run` 的 L2(staging 路径,写 L2_gbdt_top200.csv)

**Files**:Modify `autoresearch/scan/universe.py`(L2 块 ~290-297;`run` 签名加参)。
**Consumes**:Task 1 helper、Task 2 config。

- [ ] **Step 1**:`run(...)` 签名加 `l2_lane_quota: int = 0, l2_lane_channels=("momentum","heat","growth","accumulation")`。
- [ ] **Step 2**:把 L2 块(universe.py:290-294)改为调 helper:

```python
    from autoresearch.scan.recall.l2_quota import apply_l2_lane_quota
    if scores is not None:
        ranked = recall.assign(gbdt_score=scores.to_numpy()).sort_values(
            "gbdt_score", ascending=False, kind="stable")
    else:
        ranked = recall.assign(gbdt_score=float("nan"))
    l2 = apply_l2_lane_quota(ranked, l2_n, l2_lane_quota, l2_lane_channels)
    l2.insert(0, "l2_rank", range(1, len(l2) + 1))
```

并把 `l2_lane_reserved` 加进 `l2_cols`(universe.py:296):
```python
    l2_cols = ["l2_rank", "gbdt_score", "l2_lane_reserved", *keep]
```
meta.json(universe.py:301-306)加 `"l2_lane_quota": l2_lane_quota`。

- [ ] **Step 3**:cli.py 调 `universe.run` 处把 `cfg.l2_lane_quota/channels` 传下去(若 cli 经 universe.run);确认 staging 路径拿到参数。
- [ ] **Step 4**:回归——`uv run --no-sync python -m pytest tests/scan -q` 全绿(尤其 parity/selftest:默认 Q=0 不破)。
- [ ] **Step 5**:commit `feat(scan-l2): universe.run L2 接 lane 配额(默认关,parity 保持)`。

---

## Task 4:接入 `L2Rank` stage(typed trace 路径)

**Files**:Modify `autoresearch/scan/stages/l2_rank.py`。
**Consumes**:Task 1 helper、config(`ctx.config.l2_lane_quota`)。

- [ ] **Step 1**:`L2Rank.run` 把 `sort_values(...).head(l2_n)` 改为:

```python
    from autoresearch.scan.recall.l2_quota import apply_l2_lane_quota
    ranked = recall.assign(l2_score=scores.to_numpy()).sort_values(
        "l2_score", ascending=False, kind="stable")
    l2 = apply_l2_lane_quota(ranked, l2_n, ctx.config.l2_lane_quota, ctx.config.l2_lane_channels)
    l2.insert(0, "l2_rank", range(1, len(l2) + 1))
```

`cols`(l2_rank.py:52)加 `"l2_lane_reserved"`;`put_meta` 加 `"l2_lane_quota"`。

- [ ] **Step 2**:回归 `uv run --no-sync python -m pytest tests/scan -q` 全绿;若有 `scan check` golden parity,默认 Q=0 应不破。
- [ ] **Step 3**:commit `feat(scan-l2): L2Rank stage 接 lane 配额(与 staging 口径一致)`。

---

## Task 5:下游可见性 + L3 lane 软指引(文档/留痕)

**Files**:Modify `.claude/skills/scan-market/screening-playbook.md`;(若 `l3_table_md`/`assemble` 不自动带 `l2_lane_reserved` 则补列)。

- [ ] **Step 1**:确认 `l3_select.l3_table_md` 读 L2 表时是否带出 `l2_lane_reserved`;不带则把它加进喂给 holistic 的紧凑表列。
- [ ] **Step 2**:`screening-playbook.md` L3 段补一句:`l2_lane_reserved=True`(配额救回的动量/题材票)judge 倾向打 `lane="trend"`,让 `trend_quota` 在 200→30 接住;并提醒 L4 照常用 rubric 三门定级(多数应被否,留尾部赢家)。
- [ ] **Step 3**:commit `docs(scan): L2 lane 配额下游可见性 + L3 lane 软指引`。

---

## 收尾

- [ ] 全套件 `uv run --no-sync python -m pytest -q` + `ruff check` 绿。
- [ ] 用 `finishing-a-development-branch` 呈现合并选项(默认 Q=0,合并零行为变化;实扫 `--l2-lane-quota 30` 才生效)。
