# Token 经济 × L3 质量波 P0 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec `docs/specs/2026-07-08-token-economy-l3-quality-wave-design.md` 的 P0 全部 8 项（T1-T8）：同判断质量下压缩 scan 真实 token（slim 二段式/地形裁剪/文档瘦身）+ 修 L3 误读三模式（预警旗前置）+ 契约加固（早停假阳/cache 前缀）。

**Architecture:** 全部沿既有 presence-gated/parity 模式做加法：harvest 的 slim 从"单文件重排"改为"表面/深核两文件"（早停卡永不读 deep）；误读旗以 `common/scoring` 纯函数为单一事实源，L3 表列 + L4 简报行两处消费；workflow.js 只动 sector-list 一条命令（治复用白做）。**Plan 级更正：spec T5（bash 步合并）侦察发现已是现状**（workflow.js `l4-prep` 五命令本就在一个 `bash()` 里），本计划无对应 task，记为已满足。

**Tech Stack:** Python 3.13 / pandas / pytest / ruff；workflow 为 `.claude/workflows/scan-market.js`（JS，`node --check` 验语法）；agent 定义 markdown 契约锚由 `tests/test_agent_defs.py` 钉。

## Global Constraints

- 一切命令用 `uv run --no-sync python -m ...`（CLAUDE.md 铁律）。
- 每个 task 完成门：`uv run --no-sync python -m pytest tests/ -q` 全绿（基线 775）+ `uv run --no-sync ruff check .` 零错。
- Parity 铁律：新行为 presence-gated 或默认关；staging/契约文件缺失时逐字回退旧输出；不放宽任何质量门。
- 读 CSV 的 `code` 列一律 `dtype={"code": str}` + `.str.zfill(6)`（前导零坑，MEMORY 有案）。
- 阈值只写一处：旗阈值进 `autoresearch/common/scoring.py` 常量/默认参，prompt 文档只引用语义不复写数字表达式。
- Commit 中文 conventional style，尾注 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 不新增付费依赖；不动 LLM 判断层的评级/门语义。

---

### Task 1: slim 二段式（表面/深核两文件，早停卡永不读 deep）

**Files:**
- Modify: `autoresearch/analyze/harvest.py:1057-1081`（`_P4_MARKER`/`_reorder_slim_for_progressive` → `_P4_POINTER`/`_split_slim_for_progressive`/`_write_slim_files`）
- Modify: `autoresearch/analyze/harvest.py:1211-1214` 附近（slim 落盘改走 `_write_slim_files`）
- Modify: `autoresearch/scan/agents/l4_card.py`（~:452 prompt 的 slim 行 + `harvest_slim_batch` `min_bytes` 10_240→8_192，:677-704）
- Modify: `.claude/agents/l4-card.md:18-19`（渐进读盘/可信地板文案）与 `.claude/skills/stock-research/lite-playbook.md` 同步句
- Modify: `.claude/workflows/scan-market.js`（GATE3 log 文案 ">10KB"→">8KB(surface)"）
- Test: 重写 `tests/analyze/test_harvest_slim_order.py` → `tests/analyze/test_harvest_slim_split.py`；调整 `tests/scan/test_harvest_slim.py` 地板断言

**Interfaces:**
- Produces: `_split_slim_for_progressive(parts: list[str]) -> tuple[list[str], list[str]]`；`_write_slim_files(out_dir: Path, ticker: str, trade_date: str, parts: list[str]) -> Path`；deep 文件命名 `{ticker}_{trade_date}_slim_deep.md`；surface 可信地板 8_192B（Task 6 与后续 P1 短卡引用此地板）。
- Consumes: 既有 `_P4_DEEP_TITLES = ("Income statement", "Earnings quality", "Solvency")`（不改）。

- [ ] **Step 1: 写失败测试（新文件替换旧 reorder 测试）**

删除 `tests/analyze/test_harvest_slim_order.py`，新建 `tests/analyze/test_harvest_slim_split.py`：

