# Wave 3:档案增量接线(L4 注入+档案对账+δ 回写 / 判例聚合 / 季度对账)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Wave 2 建成的覆盖档案接进每日扫描:L4 prompt 注入档案摘要、卡加「档案对账」节、卡尾确定性回写档案 §8 + 摘要机算行;§7 判例账本升级为 t1/retro 真战绩聚合;新增季度对账 CLI(spec ④+⑦+⑤-4)。

**Architecture:** 全部确定性零 LLM。新增 `autoresearch/dossier/delta.py`(节切片原语+δ 回写)、`autoresearch/dossier/ledger.py`(per-code 判例聚合)、`autoresearch/dossier/reconcile.py`(季度对账 CLI);改 `l4_card.compose_funnel_brief`(注入腿)、`self_review.card_contract_lint`(档案对账探针)、`assemble.run()`(is_real 段挂 δ 回写)、`.claude/agents/l4-card.md`+`l4-intel.md`+`.claude/workflows/l4-stock.js`(契约文字)。

**Tech Stack:** Python 3 + pandas + pytest;`uv run --no-sync` 调用;markdown 档案以 `schema.SECTIONS` 八节锚为机器契约。

**Spec:** `docs/specs/2026-07-22-research-depth-dossier-design.md`(④ L4 每日增量模式、⑦ 判例账本、⑤-4 预测留档对账)。

## Global Constraints

- **Parity 铁律**:池空/无档案/档案未首覆(frontmatter `initiated` 为 null)→ 所有现行为**逐字节不变**(presence-gated,repo 惯例"全默认关→parity 不破")。
- **超短 T+2 尺不变**(2026-07-10 用户裁定):判例聚合口径 = t1 快环 `excess_ind`(行业中性超额)+ retro `fwd_2_oc` 主尺,勿引入 T+5/swing 口径。
- **cache 前缀契约(T8)**:任何逐卡注入必须在 `_l4_shared_instructions.md` 共享块**之后**的逐卡 body 内,共享块之前不得出现逐卡可变内容(否则 30 卡并发 prompt-cache 全 miss)。
- **staging 不动**:档案回写只动 `context/knowledge/dossiers/`,扫描日 staging(`context/scan/<date>/`)与发布报告一律不改写(价格断言对账同款惯例)。
- **降级留痕**:数据缺 → `[数据缺,YYYY-MM-DD]` 或 stdout skip 记账,不空写不吞(数据契约 B 级)。
- **档案锚是机器契约**:节头一律引用 `schema.SECTIONS`/`SUMMARY_HEAD`/`SUMMARY_ANCHORS` 常量,禁止重复字面量。
- **测试隔离**:Task 1 起全局 autouse fixture 把 `schema.DOSSIER_DIR`/`prefetch.PREFETCH_DIR` 指到 tmp,防测试读写真实档案(conftest 隔离三件套的第四件)。
- 测试命令:`uv run --no-sync python -m pytest <path> -x -q`;频繁 commit,每 task 至少一个。
- **PIT**:δ/对账回写发生在当日卡发布之后;回放读当日 `_l4_prompt_*` 落稿,不受档案后续演化影响(现机制已保证,勿破坏)。

---

### Task 1: `dossier/delta.py` — 节切片原语 + §8 追加 + §3/§2 刷新 + 摘要机算重算

**Files:**
- Create: `autoresearch/dossier/delta.py`
- Modify: `tests/conftest.py`(追加 autouse 隔离 fixture)
- Test: `tests/dossier/test_delta.py`

**Interfaces:**
- Consumes: `schema.SECTIONS/SUMMARY_HEAD/dossier_path/parse_frontmatter/lint_dossier`、`builder._load_prefetch/_val_band_table/_band_position_text/_fwd_eps_line/render_summary_calc/_PRECEDENT_WINDOW`、`scan.dossier.stock_dossier`。
- Produces(后续 task 依赖的精确签名):
  - `_section_span(text: str, idx: int) -> tuple[int, int]`
  - `section_body(text: str, idx: int) -> str`
  - `replace_section(text: str, idx: int, body: str) -> str`
  - `append_delta_line(text: str, date: str, line: str, *, key: str | None = None) -> str`
  - `refresh_summary_line(text: str, anchor: str, value: str) -> str`
  - `set_frontmatter_key(text: str, key: str, value: str) -> str`
  - `record_scan_delta(code6: str, date: str, *, rating: str, conviction=None, scan_root: str | Path = "context/scan") -> dict`(返回 `{"code",...,"updated":True,"issues":[...]}` 或 `{"code","skipped":"no_dossier"|"not_initiated"}`)

- [ ] **Step 1: 先加全局测试隔离 fixture**

Read `tests/conftest.py` 现有内容(已有 pinned/temperature/scan_config 隔离三件套),**追加**(勿动现有 fixture):

```python
@pytest.fixture(autouse=True)
def _isolate_dossier_dir(tmp_path, monkeypatch):
    """dossier 层隔离(Wave3):防任何测试读写真实 context/knowledge/dossiers。

    module-attr 派发(builder._load_prefetch 读 prefetch.PREFETCH_DIR、schema.dossier_path
    读 schema.DOSSIER_DIR 均为调用时取值)→ monkeypatch 生效;两常量独立(PREFETCH_DIR
    在 import 时由 DOSSIER_DIR 计算,patch 前者不会带动后者),必须双 patch。
    """
    from autoresearch.dossier import prefetch as _pf, schema as _sch
    d = tmp_path / "_dossiers"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_sch, "DOSSIER_DIR", d)
    monkeypatch.setattr(_pf, "PREFETCH_DIR", d / "_prefetch")
    yield
```

若 conftest 顶部无 `import pytest` 则补。跑一次全量确认无回归:`uv run --no-sync python -m pytest tests/dossier tests/scan -x -q`(现有 dossier 测试自带局部 patch,双 patch 兼容)。

- [ ] **Step 2: 写失败测试**

`tests/dossier/test_delta.py`:

```python
"""delta.py 契约:节切片/幂等追加/机算刷新/frontmatter 回写(Wave3 Task 1)。"""
from autoresearch.dossier import builder, delta, schema


def _mk_dossier(code="300857", today="2026-07-23", initiated=True):
    """真 builder 骨架 + 手工置 initiated(模拟已首覆档案)。"""
    out = builder.build_skeleton(code, today, name="协创数据", sector="消费电子")
    p = out["path"]
    if initiated:
        text = delta.set_frontmatter_key(p.read_text(encoding="utf-8"),
                                         "initiated", today)
        p.write_text(text, encoding="utf-8")
    return p


def test_section_span_and_replace_roundtrip():
    p = _mk_dossier()
    text = p.read_text(encoding="utf-8")
    body = delta.section_body(text, 7)
    assert "建档" in body
    new = delta.replace_section(text, 7, "- X\n")
    assert delta.section_body(new, 7) == "- X\n"
    for s in schema.SECTIONS:            # 八节锚一个不丢
        assert s in new


def test_append_delta_line_idempotent_and_rolling():
    p = _mk_dossier()
    text = p.read_text(encoding="utf-8")
    t1 = delta.append_delta_line(text, "2026-07-24", "入围:评级 Hold(conv 60)", key="入围")
    t2 = delta.append_delta_line(t1, "2026-07-24", "入围:评级 Underweight(conv 55)", key="入围")
    body = delta.section_body(t2, 7)
    assert body.count("- 2026-07-24 入围") == 1          # 同日同 key 整行替换,不重复
    assert "Underweight" in body and "Hold(conv 60)" not in body
    for i in range(30):                                   # 滚动窗:只留近 20 条
        t2 = delta.append_delta_line(t2, f"2026-08-{i + 1:02d}", "入围:评级 Hold", key="入围")
    assert len([ln for ln in delta.section_body(t2, 7).splitlines() if ln.strip()]) == 20


def test_record_scan_delta_full_pipeline(monkeypatch):
    p = _mk_dossier()
    monkeypatch.setattr(builder, "_load_prefetch", lambda c: {
        "val_band": {"pe_p25": 10.0, "pe_p50": 20.0, "pe_p75": 30.0, "pe_now": 25.0},
        "fwd_eps": {"asof": "2026-07-24", "fwd_eps_2026": 5.0}})
    res = delta.record_scan_delta("300857", "2026-07-24", rating="Hold", conviction=60)
    assert res["updated"] and res["issues"] == []
    text = p.read_text(encoding="utf-8")
    assert "- 2026-07-24 入围:评级 Hold(conv 60)" in delta.section_body(text, 7)
    assert "P50~P75" in delta.section_body(text, 2)       # §3 由 prefetch 重算
    assert "- 快照 2026-07-24:一致预期 fwd-EPS:2026=5.00" in delta.section_body(text, 1)
    assert schema.parse_frontmatter(text)["last_delta"] == "2026-07-24"
    assert "- 带位: 当前 PE=25.0" in text                  # 摘要机算行同步
    # 幂等:同日重跑不膨胀
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", conviction=60)
    t3 = p.read_text(encoding="utf-8")
    assert t3.count("- 快照 2026-07-24") == 1
    assert delta.section_body(t3, 7).count("- 2026-07-24 入围") == 1


def test_record_scan_delta_presence_gated():
    assert delta.record_scan_delta("999999", "2026-07-24", rating="Hold")["skipped"] == "no_dossier"
    _mk_dossier(code="600000", initiated=False)           # 骨架未首覆
    assert delta.record_scan_delta("600000", "2026-07-24",
                                   rating="Hold")["skipped"] == "not_initiated"


def test_nan_conviction_not_rendered():
    p = _mk_dossier()
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold", conviction=float("nan"))
    assert "nan" not in delta.section_body(p.read_text(encoding="utf-8"), 7)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/dossier/test_delta.py -x -q`
