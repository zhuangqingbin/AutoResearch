# scan-market v2 · Phase 1 — 召回内核(factor_lab T+1 校准 + L1 复合分)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 L1 从「四透镜并集」换成「轻门 → 行业条件化复合分 → top1000 召回集」,权重由 `factor_lab` 用 T+1 IC 校准并按行业层级收缩,落 `weights.json`。

**Architecture:** 确定性、零 LLM。`tushare_source` 多取富因子 → `screen_market` Step A 轻门 + Step B 复合分(读 weights.json)→ 召回 CSV;`factor_lab` 扩 `calibrate` 模式产 weights.json。全部离线 selftest + 缓存迭代。

**Tech Stack:** Python, pandas, numpy, tushare(venv-only,`uv run --no-sync`), 项目 `parse_rating`。

**Spec:** `docs/specs/2026-06-20-scan-market-v2-design.md`(§3 召回、§8 数据、§9 校准)。

**Phase 1 完成 = 可工作可测的软件:** `screen_market.py <date> --source tushare` 产出 `context/scan/<date>/L1_recall_top1000.csv`(复合分 + 8 子分 + 原始因子),`factor_lab.py calibrate` 产出 `weights.json`;两者 selftest 绿、ruff 绿。**不含** L2/L3/L5(P2/P3)。

**铁律(贯穿):** 不 commit 除非用户要;`uv run --no-sync`;akshare/tushare 不进 pyproject/uv.lock;不碰 `fred.py`/`test_fred.py`/编辑器垃圾;commit message 结尾 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `scripts/sw_sector_map.py` | 申万/东财行业 → ~7 大类板块映射 + `super_sector(label)` | 新建 |
| `scripts/tushare_source.py` | 富因子 bulk 取数(moneyflow 结构 / cyq 全分布 / 扩展技术 / hk_hold) | 改 |
| `scripts/screen_market.py` | L0 含北交所;L1 = 轻门 + 复合分(读 weights.json);召回 CSV | 改 |
| `scripts/factor_lab.py` | `calibrate` 模式:逐因子+逐行业 T+1 IC → 层级收缩 → weights.json | 改 |
| `context/factor_lab/weights.json` | 校准产物(行业→因子→权重 + 元信息) | 生成(gitignored) |

---

## Task 1: 大类板块映射(`sw_sector_map.py`)

**Files:**
- Create: `scripts/sw_sector_map.py`
- Test: 内置 `--selftest`(无 pytest 依赖,沿用项目 selftest 风格)

**背景:** universe 的 `industry` 来自 `stock_yjbb_em` 所处行业(东财口径,~80+ 标签,非申万一级)。收缩需要中间层「大类板块」。先用一张可扩映射把任意 industry 标签归到 ~7 大类,未知标签落 `其它`。

- [ ] **Step 1: 写映射 + 函数**

```python
#!/usr/bin/env python3
"""industry 标签(东财所处行业/申万一级)→ ~7 大类板块。用于 factor_lab 层级收缩的中间层。"""
from __future__ import annotations

# 大类板块(收缩中间层)。键 = 大类,值 = 命中子串(对 industry 标签做"包含"匹配,鲁棒于口径漂移)。
_SECTOR_RULES: dict[str, tuple[str, ...]] = {
    "周期资源": ("煤炭", "石油", "有色", "钢铁", "化工", "化学", "采掘", "建材", "建筑材料"),
    "制造": ("机械", "电力设备", "电气", "军工", "汽车", "新能源", "光伏", "电池", "装备", "工业"),
    "消费": ("食品", "饮料", "白酒", "家电", "家用电器", "纺织", "服装", "轻工", "商贸", "零售",
             "社会服务", "旅游", "酒店", "美容", "农林", "牧渔", "养殖", "食品饮料"),
    "医药": ("医药", "生物", "医疗", "中药", "器械"),
    "TMT成长": ("电子", "半导体", "计算机", "软件", "通信", "传媒", "互联网", "光模块", "消费电子"),
    "金融地产": ("银行", "保险", "证券", "非银", "金融", "房地产", "地产"),
    "公用": ("公用", "电力", "燃气", "水务", "环保", "交通运输", "港口", "高速", "机场", "运输"),
}


def super_sector(industry: str | None) -> str:
    """industry 标签 → 大类板块;无标签/未命中 → '其它'。"""
    s = str(industry or "")
    for sector, needles in _SECTOR_RULES.items():
        if any(n in s for n in needles):
            return sector
    return "其它"


def _selftest() -> int:
    cases = {
        "煤炭开采": "周期资源", "半导体": "TMT成长", "白酒": "消费", "中药Ⅱ": "医药",
        "股份制银行Ⅱ": "金融地产", "电力": "公用", "电池": "制造", "未知行业xyz": "其它",
        "": "其它", None: "其它",
    }
    fails = [f"{k!r}→{super_sector(k)} 期望 {v}" for k, v in cases.items() if super_sector(k) != v]
    if fails:
        print("SELFTEST ❌");  [print(" -", f) for f in fails];  return 1
    print(f"SELFTEST ✅  super_sector 映射 {len(cases)} 例全过");  return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)
```