```python
"""slim 二段式:深核块分离到 *_slim_deep.md(spec 2026-07-08 T1;取代旧同文件重排)。"""
from autoresearch.analyze.harvest import _split_slim_for_progressive, _write_slim_files


def _parts():
    return [
        "# Data context — X\n",
        "\n## Instrument identity\n\nA\n",
        "\n## Verified market snapshot (source of truth)\n\nB\n",
        "\n## Income statement (quarterly)\n\nDEEP1\n",
        "\n## Ticker news 2026-07-01 → 2026-07-08\n\nC\n",
        "\n## Earnings quality / forensics (v3)\n\nDEEP2\n",
        "\n## Solvency & refinancing (v4)\n\nDEEP3\n",
    ]


def test_split_separates_three_deep_blocks():
    surface, deep = _split_slim_for_progressive(_parts())
    assert len(deep) == 3 and all("DEEP" in p for p in deep)
    assert all("DEEP" not in p for p in surface)


def test_split_surface_order_preserved():
    surface, _ = _split_slim_for_progressive(_parts())
    assert "Instrument identity" in surface[1]
    assert "Ticker news" in surface[3]


def test_split_no_deep_passthrough():
    only_surface = [p for p in _parts() if "DEEP" not in p]
    surface, deep = _split_slim_for_progressive(only_surface)
    assert surface == only_surface and deep == []


def test_write_slim_files_two_files_and_pointer(tmp_path):
    out = _write_slim_files(tmp_path, "000062.SZ", "2026-07-08", _parts())
    deep_f = tmp_path / "000062.SZ_2026-07-08_slim_deep.md"
    assert out == tmp_path / "000062.SZ_2026-07-08_slim.md" and deep_f.exists()
    surface_txt = out.read_text(encoding="utf-8")
    assert "DEEP" not in surface_txt                       # 深核不在表面文件
    assert "000062.SZ_2026-07-08_slim_deep.md" in surface_txt  # 尾指针指向 deep
    assert "DEEP1" in deep_f.read_text(encoding="utf-8")


def test_write_slim_files_no_deep_single_file(tmp_path):
    only_surface = [p for p in _parts() if "DEEP" not in p]
    out = _write_slim_files(tmp_path, "600519.SS", "2026-07-08", only_surface)
    assert not (tmp_path / "600519.SS_2026-07-08_slim_deep.md").exists()
    assert "深核分界" not in out.read_text(encoding="utf-8")   # 老路不插指针
```

- [ ] **Step 2: 跑测试看它红**

Run: `uv run --no-sync python -m pytest tests/analyze/test_harvest_slim_split.py -q`
Expected: FAIL `ImportError: cannot import name '_split_slim_for_progressive'`

- [ ] **Step 3: 实现（harvest.py 替换 :1057-1081 的 marker+reorder 函数）**

```python
_P4_DEEP_TITLES = ("Income statement", "Earnings quality", "Solvency")
_P4_POINTER = ("\n<!-- P4 深核分界:深核块(利润表全表/盈利质量/偿付)已拆到同目录 `{deep_name}`。"
               "P1–P3/早停不读;survivor 进 P4 才 Read -->\n")


def _split_slim_for_progressive(parts: list[str]) -> tuple[list[str], list[str]]:
    """slim 二段式:深核块(P4 陷阱维)与表面块分离,表面保序。早停率 ~90% 下深核随文件
    推送 = 多数卡白烧;survivor 用 Read 按需拉 deep 文件。无深核 → deep 空表。"""
    def _is_deep(p: str) -> bool:
        head = p[:120]
        return any(f"## {t}" in head for t in _P4_DEEP_TITLES)

    deep = [p for p in parts if _is_deep(p)]
    surface = [p for p in parts if not _is_deep(p)]
    return surface, deep


def _write_slim_files(out_dir: Path, ticker: str, trade_date: str, parts: list[str]) -> Path:
    """slim 落盘(二段式):表面块写 *_slim.md(尾插 deep 指针),深核块写 *_slim_deep.md。
    无深核块 → 只写单文件不插指针(老路不破)。纯函数式落盘,可 tmp_path 测。"""
    surface, deep = _split_slim_for_progressive(parts)
    out_path = out_dir / f"{ticker}_{trade_date}_slim.md"
    if deep:
        deep_path = out_dir / f"{ticker}_{trade_date}_slim_deep.md"
        deep_path.write_text("\n".join([
            f"# Deep 深核块(P4 陷阱核用) — {ticker} @ {trade_date}\n",
            "_survivor 进 P4 才 Read 本文件;早停卡不读。_\n", *deep]), encoding="utf-8")
        surface = [*surface, _P4_POINTER.format(deep_name=deep_path.name)]
    out_path.write_text("\n".join(surface), encoding="utf-8")
    return out_path
```

同文件 `main()` 落盘处（:1211 附近，先 `sed -n '1205,1225p'` 看清现状再改）：原
`out_path = out_dir / f"{ticker}_{trade_date}{'_slim' if slim else ''}.md"` + `if slim: parts = _reorder_slim_for_progressive(parts)` + 后续 write 行，改为：

```python
    if slim:
        out_path = _write_slim_files(out_dir, ticker, trade_date, parts)
    else:
        out_path = out_dir / f"{ticker}_{trade_date}.md"
        # (原 write_text 行原样保留在此分支)
```