Expected: FAIL(`No module named 'autoresearch.dossier.delta'`)

- [ ] **Step 4: 实现 `autoresearch/dossier/delta.py`**

```python
"""dossier δ 增量回写(spec ④⑦;确定性,零 LLM)。

design: docs/specs/2026-07-22-research-depth-dossier-design.md ④;
plan: docs/plans/2026-07-24-wave3-dossier-wiring-plan.md Task 1。

节切片一律以 schema.SECTIONS 锚定位(与 builder 同源);§8 append-only 近 20 条滚动,
同日同事件 key 整行替换(幂等);写盘走「读全文→切片改→整写回」。presence-gated:
档案缺 / 未首覆(initiated 空)→ skip 不建骨架(建档归 dossier-init 链,职责不混)。
异常上抛,由调用方决定兜底(assemble 挂钩 suppress,CLI 直接报)。
"""
from __future__ import annotations

from pathlib import Path

from autoresearch.dossier import builder, schema

_DELTA_KEEP = 20      # §8 滚动窗(spec ①:append-only,近 20 条滚动)


def _section_span(text: str, idx: int) -> tuple[int, int]:
    """§idx 正文区间 [start, end)(不含节头行);节锚缺 → (-1, -1)。"""
    head = schema.SECTIONS[idx]
    i = text.find(head)
    if i < 0:
        return (-1, -1)
    start = text.find("\n", i) + 1
    j = text.find("\n## ", i + len(head))
    return (start, j if j > 0 else len(text))


def section_body(text: str, idx: int) -> str:
    start, end = _section_span(text, idx)
    return text[start:end] if start >= 0 else ""


def replace_section(text: str, idx: int, body: str) -> str:
    """整替 §idx 正文(节头不动);body 末尾自动补换行。节锚缺 → 原文返回(交 lint 报)。"""
    start, end = _section_span(text, idx)
    if start < 0:
        return text
    if not body.endswith("\n"):
        body += "\n"
    return text[:start] + body + text[end:]


def append_delta_line(text: str, date: str, line: str, *, key: str | None = None) -> str:
    """§8 追加 `- {date} {line}`;同日同 key 已有 → 整行替换(幂等);滚动保近 _DELTA_KEEP 条。"""
    if _section_span(text, 7)[0] < 0:
        return text
    rows = [ln for ln in section_body(text, 7).splitlines() if ln.strip()]
    prefix = f"- {date} {key or line}"
    rows = [r for r in rows if not r.startswith(prefix)]
    rows.append(f"- {date} {line}")
    return replace_section(text, 7, "\n".join(rows[-_DELTA_KEEP:]) + "\n")


def refresh_summary_line(text: str, anchor: str, value: str) -> str:
    """摘要块内单锚行重写为 `- {anchor} {value}`;锚行缺 → 原文返回(lint 另报)。"""
    i = text.find(schema.SUMMARY_HEAD)
    if i < 0:
        return text
    j = text.find("\n## ", i)
    j = j if j > 0 else len(text)
    lines = text[i:j].splitlines()
    for k, ln in enumerate(lines):
        if ln.strip().startswith(f"- {anchor}"):
            lines[k] = f"- {anchor} {value}"
            return text[:i] + "\n".join(lines) + text[j:]
    return text


def set_frontmatter_key(text: str, key: str, value: str) -> str:
    """frontmatter 单键改写;键缺/无 frontmatter → 原文返回。"""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    lines = text[:end].splitlines()
    for k, ln in enumerate(lines):
        if ln.split(":", 1)[0].strip() == key:
            lines[k] = f"{key}: {value}"
            return "\n".join(lines) + text[end:]
    return text


def _refresh_band(text: str, pf: dict | None) -> str:
    """§3 估值带整节重算(prefetch val_band 在才动;与 builder 同一份纯函数)。"""
    band = (pf or {}).get("val_band")
    if not band:
        return text
    return replace_section(
        text, 2, builder._val_band_table(band) + "\n\n" + builder._band_position_text(band))


def _append_eps_snapshot(text: str, pf: dict | None) -> str:
    """§2 尾追加一致预期快照行(逐次留档,spec ①§2);同 as-of 已录 → 幂等跳过。"""
    fwd = (pf or {}).get("fwd_eps") or {}
    asof = fwd.get("asof")
    if not (asof and isinstance(fwd, dict)
            and any(str(k).startswith("fwd_eps_") for k in fwd)):
        return text
    line = f"- 快照 {asof}:{builder._fwd_eps_line(fwd)}"
    body = section_body(text, 1)
    if line in body:
        return text
    return replace_section(text, 1, body.rstrip("\n") + "\n" + line + "\n")


def record_scan_delta(code6: str, date: str, *, rating: str, conviction=None,
                      scan_root: str | Path = "context/scan") -> dict:
    """单票 δ 回写:§8 入围行 + §3 带位刷新 + §2 快照 + 摘要机算行 + last_delta。"""
    code6 = str(code6).split(".")[0].zfill(6)
    path = schema.dossier_path(code6)
    if not path.exists():
        return {"code": code6, "skipped": "no_dossier"}
    text = path.read_text(encoding="utf-8")
    if not schema.parse_frontmatter(text).get("initiated"):
        return {"code": code6, "skipped": "not_initiated"}

    bad_conv = conviction is None or conviction == "" or (
        isinstance(conviction, float) and conviction != conviction)
    conv = "" if bad_conv else f"(conv {conviction})"
    text = append_delta_line(text, date, f"入围:评级 {rating}{conv}", key="入围")

    pf = builder._load_prefetch(code6)
    text = _refresh_band(text, pf)
    text = _append_eps_snapshot(text, pf)

    from autoresearch.scan import dossier as scan_dossier   # lazy 防环(scan↔dossier,builder 同款)
    entries = scan_dossier.stock_dossier(code6, scan_root=scan_root,
                                         max_days=builder._PRECEDENT_WINDOW)
    calc = builder.render_summary_calc(pf, len(entries))
    text = refresh_summary_line(text, "带位:", calc["带位"])
    text = refresh_summary_line(text, "判例:", calc["判例"])

    text = set_frontmatter_key(text, "last_delta", date)
    path.write_text(text, encoding="utf-8")
    return {"code": code6, "updated": True, "issues": schema.lint_dossier(text)}
```

注意:测试里 `record_scan_delta` 跑在 tmp(scan_root 默认 `context/scan` 不存在该票入围史 → `stock_dossier` 返回空 → 判例行=「(无入围史)」,不影响断言)。

- [ ] **Step 5: 跑测试通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/dossier/test_delta.py -x -q && uv run --no-sync python -m pytest -x -q`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/dossier/delta.py tests/dossier/test_delta.py tests/conftest.py
git commit -m "feat(dossier): delta 回写原语(节切片+§8幂等追加+§3/§2刷新+摘要机算重算)+ 测试隔离第四件"
```

---

### Task 2: `dossier/ledger.py` — per-code 判例聚合(t1+retro)+ §7 战绩块 + 摘要判例升级