- [ ] **Step 2: 跑 selftest,确认通过**

Run: `uv run --no-sync python scripts/sw_sector_map.py --selftest`
Expected: `SELFTEST ✅  super_sector 映射 10 例全过`

- [ ] **Step 3: ruff**

Run: `uv run --no-sync ruff check scripts/sw_sector_map.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add scripts/sw_sector_map.py
git commit -m "feat(scan-v2): 大类板块映射 super_sector(收缩中间层)"
```

> **校准期复核(Task 9 后):** 用真实 universe 的 `industry.value_counts()` 核对未命中标签,把落 `其它` 的大标签补进 `_SECTOR_RULES`。把这步记进 Task 9 的验收。

---

## Task 2: tushare_source 富因子取数

**Files:**
- Modify: `scripts/tushare_source.py`(`_fetch_factors` 扩字段;新增 `_fetch_moneyflow_struct`、`_fetch_hk_hold`;`fetch_universe_tushare` 合并新列)

**新增 canonical 增强列**(列存在才用,缺则 `_wsum` 重归一):
`main_net_ratio`(主力净占比)、`retail_net_yi`(散户净额)、`chip_concentration`(筹码集中度)、`price_to_cost`(现价/主力成本)、`rsi12`、`hk_ratio`。

- [ ] **Step 1: 写失败 selftest(字段映射/单位)**

在 `tushare_source.py` 末尾加离线 selftest(合成 DataFrame,不碰网络):

```python
def _selftest_struct() -> int:
    import pandas as pd
    # moneyflow 结构:小单≈散户、大+特大≈主力;net 用万元 /1e4=亿
    mf = pd.DataFrame({
        "ts_code": ["600000.SH"], "buy_lg_amount": [5000.0], "buy_elg_amount": [3000.0],
        "sell_lg_amount": [2000.0], "sell_elg_amount": [1000.0],
        "buy_sm_amount": [800.0], "sell_sm_amount": [1500.0], "amount": [200000.0],
    })
    s = _moneyflow_struct_cols(mf)  # 见 Step 2
    row = s.iloc[0]
    fails = []
    # 主力净 = (5000+3000-2000-1000)=5000 万元;占比 = 5000/200000
    if abs(row["main_net_ratio"] - 5000 / 200000) > 1e-9:
        fails.append(f"main_net_ratio={row['main_net_ratio']}")
    # 散户净 = (800-1500)= -700 万元 → -0.07 亿
    if abs(row["retail_net_yi"] - (-700 / 1e4)) > 1e-9:
        fails.append(f"retail_net_yi={row['retail_net_yi']}")
    if fails:
        print("SELFTEST ❌");  [print(" -", f) for f in fails];  return 1
    print("SELFTEST ✅  moneyflow 结构(主力净占比/散户净)单位正确");  return 0
```

- [ ] **Step 2: 写纯函数(可离线测)+ 取数包装**