删除 `_reorder_slim_for_progressive` 与 `_P4_MARKER`（先 `grep -rn "_reorder_slim\|_P4_MARKER" autoresearch/ tests/` 确认只剩本次改动点）。

- [ ] **Step 4: 跑新测试转绿**

Run: `uv run --no-sync python -m pytest tests/analyze/ -q`
Expected: PASS（含既有 test_harvest.py 不受影响）

- [ ] **Step 5: 地板 8KB + prompt/agent 文案收口**

`l4_card.py`：`harvest_slim_batch(..., min_bytes: int = 10_240, ...)` → `8_192`，函数 docstring "slim >10KB 才可信" → ">8KB 才可信(表面块口径;深核块在 *_slim_deep.md)"；~:452 prompt 行改两行：

```python
            f"- slim 数据:`context/{ticker}_{date}_slim.md`(P1–P3 表面块;**>8KB 才可信**,≈4.8KB=NO_DATA 须重拉)",
            f"- deep 深核:`context/{ticker}_{date}_slim_deep.md`(**survivor 进 P4 才 Read**;早停卡不读;缺文件=陷阱维标「未核」)",
```

`.claude/agents/l4-card.md:18` 渐进读盘句改为：`**渐进读盘**:slim 只含表面块(P1–P3);深核块在同目录 \`<ticker>_<date>_slim_deep.md\`。主早停②触发即停笔(**不 Read deep**);survivor 进 P4 才 Read deep;deep 缺文件 → 陷阱维标「未核」,不编。`；:19 的 ">10KB 才可信" 同步 ">8KB 才可信(表面块)"。`lite-playbook.md` 里对应句（`grep -n "10KB\|深核分界" .claude/skills/stock-research/lite-playbook.md`）同改。`scan-market.js` 的 `log('GATE3 ✓ 全 slim >10KB')` → `>8KB(surface)`。

- [ ] **Step 6: 调整 CLI 地板测试**

读 `tests/scan/test_harvest_slim.py`，把 10_240/10KB 相关断言改 8_192，并沿用该文件既有 stub 模式加一例：surface 9KB（旧地板下、新地板上）→ batch 通过。

Run: `uv run --no-sync python -m pytest tests/scan/test_harvest_slim.py tests/test_agent_defs.py -q`
Expected: PASS（若 `test_l4_card_contract_anchors_synced` 钉了 ">10KB" 锚串，把锚更新为新句并保证 agent/playbook 双向在场）

- [ ] **Step 7: 全量门 + commit**

```bash
uv run --no-sync python -m pytest tests/ -q && uv run --no-sync ruff check . && node --check .claude/workflows/scan-market.js
git add -A autoresearch/analyze/harvest.py autoresearch/scan/agents/l4_card.py .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md .claude/workflows/scan-market.js tests/analyze/ tests/scan/test_harvest_slim.py tests/test_agent_defs.py
git commit -m "feat(scan): slim 二段式(表面/深核两文件·早停卡永不读deep·地板10K→8K)"
```

---

### Task 2: L3 误读三预警旗（scoring 单一事实源 → L3 表列 + L4 简报行 + 硬约束 E）

**Files:**
- Modify: `autoresearch/common/scoring.py`（`main_net_distortion_label` :203 邻位新增 `l3_misread_flags`）
- Modify: `autoresearch/scan/agents/l3_select.py:142-230`（`l3_table_md` 加 `misread_flag: bool = False` 参数，镜像 `dist_flag` 模式）与 :346-358（`prepare_l3_table` 传 `misread_flag=True`）
- Modify: `autoresearch/scan/agents/l4_card.py`（`compose_funnel_brief` :224 内、`_pledge_mark` 调用点旁加 `_misread_mark`）
- Modify: `.claude/agents/l3-rank.md`（硬约束 D 之后加 E）
- Test: Create `tests/scan/test_l3_misread_flag.py`（镜像 `tests/scan/test_l3_dist_flag.py` 结构）

**Interfaces:**
- Produces: `l3_misread_flags(row: dict) -> str`（返回 `"低基·背离·套牢"` 子集徽标串，空串=无旗；NaN 任一输入该旗不亮）。Task 内两个消费方 + P1 波 T9/T13 复用。
- Consumes: L2/L3 表既有列 `np_yoy, roe, cmf_20, obv_mom_20, main_net_ratio, winner_rate, ma_bull`。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_l3_misread_flag.py`：

```python
"""L3 误读三预警旗(spec 2026-07-08 T6;诊断:07-06 被打脸前提 22/31 证据纯 L3 表内可见)。"""
import math

from autoresearch.common.scoring import l3_misread_flags


def _row(**kw):
    base = dict(np_yoy=10.0, roe=12.0, cmf_20=0.0, obv_mom_20=0.0,
                main_net_ratio=0.01, winner_rate=60.0, ma_bull=1.0)
    base.update(kw)
    return base


