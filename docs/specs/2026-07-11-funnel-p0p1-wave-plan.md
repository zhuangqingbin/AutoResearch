# 漏斗 P0+P1 波实施计划(六问 brainstorm 已拍板)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 2026-07-11 漏斗六问 brainstorm 拍板的 P0(仪器修账/S1 温度计/目标锚/纸面法庭/提案过堂+配额接线)+ P1(L3/L4 prompt 三件套)+ 已批的买单 ensemble。

**Architecture:** 全部沿用现有分层——确定性件进 `autoresearch/`(纯 pandas、presence-gated、parity 不破),prompt 语义进 `.claude/agents/*.md`(契约锚由 `tests/test_agent_defs.py` 锁),编排改动只在 `.claude/workflows/scan-market.js`。每个提案自带账本裁决路径,不放宽任何 binding gate。

**Tech Stack:** Python 3 + pandas + pytest(`uv run --no-sync`,venv-only akshare/tushare/lightgbm);tushare `limit_list_d`(高权限 token);Claude Code workflow js。

**Spec:** `docs/specs/2026-07-11-funnel-six-questions-brainstorm.md`(§7 优先级矩阵 + §8 拍板记录)。

## Global Constraints

- 一切命令 `uv run --no-sync python -m ...`,仓库根目录跑。
- **parity 铁律**:新旗/新块全 presence-gated,默认关或缺数据时输出与改前逐字一致;`tests/scan/test_parity.py` golden 不许破。
- **不放宽 binding gates**;涨停数据只进温度计(负结果清单:不做打板/隔日溢价交易信号)。
- 账本改法一律**加列不改旧列**,回填幂等,样本 n 不清零(07-10 惯例)。
- 卡片/表格机器契约行(`**Rating**`、`FINAL TRANSACTION PROPOSAL`、`进入P4倾向`、`〔卡契约 v3`)不得改名;l4-card.md 改动必须同步 `stock-research/lite-playbook.md`(`tests/test_agent_defs.py::test_l4_card_contract_anchors_synced`)。
- L4 派发 prompt 的 **cache 前缀契约**:`_l4_prompt_<code>.md` = 固定标头→共享块→逐卡块,共享块之前不得出现逐卡可变内容(byte-identical 契约测试锁死);本波所有简报新行都加在**逐卡块内**。
- n<10 的校准/基率行标 ⚠禁注,不注入 prompt(sector_ledger 惯例)。
- 每 task 独立可测可提交;commit message 中文、结尾带 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 基线:1053 测试绿;每 task 结束跑该 task 的测试 + 波尾跑全量。

---

### Task 1: attribution 落盘 `bought` 列 + 历史回填(修 zero_buy 台账污染)

**Files:**
- Modify: `autoresearch/learning/retro.py`(`_KEEP` 白名单,约 :440-458;新增 `backfill_bought`;CLI 入口)
- Test: `tests/learning/test_retro_bought.py`(新)

**Interfaces:**
- Consumes: `attribute_frame()` 已算好的 `m["bought"]`(retro.py:64,`rating.isin(("Overweight","Buy"))`)。
- Produces: `attribution.csv` 永久含 `bought` 列;`backfill_bought(scan_root: Path|str|None = None) -> int`(返回补写文件数,幂等);CLI `python -m autoresearch.learning.retro backfill-bought`。`zero_buy_ledger.roll()`(:32 已容错读 `bought` 列)无需改动,自动去污。

- [ ] **Step 1: 写失败测试**

```python
# tests/learning/test_retro_bought.py
from pathlib import Path
import pandas as pd
from autoresearch.learning import retro


def _fake_attr_csv(tmp_path: Path, date: str, rating: str) -> Path:
    d = tmp_path / date / "retro"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"], "rating": [rating],
                  "fwd_2_oc": [0.01]}).to_csv(d / "attribution.csv", index=False)
    return d / "attribution.csv"


def test_keep_whitelist_contains_bought():
    assert "bought" in retro._KEEP


def test_backfill_bought_idempotent(tmp_path):
    p = _fake_attr_csv(tmp_path, "2026-07-08", "Overweight")
    n1 = retro.backfill_bought(scan_root=tmp_path)
    assert n1 == 1
    df = pd.read_csv(p)
    assert bool(df.loc[0, "bought"]) is True
    n2 = retro.backfill_bought(scan_root=tmp_path)   # 已有列 → 跳过
    assert n2 == 0


def test_backfill_bought_hold_is_false(tmp_path):
    p = _fake_attr_csv(tmp_path, "2026-07-09", "Hold")
    retro.backfill_bought(scan_root=tmp_path)
    assert bool(pd.read_csv(p).loc[0, "bought"]) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_retro_bought.py -v`
Expected: FAIL(`_KEEP` 无 bought / `backfill_bought` 不存在)。

- [ ] **Step 3: 最小实现**

retro.py:① `_KEEP` 列表加 `"bought"`(放 `"rating"` 旁);② 新函数:

```python
def backfill_bought(scan_root: Path | str | None = None) -> int:
    """历史 attribution.csv 补 `bought` 列(rating∈OW/Buy);已有列跳过 → 幂等。返回补写文件数。"""
    root = Path(scan_root) if scan_root else Path("context/scan")
    n = 0
    for p in sorted(root.glob("*/retro/attribution.csv")):
        df = pd.read_csv(p, dtype={"code": str})
        if "bought" in df.columns:
            continue
        df["bought"] = df.get("rating", pd.Series("", index=df.index)).astype(str).isin(_BUY)
        df.to_csv(p, index=False)
        n += 1
    return n
```

③ CLI(`main`/argparse 处)加子命令 `backfill-bought` 调它并打印补写数。

- [ ] **Step 4: 跑测试通过**;跑 `uv run --no-sync python -m pytest tests/learning/ -q` 无回归。
- [ ] **Step 5: 对真数据执行一次回填 + 目检**

Run: `uv run --no-sync python -m autoresearch.learning.retro backfill-bought && uv run --no-sync python -m autoresearch.learning.zero_buy_ledger`
Expected: 06-19/06-22/07-08 三日 `n_bought>0`,0 买日汇总重算(数字与旧值不同属预期,新值入 zero_buy_ledger.md)。

- [ ] **Step 6: Commit** `fix(learning): attribution 落盘 bought 列+幂等回填——zero_buy 台账把 3 个买单日记成 0 买的污染修复`

---

### Task 2: OW 三门结构化账本(建账)+ gate_ledger 左尾 KPI

**Files:**
- Modify: `autoresearch/learning/self_review.py`(`dump_gate_fires` 旁新增 `dump_ow_gate_fires`)
- Modify: `autoresearch/scan/assemble.py`(`_self_review_banner` :691 dump 处顺带调用)
- Modify: `autoresearch/learning/gate_ledger.py`(roll 加 `tail_rate`,render 加列)
- Test: `tests/learning/test_ow_gate_fires.py`(新)、`tests/learning/` 既有 gate_ledger 测试扩展