**Files:**
- Create: `autoresearch/dossier/ledger.py`
- Modify: `autoresearch/dossier/delta.py`(`record_scan_delta` 接入 §7 刷新 + 判例行升级)
- Test: `tests/dossier/test_ledger.py` + `tests/dossier/test_delta.py`(追加)

**Interfaces:**
- Consumes: `context/learning/t1_review.jsonl`(逐行 JSON:`code/rating/verdict∈{准,不准,中性,—}/excess_ind/excess/sealed`)、`context/scan/*/retro/attribution.csv`(列 `code,bucket`)、Task 1 全部原语、`scan.dossier.render_dossier`。
- Produces:
  - `code_track_record(code6, *, ledger_path=None) -> dict`(`{"n_dir","right","wrong","neutral","avg_pp"}`)
  - `retro_buckets(code6, *, scan_root="context/scan", max_days=20) -> dict[str, int]`
  - `render_precedent_value(precedent_n: int, rec: dict) -> str`(摘要「判例:」实值)
  - `render_track_block(code6, *, scan_root="context/scan", ledger_path=None) -> str`(§7 尾块;无读数 → `""`)

- [ ] **Step 1: 写失败测试**

`tests/dossier/test_ledger.py`:

```python
"""ledger.py 契约:t1 逐笔按票聚合 + retro 桶 + §7/摘要渲染(Wave3 Task 2)。

口径与 t1_review.render_ledger_report 对齐:行业超额优先/sealed 不计可实现/
Hold(verdict「—」)不算方向票/UW·Sell 顺方向 = 负超额为赢(sign=-1)。
"""
import json

from autoresearch.dossier import ledger


def _write_ledger(tmp_path, rows):
    p = tmp_path / "t1_review.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    return p


def test_code_track_record_direction_and_sign(tmp_path):
    p = _write_ledger(tmp_path, [
        {"t": "2026-07-14", "code": "300857", "rating": "Underweight",
         "verdict": "准", "excess_ind": -0.03, "sealed": False},
        {"t": "2026-07-15", "code": "300857", "rating": "Underweight",
         "verdict": "不准", "excess_ind": 0.01, "sealed": False},
        {"t": "2026-07-16", "code": "300857", "rating": "Sell",
         "verdict": "准", "excess_ind": -0.02, "sealed": True},   # sealed:计方向不计 pnl
        {"t": "2026-07-16", "code": "300857", "rating": "Hold",
         "verdict": "—", "excess_ind": 0.005, "sealed": False},   # Hold 无方向,不计
        {"t": "2026-07-16", "code": "999999", "rating": "Sell",
         "verdict": "准", "excess_ind": -0.09, "sealed": False},  # 别的票,不计
    ])
    rec = ledger.code_track_record("300857", ledger_path=p)
    assert (rec["n_dir"], rec["right"], rec["wrong"], rec["neutral"]) == (3, 2, 1, 0)
    # pnl 只有前两笔:UW sign=-1 → (+0.03) 与 (-0.01) → 均值 +1.0pp
    assert abs(rec["avg_pp"] - 1.0) < 1e-9


def test_code_track_record_missing_ledger(tmp_path):
    rec = ledger.code_track_record("300857", ledger_path=tmp_path / "nope.jsonl")
    assert rec == {"n_dir": 0, "right": 0, "wrong": 0, "neutral": 0, "avg_pp": None}


def test_retro_buckets(tmp_path):
    for d, bucket in (("2026-07-14", "recalled_cut"), ("2026-07-15", "caught"),
                      ("2026-07-16", "")):
        rd = tmp_path / d / "retro"
        rd.mkdir(parents=True)
        (rd / "attribution.csv").write_text(
            f"code,bucket\n300857,{bucket}\n", encoding="utf-8")
    out = ledger.retro_buckets("300857", scan_root=tmp_path)
    assert out == {"recalled_cut": 1, "caught": 1}       # 空桶不计


def test_render_precedent_value_presence_gated():
    base = ledger.render_precedent_value(5, {"n_dir": 0})
    assert base == "近 10 扫描日入围 5 次"                 # 无战绩 = 现行文本逐字不变(parity)
    up = ledger.render_precedent_value(
        5, {"n_dir": 3, "right": 2, "wrong": 1, "neutral": 0, "avg_pp": 1.0})
    assert up.startswith("近 10 扫描日入围 5 次;t1 方向 3 笔 准2/不准1")
    assert "+1.0pp" in up


def test_render_track_block_empty_when_no_data(tmp_path):
    assert ledger.render_track_block("300857", scan_root=tmp_path,
                                     ledger_path=tmp_path / "nope.jsonl") == ""
```

`tests/dossier/test_delta.py` 追加:

```python
def test_delta_refreshes_section7_with_track_block(tmp_path, monkeypatch):
    import json
    from autoresearch.dossier import delta, ledger
    p = _mk_dossier()
    lp = tmp_path / "t1.jsonl"
    lp.write_text(json.dumps({"t": "2026-07-14", "code": "300857", "rating": "Underweight",
                              "verdict": "准", "excess_ind": -0.03, "sealed": False},
                             ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(ledger, "_T1_LEDGER", lp)
    delta.record_scan_delta("300857", "2026-07-24", rating="Hold")
    text = p.read_text(encoding="utf-8")
    assert "t1 快环战绩" in delta.section_body(text, 6)   # §7 尾战绩块
    assert "t1 方向 1 笔 准1/不准0" in text               # 摘要判例行升级
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/dossier/test_ledger.py tests/dossier/test_delta.py -x -q`
Expected: FAIL(`No module named 'autoresearch.dossier.ledger'`)

- [ ] **Step 3: 实现 `autoresearch/dossier/ledger.py`**

```python
"""dossier 判例聚合(spec ⑦;确定性,零 LLM):t1 快环逐笔 + retro 归因 → per-code 战绩。

口径与 `t1_review.render_ledger_report` 显式对齐(勿另起炉灶):行业超额优先
(`excess_ind` 缺退 `excess`)、sealed(一字板)不计可实现 pnl、Hold(verdict「—」)
不算方向票、UW/Sell 顺方向收益 = 负超额为赢(sign=-1)。保送票本就不进 t1 账本
(2026-07-17 用户裁定「保送不算」),此处天然继承该口径。
"""
from __future__ import annotations

import json
from pathlib import Path

_T1_LEDGER = Path("context/learning/t1_review.jsonl")
_DIR_SIGN = {"Overweight": 1.0, "Buy": 1.0, "Underweight": -1.0, "Sell": -1.0}
_RETRO_WINDOW = 20


def code_track_record(code6: str, *, ledger_path: Path | str | None = None) -> dict:
    """t1 快环按票聚合:方向判定 n/准/不准/中性 + 顺方向超额均值(pp)。缺账本 → 全零。"""
    p = Path(ledger_path or _T1_LEDGER)
    out = {"n_dir": 0, "right": 0, "wrong": 0, "neutral": 0, "avg_pp": None}
    if not p.exists():
        return out
    code6 = str(code6).zfill(6)
    pnl: list[float] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except Exception:  # noqa: BLE001 — 坏行跳过,余行照聚
            continue
        if str(r.get("code", "")).zfill(6) != code6:
            continue
        v = r.get("verdict")
        if v not in ("准", "不准", "中性"):
            continue
        out["n_dir"] += 1
        out["right"] += v == "准"
        out["wrong"] += v == "不准"
        out["neutral"] += v == "中性"
        sign = _DIR_SIGN.get(r.get("rating"))
        ex = r.get("excess_ind") if r.get("excess_ind") is not None else r.get("excess")
        if sign is not None and ex is not None and not r.get("sealed"):
            pnl.append(sign * float(ex))
    if pnl:
        out["avg_pp"] = sum(pnl) / len(pnl) * 100
    return out


def retro_buckets(code6: str, *, scan_root: str | Path = "context/scan",
                  max_days: int = _RETRO_WINDOW) -> dict[str, int]:
    """retro 归因按票聚合:近 max_days 个有归因的扫描日,该票的桶计数(空桶不计)。"""
    import pandas as pd
    root = Path(scan_root)
    out: dict[str, int] = {}
    if not root.exists():
        return out
    code6 = str(code6).zfill(6)
    days = sorted((p for p in root.iterdir()
                   if p.is_dir() and (p / "retro" / "attribution.csv").exists()),
                  key=lambda p: p.name, reverse=True)[:max_days]
    for d in days:
        try:
            df = pd.read_csv(d / "retro" / "attribution.csv", dtype={"code": str})
            sub = df[df["code"].astype(str).str.zfill(6) == code6]
            if not len(sub):
                continue
            b = str(sub.iloc[0].get("bucket") or "").strip()
            if b and b.lower() != "nan":
                out[b] = out.get(b, 0) + 1
        except Exception:  # noqa: BLE001 — 单日坏档不挡聚合
            continue
    return out


def render_precedent_value(precedent_n: int, rec: dict) -> str:
    """摘要「判例:」实值:入围计数(builder 现文本,parity)+ t1 战绩尾巴(有才附)。"""
    from autoresearch.dossier import builder
    base = builder.render_summary_calc(None, precedent_n)["判例"]
    if not rec or not rec.get("n_dir"):
        return base
    tail = f";t1 方向 {rec['n_dir']} 笔 准{rec['right']}/不准{rec['wrong']}"
    if rec.get("avg_pp") is not None:
        tail += f",顺方向超额均值 {rec['avg_pp']:+.1f}pp"
    return base + tail


def render_track_block(code6: str, *, scan_root: str | Path = "context/scan",
                       ledger_path: Path | str | None = None) -> str:
    """§7 尾部「覆盖战绩」确定性块;无任何读数 → ""(presence-gated)。"""
    rec = code_track_record(code6, ledger_path=ledger_path)
    buckets = retro_buckets(code6, scan_root=scan_root)
    lines: list[str] = []
    if rec.get("n_dir"):
        avg = (f",顺方向超额均值 {rec['avg_pp']:+.1f}pp"
               if rec.get("avg_pp") is not None else "")
        small = "(⚠n<10 只看不裁)" if rec["n_dir"] < 10 else ""
        lines.append(f"- **t1 快环战绩**:方向判定 {rec['n_dir']} 笔,"
                     f"准{rec['right']}/不准{rec['wrong']}/中性{rec['neutral']}{avg}{small}")
    if buckets:
        seg = "、".join(f"{k}×{v}" for k, v in sorted(buckets.items()))
        lines.append(f"- **retro 归因桶(近{_RETRO_WINDOW}日)**:{seg}")
    if not lines:
        return ""
    return "### 📊 覆盖战绩(确定性聚合,δ 自动刷新)\n" + "\n".join(lines)
```