def test_low_base_flag():
    assert "低基" in l3_misread_flags(_row(np_yoy=568.0, roe=4.1))
    assert "低基" not in l3_misread_flags(_row(np_yoy=568.0, roe=15.0))   # 高 ROE 真成长
    assert "低基" not in l3_misread_flags(_row(np_yoy=50.0, roe=4.0))


def test_flow_divergence_flag():
    assert "背离" in l3_misread_flags(_row(cmf_20=0.11, main_net_ratio=-0.02))
    assert "背离" in l3_misread_flags(_row(obv_mom_20=0.2, main_net_ratio=-0.01))
    assert "背离" not in l3_misread_flags(_row(cmf_20=0.11, main_net_ratio=0.02))


def test_trapped_flag():
    assert "套牢" in l3_misread_flags(_row(winner_rate=16.0, ma_bull=0.0))
    assert "套牢" not in l3_misread_flags(_row(winner_rate=16.0, ma_bull=1.0))  # 多头低 winner=真空间


def test_nan_never_flags_never_raises():
    assert l3_misread_flags(_row(np_yoy=math.nan, roe=math.nan)) == ""
    assert l3_misread_flags({"np_yoy": "x"}) == ""   # 缺列/脏值不抛


def test_table_column_and_legend(tmp_path):
    # staging 构造:先打开 tests/scan/test_l3_dist_flag.py,把它驱动 l3_table_md 的最小
    # staging 构造(L2_gbdt_top200.csv 等)整段**复制**到本文件(勿跨测试文件 import 私有
    # fixture——宁可重复不共享脆弱内部),仅把因子列值改为可触发旗的组合
    #(np_yoy=568/roe=4.1 → 低基)。然后:
    from autoresearch.scan.agents.l3_select import l3_table_md
    date_dir = _staging(tmp_path)   # ← 复制来的构造函数
    on = l3_table_md(date_dir.name, root=date_dir.parent, misread_flag=True)
    off = l3_table_md(date_dir.name, root=date_dir.parent, misread_flag=False)
    assert "misread" in on and "低基" in on and "自证" in on
    assert "misread" not in off


def test_l3_rank_agent_has_constraint_e():
    txt = open(".claude/agents/l3-rank.md", encoding="utf-8").read()
    assert "硬约束" in txt and "misread" in txt and "自证" in txt
```

- [ ] **Step 2: 跑测试看它红**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_misread_flag.py -q`
Expected: FAIL `ImportError: cannot import name 'l3_misread_flags'`

- [ ] **Step 3: 实现 scoring 旗函数（`main_net_distortion_label` 邻位）**

```python
def l3_misread_flags(row) -> str:
    """L3 误读三预警旗(07-08 诊断:L4 打脸 L3 的证据 22/31 纯表内可见,三模式确定性可预检)。
    低基=np_yoy>100∧roe<8(低基数幻觉);背离=cmf/obv 任一>0∧当日主力<0(拉高派发嫌疑);
    套牢=winner_rate<25∧ma_bull=0(低获利盘≠上行空间)。任一输入缺/NaN → 该旗不亮(不冤枉)。"""
    import pandas as pd

    def _f(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return None if pd.isna(v) else v

    g = row.get if hasattr(row, "get") else (lambda k, d=None: None)
    flags = []
    np_yoy, roe = _f(g("np_yoy")), _f(g("roe"))
    if np_yoy is not None and roe is not None and np_yoy > 100 and roe < 8:
        flags.append("低基")
    cmf, obv, main = _f(g("cmf_20")), _f(g("obv_mom_20")), _f(g("main_net_ratio"))
    if main is not None and main < 0 and ((cmf is not None and cmf > 0) or (obv is not None and obv > 0)):
        flags.append("背离")
    wr, ma = _f(g("winner_rate")), _f(g("ma_bull"))
    if wr is not None and ma is not None and wr < 25 and ma == 0:
        flags.append("套牢")
    return "·".join(flags)
```

- [ ] **Step 4: l3_select 表列（镜像 dist_flag 块，:198 附近 delta 块之后）**

签名加 `misread_flag: bool = False`（docstring 补一行：`misread_flag=True:加 misread 预警列(低基/背离/套牢,谓词=scoring.l3_misread_flags 单一事实源)+图例禁则;默认 False = 逐字 parity`）。实现块：