**Interfaces:**
- Consumes: `autoresearch.scan.assemble.gate_status(text) -> dict[str,bool]|None`(:222,**True=失守**;满卡才有三门行,早停卡返回 None)。
- Produces: `gate_fires.csv` 新行 `{"date","check","code","level"}`,check 取值 `OW三门·主力真在` / `OW三门·业绩真兑现` / `OW三门·估值不透支`,level=`binding`;(date,check,code) 幂等。`gate_ledger.roll()` 输出新列 `tail_rate`(被拦票 `fwd_2_oc ≤ -0.05` 占比;拍板 3:门=避雷器,KPI=左尾避免)。

- [ ] **Step 1: 写失败测试**

```python
# tests/learning/test_ow_gate_fires.py
from pathlib import Path
import pandas as pd
from autoresearch.learning import self_review

CARD = """# 决策卡 — 600000 测试 @ 2026-07-09
**Rubric建议**(评分卡派生): 6 维净分 +1/6 ｜ OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✓ → **建议 Hold**
**Rating**: Hold
"""

def test_dump_ow_gate_fires_appends_binding_rows(tmp_path):
    d = tmp_path / "2026-07-09"; (d / "details").mkdir(parents=True)
    (d / "details" / "600000.md").write_text(CARD, encoding="utf-8")
    n = self_review.dump_ow_gate_fires(d)
    assert n == 1
    df = pd.read_csv(d / "gate_fires.csv")
    row = df.iloc[-1]
    assert row["check"] == "OW三门·业绩真兑现" and row["code"] == "600000" and row["level"] == "binding"
    assert self_review.dump_ow_gate_fires(d) == 0     # 幂等


def test_gate_ledger_tail_rate(tmp_path):
    from autoresearch.learning import gate_ledger
    d = tmp_path / "2026-07-09"; (d / "retro").mkdir(parents=True)
    pd.DataFrame({"date": ["2026-07-09"], "check": ["OW三门·估值不透支"],
                  "code": ["000002"], "level": ["binding"]}).to_csv(d / "gate_fires.csv", index=False)
    pd.DataFrame({"code": ["000002"], "fwd_1_oo": [-0.06], "fwd_2_oc": [-0.08],
                  "fwd_5_oc": [-0.1]}).to_csv(d / "retro" / "attribution.csv", index=False)
    led = gate_ledger.roll(scan_root=tmp_path)
    assert "tail_rate" in led.columns
    assert led.iloc[0]["tail_rate"] == 1.0            # -8% ≤ -5% 左尾
```

- [ ] **Step 2: 跑测试确认失败**(函数/列不存在)。
- [ ] **Step 3: 实现**

self_review.py:

```python
def dump_ow_gate_fires(scan_dir: Path | str) -> int:
    """逐满卡解析 OW 三门失守 → gate_fires.csv 追加 binding 行((date,check,code) 幂等)。返回新增行数。"""
    from autoresearch.scan.assemble import gate_status
    scan_dir = Path(scan_dir); date = scan_dir.name
    fp = scan_dir / "gate_fires.csv"
    old = pd.read_csv(fp, dtype=str) if fp.exists() else pd.DataFrame(columns=["date", "check", "code", "level"])
    seen = {(r["date"], r["check"], r["code"]) for _, r in old.iterrows()}
    rows = []
    for card in sorted((scan_dir / "details").glob("*.md")):
        gates = gate_status(card.read_text(encoding="utf-8")) or {}
        code = card.stem.split(".")[0]
        for gate, failed in gates.items():
            key = (date, f"OW三门·{gate}", code)
            if failed and key not in seen:
                rows.append(dict(zip(("date", "check", "code"), key), level="binding"))
    if rows:
        pd.concat([old, pd.DataFrame(rows)], ignore_index=True).to_csv(fp, index=False)
    return len(rows)
```

(注意 `gate_status` 返回的门名 key——以其源码实际 key 为准拼 `OW三门·<key>`,先读 assemble.py:222-241 确认。)
assemble.py `_self_review_banner` 里 `self_review.dump_gate_fires(...)` 之后追一行 `self_review.dump_ow_gate_fires(scan_dir)`(同样 try/except IO 失败不阻发布)。
gate_ledger.py `roll()` 聚合处(:59-62)加:

```python
tail_rate=("fwd2_raw", lambda s: float((s.dropna() <= -0.05).mean()) if s.notna().any() else None),
```

其中 join 后先留原始列 `j["fwd2_raw"] = pd.to_numeric(j.get("fwd_2_oc"), errors="coerce")`;render 表头加 `拦对率(左尾≤-5%)` 列。

- [ ] **Step 4: 跑测试通过** + `uv run --no-sync python -m pytest tests/learning tests/scan -q` 无回归。
- [ ] **Step 5: 真数据冒烟**:`uv run --no-sync python -c "from autoresearch.learning.self_review import dump_ow_gate_fires; print(dump_ow_gate_fires('context/scan/2026-07-09'))"` → 应从 07-09 满卡(思特威复核卡)解析出 ≥0 行且不炸;再跑 `python -m autoresearch.learning.gate_ledger` 出新列。
- [ ] **Step 6: Commit** `feat(learning): OW三门结构化账本(gate_fires binding 行)+ gate_ledger 左尾 KPI——门=避雷器的记账口径落地`

---

### Task 3: frame --json 纯净 stdout(修 market_pack.json 日志污染)

**Files:**
- Modify: `autoresearch/scan/frame.py`(main :145-155 的信息行改 stderr)
- Test: `tests/scan/test_frame_json_clean.py`(新)

**Interfaces:**
- Produces: `frame <date> --json` 的 **stdout 只有一行 JSON**;`[frame]/[sentinel]/[macro_state]` 行全走 stderr(无 --json 时行为照旧可见,只是通道变了)。workflow 的 `frame ... --json > market_pack.json` 从此得到纯 JSON。

- [ ] **Step 1: 写失败测试**(monkeypatch 掉取数,不碰网络)

```python
# tests/scan/test_frame_json_clean.py
import json
import pandas as pd
from autoresearch.scan import frame


def test_json_stdout_is_pure_json(monkeypatch, capsys):
    df = pd.DataFrame({"code": ["000001"], "close": [10.0]})
    monkeypatch.setattr(frame, "build_market_frame", lambda d, **k: (df, {"universe_raw": 1, "universe": 1, "after_gate_a": 1}))
    monkeypatch.setattr("autoresearch.scan.market.market_pack_from_frame", lambda f: {"date": "2026-07-09"})
    monkeypatch.setattr(frame, "_sentinel_from_frame", lambda *a, **k: ("full", "ok"), raising=False)
    # 以 frame.main 实际调用的哨兵/macro_state helper 名为准 monkeypatch(读源码 :128-160)
    rc = frame.main(["2026-07-09", "--json"])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    json.loads(out)          # 整个 stdout 必须是可解析 JSON
```

- [ ] **Step 2: 跑测试确认失败**(stdout 混有 `[frame]` 行)。
- [ ] **Step 3: 实现**:main 里三处 `print(f"[frame]...")` / `print(f"[sentinel...]")` / `print(f"[macro_state]...")` 全部加 `file=sys.stderr`(无条件;人看终端不受影响)。
- [ ] **Step 4: 测试通过** + 修复现场:`uv run --no-sync python -c "import json;json.load(open('context/scan/2026-07-09/market_pack.json'))"` 目前会炸——手工把该文件头部日志行删掉使其可解析(一次性数据修复,commit message 里注明)。
- [ ] **Step 5: Commit** `fix(scan): frame --json 信息行改 stderr——market_pack.json 日志污染根修 + 07-09 现场清洗`