```python
def _moneyflow_struct_cols(mf: pd.DataFrame) -> pd.DataFrame:
    """moneyflow 全单结构 → 主力净占比 + 散户净额(亿)。纯函数,便于离线测。"""
    g = lambda c: _num(mf[c]) if c in mf.columns else 0.0  # noqa: E731
    main_net = (g("buy_lg_amount") + g("buy_elg_amount") - g("sell_lg_amount") - g("sell_elg_amount"))
    amount = _num(mf["amount"]).replace(0, np.nan) if "amount" in mf.columns else np.nan
    retail_net = g("buy_sm_amount") - g("sell_sm_amount")
    return pd.DataFrame({
        "code": _code6(mf["ts_code"]),
        "main_net_ratio": (main_net / amount),       # 占成交额比(无量纲)
        "retail_net_yi": (retail_net / 1e4),          # 万元 → 亿
    })


def _fetch_moneyflow_struct(pro, last: str) -> pd.DataFrame | None:
    try:
        mf = _ts_call(lambda: pro.moneyflow(
            trade_date=last,
            fields="ts_code,buy_sm_amount,sell_sm_amount,buy_lg_amount,sell_lg_amount,"
                   "buy_elg_amount,sell_elg_amount,net_mf_amount,amount"))
        out = _moneyflow_struct_cols(mf)
        out["main_inflow_yi"] = _num(mf["net_mf_amount"]) / 1e4   # 复用原列
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[warn] moneyflow 结构取数失败({e!r})→ 资金结构因子降级", flush=True)
        return None
```

`_fetch_factors`:`cyq_perf` fields 加 `cost_15pct,cost_85pct`;算 `chip_concentration=(cost_85-cost_15)/cost_50`、`price_to_cost=close/cost_50`;`stk_factor_pro` fields 加 `rsi_qfq_12`。新增 `_fetch_hk_hold(pro,last)` 取 `hk_hold(trade_date=last, fields="ts_code,ratio")` → `hk_ratio`(失败返回 None)。
`fetch_universe_tushare`:把 `_fetch_moneyflow_struct`(替代原只取 net_mf_amount 的 `mf` 块)、扩展后的 `_fetch_factors`、`_fetch_hk_hold` 依次 merge。

- [ ] **Step 3: 跑 selftest**

Run: `uv run --no-sync python -c "import sys; sys.path.insert(0,'scripts'); import tushare_source as t; sys.exit(t._selftest_struct())"`
Expected: `SELFTEST ✅  moneyflow 结构(主力净占比/散户净)单位正确`

- [ ] **Step 4: 联网 smoke(取一日,确认列齐、无异常)**

Run: `perl -e 'alarm 90; exec @ARGV' uv run --no-sync python -c "import sys; sys.path.insert(0,'scripts'); from tushare_source import fetch_universe_tushare as f; df=f('2026-06-19'); print(sorted(c for c in ['main_net_ratio','retail_net_yi','chip_concentration','price_to_cost','rsi12','hk_ratio'] if c in df.columns)); print(len(df))"`
Expected: 打印出大部分新列名 + universe 行数(~4000+)。缺某列 = 该端点降级(可接受,记录之)。

- [ ] **Step 5: ruff + commit**

```bash
uv run --no-sync ruff check scripts/tushare_source.py
git add scripts/tushare_source.py
git commit -m "feat(scan-v2): tushare 富因子(主力净占比/散户净/筹码集中度/北向/RSI12)"
```

---

## Task 3: factor_lab — 富因子进 factor_frame + CANDIDATES

**Files:**
- Modify: `scripts/factor_lab.py`(`_FIELDS` 扩字段;`factor_frame` 算新因子列;`CANDIDATES` 加新因子,sign 待 IC 定)

- [ ] **Step 1: 扩 `_FIELDS`**

`moneyflow` fields 改为带全单结构(同 Task 2);`cyq_perf` 加 `cost_15pct,cost_85pct`;`stk_factor_pro` 加 `rsi_qfq_12`;新增 `hk_hold: "ts_code,ratio"`。

- [ ] **Step 2: `factor_frame` 算新因子**

在横截面构造里加:`main_net_ratio`、`retail_net_yi`、`chip_concentration`、`price_to_cost`、`rsi12`、`hk_ratio`(算法同 Task 2 的纯函数,直接 import 复用 `from tushare_source import _moneyflow_struct_cols`)。