```python
    if misread_flag:
        from autoresearch.common.scoring import l3_misread_flags
        df["misread"] = df.apply(l3_misread_flags, axis=1)
        cols = [*cols, "misread"]
        header.append(
            "misread 预警:低基=净利暴增但 ROE 极低(低基数幻觉,勿当真成长);背离=cmf/obv 正但当日主力净流出"
            "(拉高派发嫌疑);套牢=低获利盘且非多头排列(≠上行空间)。**旗亮仍以对应论点入选者,thesis 必须一句自证非陷阱**。")
```

`prepare_l3_table`（:346）调用处传 `misread_flag=True`（与 `cat_flag=True, sector_terrain=True` 并列）。

- [ ] **Step 5: L4 简报行 + 硬约束 E**

`l4_card.py` 加（与 `_pledge_mark`/`_seat_mark` 同 class 的 never-raise 风格；先 `grep -n "_pledge_mark(" autoresearch/scan/agents/l4_card.py` 找 compose 内调用点，把新行加在其后）：

```python
def _misread_mark(scan_dir, code: str) -> str:
    """误读三预警徽标(presence-gated:L2 行在才算;never raises,缺了不挡简报)。"""
    try:
        import pandas as pd
        l2 = pd.read_csv(Path(scan_dir) / "L2_gbdt_top200.csv", dtype={"code": str})
        l2["code"] = l2["code"].str.zfill(6)
        row = l2[l2["code"] == str(code).zfill(6)]
        if not len(row):
            return ""
        from autoresearch.common.scoring import l3_misread_flags
        m = l3_misread_flags(row.iloc[0].to_dict())
        return f"⚠️误读预警: {m} —— 该论点若为 L3 选票理由,P1-P3 优先证伪" if m else ""
    except Exception:
        return ""   # 行可选,缺了不挡简报
```

`.claude/agents/l3-rank.md` 硬约束 D 之后加：`- **E(误读预警)**:表有 misread 列时,以成长/资金/空间为核心论点且对应旗亮(低基/背离/套牢)的票,thesis 必须一句自证为何非陷阱;无法自证 → 不得入选。`

- [ ] **Step 6: 转绿 + 07-06 回放观察**

Run: `uv run --no-sync python -m pytest tests/scan/test_l3_misread_flag.py tests/scan/test_l3_dist_flag.py tests/scan/test_agents.py -q`
Expected: PASS

观察步（不断言，报数进 task report）：对 `context/scan/2026-07-06/L2_gbdt_top200.csv` 的 20 只 finalist 逐行跑 `l3_misread_flags`，预期 ≥5 只亮旗（华天科技 002185 必亮「低基」：np_yoy+568/roe4.1）。

- [ ] **Step 7: 全量门 + commit**

```bash
uv run --no-sync python -m pytest tests/ -q && uv run --no-sync ruff check .
git add autoresearch/common/scoring.py autoresearch/scan/agents/l3_select.py autoresearch/scan/agents/l4_card.py .claude/agents/l3-rank.md tests/scan/test_l3_misread_flag.py
git commit -m "feat(scan): L3 误读三预警旗(低基/背离/套牢·scoring 单一事实源→L3列+L4简报+硬约束E)"
```

---

### Task 3: 早停卡假阳根治 + 短格式输出纪律

**Files:**
- Modify: `autoresearch/learning/self_review.py:148-157`（`card_contract_lint` 早停豁免判定）
- Modify: `.claude/agents/l4-card.md`（输出节加早停短格式 bullet）+ `.claude/skills/stock-research/lite-playbook.md` 同步句
- Test: 既有 lint 测试文件（`grep -rln "card_contract_lint" tests/` 定位）加回归用例；`tests/test_agent_defs.py` 锚表加「早停卡短格式」

**Interfaces:**
- Consumes: 卡片标题行约定 `# 决策卡 — <code> <名> @ <date>  ·  〔早停·表面 DD〕`（07-06 真卡实测格式）。
- Produces: 早停卡的 lint 豁免语义 = 「正文含『早停因』**或标题行含『早停』**」。

- [ ] **Step 1: 写失败测试（复现假阳）**

在 `card_contract_lint` 的既有测试文件加：

```python
def test_early_stop_title_card_exempt_from_p4_line(tmp_path):
    # 07-06 假阳:标题标〔早停·表面 DD〕但正文无「早停因」字样的卡,被当满卡查 P4 行
    d = tmp_path / "details"
    d.mkdir(parents=True)
    (d / "002185.md").write_text(
        "# 决策卡 — 002185 华天科技 @ 2026-07-06  ·  〔早停·表面 DD〕\n\n"
        "**Rating**: Underweight\n\nFINAL TRANSACTION PROPOSAL: **HOLD**\n",
        encoding="utf-8")
    from autoresearch.learning.self_review import card_contract_lint
    fires = card_contract_lint(tmp_path)
    assert not [f for f in fires if f["check"].startswith("卡片契约·P4倾向")], fires
```