---

### Task 4: S1 温度计 · 数据与纯函数(limit_list_d 入湖 + 五序列 + 相位)

**Files:**
- Modify: `autoresearch/data/tushare_source.py`(新 `fetch_limit_list_d`)
- Create: `autoresearch/scan/temperature.py`
- Test: `tests/scan/test_temperature.py`(新,纯函数,零网络)

**Interfaces:**
- Consumes: tushare `pro.limit_list_d(trade_date=YYYYMMDD, fields="ts_code,trade_date,limit,limit_times,open_times")`,`limit` ∈ U(涨停)/D(跌停)/Z(炸板);`pro.daily(trade_date=..., fields="ts_code,pct_chg")` 算昨涨停今溢价;lake 写入走 `autoresearch.data.cache.lake_path("limit_list_d", {"trade_date": d})`(镜像 cache.py 既有 parquet 惯例)。
- Produces:
  - `fetch_limit_list_d(trade_date: str) -> pd.DataFrame`(落湖,重复调用湖命中)。
  - `temperature.daily_metrics(lu: pd.DataFrame, prev_lu: pd.DataFrame|None, today_pct: pd.DataFrame|None) -> dict`:键 `n_limit_up/n_limit_down/n_fried/max_streak/promote_rate/fried_rate/yesterday_premium`(输入缺 → 对应键 None,presence-gated)。
  - `temperature.score(metrics: dict) -> float|None`(0-100;任一核心键缺 → None)。
  - `temperature.phase(score: float|None, prev_score: float|None, prev_phase: str|None) -> str`:五相位 冰点/修复/发酵/高潮/退潮,带滞回。
  - `temperature.rollup(start: str, end: str) -> pd.DataFrame`(逐日 metrics+score+phase,写 `context/learning/temperature.csv`,幂等增量);CLI `python -m autoresearch.scan.temperature backfill <start>` 与 `show <date>`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_temperature.py
import pandas as pd
from autoresearch.scan import temperature as T


def _lu(rows):   # rows = [(code, limit, limit_times)]
    return pd.DataFrame([{"ts_code": c, "limit": l, "limit_times": t} for c, l, t in rows])


def test_daily_metrics_counts_and_promote():
    prev = _lu([("A.SZ", "U", 1), ("B.SZ", "U", 2), ("C.SZ", "U", 1)])
    today = _lu([("A.SZ", "U", 2), ("D.SZ", "U", 1), ("E.SZ", "Z", 0), ("F.SZ", "D", 1)])
    pct = pd.DataFrame({"ts_code": ["A.SZ", "B.SZ", "C.SZ"], "pct_chg": [10.0, -2.0, 1.0]})
    m = T.daily_metrics(today, prev, pct)
    assert m["n_limit_up"] == 2 and m["n_limit_down"] == 1 and m["n_fried"] == 1
    assert m["max_streak"] == 2
    assert abs(m["promote_rate"] - 0.5) < 1e-9        # 昨 1 板 2 只(A,C)→今 2 板 1 只(A)
    assert abs(m["fried_rate"] - 1 / 3) < 1e-9        # Z / (U+Z)
    assert abs(m["yesterday_premium"] - 3.0) < 1e-9   # 昨 U 三只今日均涨幅
def test_metrics_presence_gated():
    m = T.daily_metrics(_lu([("A.SZ", "U", 1)]), None, None)
    assert m["promote_rate"] is None and m["yesterday_premium"] is None


def test_score_bounds_and_none():
    hot = {"n_limit_up": 120, "max_streak": 7, "fried_rate": 0.1, "yesterday_premium": 4.0}
    cold = {"n_limit_up": 15, "max_streak": 2, "fried_rate": 0.45, "yesterday_premium": -2.0}
    assert T.score(hot) > 70 > T.score(cold) > 0
    assert T.score({"n_limit_up": None, "max_streak": 3, "fried_rate": 0.2, "yesterday_premium": 1}) is None


def test_phase_hysteresis():
    assert T.phase(15, None, None) == "冰点"
    assert T.phase(30, 15, "冰点") == "修复"           # 上行跨带
    assert T.phase(50, 30, "修复") == "发酵"
    assert T.phase(70, 50, "发酵") == "高潮"
    assert T.phase(55, 70, "高潮") == "退潮"           # 下行 → 退潮
    assert T.phase(41, 42, "发酵") == "发酵"           # 带内小幅回落(<3)不切 = 滞回
```

- [ ] **Step 2: 跑测试确认失败**(模块不存在)。
- [ ] **Step 3: 实现**(要点;完整写进模块 docstring:v1 权重为待校准先验,校准走 Task 5 的 calib 报告)

```python
# autoresearch/scan/temperature.py 核心
def _norm(v, lo, hi):
    if v is None or v != v: return None
    return max(0.0, min(1.0, (float(v) - lo) / (hi - lo)))

def score(m: dict) -> float | None:
    parts = [(_norm(m.get("n_limit_up"), 10, 150), 0.40),
             (_norm(m.get("max_streak"), 1, 8), 0.20),
             (None if m.get("fried_rate") is None else 1 - _norm(m["fried_rate"], 0.0, 0.5), 0.20),
             (_norm(m.get("yesterday_premium"), -3.0, 5.0), 0.20)]
    if any(p is None for p, _ in parts): return None
    return round(100 * sum(p * w for p, w in parts), 1)

_BANDS = [(0, 20, "冰点"), (20, 40, None), (40, 65, None), (65, 101, "高潮")]  # 中间两带按方向定名

def phase(s, prev_s, prev_phase):
    if s is None: return prev_phase or "未知"
    if prev_s is not None and abs(s - prev_s) < 3 and prev_phase:   # 滞回:±3 内不切
        return prev_phase
    rising = prev_s is None or s >= prev_s
    if s < 20: return "冰点"
    if s >= 65: return "高潮"
    if s >= 40: return "发酵" if rising else "退潮"
    return "修复" if rising else "退潮"