- [ ] **Step 3: `CANDIDATES` 登记新因子**

```python
# (factor, sign) — sign 先按先验填,真权重由 calibrate 的 IC 符号定;这里只为 eval 出 IC 表
CANDIDATES += [
    ("main_net_ratio", +1), ("retail_net_yi", -1), ("chip_concentration", +1),
    ("price_to_cost", -1), ("rsi12", -1), ("hk_ratio", +1),
]
```

- [ ] **Step 4: selftest 仍绿(IC 数学不变)**

Run: `uv run --no-sync python scripts/factor_lab.py --selftest 2>&1 | tail -1`
Expected: 含 `SELFTEST ✅`(原 IC/板幅自测不受影响)。

- [ ] **Step 5: ruff + commit**

```bash
uv run --no-sync ruff check scripts/factor_lab.py
git add scripts/factor_lab.py
git commit -m "feat(scan-v2): factor_lab 纳入富因子(资金结构/筹码/北向/RSI12)候选"
```

---

## Task 4: factor_lab — 层级收缩函数(核心,可纯测)

**Files:**
- Modify: `scripts/factor_lab.py`(新增 `_shrink_weights`,纯函数)

- [ ] **Step 1: 写失败 selftest**

```python
def _selftest_shrink() -> int:
    # 大样本行业 → 接近自身 IC;小样本行业 → 拉向 parent
    fails = []
    w_big = _shrink_weights(ic_ind=0.10, n_ind=2000, ic_parent=0.02, ic_global=0.0, k=200)
    w_small = _shrink_weights(ic_ind=0.10, n_ind=20, ic_parent=0.02, ic_global=0.0, k=200)
    if not (abs(w_big - 0.10) < abs(w_small - 0.10)):
        fails.append(f"大样本应更贴自身 IC: big={w_big} small={w_small}")
    # n=0 → 完全回落到 global(此处 parent 再回落 global)
    w_zero = _shrink_weights(ic_ind=0.9, n_ind=0, ic_parent=0.0, ic_global=0.0, k=200)
    if abs(w_zero) > 1e-9:
        fails.append(f"n=0 应回落基准: {w_zero}")
    if fails:
        print("SELFTEST ❌");  [print(" -", f) for f in fails];  return 1
    print("SELFTEST ✅  层级收缩(大样本贴自身/小样本回落)正确");  return 0
```

- [ ] **Step 2: 写函数**

```python
def _shrink_weights(ic_ind: float, n_ind: int, ic_parent: float, ic_global: float, k: float = 200.0) -> float:
    """两级经验贝叶斯收缩:行业 IC → 大类 IC → 全市场 IC。

    λ = n/(n+k):样本足→贴行业自身;样本少→拉向 parent。parent 再以同式向 global 收缩
    (这里用固定 0.5 简化二级,k 控一级;实现可调)。返回收缩后的 IC(即该(行业,因子)权重基)。
    """
    lam1 = n_ind / (n_ind + k)
    parent = 0.5 * ic_parent + 0.5 * ic_global
    return lam1 * ic_ind + (1 - lam1) * parent
```

- [ ] **Step 3/4: 跑 selftest 通过 + ruff**

Run: `uv run --no-sync python -c "import sys; sys.path.insert(0,'scripts'); import factor_lab as f; sys.exit(f._selftest_shrink())"`
Expected: `SELFTEST ✅  层级收缩…正确`
Run: `uv run --no-sync ruff check scripts/factor_lab.py`

- [ ] **Step 5: Commit**

```bash
git add scripts/factor_lab.py
git commit -m "feat(scan-v2): factor_lab 层级收缩 _shrink_weights(经验贝叶斯)"
```

---

## Task 5: factor_lab — `calibrate` 模式 → weights.json

**Files:**
- Modify: `scripts/factor_lab.py`(新增 `calibrate()` + CLI `calibrate` 子命令)

- [ ] **Step 1: 写 calibrate(读缓存面板 → 逐因子+逐行业 T+1 IC → 收缩 → weights.json)**