- [ ] **Step 4: `delta.record_scan_delta` 接入 §7 刷新 + 判例升级**

在 `record_scan_delta` 里,把「摘要机算行」段替换为(`from autoresearch.scan import dossier as scan_dossier` lazy import 行保留):

```python
    entries = scan_dossier.stock_dossier(code6, scan_root=scan_root,
                                         max_days=builder._PRECEDENT_WINDOW)
    prec_text = scan_dossier.render_dossier(code6, scan_root=scan_root,
                                            max_days=builder._PRECEDENT_WINDOW)
    from autoresearch.dossier import ledger as dledger
    rec = dledger.code_track_record(code6)
    track = dledger.render_track_block(code6, scan_root=scan_root)
    body7 = "\n\n".join(p for p in ((prec_text or builder._NO_PRECEDENT), track) if p)
    text = replace_section(text, 6, body7)
    calc = builder.render_summary_calc(pf, len(entries))
    text = refresh_summary_line(text, "带位:", calc["带位"])
    text = refresh_summary_line(text, "判例:",
                                dledger.render_precedent_value(len(entries), rec))
```

(δ 时 `stock_dossier`/`render_dossier` **不 exclude 当日**——卡已发布,当日入围就该入史;builder 建档时 exclude 是因为跑在卡前,语义不同,勿抄。)

- [ ] **Step 5: 跑测试通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/dossier -x -q && uv run --no-sync python -m pytest -x -q`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/dossier/ledger.py autoresearch/dossier/delta.py tests/dossier/
git commit -m "feat(dossier): 判例聚合(t1按票战绩+retro桶)刷 §7 战绩块+摘要判例行升级"
```

---

### Task 3: L4 注入腿 — `compose_funnel_brief` 读覆盖档案摘要(presence-gated)

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(新 helper + `compose_funnel_brief` 尾部装配)
- Modify: `.claude/skills/scan-market/SKILL.md`(派发 args 补 `pinned` 文档)
- Test: `tests/scan/test_l4_dossier_inject.py`

**Interfaces:**
- Consumes: `dossier.schema.dossier_path/_summary_block/est_tokens/parse_frontmatter`(lazy import)。
- Produces: `_dossier_summary_mark(code6: str) -> str`(缺档案/未首覆/摘要超 3k → `""`)。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_l4_dossier_inject.py`:

```python
"""Wave3 ④:L4 prompt 注入覆盖档案摘要(presence-gated·parity)。"""
from autoresearch.dossier import delta, schema
from autoresearch.scan.agents.l4_card import _dossier_summary_mark


def _mk(code="300857", initiated="2026-07-23", summary_pad=""):
    p = schema.dossier_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = ("---\ncode: " + code + "\nname: 协创数据\nsector: 消费电子\n"
            "pool_status: active\nentered: 2026-07-23\nentry_reason: pinned\n"
            f"initiated: {initiated}\nlast_refresh: null\nlast_delta: null\n---\n"
            f"{schema.SUMMARY_HEAD}\n- 业务: 算力租赁{summary_pad}\n- 驱动: NAND 周期\n"
            "- 带位: >P75\n- 风险: CFO/NI 0.36\n- 催化: 8/28 中报\n- 判例: 入围 5 次\n"
            + "".join(f"{s}\n(略)\n" for s in schema.SECTIONS))
    p.write_text(text, encoding="utf-8")
    return p


def test_mark_injects_summary_and_contract_line():
    _mk()
    out = _dossier_summary_mark("300857")
    assert "📚 覆盖档案摘要" in out
    assert "- 业务: 算力租赁" in out and "- 判例: 入围 5 次" in out
    assert "档案对账" in out                      # 卡内节要求随注入声明
    assert str(schema.dossier_path("300857")) in out   # 全文路径指针
    assert schema.SECTIONS[0] not in out          # 只注摘要块,不带八节正文


def test_mark_presence_gated_missing_and_skeleton():
    assert _dossier_summary_mark("999999") == ""          # 无档案
    _mk(code="600000", initiated="null")
    assert _dossier_summary_mark("600000") == ""          # 骨架未首覆(四行占位是噪声)


def test_mark_skips_over_cap_summary():
    _mk(code="600001", summary_pad="х" * 12000)           # 摘要超 3k token → 不注
    assert _dossier_summary_mark("600001") == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_dossier_inject.py -x -q`
Expected: FAIL(`cannot import name '_dossier_summary_mark'`)

- [ ] **Step 3: 实现**

在 `l4_card.py` 的 `_precedent_mark` 附近新增:

```python
def _dossier_summary_mark(code6: str) -> str:
    """Wave3 ④:覆盖档案摘要注入(presence-gated)。

    缺档案 / 未首覆(骨架四行占位是噪声)/ 摘要超 3k 帽(lint 已在建档/δ 侧 warn,
    此处只跳不注)→ ""(byte-parity)。进逐卡 body,天然在共享前缀之后(cache 契约安全)。
    """
    try:
        from autoresearch.dossier import schema as dschema
        p = dschema.dossier_path(code6)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")
        meta = dschema.parse_frontmatter(text)
        if not meta.get("initiated"):
            return ""
        block = dschema._summary_block(text)
        if not block or dschema.est_tokens(block) > 3000:
            return ""
        asof = meta.get("last_delta") or meta.get("initiated")
        head = (f"### 📚 覆盖档案摘要(常备模型 as-of {asof};**增量研究**:"
                "已覆盖项只核对不重写,深度花在变化上)")
        tail = (f"_档案全文按需 Read:`{p}`;本卡必须含「**档案对账**」节:"
                "驱动变量哪个动了/风险矩阵哪条触发或解除/判例账本一行。_")
        return "\n".join([head, block.strip(), tail])
    except Exception:  # noqa: BLE001 — 档案层可选,坏档不挡派发
        return ""
```

`compose_funnel_brief` 尾部装配处(`ctx = _market_ctx(base, ind)` 之后)改为:

```python
    ctx = _market_ctx(base, ind)
    dsum = _dossier_summary_mark(code6)      # Wave3 ④:覆盖档案摘要(presence-gated,缺="")
    doss = ""
    try:                                     # R5·前科卡(历史事实,增量研究;异常吞掉老 brief 不破)
        from autoresearch.scan.dossier import render_dossier
        doss = render_dossier(code6, scan_root=base.parent, exclude=base.name)
    except Exception:  # noqa: BLE001
        doss = ""
    parts = [p for p in (ctx, dsum, doss, brief) if p]
    return "\n".join(parts)