```

`daily_metrics`:U/D/Z 计数、`max_streak=limit_times.max()`(仅 U 行)、`promote_rate=今日 limit_times==k+1 且昨为 k 的只数 / 昨 limit_times==k 只数`(k 取昨日全体 U,按码 join)、`fried_rate=Z/(U+Z)`、`yesterday_premium=昨 U 码今日 pct_chg 均值`。`fetch_limit_list_d`:`_ts_call(lambda: pro.limit_list_d(...))` + lake parquet 读写(镜像 `cache.lake_path`)。`rollup`/CLI:trade_cal 逐日循环(限频友好,失败日跳过并记 warn),增量幂等写 `context/learning/temperature.csv`(列 `date,n_limit_up,n_limit_down,n_fried,max_streak,promote_rate,fried_rate,yesterday_premium,score,phase`)。

- [ ] **Step 4: 测试通过**。
- [ ] **Step 5: 真数据回填 ≥120 日**(高权限 token 可回填,S1 spec 验收①):

Run: `uv run --no-sync python -m autoresearch.scan.temperature backfill 2026-01-05`
Expected: `context/learning/temperature.csv` ≥120 行,近期 phase 多为 冰点/修复(与 risk_off 窗口自洽)。若 `limit_list_d` 无权限 → 打印明确降级信息并空 csv(presence-gated,下游全静默),plan 余下照常。

- [ ] **Step 6: Commit** `feat(scan): S1 情绪温度计数据层——limit_list_d 入湖+五序列/评分/五相位纯函数+回填 CLI(零 token)`

---

### Task 5: S1 温度计 · 消费(market_pack 块 + L5 展示 + prelude 步 + 校准报告)

**Files:**
- Modify: `autoresearch/scan/market.py`(`market_pack` :104 与 `market_pack_from_frame` :145 各加 `temperature` 块)
- Modify: `autoresearch/scan/assemble.py`(regime 行旁加 🌡 温度行,presence-gated)
- Modify: `autoresearch/scan/prelude.py`(:150 `all_steps` 加 `("temperature", _temperature)` 步:当日 fetch+rollup 增量)
- Create: `autoresearch/scan/temperature_calib.py`(温度分段 × 市场 fwd_1/fwd_2 条件分布 + 与 regime 交叉表 → `reports/research/temperature_calib.md`;S1 spec 验收①③的报告端)
- Test: `tests/scan/test_temperature_pack.py`(新)

**Interfaces:**
- Consumes: Task 4 的 `context/learning/temperature.csv`。
- Produces: market_pack 新键 `temperature: {"score","phase","trend5"} | None`(csv 缺/当日缺行 → 键不出现 = parity);assemble summary regime 行下一行 `🌡 情绪温度 41(发酵)·近5日 28→41`(缺数据 → 无此行);**本波不接菜单/预算联动**(拍板:展示先行,联动等相位质量复审 = 下一波)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_temperature_pack.py
import pandas as pd
from autoresearch.scan import market


def test_pack_has_temperature_when_csv_present(tmp_path, monkeypatch):
    csv = tmp_path / "temperature.csv"
    pd.DataFrame({"date": ["2026-07-08", "2026-07-09"], "score": [28.0, 41.0],
                  "phase": ["修复", "发酵"]}).to_csv(csv, index=False)
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH", csv)
    pack = market.market_pack_from_frame(None) or {}
    # 以 market_pack_from_frame 实际入参组装方式为准:temperature 块注入两个 pack 函数共用的 helper
    blk = market._temperature_block("2026-07-09")
    assert blk == {"score": 41.0, "phase": "发酵", "trend5": [28.0, 41.0]}


def test_pack_parity_without_csv(monkeypatch, tmp_path):
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH", tmp_path / "none.csv")
    assert market._temperature_block("2026-07-09") is None
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**:temperature.py 暴露模块级 `CSV_PATH`;market.py 新 `_temperature_block(date) -> dict|None`(读 csv 尾 5 行),两个 pack 组装处 `if blk: pack["temperature"] = blk`。assemble:`regime_and_drift` 输出旁(build_summary :719 附近)`if pack 或 csv 有当日行 → 插一行`,同函数内 presence-gated。prelude `_temperature`:`fetch_limit_list_d(date)+rollup 增量`,失败不阻断(prelude 惯例)。temperature_calib.py:读 temperature.csv × `paper_nav.market_nav` 日收益,按 phase 分组给 `n/市场次日均值/fwd_2 均值` + phase×regime 交叉表,n<10 行标 ⚠。
- [ ] **Step 4: 测试全绿** + parity:`uv run --no-sync python -m pytest tests/scan -q`。
- [ ] **Step 5: 真数据冒烟**:`python -m autoresearch.scan.temperature_calib` 出报告;对 `context/scan/2026-07-09` 重跑 `python -m autoresearch.scan.assemble 2026-07-09` 确认 🌡 行出现(有 csv)且其余逐字不变(diff 旧 summary 只多温度行)。
- [ ] **Step 6: Commit** `feat(scan): 温度计消费端——market_pack temperature 块+L5 🌡行+prelude 步+分段校准报告(展示先行,不接菜单/预算)`

---

### Task 6: 目标价 hi_2_oc 基率锚(校准 json + 简报注入 + 卡契约行)

**Files:**
- Modify: `autoresearch/learning/buy_ledger.py`(新 `hi2_calibration(scan_root=None, window=30) -> dict` + 写 `context/learning/target_calib.json`)
- Modify: `autoresearch/scan/agents/l4_card.py`(`write_dispatch_pack` 逐卡块注入 📐 行)
- Modify: `.claude/agents/l4-card.md` + `.claude/skills/stock-research/lite-playbook.md`(P5 目标价规则一句)
- Test: `tests/learning/test_target_calib.py`(新)

**Interfaces:**
- Consumes: `context/scan/*/retro/attribution.csv` 的 `hi_2_oc` 列(已存在)+ 各日 `meta.json` 的 regime。
- Produces: `target_calib.json` 形如 `{"all": {"n": 37, "hi2_p50": 0.021, "hi2_p60": 0.028, "touch8_rate": 0.11}, "by_regime": {"risk_off": {...}}}`(touch8 = hi_2_oc ≥ 8% 占比,即"旧中位目标在 2 日窗的真实触达率");简报逐卡块新行:
  `📐 目标校准:全体 2 日 MFE p60=+2.8%(n=37)·同 regime p60=+2.1%(n=21)——目标价超 p60 须在卡内给硬理由`(n<10 的分组不注,全体 n<10 整行不注)。
- 卡契约:l4-card.md P5/满卡说明加一句 `目标价默认 ≤ 简报 📐 p60 锚;超锚必须在三档情景旁写一行硬理由`(不加新机器契约行,`test_l4_card_contract_anchors_synced` 既有锚不动,新句子同步 lite-playbook)。

- [ ] **Step 1: 写失败测试**

```python
# tests/learning/test_target_calib.py
import json
from pathlib import Path
import pandas as pd
from autoresearch.learning import buy_ledger


def _day(tmp, date, hi2, regime="risk_off"):
    d = tmp / date; (d / "retro").mkdir(parents=True)
    pd.DataFrame({"code": [f"{i:06d}" for i in range(len(hi2))], "hi_2_oc": hi2}).to_csv(
        d / "retro" / "attribution.csv", index=False)
    (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")


def test_hi2_calibration_quantiles(tmp_path):
    _day(tmp_path, "2026-07-08", [0.01] * 6 + [0.05] * 4)
    _day(tmp_path, "2026-07-09", [0.02] * 10, regime="range")
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert out["all"]["n"] == 20
    assert 0.01 <= out["all"]["hi2_p60"] <= 0.05
    assert out["by_regime"]["range"]["n"] == 10


def test_thin_regime_dropped(tmp_path):
    _day(tmp_path, "2026-07-09", [0.02] * 5, regime="trend")   # n=5 <10 → 不出分组
    out = buy_ledger.hi2_calibration(scan_root=tmp_path, window=30)
    assert "trend" not in out["by_regime"]
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**:遍历近 window 个 `*/retro/attribution.csv`,concat `hi_2_oc` 有值行,regime 从同日 meta.json;分位用 `series.quantile(0.5/0.6)`;`touch8_rate=(s>=0.08).mean()`;n<10 分组丢弃;`write_target_calib()` 落 json。l4_card.py `write_dispatch_pack` 逐卡块组装处(:568 之后的逐卡段)读 json 组 📐 行(lazy/try 惯例,缺文件不挡简报)。两个 md 加规则句(l4-card.md 与 lite-playbook 同句)。
- [ ] **Step 4: 测试绿** + `pytest tests/test_agent_defs.py -q` 绿(锚未破)。
- [ ] **Step 5: 真数据**:`uv run --no-sync python -c "from autoresearch.learning.buy_ledger import write_target_calib; print(write_target_calib())"` → json 落盘,p60 应在 +2%~+5% 量级(与"中位 MFE +4%"自洽)。
- [ ] **Step 6: Commit** `feat(l4): 目标价 hi_2_oc 基率锚——target_calib.json+简报📐行+卡目标规则(治 43% 触达/目标 2× 过乐观)`

---

### Task 7: 纸面法庭一等公民 + 提案 nag(L5 两行)

**Files:**
- Modify: `autoresearch/scan/assemble.py`(build_summary :735-737 paper_nav 嵌入处 + 新 `_proposals_nag()`)
- Test: `tests/scan/test_assemble_court_nag.py`(新)

**Interfaces:**
- Produces: summary 固定两处(全 presence-gated):
  ① 漏斗数量节后 `🧾 纸面法庭:真实 −0.30%(7笔) vs 影子(若门不拦最想买3只) −4.65%(45笔) vs 市场 −5.83%(hold=2 主尺)`——直接引用 `reports/learning/paper_nav_summary.txt` 的 hold=2 主行(该 txt 由 paper_nav.main 写;若现状写的是 hold=10 行,paper_nav.main 改为主行=hold2、副行=hold10,加列不删行);
  ② 尾部新小节 `## ⏳ 待裁决提案`:读 `context/knowledge/proposals.jsonl`,列 status=="open" 的 `id + kind + 一句 title`,>0 条时出现,并附一行 `——满 20 交易日未裁将持续置顶提醒`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_assemble_court_nag.py