（先读该测试文件确认 `card_contract_lint` 的 fixture 目录约定——若函数读的是 `scan_dir/details` 之外的布局，按既有用例镜像。）

- [ ] **Step 2: 跑测试看它红**

Run: `uv run --no-sync python -m pytest <定位到的测试文件> -q`
Expected: FAIL——assert 抓到 `卡片契约·P4倾向缺失` 假阳 warn

- [ ] **Step 3: 修 self_review.py（:148-157）**

```python
    p4_re = re.compile(r"进入P4倾向[:：]")
    early_title_re = re.compile(r"^#.*早停", re.M)   # 标题行〔早停·表面 DD〕= 早停卡
    ...
        if ("早停因" not in text and not early_title_re.search(text)
                and not p4_re.search(text)):
```

（保留原「早停因」豁免——两代卡片格式都认。）

- [ ] **Step 4: 转绿**

Run: `uv run --no-sync python -m pytest <该测试文件> -q`
Expected: PASS，且既有满卡缺 P4 行仍 warn 的用例不回归

- [ ] **Step 5: 短格式纪律进 agent 定义 + playbook + 锚**

`.claude/agents/l4-card.md` 输出节加 bullet：`- **早停卡短格式**:早停卡正文 ≤35 行——保留 决策仪表盘/一段话研判(≤120字)/L3 论点裁决表/重估触发行;未核维只在评分卡标「未核」,不写散文段。`
`lite-playbook.md` 对应节同句。`tests/test_agent_defs.py` 的 l4-card 锚表追加 `"早停卡短格式"`（双向在场断言随既有机制生效）。

- [ ] **Step 6: 全量门 + commit**

```bash
uv run --no-sync python -m pytest tests/ -q && uv run --no-sync ruff check .
git add autoresearch/learning/self_review.py .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md tests/
git commit -m "fix(learning): 早停卡标题豁免 P4 行 lint(根治假阳)+ 早停短格式纪律进契约锚"
```

---

### Task 4: L3 地形段裁剪到 top200 覆盖行业

**Files:**
- Modify: `autoresearch/sector/pack.py:168-180`（`sector_terrain_md` 加 `top200_only: bool = False`）
- Modify: `autoresearch/scan/agents/l3_select.py:216`（调用处传 `top200_only=True`）
- Test: `tests/sector/` 内 terrain 既有测试文件（`grep -rln "sector_terrain_md" tests/`）加用例

**Interfaces:**
- Produces: `sector_terrain_md(scan_dir, max_rows=40, top200_only=False) -> str`（True 时只渲染 L2 top200 出现过的申万一级行业；默认 False 逐字 parity）。

- [ ] **Step 1: 写失败测试**

```python
import pandas as pd

from autoresearch.sector.pack import sector_terrain_md


def test_terrain_top200_only_filters_uncovered_industries(tmp_path):
    # L1 有 A/B 两行业,L2 只覆盖 行业A → top200_only=True 地形只含 行业A;默认含两者(parity)
    pd.DataFrame({"industry": ["行业A", "行业A", "行业B"],
                  "code": ["000001", "000002", "600519"]}).to_csv(
        tmp_path / "L1_scored_full.csv", index=False)
    pd.DataFrame({"industry": ["行业A"], "code": ["000001"]}).to_csv(
        tmp_path / "L2_gbdt_top200.csv", index=False)
    on = sector_terrain_md(tmp_path, top200_only=True)
    off = sector_terrain_md(tmp_path)
    assert "行业A" in on and "行业B" not in on
    assert "行业A" in off and "行业B" in off
```

（列名对齐 pack.py:172-176 读取面：L1/L2 各需 `industry` 列；healthy 掩码在 pack 内 try 包裹，缺列自动跳过。若既有 terrain 用例已有 staging 构造函数，优先沿用其列集。）

- [ ] **Step 2: 看红** — Run 该文件，Expected: FAIL `unexpected keyword argument 'top200_only'`

- [ ] **Step 3: 实现（pack.py :175-177 l2n 计算后插一段）**

```python
    if top200_only and l2n:
        l1 = l1[l1["industry"].astype(str).isin(l2n)]   # 只渲染 top200 覆盖行业(≈110→30-50 行)
```

签名与 docstring 同步（注明「默认 False = 逐字 parity;l3_select 传 True 省 L3 表 ~60% 地形字节」）。`l3_select.py:216` 调用改 `sector_terrain_md((root or Path("context/scan")) / date, top200_only=True)`。

- [ ] **Step 4: 转绿 + 全量门 + commit**