```

- [ ] **Step 4: SKILL.md 派发文档补 `pinned`**

Grep `.claude/skills/scan-market/SKILL.md` 中 l4-stock 派发示例(`args:{date, code, name, sector, cfg}` 一带,约 :90):把示例改为 `args:{date, code, name, sector, cfg, pinned}` 并紧跟一行说明:`pinned 取自 dispatch-plan 的 meta[code].pinned——漏传→保送票 SELL 双复核断链(probe 9 sell_review_missing 会逮)`。(Wave 1 已立此契约,SKILL 示例行漏列,此处补文档。)

- [ ] **Step 5: 跑测试通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_dossier_inject.py -x -q && uv run --no-sync python -m pytest -x -q`
Expected: 全 PASS(现有 l4_card 测试在 DOSSIER_DIR 隔离下 dsum 恒 "" = parity)。

- [ ] **Step 6: Commit**

```bash
git add autoresearch/scan/agents/l4_card.py tests/scan/test_l4_dossier_inject.py .claude/skills/scan-market/SKILL.md
git commit -m "feat(scan): L4 prompt 注入覆盖档案摘要(presence-gated·cache契约内)+ SKILL 派发 pinned 文档补漏"
```

---

### Task 4: 卡契约「档案对账」— 模板双侧锚 + card lint 分档探针

**Files:**
- Modify: `.claude/agents/l4-card.md`(铁律 :25 附近 + 早停卡 :72 + 满卡 :90 模板)
- Modify: `.claude/skills/stock-research/lite-playbook.md`(:90 同步)
- Modify: `autoresearch/learning/self_review.py`(`card_contract_lint` 变化项检查升级)
- Test: `tests/scan/test_card_lint.py`(追加)+ `tests/test_agent_defs.py`(锚同步)

**Interfaces:**
- Consumes: `dossier.schema.dossier_path/parse_frontmatter`(lazy)。
- Produces: lint 新 check 名 `卡片契约·档案对账缺失`(severity=warn);判定分档:有已首覆覆盖档案 → 查「档案对账」;否则有前科卡 → 查「变化项」(现行为不变)。

- [ ] **Step 1: 写失败测试**

`tests/scan/test_card_lint.py` 追加(沿用该文件现有 fixture 风格,FULL_OK 等常量不动):

```python
def _mk_cov_dossier(code):
    from autoresearch.dossier import schema
    p = schema.dossier_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\ncode: " + code + "\nname: x\nsector: x\npool_status: active\n"
                 "entered: 2026-07-23\nentry_reason: pinned\ninitiated: 2026-07-23\n"
                 "last_refresh: null\nlast_delta: null\n---\n", encoding="utf-8")


def test_card_lint_covered_stock_requires_reconcile_section(tmp_path):
    from autoresearch.learning.self_review import card_contract_lint
    d = tmp_path / "details"
    d.mkdir(parents=True)
    _mk_cov_dossier("300857")
    (d / "300857.md").write_text(FULL_OK, encoding="utf-8")        # 有变化项、无档案对账
    warns = [w for w in card_contract_lint(tmp_path)
             if w["check"] == "卡片契约·档案对账缺失"]
    assert len(warns) == 1 and warns[0]["code"] == "300857"


def test_card_lint_covered_stock_with_reconcile_ok(tmp_path):
    from autoresearch.learning.self_review import card_contract_lint
    d = tmp_path / "details"
    d.mkdir(parents=True)
    _mk_cov_dossier("300858")
    (d / "300858.md").write_text(FULL_OK + "\n**档案对账**:驱动无变化;风险无触发;判例一致\n",
                                 encoding="utf-8")
    assert not [w for w in card_contract_lint(tmp_path)
                if w["check"] == "卡片契约·档案对账缺失"]
```

(无覆盖档案的现行「变化项」用例已被现存测试锁死,勿动。)

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_card_lint.py -x -q`
Expected: 新增两条 FAIL(check 名不存在)。

- [ ] **Step 3: 实现 lint 分档**

`self_review.py` 的 `card_contract_lint` 里,将现有块:

```python
        if "变化项" not in text:
            try:
                from autoresearch.scan.dossier import render_dossier
                ...
```

替换为(内层 render_dossier 逻辑原样保留):

```python
        has_cov = False
        try:                             # Wave3 ④:覆盖档案优先——有已首覆档案 → 查「档案对账」
            from autoresearch.dossier import schema as _dsch
            _dp = _dsch.dossier_path(code)
            has_cov = _dp.exists() and bool(
                _dsch.parse_frontmatter(_dp.read_text(encoding="utf-8")).get("initiated"))
        except Exception:  # noqa: BLE001 — 档案层可选
            has_cov = False
        if has_cov:
            if "档案对账" not in text:
                out.append({"check": "卡片契约·档案对账缺失", "severity": "warn", "code": code,
                            "detail": f"{code} 有覆盖档案但卡片无『档案对账』节"
                                      "(驱动/风险/判例逐条核对,增量研究契约)"})
        elif "变化项" not in text:
            try:
                from autoresearch.scan.dossier import render_dossier
                if render_dossier(code, scan_root=scan_dir.parent, exclude=scan_dir.name):
                    out.append({"check": "卡片契约·变化项缺失", "severity": "warn", "code": code,
                                "detail": f"{code} 有个股档案但卡片无『变化项(vs 档案)』节(增量研究契约)"})
            except Exception:  # noqa: BLE001 — 档案层可选
                pass
```

- [ ] **Step 4: 模板双侧锚同步**

- `.claude/agents/l4-card.md` 铁律区(:25「📁有前科档案的票」条后)新增一条:
  `- **📚有覆盖档案摘要注入的票**(prompt 含「覆盖档案摘要」块):卡片必须含「**档案对账**」节——驱动变量哪个动了/风险矩阵哪条触发或解除/判例账本一行;已覆盖项**只核对不重写**,把深度花在变化上。`
- 早停卡模板行(:72)改为:`(若有前科档案:**变化项(vs 档案)**:<增量>;若注入覆盖档案摘要:改写为**档案对账**:<驱动/风险/判例三行>)`
- 满卡模板行(:90)改为:`(若有前科档案:**变化项(vs 档案)** 节;若注入覆盖档案摘要:改写为**档案对账** 节)`
- `.claude/skills/stock-research/lite-playbook.md` :90 同款文字同步(该文件是真值源同步件)。
- `tests/test_agent_defs.py` 的 `test_l4_card_contract_anchors_synced` anchors 列表**追加** `"档案对账"`(锁双侧同步,防单边漂移)。

- [ ] **Step 5: 跑测试通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/scan/test_card_lint.py tests/test_agent_defs.py -x -q && uv run --no-sync python -m pytest -x -q`
Expected: 全 PASS。

- [ ] **Step 6: Commit**

```bash
git add .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md autoresearch/learning/self_review.py tests/
git commit -m "feat(contract): 卡「档案对账」节(覆盖票)+ lint 分档探针 + 模板双侧锚同步"
```

---

### Task 5: 回写接线 — `record_scan_deltas` 批量入口 + assemble 挂钩 + intel 已知底

**Files:**
- Modify: `autoresearch/dossier/delta.py`(新增 `record_scan_deltas`)
- Modify: `autoresearch/scan/assemble.py`(`run()` is_real 段新 suppress 块)
- Modify: `.claude/workflows/l4-stock.js`(Intel prompt 一行)+ `.claude/agents/l4-intel.md`(tools 加 Read + 人设一句)
- Test: `tests/dossier/test_delta.py`(追加)

**Interfaces:**
- Consumes: `<scan_dir>/finalists.csv`(列 code/conviction)、`<scan_dir>/_final_ratings.json`(`{code6: rating}`,build_summary 落盘于 is_real 段之前)、Task 1/2 的 `record_scan_delta`。
- Produces: `record_scan_deltas(scan_dir: Path | str, date: str) -> int`(实际更新档案数;单票失败不断链)。

- [ ] **Step 1: 写失败测试**

`tests/dossier/test_delta.py` 追加:

```python
def test_record_scan_deltas_batch(tmp_path, monkeypatch):
    import json
    from autoresearch.dossier import delta
    p = _mk_dossier()                                     # 300857 已首覆
    _mk_dossier(code="600000", initiated=False)           # 骨架票:应 skip
    sd = tmp_path / "2026-07-24"
    sd.mkdir()
    (sd / "finalists.csv").write_text(
        "code,name,conviction\n300857,协创数据,58\n600000,浦发银行,50\n000001,平安银行,60\n",
        encoding="utf-8")
    (sd / "_final_ratings.json").write_text(
        json.dumps({"300857": "Underweight", "600000": "Hold"}), encoding="utf-8")
    n = delta.record_scan_deltas(sd, "2026-07-24")
    assert n == 1                                         # 只有已首覆的 300857 落 δ
    body = delta.section_body(p.read_text(encoding="utf-8"), 7)
    assert "- 2026-07-24 入围:评级 Underweight(conv 58)" in body


def test_record_scan_deltas_missing_inputs(tmp_path):
    from autoresearch.dossier import delta
    assert delta.record_scan_deltas(tmp_path / "nope", "2026-07-24") == 0   # 无 finalists
    sd = tmp_path / "d"
    sd.mkdir()
    (sd / "finalists.csv").write_text("code,name\n300857,协创数据\n", encoding="utf-8")
    assert delta.record_scan_deltas(sd, "2026-07-24") == 0   # 无 _final_ratings.json → 不记
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/dossier/test_delta.py -x -q`
Expected: 新增两条 FAIL(`record_scan_deltas` 不存在)。

- [ ] **Step 3: 实现批量入口**

`delta.py` 追加:

```python
def record_scan_deltas(scan_dir: Path | str, date: str) -> int:
    """整日批量 δ:finalists × 终评级(_final_ratings.json,ensemble/verify 折回后)。

    终评级缺(文件缺/该票无卡「—」)→ 该票不记(防「无卡」污染 §8;卡面评级不可靠,
    P0-2 教训:折回只改 rows 不回写卡面)。单票失败不断链;返回实际更新档案数。
    """
    import contextlib
    import json as _json

    import pandas as pd
    scan_dir = Path(scan_dir)
    fp = scan_dir / "finalists.csv"
    if not fp.exists():
        return 0
    try:
        fin = pd.read_csv(fp, dtype={"code": str})
    except Exception:  # noqa: BLE001 — 坏 csv 当无处理
        return 0
    if "code" not in fin.columns:
        return 0
    ratings: dict = {}
    with contextlib.suppress(Exception):
        ratings = _json.loads((scan_dir / "_final_ratings.json").read_text(encoding="utf-8"))
    n = 0
    for _, r in fin.iterrows():
        code6 = str(r.get("code", "") or "").split(".")[0].zfill(6)
        rating = ratings.get(code6)
        if not code6.strip("0") or not rating or rating == "—":
            continue
        with contextlib.suppress(Exception):    # 单票坏档不断链(δ 是记账,不是发布门)
            res = record_scan_delta(code6, date, rating=rating,
                                    conviction=r.get("conviction"),
                                    scan_root=scan_dir.parent)
            n += bool(res.get("updated"))
    return n
```

- [ ] **Step 4: assemble 挂钩**

`assemble.py` `run()` 的 `is_real` 段,在 precedents `build_index` 那个 suppress 块**之后**追加:

```python
        with contextlib.suppress(Exception):           # Wave3 ④:覆盖档案 δ 回写(§8+摘要机算;失败不阻发布)
            from autoresearch.dossier.delta import record_scan_deltas
            n_doss = record_scan_deltas(scan_dir, analysis_date)
            if n_doss:
                print(f"[dossier] δ 回写 {n_doss} 份覆盖档案 → context/knowledge/dossiers/")
```

(时序已核:`build_summary`(:1223)先落 `_final_ratings.json`,is_real 段在后 → 终评级可读。)

- [ ] **Step 5: intel 已知底(spec ④「情报站聚焦增量」)**

- `.claude/agents/l4-intel.md`:frontmatter `tools:` 加 `Read`;人设正文加一句:`若任务提示给出覆盖档案路径:先 Read 其「## 摘要(注入用)」节作已知底(仅标题级事实,非方向指令),已知事实不复查,查询额度全花在增量与新事件上。`(档案是历史事实非 L3 当日论点,不破情报站结构性盲设计;**agent def 编辑下会话才生效**——装载竞态坑,验收记入下次真扫描。)
- `.claude/workflows/l4-stock.js` Intel 相位的 prompt 模板串,`按你的人设六面全查` 前插入:`` 若存在 context/knowledge/dossiers/${code}.md:先读其摘要节作已知底,只查增量。``
- 若 `tests/test_agent_defs.py` 锁了 l4-intel 的 tools 列表或人设锚,同步更新(先 grep 确认)。

- [ ] **Step 6: 跑测试通过 + 全量回归 + Commit**

Run: `uv run --no-sync python -m pytest tests/dossier tests/scan tests/test_agent_defs.py -x -q && uv run --no-sync python -m pytest -x -q`
Expected: 全 PASS。

```bash
git add autoresearch/dossier/delta.py autoresearch/scan/assemble.py .claude/workflows/l4-stock.js .claude/agents/l4-intel.md tests/
git commit -m "feat(scan): assemble 挂档案 δ 批量回写(终评级口径)+ intel 档案已知底(下会话生效)"
```

---

### Task 6: `dossier/reconcile.py` — 季度对账 CLI(⑤-4)

**Files:**
- Create: `autoresearch/dossier/reconcile.py`
- Test: `tests/dossier/test_reconcile.py`

**Interfaces:**
- Consumes: `sources.fetch("express"|"forecast", {"ts_code", "period"})`(直连不入湖,prefetch 估值带腿同款惯例)、`symbol_utils.to_ts_code`(**单一事实源,92xxxx 北交所坑勿手搓后缀**)、Task 1 的 `delta.section_body/replace_section/append_delta_line/set_frontmatter_key`、`pool.load_pool`。
- Produces: `reconcile_one(code6, period, today, *, fetch=None) -> dict`;CLI `python -m autoresearch.dossier.reconcile <period> [--code C] [--today D]`(缺省全池 active)。

- [ ] **Step 1: 写失败测试**

`tests/dossier/test_reconcile.py`:

```python
"""季度对账契约:express 优先/forecast 兜底/未披露 skip/幂等(Wave3 Task 6)。"""
import pandas as pd

from autoresearch.dossier import delta, reconcile, schema
from tests.dossier.test_delta import _mk_dossier


def _fake_fetch(express_df=None, forecast_df=None):
    def fetch(endpoint, params):
        assert params["ts_code"].endswith((".SZ", ".SH", ".BJ"))   # to_ts_code 路由过
        if endpoint == "express":
            return express_df if express_df is not None else pd.DataFrame()
        if endpoint == "forecast":
            return forecast_df if forecast_df is not None else pd.DataFrame()
        raise AssertionError(endpoint)
    return fetch


def test_reconcile_express_writes_s5_s8_and_frontmatter():
    p = _mk_dossier()
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": 2.5e8,
                        "yoy_net_profit": 240.0, "diluted_eps": 0.85}])
    res = reconcile.reconcile_one("300857", "20260630", "2026-08-29",
                                  fetch=_fake_fetch(express_df=df))
    assert res["updated"] and res["kind"] == "express" and res["issues"] == []
    text = p.read_text(encoding="utf-8")
    s5 = delta.section_body(text, 4)
    assert "季度对账 20260630" in s5 and "净利 2.5亿" in s5 and "yoy +240.0%" in s5
    assert "季度对账 20260630" in delta.section_body(text, 7)      # §8 也留痕
    assert schema.parse_frontmatter(text)["last_delta"] == "2026-08-29"
    # 幂等:重跑不重复
    reconcile.reconcile_one("300857", "20260630", "2026-08-29",
                            fetch=_fake_fetch(express_df=df))
    assert p.read_text(encoding="utf-8").count("季度对账 20260630") == 2   # §5 一次 + §8 一次


def test_reconcile_forecast_fallback_and_undisclosed():
    _mk_dossier(code="002371")
    fdf = pd.DataFrame([{"ann_date": "20260815", "type": "预增",
                         "p_change_min": 30.0, "p_change_max": 50.0}])
    res = reconcile.reconcile_one("002371", "20260630", "2026-08-16",
                                  fetch=_fake_fetch(forecast_df=fdf))
    assert res["kind"] == "forecast"
    assert "+30%~+50%" in delta.section_body(
        schema.dossier_path("002371").read_text(encoding="utf-8"), 4)
    res2 = reconcile.reconcile_one("002371", "20261231", "2027-01-05",
                                   fetch=_fake_fetch())
    assert res2["skipped"] == "undisclosed"


def test_reconcile_presence_gated():
    assert reconcile.reconcile_one("999999", "20260630", "2026-08-29",
                                   fetch=_fake_fetch())["skipped"] == "no_dossier"


def test_reconcile_nan_fields_not_rendered():
    _mk_dossier(code="601869")
    df = pd.DataFrame([{"ann_date": "20260828", "n_income": float("nan"),
                        "yoy_net_profit": float("nan"), "diluted_eps": 0.5}])
    reconcile.reconcile_one("601869", "20260630", "2026-08-29",
                            fetch=_fake_fetch(express_df=df))
    s5 = delta.section_body(schema.dossier_path("601869").read_text(encoding="utf-8"), 4)
    assert "nan" not in s5 and "摊薄EPS 0.50" in s5      # NaN 穿 or-默认防线(Wave2 教训)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync python -m pytest tests/dossier/test_reconcile.py -x -q`