```python
def calibrate(out_path: str = "context/factor_lab/weights.json", k: float = 200.0) -> dict:
    """用缓存面板算每因子对 T+1 的 rank-IC,按 super_sector + 申万/东财行业层级收缩 → weights.json。"""
    import json
    from pathlib import Path
    from sw_sector_map import super_sector

    frames = _load_cached_frames()                 # 复用 harvest 缓存(已有 _cache)
    panel = pd.concat(frames, ignore_index=True)
    panel["sector"] = panel["industry"].map(super_sector)
    factors = [f for f, _ in CANDIDATES]

    def _ic(df, col):                              # 横截面 rank-IC vs T+1,跨日均值
        sub = df.dropna(subset=[col, "fwd_1_oo"])
        if len(sub) < 30:
            return float("nan")
        return sub[col].rank().corr(sub["fwd_1_oo"].rank())

    ic_global = {f: panel.groupby("date").apply(lambda d, c=f: _ic(d, c)).mean() for f in factors}
    weights: dict[str, dict[str, float]] = {}
    for ind, gi in panel.groupby("industry"):
        sec = super_sector(ind)
        gp = panel[panel["sector"] == sec]
        w = {}
        for f in factors:
            ic_i = gi.groupby("date").apply(lambda d, c=f: _ic(d, c)).mean()
            ic_p = gp.groupby("date").apply(lambda d, c=f: _ic(d, c)).mean()
            shr = _shrink_weights(_nz(ic_i), len(gi), _nz(ic_p), _nz(ic_global[f]), k=k)
            w[f] = round(float(shr), 5)
        weights[ind] = w

    meta = {"horizon": "T+1_oo", "k": k, "n_dates": panel["date"].nunique(),
            "n_rows": len(panel), "factors": factors, "ic_global": {f: round(_nz(v), 4) for f, v in ic_global.items()}}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({"meta": meta, "weights": weights}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[calibrate] weights → {out_path}  (因子 {len(factors)} × 行业 {len(weights)}; 全市场 IC: {meta['ic_global']})")
    return {"meta": meta, "weights": weights}
```
(`_nz(x)` = `0.0 if pd.isna(x) else x`;`_load_cached_frames` 复用 harvest 缓存目录;若无 `fwd_1_oo` 列名,用 `forward_returns` 已有的 T+1 开盘列名。)

- [ ] **Step 2: CLI 加 `calibrate` 子命令**

`main()` 的 mode 分支加 `calibrate` → `calibrate()`。

- [ ] **Step 3: 干跑(需先有 harvest 缓存;无则先 harvest)**

Run: `uv run --no-sync python scripts/factor_lab.py harvest`(已有缓存则秒回)
Run: `uv run --no-sync python scripts/factor_lab.py calibrate`
Expected: 打印 `[calibrate] weights → context/factor_lab/weights.json …` + 各因子全市场 IC;文件生成。

- [ ] **Step 4: ruff + commit**

```bash
uv run --no-sync ruff check scripts/factor_lab.py
git add scripts/factor_lab.py
git commit -m "feat(scan-v2): factor_lab calibrate 模式 → weights.json(T+1 IC + 层级收缩)"
```

---

## Task 6: screen_market — L0 纳入北交所

**Files:** Modify `scripts/screen_market.py`(CLI 默认 + `run`/`fetch_*` 默认)

- [ ] **Step 1: 改默认**

`main()` 的 `--include-bj`:改为 `--exclude-bj`(`action="store_true"`),`include_bj=not args.exclude_bj`;`run`/`fetch_universe`/`fetch_universe_tushare`/`_apply_universe_gates` 的 `include_bj` 默认改 `True`。

- [ ] **Step 2: selftest 仍绿**

Run: `uv run --no-sync python scripts/screen_market.py --selftest 2>&1 | tail -1`
Expected: `SELFTEST ✅`(合成数据无北交所代码,行为不变;只验证默认翻转不破坏打分)。

- [ ] **Step 3: commit**

```bash
git add scripts/screen_market.py
git commit -m "feat(scan-v2): L0 选集阶段默认纳入北交所"
```