```bash
uv run --no-sync python -m pytest tests/sector/ tests/scan/test_l3_prepare.py -q && uv run --no-sync python -m pytest tests/ -q && uv run --no-sync ruff check .
git add autoresearch/sector/pack.py autoresearch/scan/agents/l3_select.py tests/sector/
git commit -m "feat(sector): 地形段 top200_only 裁剪(110行业→L2覆盖面·L3表最大块瘦身·默认关parity)"
```

---

### Task 5: sector 复用白做修复（fan-out 排除已有 brief 的行业）

**Files:**
- Modify: `.claude/workflows/scan-market.js`（sector-list agent 的命令：列 pack json **且 brief 不存在** 的行业）

**Interfaces:**
- Consumes: `sector.reuse --apply` 已把可复用 brief 拷进 `${SD}/sector_briefs/`（reuse.py:76 `apply_reuse`）；pack 写 `context/sector/<date>/*.json`。
- Produces: `sectors` 数组语义变更 = 「待新写 brief 的行业」（复用行业不再被 fan-out 覆盖重写）。

- [ ] **Step 1: 改 sector-list 命令（workflow.js）**

现命令为「列 `context/sector/${date}/*.json` 全部去扩展名」。替换 agent prompt 内命令为：

```js
const sectors = await agent(
  `执行:\`uv run --no-sync python -c "import json,glob,os;d='context/sector/${date}';b='${SD}/sector_briefs';print(json.dumps(sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(d+'/*.json') if not os.path.exists(os.path.join(b,os.path.splitext(os.path.basename(p))[0]+'.md')))))"\`\n它打印一行 JSON 数组 = 待写 brief 的行业(有 pack 且尚无 brief;♻️复用行业已被 reuse 拷贝,故被排除,勿再派发覆盖)。把那行 JSON 原样作为结构化返回;目录不存在则返回 []。`,
  { agentType: 'general-purpose', effort: 'low', label: 'sector-list',
    schema: { type: 'array', items: { type: 'string' } } }) || []
```

- [ ] **Step 2: 验证**

Run: `node --check .claude/workflows/scan-market.js`
Expected: 无输出（语法 OK）
Run: `uv run --no-sync python -c "..."`（把上面 python 一行本地跑一次，`date` 代入任一有 staging 的历史日期）
Expected: 打印 JSON 数组且已存在 brief 的行业不在其中

- [ ] **Step 3: 全量门 + commit**

```bash
uv run --no-sync python -m pytest tests/ -q && uv run --no-sync ruff check .
git add .claude/workflows/scan-market.js
git commit -m "fix(scan): sector fan-out 排除已复用 brief(治 reuse --apply 被重派覆盖白做·cost-only)"
```

---

### Task 6: L4 prompt cache 前缀契约测试（冻结现状防回归）

**Files:**
- Test: Create `tests/scan/test_l4_prompt_cache_prefix.py`

**Interfaces:**
- Consumes: `write_dispatch_pack(scan_dir) -> dict`（l4_card.py，CLI `prompts` 子命令背后的落稿函数）；`_l4_shared_instructions.md` 为 cache 前缀真值。

- [ ] **Step 1: 写契约测试**

```python
"""L4 prompt cache 前缀契约:共享块 byte-identical 且统一置于每张 prompt 头部区(spec T8)。
守的是 30 卡并发的 prompt cache 命中前提——当日件/简报若插进共享块之前或中段,30 卡前缀
全断、cache 全 miss。本测试冻结现状;若它红了 = 真实前缀断裂,按 bug 处理勿放宽断言。"""
from pathlib import Path

import pandas as pd

from autoresearch.scan.agents.l4_card import write_dispatch_pack

SHARED = "# 当日共享指令\n地形X · 校准Y\n"


def _fixture(tmp_path: Path) -> Path:
    sd = tmp_path / "2026-07-08"
    sd.mkdir()
    (sd / "_l4_shared_instructions.md").write_text(SHARED, encoding="utf-8")
    pd.DataFrame({"code": ["000001", "600519"],
                  "ticker": ["000001.SZ", "600519.SS"]}).to_csv(sd / "finalists.csv", index=False)
    return sd


def test_prompts_share_byte_identical_head(tmp_path):
    sd = _fixture(tmp_path)
    write_dispatch_pack(sd)
    prompts = sorted(sd.glob("_l4_prompt_*.md"))
    assert len(prompts) == 2, "finalists 2 只应产 2 张 prompt"
    texts = [p.read_text(encoding="utf-8") for p in prompts]
    idx = [t.find(SHARED) for t in texts]
    assert all(i != -1 for i in idx), "共享块未原文进入 prompt"
    assert idx[0] == idx[1] and idx[0] <= 300, f"共享块位置不统一/不在头部区: {idx}"
    head_end = idx[0] + len(SHARED)
    assert texts[0][:head_end] == texts[1][:head_end], "头部前缀不 byte-identical(cache 必 miss)"