Expected: FAIL(`No module named 'autoresearch.dossier.reconcile'`)

- [ ] **Step 3: 实现 `autoresearch/dossier/reconcile.py`**

```python
"""dossier 季度对账 CLI(spec ⑤-4;确定性,零 LLM)。

中报/年报披露后:实际业绩(express 业绩快报优先——披露最早字段全;forecast 业绩预告
兜底——只有区间)与档案 §2 一致预期快照对照,对账行写 §5 风险矩阵 + §8 变化项日志。
三情景归属/证伪点核对是 LLM 判断,留给下次 δ 卡内「档案对账」节;本 CLI 只落事实数。
取数直连 sources.fetch 不入湖(prefetch 估值带腿同款);两端点皆空 = 未披露 → skip 留痕。
短尺对账仍归 t1/retro,此处不重复(spec 非目标)。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from autoresearch.dossier import delta, pool, schema


def _num(v) -> float | None:
    """NaN/None/非数 → None(NaN 穿 `or 默认值` 防线,Wave2 教训)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _fetch_actual(code6: str, period: str, *, fetch=None) -> dict | None:
    """express 优先,forecast 兜底;皆空 → None。返回 {"kind","ann_date","line"}。"""
    from autoresearch.data import sources
    from autoresearch.dataflows.symbol_utils import to_ts_code   # 单一事实源(92xxxx→.BJ)
    fetch = fetch or sources.fetch
    ts = to_ts_code(code6)
    try:
        df = fetch("express", {"ts_code": ts, "period": period})
    except Exception:  # noqa: BLE001 — 网络腿降级走 forecast
        df = None
    if df is not None and len(df):
        r = df.iloc[0]
        parts = []
        np_v = _num(r.get("n_income"))
        if np_v is not None:
            parts.append(f"净利 {np_v / 1e8:.1f}亿")
        yoy = _num(r.get("yoy_net_profit"))
        if yoy is not None:
            parts.append(f"yoy {yoy:+.1f}%")
        eps = _num(r.get("diluted_eps"))
        if eps is not None:
            parts.append(f"摊薄EPS {eps:.2f}")
        return {"kind": "express", "ann_date": str(r.get("ann_date", "—")),
                "line": "、".join(parts) if parts else "快报关键字段缺"}
    try:
        df = fetch("forecast", {"ts_code": ts, "period": period})
    except Exception:  # noqa: BLE001 — 两腿皆断按未披露处理(skip 留痕在调用方)
        df = None
    if df is not None and len(df):
        r = df.iloc[0]
        lo, hi = _num(r.get("p_change_min")), _num(r.get("p_change_max"))
        line = (f"预告净利变动 {lo:+.0f}%~{hi:+.0f}%" if lo is not None and hi is not None
                else f"预告类型 {r.get('type', '—')}")
        return {"kind": "forecast", "ann_date": str(r.get("ann_date", "—")), "line": line}
    return None


def reconcile_one(code6: str, period: str, today: str, *, fetch=None) -> dict:
    """单票对账;presence-gated(无档案/未首覆 skip),同 period 幂等。"""
    code6 = str(code6).split(".")[0].zfill(6)
    path = schema.dossier_path(code6)
    if not path.exists():
        return {"code": code6, "skipped": "no_dossier"}
    text = path.read_text(encoding="utf-8")
    if not schema.parse_frontmatter(text).get("initiated"):
        return {"code": code6, "skipped": "not_initiated"}
    actual = _fetch_actual(code6, period, fetch=fetch)
    if actual is None:
        return {"code": code6, "skipped": "undisclosed"}
    mark = f"季度对账 {period}"
    body5 = delta.section_body(text, 4)
    if mark not in body5:
        line5 = (f"- **{mark}**({today} 记,{actual['kind']} {actual['ann_date']}):"
                 f"{actual['line']};fwd-EPS 快照见 §2,三情景归属与证伪点核对由"
                 "下次 δ 卡内「档案对账」节裁决")
        text = delta.replace_section(text, 4, body5.rstrip("\n") + "\n" + line5 + "\n")
    text = delta.append_delta_line(text, today,
                                   f"{mark}:{actual['line']}({actual['kind']})", key=mark)
    text = delta.set_frontmatter_key(text, "last_delta", today)
    path.write_text(text, encoding="utf-8")
    return {"code": code6, "updated": True, "kind": actual["kind"],
            "issues": schema.lint_dossier(text)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dossier 季度对账(express/forecast vs §2 快照;确定性)")
    ap.add_argument("period", help="报告期 YYYYMMDD,如 20260630")
    ap.add_argument("--code", default=None, help="单票;缺省 = 全池 active")
    ap.add_argument("--today", default=None, help="记账日 YYYY-MM-DD,缺省=今天")
    args = ap.parse_args(argv)
    from datetime import datetime
    today = args.today or datetime.now().strftime("%Y-%m-%d")
    codes = [args.code] if args.code else sorted(
        c for c, e in pool.load_pool()["stocks"].items() if e.get("status") == "active")
    n = 0
    for c in codes:
        res = reconcile_one(c, args.period, today)
        tag = "✓" if res.get("updated") else f"skip({res.get('skipped')})"
        issues = f" issues={res['issues']}" if res.get("issues") else ""
        print(f"[reconcile] {c} {args.period}: {tag}{issues}")
        n += bool(res.get("updated"))
    print(f"[reconcile] 更新 {n}/{len(codes)} 份档案")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试通过 + 全量回归**

Run: `uv run --no-sync python -m pytest tests/dossier/test_reconcile.py -x -q && uv run --no-sync python -m pytest -x -q`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add autoresearch/dossier/reconcile.py tests/dossier/test_reconcile.py
git commit -m "feat(dossier): 季度对账 CLI(express优先/forecast兜底/未披露skip,对账行落§5+§8)"
```

---

### Task 7: 控制端收尾 — 真数据活体冒烟 + 计划回填(控制端自跑,不派 subagent)

**Files:**
- Modify: `docs/plans/2026-07-24-wave3-dossier-wiring-plan.md`(冒烟实录回填)
- Modify: `.superpowers/sdd/progress.md`(台账收口)

- [ ] **Step 1: 注入腿活体冒烟(07-21 真数据)**

```bash
uv run --no-sync python -c "
from autoresearch.scan.agents.l4_card import _dossier_summary_mark, compose_funnel_brief
m = _dossier_summary_mark('300857')
assert '📚 覆盖档案摘要' in m and '档案对账' in m, 'FAIL: 已首覆档案未注入'
assert _dossier_summary_mark('002926') == '', 'FAIL: 无档案票应为空(parity)'
b = compose_funnel_brief('300857', 'context/scan/2026-07-21')
assert '📚 覆盖档案摘要' in b and '📁 个股档案' in b, 'FAIL: 新摘要与旧前科卡应并存'
print('inject smoke OK', len(m), 'bytes')"
```

- [ ] **Step 2: δ 回写活体冒烟(07-21 终评级已在盘)**

```bash
uv run --no-sync python -c "
from autoresearch.dossier.delta import record_scan_deltas
n = record_scan_deltas('context/scan/2026-07-21', '2026-07-21')
print('deltas:', n)"   # 预期 4(四持仓已首覆;其余 finalist 无档案 skip)
```