import json
from autoresearch.scan import assemble


def test_proposals_nag_lists_open(tmp_path, monkeypatch):
    p = tmp_path / "proposals.jsonl"
    p.write_text(json.dumps({"id": "pr_x", "status": "open", "kind": "factor", "title": "t"}) + "\n" +
                 json.dumps({"id": "pr_y", "status": "resolved", "kind": "quota", "title": "t2"}) + "\n",
                 encoding="utf-8")
    monkeypatch.setattr(assemble, "_PROPOSALS_PATH", p, raising=False)
    out = assemble._proposals_nag()
    assert "pr_x" in out and "pr_y" not in out


def test_proposals_nag_empty_is_blank(tmp_path, monkeypatch):
    monkeypatch.setattr(assemble, "_PROPOSALS_PATH", tmp_path / "none.jsonl", raising=False)
    assert assemble._proposals_nag() == ""
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**(nag 函数 ~15 行;纸面法庭行 = 现 paper_nav 嵌入行改造:主行加 `(若门不拦最想买3只)` 说明并确保 hold=2;proposals.jsonl 字段名以真实文件首行为准,实现前 `head -1 context/knowledge/proposals.jsonl` 校对)。
- [ ] **Step 4: 测试绿 + 对 2026-07-09 重跑 assemble diff**:只新增 🧾 说明与 ⏳ 小节,其余逐字不变。
- [ ] **Step 5: Commit** `feat(l5): 纸面法庭行(hold=2 主尺+影子语义标注)+待裁决提案 nag——反事实账本升一等公民`

---

### Task 8: channel_quotas/floors 接线 + 配额 advisory 应用 + rz 入组 + 提案过堂

**Files:**
- Modify: `autoresearch/scan/universe.py`(`recall_select` :215-233 与 `run` :269 透传 overrides)
- Modify: `autoresearch/scan/cli.py`(cmd_run :81-84 传 `channel_quotas=cfg.channel_quotas, channel_floors=cfg.channel_floors`;:39 注释改"已消费")
- Modify: `autoresearch/common/scoring.py`(`_GROUPS` 加 `"rz"`、`_PRIOR_WEIGHTS` 加 `"rz": 0.02`、组构造处 rz_buy_intensity→rz 组;缺列 NaN 降级重归一走既有机制)
- Modify: `.claude/skills/scan-market/scan_config.jsonc`(funnel 加 `"channel_quotas": {"value": 250, "heat": 150, "main_fund": 150}` 并去掉"未消费"注释)
- Modify: `context/knowledge/proposals.jsonl`(状态过堂,见 Step 6)
- Test: `tests/scan/test_channel_quota_override.py`(新)+ `tests/common/` 现有 scoring 测试扩一条 rz 组

**Interfaces:**
- Produces: `recall_select(..., channel_quotas: dict[str,int]|None = None, channel_floors: dict[str,int]|None = None)`——override 语义:`effective = {n: dataclasses.replace(CHANNEL_DEFAULTS[n], quota=q?, floor=f?) for ...}`,build 与 quota_union 都吃 effective;None = 现行为(parity,`tests/scan/test_parity.py` 不许破)。`universe.run` 同名参数透传。scoring 出新组 `rz`(自然朝向 +,真方向随 weights.json)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_channel_quota_override.py
import pandas as pd
from autoresearch.scan import universe


def _scored(n=300):
    return pd.DataFrame({"code": [f"{i:06d}" for i in range(n)],
                         "name": ["x"] * n, "composite": range(n, 0, -1),
                         "momentum": range(n), "value": range(n)})


def test_quota_override_shrinks_channel(monkeypatch):
    df = _scored()
    r_default, per_d = universe.recall_select(df, "2026-07-09", recall_n=200, mode="multi",
                                              channels=["composite", "heat"])
    r_cut, per_c = universe.recall_select(df, "2026-07-09", recall_n=200, mode="multi",
                                          channels=["composite", "heat"],
                                          channel_quotas={"heat": 10})
    assert len(per_c["heat"]) <= 10 < len(per_d["heat"])


def test_no_override_is_parity():
    df = _scored()
    a, _ = universe.recall_select(df, "2026-07-09", recall_n=200, mode="multi",
                                  channels=["composite", "heat"])
    b, _ = universe.recall_select(df, "2026-07-09", recall_n=200, mode="multi",
                                  channels=["composite", "heat"], channel_quotas=None, channel_floors=None)
    assert a["code"].tolist() == b["code"].tolist()