---

## Task 7: screen_market — Step A 轻门

**Files:** Modify `scripts/screen_market.py`(新增 `_recall_gate_a`)

- [ ] **Step 1: 写失败 selftest(轻门只去不可交易/无核心数据)**

```python
def _selftest_gate_a() -> int:
    import pandas as pd
    df = pd.DataFrame({
        "code": ["1", "2", "3", "4"], "name": ["a", "b", "c", "d"],
        "amount_yi": [5.0, 0.0, 3.0, 2.0], "close": [10, 10, None, 8],
        "pct_60d": [5, 5, 5, None], "main_inflow_yi": [1, 1, 1, None],
    })
    keep = _recall_gate_a(df)
    # 2 无成交额、3 无价 → 剔;1 保;4 缺非核心可保(有价有量有动量)
    got = set(df[keep]["code"])
    if got != {"1", "4"}:
        print(f"SELFTEST ❌  gate_a 保留 {got} 期望 {{1,4}}");  return 1
    print("SELFTEST ✅  Step A 轻门(只去不可交易/无核心数据)");  return 0
```

- [ ] **Step 2: 写函数**

```python
def _recall_gate_a(df: pd.DataFrame, min_amount_yi: float = 0.0) -> pd.Series:
    """召回轻门:只去真正不可交易/无核心数据的尾部(召回优先,尽量不误杀)。"""
    keep = df["amount_yi"].fillna(0) > min_amount_yi      # 有流动性/非停牌
    keep &= df["close"].notna()                            # 有价
    keep &= df["pct_60d"].notna() | df["pct_ytd"].notna()  # 有动量价(打分核心)
    return keep
```

- [ ] **Step 3/4: selftest + ruff**

Run: `uv run --no-sync python -c "import sys; sys.path.insert(0,'scripts'); import screen_market as s; sys.exit(s._selftest_gate_a())"`
Expected: `SELFTEST ✅  Step A 轻门…`

- [ ] **Step 5: commit**

```bash
git add scripts/screen_market.py
git commit -m "feat(scan-v2): L1 Step A 轻门 _recall_gate_a"
```

---

## Task 8: screen_market — Step B 行业条件化复合分

**Files:** Modify `scripts/screen_market.py`(新增 `_factor_groups`、`composite_score`,读 weights.json)

- [ ] **Step 1: 写失败 selftest(子分 0–100、复合分单调、缺权重回落全市场)**

```python
def _selftest_composite() -> int:
    import pandas as pd
    df = _fake_universe(50)                 # 复用现有 _selftest 的合成构造(抽成 helper)
    weights = {"meta": {"factors": ["mom", "fund"]},
               "weights": {"__global__": {"mom": 0.1, "fund": 0.05}}}
    out = composite_score(df, weights)
    sc = out["composite"]
    fails = []
    if not ((sc.dropna() >= 0).all() and (sc.dropna() <= 100).all()):
        fails.append(f"composite 越界 [{sc.min()},{sc.max()}]")
    for c in ("score_momentum", "score_fund_main", "score_chip"):
        if c not in out.columns:
            fails.append(f"缺子分列 {c}")
    if fails:
        print("SELFTEST ❌");  [print(" -", f) for f in fails];  return 1
    print("SELFTEST ✅  复合分(子分齐/0-100/缺权重回落)");  return 0
```

- [ ] **Step 2: 写 `_factor_groups`(原始因子 → 8 组子分,0–1 分位)+ `composite_score`(读权重、行业条件化、回落 `__global__`)**