```

（若 `write_dispatch_pack` 对最小 fixture 报缺列，读 `grep -rln "write_dispatch_pack" tests/` 的既有用例镜像其 finalists.csv 列约定；presence-gated 设计下缺 staging 应产占位 prompt 而非抛。）

- [ ] **Step 2: 跑——预期直接绿（契约冻结）**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_prompt_cache_prefix.py -q`
Expected: PASS。**若 FAIL = 发现真实的前缀断裂 bug**：停下修 `write_dispatch_pack` 的拼装顺序（共享块前只允许 ≤300B 固定标头），修完测试原样通过，此情况在 task report 里显著上报。

- [ ] **Step 3: 全量门 + commit**

```bash
uv run --no-sync python -m pytest tests/ -q && uv run --no-sync ruff check .
git add tests/scan/test_l4_prompt_cache_prefix.py
git commit -m "test(scan): L4 prompt 共享前缀 byte-identical 契约(守 30 卡并发 cache 命中前提)"
```

---

### Task 7: SKILL/STAGES 文档瘦身（51KB→≤30KB，逐日沿革移 git）

**Files:**
- Modify: `.claude/skills/scan-market/STAGES.md`（26.9KB）
- Modify: `.claude/skills/scan-market/SKILL.md`（24.8KB）

**Interfaces:**
- Consumes: 文档契约测试 `tests/test_skill_docs_refs.py`、`tests/test_agent_defs.py::test_skill_docs_wire_agent_types`、`tests/scan/test_sentinel_tokens.py`（钉了哨兵/串锚）——**删段前先跑一遍记录基线**。

- [ ] **Step 1: 基线**

Run: `wc -c .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/STAGES.md && uv run --no-sync python -m pytest tests/test_skill_docs_refs.py tests/test_agent_defs.py tests/scan/test_sentinel_tokens.py -q`
Expected: 全绿；记下两文件字节数。

- [ ] **Step 2: STAGES.md 删逐日沿革**

`grep -n "^## \|^### " .claude/skills/scan-market/STAGES.md` 列出节目录：**保留** 漏斗架构快照/当前参数表/校准读数节/开放线头节；**整段删除** 带日期的 wave 沿革/changelog 段（07-02/03/04/05… 逐日记事——git 历史有）。删后文件头加一行：`> 沿革见 git log(docs/specs/ 各 wave 设计稿);本文件只保留当前态快照,冲突以源码为准。`
Target: STAGES.md ≤ 15,000 bytes。

- [ ] **Step 3: SKILL.md 压缩手工步骤段**

手工步骤 1/2/2.5（已被 workflow.js Prelude 并行+哨兵分支取代）压缩为一段：`> 编排真身 = .claude/workflows/scan-market.js(4 相位/4 GATE);以下命令为**调参/单步重跑入口**,正常跑动直接用 workflow。` 保留命令行本身（速查价值），删除其周围的编排性散文。
Target: SKILL.md ≤ 15,000 bytes。

- [ ] **Step 4: 契约回归 + 全量门**

Run: `uv run --no-sync python -m pytest tests/test_skill_docs_refs.py tests/test_agent_defs.py tests/scan/test_sentinel_tokens.py -q && uv run --no-sync python -m pytest tests/ -q && uv run --no-sync ruff check . && wc -c .claude/skills/scan-market/*.md`
Expected: 全绿 + 两文件均 ≤15KB。若某锚测试红：**把被删段里的锚句移回保留节**（锚是契约不是沿革），不放宽测试。

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/STAGES.md
git commit -m "docs(scan): SKILL/STAGES 瘦身 51K→≤30K(逐日沿革移 git·契约锚保留·skill 触发固定开销减半)"
```

---

## 收尾（全部 task 完成后）

- [ ] 全量终验：`uv run --no-sync python -m pytest tests/ -q`（775 基线 + 新增全绿）+ `uv run --no-sync ruff check .` + `node --check .claude/workflows/scan-market.js`
- [ ] 按 house 流程：whole-branch 终审(opus) → finishing-a-development-branch(用户既定"本地合进 main"模式)
- [ ] 更新 spec `docs/specs/2026-07-08-token-economy-l3-quality-wave-design.md`：加「实现进度」节标记 P0 落地 + T5 已满足更正；P1(T9/T10/T11)另立 plan(T9 依赖本波 T1 的 surface/deep 文件形态)
- [ ] 下次真扫描 = P0 验收日：token 表对比(slim surface/deep 两桶)+ misread 列上表 + 早停卡短格式 + GATE3 8K 地板 + sector 复用不再被覆盖(♻️ banner 后无重派)