```

(`recall_select` 实际形参名以 :215 签名为准——`mode/channels` 若名不同,测试与实现同步用真名;heat 通道需要 `amount` 列则 fixture 补列。)

- [ ] **Step 2: 确认失败** → **Step 3: 实现**(dataclasses.replace 组 effective dict;run/cli 透传;scoring 加组;scan_config.jsonc 应用 advisory 三值——**拍板 5:heat 取 150 非提案的 100**)。
- [ ] **Step 4: 测试绿 + parity golden 绿**:`uv run --no-sync python -m pytest tests/scan tests/common -q`。
- [ ] **Step 5: 重校权重(rz 进组后)**:走既有可回滚通道

Run: `uv run --no-sync python -c "from autoresearch.learning.retro import recalibrate_and_log; print(recalibrate_and_log(note='rz 入组+quota advisory 应用(pr_20260710_001/pr_20260711_003)'))"`
Expected: weights.json 重算含 rz 组、changelog_ledger 记一条(快照可回滚)。(函数名/签名以 retro.py 实际为准;若签名不同,以 SKILL.md「重标定一律走 retro.recalibrate_and_log」的真实入口调。)

- [ ] **Step 6: 提案过堂(proposals.jsonl 逐条改 status,理由字段写明)**
  - `pr_20260710_001` → `resolved`:rz 已入组+重校(本 task)。
  - `pr_20260711_003` → `resolved`:heat quota 200→150(取 ledger advisory 档,非 100;经 scan_config channel_quotas 生效)。
  - `pr_20260624_001` → 保持 `open` + note:`本波以影子变体验证——universe 影子漏斗新增 capfloor20 变体`(下一步)。
  - `pr_20260625_001` / `pr_20260702_002` → 保持 `open` + note:`deferred:等 Task 2 三门账本 ≥20 日后随雷分级一并裁`。
  - universe.py 影子变体处(`nostrat/nocap/pre_healthy` 所在)加第 4 个变体 `capfloor20`(cap_floor_yi=20 重跑 L0-L1 落 staging,镜像既有变体写法;retro 对照自动捕获)。
- [ ] **Step 7: Commit** `feat(recall): channel_quotas/floors 真接线+advisory 应用(value250/heat150/main_fund150)+rz 入组重校+提案过堂(2 resolved/3 挂注)+capfloor20 影子变体`

---

### Task 9: L3 确定性三件(行指纹 / thesis 数字机检 / lane 分块渲染)

**Files:**
- Modify: `autoresearch/scan/agents/l3_select.py`(`_L3_COLS`/`compact_table`/`l3_table_md` + 新 `row_profile`、`lint_judged`;CLI 加 `lint` 子命令)
- Modify: `.claude/workflows/scan-market.js`(L3 agent 之后、finalists 之前插 lint→一次打回自修)
- Test: `tests/scan/test_l3_profile_lint.py`(新)

**Interfaces:**
- Produces:
  - `row_profile(r: dict) -> str`:确定性画像短语,`·`连接,词表固定:位置(高位≥40/中位≥10/低位>−10/深跌)、放量(vol_ratio≥2)、主力(主力+/主力背离/主力−/主力平:main_net_ratio 与 cmf_20/obv_mom_20 同向判)、估值(PE负/PE低<20/PE中<60/PE高)、筹码(满盈利⚠ winner≥90/深套牢 winner<25)、超买 rsi6≥80/超卖 ≤20。
  - 表新列 `pf`(`_L3_COLS` 在 `name` 后插入 `"pf"`;l3_table_md 组表时对每行算)。
  - 表渲染分块:按 `l2_lane_reserved` 非空值分块、其余行归 `recall_channels` 首通道块;每块一个 `### lane:<名>` 小标题,块内按 composite 降序;`meta` 尾行记 `render_order=lane_blocks`。
  - `lint_judged(date, root=None) -> dict`:`{"ok": bool, "reason": str}`;规则:每条 thesis 里的数字 token(排除 4 位年份/6 位代码/日期形如 07-15)必须能在该票行的数值列(±1% 相对或 ±0.1 绝对容差,百分数与小数互认)或 `catalyst` 字段里找到;找不到 → ok=False,reason 列 `code:数字`。CLI `python -m autoresearch.scan.agents.l3_select lint <date>` 打一行 JSON(GATE 惯例)。
- Consumes(workflow):`gate('l3-lint', ...)` + 失败时一个 `agentType:'l3-rank', effort:'medium'` 修复 agent(只修 reason 列出的票,Write 覆写 `_l3_judged.json`),修复后**不再二检**(一次打回上限,防循环)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_l3_profile_lint.py
import json
from autoresearch.scan.agents import l3_select as L


def test_row_profile_words():
    r = {"pct_60d": 45.0, "vol_ratio": 2.5, "main_net_ratio": 1.2, "cmf_20": 0.1,
         "obv_mom_20": 0.2, "pe": 15.0, "winner_rate": 95.0, "rsi6": 85.0}
    p = L.row_profile(r)
    assert p == "高位·放量·主力+·PE低·满盈利⚠·超买"


def test_lint_judged_catches_misquote(tmp_path):
    d = tmp_path / "2026-07-09"; d.mkdir(parents=True)
    (d / "_l3_table.md").write_text("stub", encoding="utf-8")
    import pandas as pd
    pd.DataFrame({"code": ["000001"], "pct_60d": [12.0], "pe": [30.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "60日涨12%,PE 30 合理", "catalyst": ""}]), encoding="utf-8")
    assert L.lint_judged("2026-07-09", root=tmp_path)["ok"] is True
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "000001", "thesis": "60日涨35%,PE 30", "catalyst": ""}]), encoding="utf-8")
    res = L.lint_judged("2026-07-09", root=tmp_path)
    assert res["ok"] is False and "000001" in res["reason"] and "35" in res["reason"]
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**(row_profile 纯函数;lint:`re.findall(r"-?\d+(?:\.\d+)?", thesis)` 过滤年份/代码/日期后逐个到行值集合找容差匹配;行值集合 = L2 csv 该码全部数值列 + judged 行自身 pct_60d/conviction)。workflow js 在 `L3 精排 agent` 与 `finalists` 之间插:

```js
const l3lint = await gate('l3-lint', `${R} autoresearch.scan.agents.l3_select lint ${date}`, OK, 'L3')
if (l3lint && l3lint.ok === false) {
  log(`L3 数字机检未过 → 打回一次自修:${(l3lint.reason || '').slice(0, 200)}`)
  await agent(
    `你之前写的 ${SD}/_l3_judged.json 有 thesis 引用数字与 ${SD}/_l3_table.md 不符:\n${l3lint.reason}\n只修这些票的 thesis/数字(以表为准或删掉具体数字改定性措辞),其余票原样保留,用 Write 覆写同一文件。`,
    { agentType: 'l3-rank', effort: 'medium', label: 'L3-lint-fix', phase: 'L3' })
}
```

- [ ] **Step 4: 测试绿** + 对 2026-07-09 真数据跑 `l3_select lint 2026-07-09`(历史 judged 可能报 misquote——只观察输出合理性,不改历史文件)。
- [ ] **Step 5: Commit** `feat(l3): 行语义指纹 pf 列+lane 分块渲染+thesis 数字机检 lint(workflow 一次打回自修)——直击误读 22/31`

---

### Task 10: L3/L4 prompt 语义波(兑现机制维 + conviction 重锚 + 盲读微 pass + 中性前提 + 基率注入)