```python
def _factor_groups(df: pd.DataFrame) -> dict[str, pd.Series]:
    """8 组子分(各 0–1 横截面分位)。缺列的组返回全 NaN,composite 自动重归一。"""
    g = df
    def p(col, asc=True):
        return _pct(g[col], ascending=asc) if col in g.columns else pd.Series(np.nan, index=g.index)
    return {
        "momentum": 0.6 * p("pct_60d") + 0.4 * p("pct_ytd"),
        "fund_main": p("main_net_ratio") if "main_net_ratio" in g else p("main_inflow_yi"),
        "fund_retail": p("retail_net_yi", asc=False),     # 散户净流出常为正向(符号由 IC 定,这里给分位)
        "chip": p("chip_concentration") * 0.5 + p("price_to_cost", asc=False) * 0.5,
        "north": p("hk_ratio"),
        "tech": p("rsi6", asc=False) * 0.5 + p("rsi12", asc=False) * 0.5,
        "growth": p("np_yoy") * 0.5 + p("rev_yoy") * 0.3 + p("roe") * 0.2,
        "value": _pct_within(g, "pe", "industry", ascending=False) if "pe" in g else pd.Series(np.nan, index=g.index),
    }


def composite_score(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """行业条件化复合分:每组子分 × (该行业的因子权重,缺则回落 __global__)。"""
    groups = _factor_groups(df)
    wmap = weights.get("weights", {})
    glob = wmap.get("__global__", {})
    out = df.copy()
    for name, series in groups.items():
        out[f"score_{name}"] = (series * 100).round(1)
    comp = pd.Series(0.0, index=df.index); wsum = pd.Series(0.0, index=df.index)
    for name, series in groups.items():
        w_by_ind = df["industry"].map(lambda ind, n=name: abs(wmap.get(ind, {}).get(n, glob.get(n, 0.0))))
        s = series.fillna(0.0); present = series.notna().astype(float)
        comp += s * w_by_ind; wsum += present * w_by_ind
    out["composite"] = (comp / wsum.replace(0, np.nan) * 100).round(1)
    return out
```
(子分组名 → weights.json 的 factor 键需对齐;calibrate 的 CANDIDATES 因子名与此处组名映射在实现时统一,必要时加 `_GROUP_FACTORS` 字典把组↔原始因子绑定。**Task 10 校准期对齐**。)

- [ ] **Step 3/4: selftest + ruff**

Run: `uv run --no-sync python -c "import sys; sys.path.insert(0,'scripts'); import screen_market as s; sys.exit(s._selftest_composite())"`
Expected: `SELFTEST ✅  复合分…`

- [ ] **Step 5: commit**

```bash
git add scripts/screen_market.py
git commit -m "feat(scan-v2): L1 Step B 行业条件化复合分 composite_score"
```

---

## Task 9: screen_market — 召回编排 + 输出 CSV

**Files:** Modify `scripts/screen_market.py`(`run()` 接 Step A→B→top1000;`aggregate_sectors` 降级为概览;输出 `L1_recall_top1000.csv`)

- [ ] **Step 1: 改 `run()`**

L0 universe → `_recall_gate_a` → 读 `weights.json`(缺则用内置先验 dict 兜底)→ `composite_score` → 按 `composite` 排序取 `--recall-n`(默认 1000)→ 写 `context/scan/<date>/L1_recall_top1000.csv`(`code,name,industry,composite,score_*` + 原始因子列)。`aggregate_sectors` 仍算但仅写 `sectors.csv` 供 L5 描述(不再做截断)。`meta.json` 加 `recall_n`、`weights_asof`(读 weights.json meta)。

- [ ] **Step 2: 加 `--recall-n` CLI(默认 1000);selftest 串起 gate_a+composite**

扩 `_selftest`:合成 universe → `_recall_gate_a` → `composite_score`(内置先验权重)→ 断言输出行数 ≤ recall-n、`composite` 降序、子分列齐。

- [ ] **Step 3: selftest 全绿**

Run: `uv run --no-sync python scripts/screen_market.py --selftest 2>&1 | tail -1`
Expected: `SELFTEST ✅`(含召回编排断言)。

- [ ] **Step 4: 联网干跑(真出召回集)**

Run: `perl -e 'alarm 180; exec @ARGV' uv run --no-sync python scripts/screen_market.py 2026-06-19 --source tushare`
Expected: `[done] … L1_recall_top1000.csv`;打开确认 ~1000 行、composite + 8 子分 + 原始因子齐。
**验收附加(Task 1 复核):** `python -c "...; print(df['industry'].value_counts())"` 看落 `其它` 的大标签,补 `_SECTOR_RULES`。