然后 Read `context/knowledge/dossiers/300857.md` 人工核:§8 有 `- 2026-07-21 入围:评级 Underweight`、frontmatter `last_delta: 2026-07-21`、摘要判例行(t1 账本只记真选,保送票大概率无战绩尾巴 = presence-gated 正确)、`lint_dossier` 空。重跑一次同命令确认幂等(§8 不膨胀)。

- [ ] **Step 3: 季度对账活体冒烟(年报期,网络 best-effort)**

```bash
uv run --no-sync python -m autoresearch.dossier.reconcile 20251231 --code 002371 --today 2026-07-24
```

预期:express/forecast 有 20251231 数据 → `✓` 且 002371.md §5/§8 落对账行;网络断/无数据 → `skip(undisclosed)` 同样是降级路的有效冒烟。**照实记录**读数,勿粉饰。

- [ ] **Step 4: 全量回归 + ruff**

Run: `uv run --no-sync python -m pytest -q && uv run --no-sync ruff check autoresearch/dossier autoresearch/scan autoresearch/learning`
Expected: 全绿 + ruff 干净。

- [ ] **Step 5: 计划回填 + 台账 + Commit**

- 本计划文件尾部追加 `## 冒烟实录(2026-07-24)` 记录 Step 1-3 真实读数(含任何 skip/降级)。
- `.superpowers/sdd/progress.md` 收口 Wave 3。
- 下次真扫描验收清单(追加到实录尾):①池内票 prompt 含 📚 块且卡出「档案对账」节;②assemble 尾 `[dossier] δ 回写 N 份`;③probe「档案对账缺失」对漏写卡开火;④intel 已知底(agent def 下会话生效后)网查额度花在增量;⑤§8 逐日增长且 lint 全 clean。

```bash
git add docs/plans/2026-07-24-wave3-dossier-wiring-plan.md .superpowers/sdd/progress.md context/knowledge/dossiers/ 2>/dev/null; git commit -m "docs(plan): Wave3 冒烟实录回填(注入/δ回写/季度对账活体读数)"
```

(注:`context/knowledge/` 已 gitignore,add 会自然跳过——档案变更不进 git,只有 plan/台账进。)

---

## 冒烟实录(2026-07-24·控制端自跑)

**Step 1 注入腿(只读)**:`_dossier_summary_mark('300857')` = 515B,含 📚 头/六锚摘要/档案对账要求/全文路径;`_dossier_summary_mark('002926')`(无档案)= `""`;`compose_funnel_brief('300857','context/scan/2026-07-21')` 新摘要与旧前科卡 `📁 个股档案` **并存**。✅

**Step 2 δ 回写(真数据 07-21)**:`record_scan_deltas` → **4/4 更新**,`lint_dossier` 全 `[]`,`last_delta=2026-07-21`,§8 各得 `- 2026-07-21 入围:评级 X(conv N)`(conviction 无浮点尾巴),摘要带位/判例行同步;§7 出现 retro 归因桶(300857 `missed_l1×3`)。**幂等**:重跑 md5 前后一致。✅

**Step 3 季度对账(真网调)**:
- 300857 → forecast 路 `预告净利变动 +52%~+81%`(ann 20260129)
- 688766 → express 路 `净利 2.1亿、yoy -28.8%、摊薄EPS 1.41、ROE 9.0%`(ann 20260227)
- 002371 / 601869 → `skip(undisclosed)`。**已证伪为真读数**:直探端点确认这两票 express/forecast 皆 0 行(蓝筹直接出年报),而同批 300857/688766 有数据 = 端点健康。
- 跨日重跑同 period:§5 单行(幂等)、§8 两行(日志语义,已裁定可接受)。

**🚨活体逮到 plan 自带代码的真缺陷(单测与两轮 review 都没逮到)**:express 的 `yoy_net_profit` 被当成同比增速渲染出 `+292416600.0%`——该字段实为**去年同期净利润金额**。自算 `208232900/292416600−1 = −28.8%`,与该票 forecast「略减 −29.89%」独立交叉验证吻合。修于 `f5bcb4f`。
**教训**:fake fetch 用的是 plan 作者假设的字段语义,所以单测和 review 都验不出——**外部字段语义只有真数据能证伪**。

**控制端两次自造假阳(记账)**:①`assert 'nan' not in §5` 命中的是 `yfi**nan**ce` 子串;②`荒谬百分比` 正则命中的是 §7 前科卡里 L3 写的合法 `np +1259.87%`(低基数)。**我自己的冒烟断言同样会假阳**——与 Wave 1「Task-8 冒烟 2 条真 catch 被终审证伪为 2/2 假阳」同族。

## 终审(全支 11 commits)→ Blocked 1 critical + 3 mustfix → 全修后 Ready

- **🔴 C-1(最贵发现)**:上面那条字段误读**有孪生兄弟**在 `autoresearch/data/tushare_enrich.py` 的 express 腿,且在 L4 主数据通道上——`context/*_slim.md` 里 **419 处**荒谬百分比 + **6 处 `nan%`**,并已改写决策:07-21 普冉(持仓)卡写「预告『略减 −30%』与快报口径矛盾」,真值是两者高度吻合。**方法论**:发现一个字段被误读,第一动作是 `grep` 它的全部消费者——本次一行 grep 就能逮到。
- 控制端另独立逮到**同段第二缺陷**:`pro.express(ts_code=tc)` 不带 period 取全历史,`tail(1)` 只保证"最新存在"不保证"近期"——slim 里有 `业绩快报(tushare,20121231)` 冒充前瞻信号。
- **I-1**:季度对账 CLI 零调用点零提醒(FN-1 家族第 N 例)→ prelude `dossier_reconcile_nag` 📐 提示 + CLAUDE.md 命令。
- **I-4**:δ 的 lint issues 被静默吞掉,违反本波自己的 Global Constraint「降级留痕不吞」→ assemble 打印 `⚠️ 档案 lint`。
- 人裁项:688766 档案 §5 基于误读写的假风险行已**更正并留痕**(非静默删除)。
- 修后活体复核:688766 `−28.8%` / 002371 走过期留痕 / 300857 `+27.7%`;1448 绿、ruff 净。

**下次真扫描验收清单(9 条)**:①池内票 prompt 含 📚 块且卡出「档案对账」节 ②assemble 尾 `[dossier] δ 回写 N 份` ③probe「档案对账缺失」对漏写卡开火 ④intel 已知底生效 ⑤§8 逐日增长且 lint clean ⑥抽查 intel 稿有无只有 L3 才知道的措辞(盲性护栏已从工具级降为指令级)⑦同时有前科卡+覆盖档案的票:卡写了一节还是两节 ⑧slim「业绩快报」行是合理量级(非 8 位数%、非 nan%)⑨卡有没有拿建档日的质押/席位/解禁当"已覆盖事实"跳过。

**记账进 Wave 4**:§4/§6 每次 δ 刷新(现停在建档日快照,却被「已覆盖只核对不重写」加冕为已核事实)、intel 改「内嵌代替授权」收回 Read、确定性 δ 素材(今日 vs 基线的差)、对账偏差数、`last_refresh` 无写者+陈旧度 lint、`retro_buckets` usecols/缓存、STAGES.md 补档案机制、ledger 渲染三小改。

## Self-Review(已跑)

1. **Spec 覆盖**:④注入(Task 3)+④档案对账节(Task 4)+④卡尾回写§8/摘要重渲(Task 1/5)+④intel 聚焦增量(Task 5)+⑦判例聚合(Task 2)+⑤-4 季度对账(Task 6)——全覆盖。④的「池内 token -20~30%」是行为结果非代码,验收在下次真扫描。
2. **Placeholder 扫描**:仅 Task 3 Step 4 / Task 5 Step 5 是文档行编辑(给了精确目标文字与 grep 锚),其余全量代码。
3. **类型一致性**:`record_scan_delta` 返回 dict 键、`append_delta_line(key=)`、`section_body(idx)` 三处跨 task 签名已互查一致;`_DIR_SIGN`/`excess_ind` 口径与 t1_review 对齐。
4. **已知边界(记入验收,不阻本波)**:§8 滚动 20 条会把「建档」行滚出(设计内);express 端点 key="date" 注册但按 ts_code+period 直连查询(不入湖,合规);t1 账本只有 2026-07-10 起真选票 → 保送持仓战绩行多为空(presence-gated 正确行为)。