**Files:**
- Modify: `.claude/agents/l3-rank.md`(rubric ⑥ + conviction 行为化定义 + ≥70 限额 + 输出字段加 `mechanism`)
- Modify: `autoresearch/scan/agents/l3_select.py`(`merge_l3_finalists_v2` 容忍并透传 `mechanism` 列)
- Modify: `autoresearch/scan/agents/l4_card.py`(`compose_funnel_brief` :358-374 —— L3 论点改中性前提清单、conviction 后移;新 `write_base_rates(scan_dir) -> Path|None` 产 `_l4_base_rates.json` 并逐卡注入 ≤3 行基率)
- Modify: `.claude/agents/l4-card.md` + `.claude/skills/stock-research/lite-playbook.md`(P0/P1 盲读微 pass 顺序 + L3 论点裁决表加"兑现机制核"一行 + 铁律区一句"先读数据后读论点")
- Modify: `tests/test_agent_defs.py`(`_NAMES` 加 `"l3-rank"` + 新锚测试)
- Test: `tests/scan/test_l4_brief_neutral.py`(新)

**Interfaces:**
- l3-rank.md 输出契约新字段:`mechanism`(一句,"两日内兑现机制+明日买家",necessity 与 thesis 同级);conviction 段落替换为行为化定义(下方 Step 3 给全文);其余字段名不动(下游 `_l3_judged.json` 消费方已在 Task 9 兼容)。
- `write_base_rates(scan_dir)`:从 `cross_calib.flip_stats(window=30)`(:36,列 `lane/n_hiconv/flip_rate/thin`)+ 全库 attribution 按 rating 分组的 `fwd_2_oc` 均值/胜率,写 `_l4_base_rates.json`:`{"by_lane": {"trend": {"flip_rate": 0.33, "n": 52}}, "by_rating": {"Overweight": {"n": 4, "mean_fwd2": -0.03, "win": 0.0}}}`(n<10 条目直接不写)。简报逐卡块注入(有该票 lane 对应条目才注):
  `🔁 基率:trend lane 高确信历史被 L4 翻案 33%(n=52)｜OW 卡历史 T+2 胜率 0%(n=2)⚠样本极少`
- compose_funnel_brief 改动(cache 契约安全——全在逐卡块内):
  - 现 `- **L3 入选**:conviction X·lane Y·情感 Z` + `- 多头论点:<thesis>` 两行,改为:
    ```
    - **L3 前提清单(中性措辞,逐条核真)**:
      - 前提1:<thesis 原文>
      - 前提2(兑现机制):<mechanism 原文;缺字段则整行省略>
    - **L3 元数据(读完 P1 数字后再看)**:conviction X·lane Y·情感 Z
    ```

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_l4_brief_neutral.py
from autoresearch.scan.agents import l4_card


def test_brief_premises_before_conviction(tmp_path, monkeypatch):
    # 构造最小 scan_dir:finalists.csv + L1/L2 行(镜像 tests/ 里 compose_funnel_brief 既有测试的 fixture 写法)
    ...  # 复用现有 compose_funnel_brief 测试的 fixture(tests/scan 内已有,implementer 找到后拷贝改)
    text = l4_card.compose_funnel_brief("000001", scan_dir)
    assert "前提清单" in text
    assert text.index("前提清单") < text.index("conviction")   # conviction 必须出现在前提之后


def test_write_base_rates_thin_dropped(tmp_path, monkeypatch):
    import pandas as pd
    monkeypatch.setattr("autoresearch.learning.cross_calib.flip_stats",
                        lambda **k: pd.DataFrame([{"lane": "trend", "n_hiconv": 52, "flip_rate": 0.33, "thin": False},
                                                  {"lane": "value", "n_hiconv": 3, "flip_rate": 1.0, "thin": True}]))
    p = l4_card.write_base_rates(tmp_path)
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "trend" in data["by_lane"] and "value" not in data["by_lane"]
```

(第一个测试的 fixture:tests/scan 里已有 compose_funnel_brief 相关测试——先 `grep -rn "compose_funnel_brief" tests/`,复用其 scan_dir 构造;没有则手搭 finalists.csv+L1_recall_top1000.csv+L2_gbdt_top200.csv 三个最小 csv。)

- [ ] **Step 2: 确认失败** → **Step 3: 实现**。l3-rank.md 的 conviction/输出段替换文字(全文):

```
`conviction`(0-100,**T+2 行为化定义**):≥70 = 我能说出 D+1 谁来买、且愿意明天开盘真金买入(**每日 ≥70 至多 ~5 只**,宁缺毋滥);50-69 = 值得 L4 深核但我不背书;<50 不该出现在入选里。
`mechanism`(一句):**两日内兑现机制**——催化落地/突破跟随/板块轮动位/超跌第一波修复 之一 + 明日买家是谁;写不出兑现机制的票不选。
```

rubric 段加 `⑥ **T+2 兑现机制**:thesis 必须回答"明天、后天谁来买";机制与②资金/④催化共振才算硬。`
l4-card.md 铁律区加一行 `- **先读数据后读论点**:P1 先读 slim 数字块写 3 行独立初判(资金/技术/估值各一句),然后才读简报的 L3 前提清单做裁决;第一口必须是数据不是论点。`;L3 论点裁决表说明加 `(含 前提N=兑现机制 的 ✓/✗)`。lite-playbook 同步同句。test_agent_defs:`_NAMES` 加 `"l3-rank"`;新测试锚 `["兑现机制", "≥70", "mechanism"]` in l3-rank.md,`["先读数据后读论点"]` in l4-card.md 与 lite-playbook 双侧。

- [ ] **Step 4: 全绿**:`uv run --no-sync python -m pytest tests/test_agent_defs.py tests/scan -q`。
- [ ] **Step 5: Commit** `feat(prompt): L3 兑现机制维+conviction 行为化重锚 / L4 盲读微pass+中性前提+基率注入——B1/B2/B4/B10 防污染补基率落地`

---

### Task 11: 买单 ensemble(≥OW 追加 2 独立 run 取中位)

**Files:**
- Modify: `.claude/workflows/scan-market.js`(L4 并发段之后、Assemble 之前)
- Modify: `autoresearch/scan/assemble.py`(`_load_ensemble` + 评级折回 + 🎭 badge/人裁行;镜像既有 `_load_verify`/`_apply_verify_downgrade` :68/:89 的写法)
- Test: `tests/scan/test_ensemble_fold.py`(新)

**Interfaces:**
- workflow 产物:`context/scan/<date>/_ensemble.json` = `[{"code","ratings":["Overweight","Hold","Overweight"],"median":"Overweight","spread":2}]`;复核卡写 `context/scan/<date>/ensemble/<code>.run2.md`(**不进 details/**,防被当独立卡发布)。
- assemble:`_load_ensemble(scan_dir) -> dict[code, dict]`;评级折回 = 最终评级取 `median`(仅当 median 档 < 卡面档才向下折回;**只向下**,与"早停只向下"同族);spread≥2 → 投资建议表该行加 `🎭复核分歧` badge + 组合视角节加一行 `🎭 买单复核分歧:<code> 3 run=[...],已按中位折回,建议人工复核`。无 `_ensemble.json` → 一切照旧(parity)。

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_ensemble_fold.py
import json
from autoresearch.scan import assemble


def test_load_and_fold(tmp_path):
    (tmp_path / "_ensemble.json").write_text(json.dumps([
        {"code": "688213", "ratings": ["Overweight", "Hold", "Hold"], "median": "Hold", "spread": 1}]),
        encoding="utf-8")
    ens = assemble._load_ensemble(tmp_path)
    assert assemble._apply_ensemble_fold("Overweight", ens.get("688213")) == "Hold"
    assert assemble._apply_ensemble_fold("Hold", ens.get("688213")) == "Hold"      # 不向上
    assert assemble._apply_ensemble_fold("Overweight", None) == "Overweight"      # 无记录 = 原样
```