- [ ] **Step 5: ruff + commit**

```bash
uv run --no-sync ruff check scripts/screen_market.py
git add scripts/screen_market.py scripts/sw_sector_map.py
git commit -m "feat(scan-v2): L1 召回编排(轻门→复合分→top1000)+ 板块降级为概览"
```

---

## Task 10: 实跑校准 + 定初版 weights.json(经验迭代,非固定代码)

> 这是研究任务,产物 data-dependent。验收 = 报告 + 文件,不是固定断言。

- [ ] **Step 1: harvest 全市场面板(一次,~258 调用,缓存)**

Run: `perl -e 'alarm 1200; exec @ARGV' uv run --no-sync python scripts/factor_lab.py harvest`

- [ ] **Step 2: calibrate + eval,看各因子 T+1 IC/IC-IR + 十分位价差**

Run: `uv run --no-sync python scripts/factor_lab.py calibrate`
Run: `uv run --no-sync python scripts/factor_lab.py eval 2>&1 | tail -30`

- [ ] **Step 3: 迭代纪律**(沿用 v1 砍 vol_ratio/winner_rate 的做法)
  - 只保留两半样本 IC 符号一致、|IC-IR| 达阈(如 >0.1)的因子;不稳的权重收缩到 ~0。
  - 对齐 `composite_score` 组名 ↔ CANDIDATES 因子名(`_GROUP_FACTORS`)。
  - 核对 `super_sector` 未命中大标签并补全。

- [ ] **Step 4: 把结论写进 spec §实证 + 提交 weights.json 的 meta**

更新 `docs/specs/2026-06-20-scan-market-v2-design.md` 新增「§实证(P1 校准结果)」:各组 IC/IC-IR 表 + 收缩前后对比 + 保留/剔除决定 + 诚实局限。

- [ ] **Step 5: commit(spec + 校准说明;weights.json 在 context/ gitignored,不提交)**

```bash
git add docs/specs/2026-06-20-scan-market-v2-design.md
git commit -m "docs(scan-v2): P1 校准实证结果 + 初版权重决策"
```

---

## Task 11: Phase 1 集成验收

- [ ] **Step 1: 全 selftest 绿**

```bash
uv run --no-sync python scripts/sw_sector_map.py --selftest
uv run --no-sync python scripts/screen_market.py --selftest 2>&1 | tail -1
uv run --no-sync python scripts/factor_lab.py --selftest 2>&1 | tail -1
```
Expected: 三个 `SELFTEST ✅`。

- [ ] **Step 2: ruff 全绿**

Run: `uv run --no-sync ruff check scripts/sw_sector_map.py scripts/screen_market.py scripts/tushare_source.py scripts/factor_lab.py`
Expected: `All checks passed!`

- [ ] **Step 3: 端到端(召回集真出)**

Run: `perl -e 'alarm 180; exec @ARGV' uv run --no-sync python scripts/screen_market.py 2026-06-19 --source tushare`
Expected: `L1_recall_top1000.csv` ~1000 行,列齐;无 traceback。

- [ ] **Step 4: 不误提交检查**

Run: `git status --short`
Expected: 不含 `fred.py`/`test_fred.py`/`.DS_Store`/编辑器目录;`context/` 不进暂存。

---

## Self-Review(plan vs spec §3/§8/§9)

- ✅ 北交所(Task 6)· Step A 轻门(Task 7)· Step B 复合分+行业条件化(Task 8)· top1000 召回 CSV+带因子(Task 9)。
- ✅ 富因子:资金结构/散户/筹码集中度/北向/RSI12(Task 2/3)。
- ✅ T+1 IC 校准 + 申万/大类层级收缩 + weights.json(Task 4/5/10)。
- ✅ 大类映射 + industry 标签口径处理(Task 1 + Task 9 复核)。
- ⏭ L2 粗排 / L3 精排 / L4 研究 / L5 整合 + A_pipeline + 全量重命名 → **P2/P3 单独 plan**。
- 已知留白(spec 已声明,非 plan 缺陷):shrinkage k、组↔因子绑定、Step A 阈值 → Task 10 校准期定 + IC 验证。