- [ ] **Step 2: 确认失败** → **Step 3: 实现**。workflow js 在 `const cards = [...fresh, ...(plan.reused || [])]` 之后、`const buys` 之前插:

```js
// 买单复核 ensemble(拍板 2):≥OW 的新派卡各追加 2 个独立 run,取中位;只向下折回。
const RANK = { 'sell': 0, 'underweight': 1, 'hold': 2, 'overweight': 3, 'buy': 4 }
const tier = (r) => RANK[String(r || '').toLowerCase()] ?? 2
const owFresh = fresh.filter((c) => isOW(c.rating))
if (owFresh.length) {
  log(`🎭 买单复核:${owFresh.length} 张 ≥OW 卡各追加 2 独立 run 取中位`)
  const ens = await parallel(owFresh.map((c) => () => (async () => {
    const reruns = (await parallel([2, 3].map((i) => () => agent(
      `独立复核 run${i}(不知道其它 run 结论):执行 ${SD}/_l4_prompt_${c.code}.md 的任务包,按人设走渐进深度 DD,决策卡写到 ${SD}/ensemble/${c.code}.run${i}.md(先自行创建 ensemble/ 目录),返回 code/rating/conviction。`,
      { agentType: 'l4-card', effort: cfg.agents?.l4_card?.effort ?? 'xhigh', label: `ens${i}:${c.code}`, phase: 'L4', schema: CARD })))).filter(Boolean)
    const ratings = [c.rating, ...reruns.map((r) => r.rating)]
    const sorted = ratings.map(tier).sort((a, b) => a - b)
    const medianTier = sorted[Math.floor(sorted.length / 2)]
    const names = ['Sell', 'Underweight', 'Hold', 'Overweight', 'Buy']
    return { code: c.code, ratings, median: names[medianTier], spread: sorted[sorted.length - 1] - sorted[0] }
  })()))
  const rows = ens.filter(Boolean)
  await bash(`cat > ${SD}/_ensemble.json << 'EOF'\n${JSON.stringify(rows)}\nEOF`, 'ensemble-dump', 'L4')
  for (const e of rows) {                       // buys 判定用折回后评级
    const card = cards.find((c) => c.code === e.code)
    if (card && tier(e.median) < tier(card.rating)) card.rating = e.median
  }
}
```

assemble:`_load_ensemble`(读 json → dict by code)+ `_apply_ensemble_fold(rating, rec)`(rec 存在且 `RANK[median]<RANK[rating]` → median,否则原样)接进 `_finalist_row` 的评级链(`_apply_verify_downgrade` 之后同法叠加)+ badge/人裁行。
- [ ] **Step 4: 测试绿 + workflow js 语法自检**:`node --check .claude/workflows/scan-market.js`。
- [ ] **Step 5: Commit** `feat(l4): 买单 ensemble——≥OW 追加 2 独立 run 取中位只向下折回+🎭分歧人裁行(B10 集成配方,替代常设 skeptic)`

---

### Task 12: 波尾收口(全量回归 + 确定性冒烟 + STAGES 快照 + memory)

**Files:**
- Modify: `.claude/skills/scan-market/STAGES.md`(L1 配额表新值/温度计节/L3 lint 与 pf 列/L4 简报新行与 ensemble/L5 新行/闭环层表加 temperature 与三门账本行)
- Modify: `docs/specs/2026-07-11-funnel-six-questions-brainstorm.md`(头部状态行补 plan 完成 commit 号)

- [ ] **Step 1: 全量回归**:`uv run --no-sync python -m pytest -q` → 全绿(基线 1053+新增)。
- [ ] **Step 2: 确定性链真数据冒烟**(07-11 教训:冒烟走真实全链,不手搓中间态)
  - `uv run --no-sync python -m autoresearch.scan.frame 2026-07-09 --json | uv run --no-sync python -c "import sys,json;p=json.load(sys.stdin);print('temperature' in p, p.get('temperature'))"`(湖数据齐则 True)
  - `uv run --no-sync python -m autoresearch.scan.universe 2026-07-09 --regime-aware --source tushare` 用新配额跑通,`L1_channels.csv` 里 heat≤150/value≤250;`meta.json` 记 quota 覆盖。
  - `uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts 2026-07-09` 重落稿:抽一张 `_l4_prompt_*.md` 目检「前提清单 / 🔁基率 / 📐目标校准」行齐且在逐卡块内(共享块 byte-identical 测试兜底)。
  - `uv run --no-sync python -m autoresearch.scan.assemble 2026-07-09`:🌡/🧾/⏳ 行出现,GATE4(self_review)绿。
- [ ] **Step 3: STAGES.md 快照更新**(逐节小改,as-of 改 2026-07-12;别改机器契约原文)。
- [ ] **Step 4: Commit** `docs(scan): STAGES 快照对齐 P0+P1 波(温度计/三门账本/配额新值/L3 lint/L4 基率+ensemble)` + 汇报:改动清单、账本新读数、下次真扫描 = 正式验收清单(温度相位首日读数/基率行是否被卡引用/ensemble 是否触发)。

---

## 任务依赖与并行

- T1/T2/T3 独立可并行;T4→T5(温度计两段);T6/T7 独立;T8 独立;T9(lint/pf)先于 T10(人设引用 pf 列语义);T11 独立;T12 收口必须最后。
- 同文件冲突:T6 与 T10 都动 `l4_card.py`+`l4-card.md`+`lite-playbook.md`(T6 先);T9 与 T10 都动 `l3_select.py`(T9 先);串行执行即可,不并行这三组。

## 自审记录(writing-plans self-review)

- Spec 覆盖:brainstorm §7 P0-1→T1/T2/T3,P0-2→T4/T5,P0-3→T6,P0-4→T7,P0-5→T8;P1-6→T9,P1-7/8→T10;P2-11(已批)→T11;§8 拍板 1-6 全部落任务。P2 其余(雷分级/新召回路 IC/S3/S4/X2/持仓卡/两遍法)按拍板留待账本攒够,不在本波。
- 占位扫描:无 TBD;两处"以源码实际名为准"(gate_status 门名 key、recall_select 形参名)是防两个 Explore 报告丢失导致的 file:line 盲区,均给了确认命令。
- 类型一致:`bought`(bool)/`gate_fires` 四列/`_ensemble.json` 字段/`temperature` 块键名在产出与消费任务间逐一核对过。
