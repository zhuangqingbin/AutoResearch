# Wave 1:准确度横切 + 卡增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec `docs/specs/2026-07-22-research-depth-dossier-design.md` 的 Wave 1——价格断言对账、引用密度探针、SELL 双复核、断言分级/卡增强契约,让下次真扫描的卡片"厚且可审计"。

**Architecture:** 新增一个纯函数模块 `autoresearch/scan/price_claims.py`(抽取+对账,零 LLM),在 assemble 发布层与 product_shape_lint 各挂一个消费点;SELL 双复核复用 l4-stock.js 现有 ≥OW ensemble 机制(加 trigger 方向);断言分级/卡增强只改 agent/playbook 契约文本(输出侧,读盘边界不动)。全部 presence-gated,advisory 起步。

**Tech Stack:** Python 3.13 + pandas + pytest(现套件 1320 绿);workflow JS(.claude/workflows);agent 契约 md。

## Global Constraints

- 一切 python 命令用 `uv run --no-sync`(venv-only 依赖,勿裸 pip/python)。
- 全部改动 presence-gated:缺文件/缺列/坏 json → 静默跳过或留痕降级,**绝不抛异常阻断 assemble**。
- lint 新探针 severity 一律 `warn`/`info`(advisory 起步,攒跑数再升 enforced)。
- `.claude/agents/l4-card.md` 与 `.claude/skills/stock-research/lite-playbook.md` 是**同一契约的两份拷贝**,改一处必同步另一处(tests/test_agent_defs.py 锁);既有契约锚(`进入P4倾向`/`FINAL TRANSACTION PROPOSAL`/`**Rating**`/`卡契约 v3`/`Rubric建议`/`超短口径`/`机构面网查`)**只增不删**。
- 提交信息末尾带 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

### Task 1: price_claims 纯函数模块(抽取 + 对账)

**Files:**
- Create: `autoresearch/scan/price_claims.py`
- Test: `tests/scan/test_price_claims.py`

**Interfaces:**
- Produces(后续 Task 2/3 消费):
  - `extract_price_claims(text: str, *, name: str, code6: str, year_hint: int) -> list[dict]`,每条 `{"date": "YYYYMMDD", "kind": "pct"|"limit", "value": float|None, "snippet": str}`
  - `reconcile_claims(claims: list[dict], bars: dict[str, float], *, code6: str, tol_pp: float = 1.5) -> list[dict]`,只返回不符项,每条 `{"date","kind","claimed","actual","snippet"}`;`bars` 缺该日 → 该条跳过(nodata 不算失败)
  - `bars_for(code6: str, dates: list[str], today: str) -> dict[str, float]`(湖读,失败返回 `{}`)
  - `audit_card_text(text: str, *, name: str, code6: str, date: str, bars_fn=bars_for) -> dict`,返回 `{"n_claims": int, "mismatches": list[dict]}`

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_price_claims.py
from autoresearch.scan.price_claims import (
    extract_price_claims, reconcile_claims, audit_card_text,
)

NAME, CODE = "协创数据", "300857"


def test_extract_pct_claim_with_own_name():
    text = "协创数据 7-21 放量上涨 11.4%,量比 1.9。"
    claims = extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026)
    assert len(claims) == 1
    c = claims[0]
    assert c["date"] == "20260721" and c["kind"] == "pct" and abs(c["value"] - 11.4) < 1e-9


def test_extract_limit_claim():
    text = "本股 07-15 涨停,随后三日回落。"
    claims = extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026)
    assert len(claims) == 1 and claims[0]["kind"] == "limit" and claims[0]["date"] == "20260715"


def test_extract_skips_unattributed_index_sentence():
    # 句内没有本票名称/代码/本股指代 → 不认领(科创50 的涨幅不是本票断言)
    text = "7-21 工信部算力标准催化,科创50 单日 +10%。"
    assert extract_price_claims(text, name=NAME, code6=CODE, year_hint=2026) == []


def test_extract_skips_dateless():
    assert extract_price_claims("协创数据近期上涨 30%。", name=NAME, code6=CODE, year_hint=2026) == []


def test_reconcile_within_tolerance_passes():
    claims = [{"date": "20260721", "kind": "pct", "value": 11.4, "snippet": "s"}]
    assert reconcile_claims(claims, {"20260721": 11.42}, code6=CODE) == []


def test_reconcile_mismatch_caught():
    claims = [{"date": "20260721", "kind": "pct", "value": 5.3, "snippet": "s"}]
    out = reconcile_claims(claims, {"20260721": 1.2}, code6=CODE)
    assert len(out) == 1 and out[0]["claimed"] == 5.3 and out[0]["actual"] == 1.2


def test_reconcile_limit_board_aware():
    # 300 开头 20cm 板:实涨 19.99% 算涨停成立;9.98% 不算
    ok = [{"date": "20260715", "kind": "limit", "value": None, "snippet": "s"}]
    assert reconcile_claims(ok, {"20260715": 19.99}, code6="300857") == []
    assert len(reconcile_claims(ok, {"20260715": 9.98}, code6="300857")) == 1
    # 600 开头 10cm 板:9.98% 即成立
    assert reconcile_claims(ok, {"20260715": 9.98}, code6="600350") == []


def test_reconcile_nodata_skipped():
    claims = [{"date": "20260719", "kind": "pct", "value": 5.0, "snippet": "s"}]  # 周六,无 bar
    assert reconcile_claims(claims, {}, code6=CODE) == []


def test_audit_card_text_injectable_bars():
    text = "协创数据 07-21 大涨 11.4%;本股 07-15 涨停。"
    res = audit_card_text(text, name=NAME, code6=CODE, date="2026-07-21",
                          bars_fn=lambda c, ds, today: {"20260721": 1.0, "20260715": 19.99})
    assert res["n_claims"] == 2
    assert len(res["mismatches"]) == 1 and res["mismatches"][0]["date"] == "20260721"
```

- [ ] **Step 2: 跑测确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_price_claims.py -q`
Expected: FAIL,`ModuleNotFoundError: autoresearch.scan.price_claims`

- [ ] **Step 3: 实现模块**

```python
# autoresearch/scan/price_claims.py
"""价格断言对账(确定性,零 LLM;spec 2026-07-22 dossier design ⑤-2)。

卡片/情报里的价格类断言(某日 涨X% / 涨停)与 lake OHLCV 对账——pr_20260714_006
(intel 捏造涨停)的机制化根治。精度优先:只认领**句内出现本票名称/代码/本股指代**的断言
(防把"科创50 +10%"记到个股头上);缺 bar 的日期跳过(nodata 不算失败)。advisory 用途,
一切失败路径返回空,绝不抛异常。
"""
from __future__ import annotations

import contextlib
import re

_SENT_SPLIT = re.compile(r"[。;;\n]")
# 日期:2026-07-21 / 07-21 / 7/21 / 7月21日(可带年)
_DATE = re.compile(r"(?:(20\d{2})[-/年])?(\d{1,2})[-/月](\d{1,2})日?")
_PCT = re.compile(r"(?:上涨|大涨|涨|下跌|大跌|跌|涨幅|跌幅)[^%。;;\n]{0,12}?([+-]?\d+(?:\.\d+)?)\s*%"
                  r"|([+-]\d+(?:\.\d+)?)\s*%")
_LIMIT = re.compile(r"涨停|跌停")
_SELF_MARKS = ("本股", "个股", "该股", "本票")


def _own_sentence(sent: str, name: str, code6: str) -> bool:
    if name and name in sent:
        return True
    if code6 and code6 in sent:
        return True
    return any(m in sent for m in _SELF_MARKS)


def _fmt_date(m: re.Match, year_hint: int) -> str:
    y = int(m.group(1) or year_hint)
    return f"{y:04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"


def extract_price_claims(text: str, *, name: str, code6: str, year_hint: int) -> list[dict]:
    out: list[dict] = []
    for sent in _SENT_SPLIT.split(text or ""):
        if not sent.strip() or not _own_sentence(sent, name, code6):
            continue
        dm = _DATE.search(sent)
        if not dm:
            continue
        date = _fmt_date(dm, year_hint)
        pm = _PCT.search(sent)
        if pm:
            val = float(pm.group(1) or pm.group(2))
            out.append({"date": date, "kind": "pct", "value": val, "snippet": sent.strip()[:60]})
            continue
        if _LIMIT.search(sent):
            out.append({"date": date, "kind": "limit", "value": None, "snippet": sent.strip()[:60]})
    return out


def _limit_floor(code6: str) -> float:
    # 创业板 300/301、科创板 688/689 = 20cm;其余按 10cm 主板口径(ST 不细分,advisory 容忍)
    return 19.0 if code6.startswith(("30", "68")) else 9.5


def reconcile_claims(claims: list[dict], bars: dict[str, float], *,
                     code6: str, tol_pp: float = 1.5) -> list[dict]:
    bad: list[dict] = []
    for c in claims:
        actual = bars.get(c["date"])
        if actual is None:                      # nodata:非交易日/湖缺 → 跳过,不算失败
            continue
        if c["kind"] == "pct":
            if abs(abs(float(c["value"])) - abs(float(actual))) > tol_pp:
                bad.append({**c, "claimed": float(c["value"]), "actual": round(float(actual), 2)})
        elif c["kind"] == "limit" and abs(float(actual)) < _limit_floor(code6):
            bad.append({**c, "claimed": None, "actual": round(float(actual), 2)})
    return bad


def bars_for(code6: str, dates: list[str], today: str) -> dict[str, float]:
    """按日整市场 daily(湖命中为主,universe 已预热)→ 过滤本票 pct_chg。失败 → {}。"""
    out: dict[str, float] = {}
    for dd in sorted(set(dates)):
        with contextlib.suppress(Exception):
            from autoresearch.data.cache import get_or_fetch
            df = get_or_fetch("daily", {"trade_date": dd}, today=today)
            if df is None or not len(df) or "ts_code" not in df.columns:
                continue
            hit = df[df["ts_code"].astype(str).str.startswith(code6)]
            if len(hit):
                out[dd] = float(hit.iloc[0]["pct_chg"])
    return out


def audit_card_text(text: str, *, name: str, code6: str, date: str, bars_fn=bars_for) -> dict:
    year_hint = int(str(date)[:4])
    claims = extract_price_claims(text or "", name=name, code6=code6, year_hint=year_hint)
    if not claims:
        return {"n_claims": 0, "mismatches": []}
    bars = bars_fn(code6, [c["date"] for c in claims], date) or {}
    return {"n_claims": len(claims), "mismatches": reconcile_claims(claims, bars, code6=code6)}
```

- [ ] **Step 4: 跑测确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_price_claims.py -q`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/price_claims.py tests/scan/test_price_claims.py
git commit -m "feat(scan): 价格断言对账纯函数(抽取+湖对账,pr_20260714_006 机制化根治地基)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: assemble 发布层挂对账尾行

**Files:**
- Modify: `autoresearch/scan/assemble.py`(函数 `_publish_details`,约 1068–1103 行)
- Test: `tests/scan/test_assemble_wave1.py`(新)

**Interfaces:**
- Consumes: Task 1 `audit_card_text(text, *, name, code6, date, bars_fn)`
- Produces: 发布副本卡尾追加一段 `_🔎 价格断言对账…_`;staging 卡不动(与 intel 附录同惯例)

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_assemble_wave1.py
from pathlib import Path

from autoresearch.scan import assemble


def _mk_staging(tmp_path: Path) -> Path:
    sd = tmp_path / "2026-07-21"
    (sd / "details").mkdir(parents=True)
    (sd / "details" / "300857.md").write_text(
        "# 决策卡 — 300857 协创数据 @ 2026-07-21\n协创数据 07-21 大涨 11.4%。\n"
        "**Rating**: Underweight\nFINAL TRANSACTION PROPOSAL: **SELL**\n", encoding="utf-8")
    (sd / "finalists.csv").write_text("code,name,sector,lane\n300857,协创数据,消费电子,pinned\n",
                                      encoding="utf-8")
    return sd


def test_publish_details_appends_reconcile_tail(tmp_path, monkeypatch):
    sd = _mk_staging(tmp_path)
    out = tmp_path / "pub"
    out.mkdir()
    # 注入假 bars:实涨 1.0% → 断言 11.4% 不符
    monkeypatch.setattr("autoresearch.scan.price_claims.bars_for",
                        lambda c, ds, today: {"20260721": 1.0})
    n = assemble._publish_details(sd, out)
    assert n == 1
    body = (out / "协创数据.md").read_text(encoding="utf-8")
    assert "价格断言对账" in body and "11.4" in body and "1.0" in body
    # staging 卡不动
    assert "价格断言对账" not in (sd / "details" / "300857.md").read_text(encoding="utf-8")


def test_publish_details_all_clear_line(tmp_path, monkeypatch):
    sd = _mk_staging(tmp_path)
    out = tmp_path / "pub"
    out.mkdir()
    monkeypatch.setattr("autoresearch.scan.price_claims.bars_for",
                        lambda c, ds, today: {"20260721": 11.4})
    assemble._publish_details(sd, out)
    body = (out / "协创数据.md").read_text(encoding="utf-8")
    assert "价格断言对账" in body and "0 条不符" in body


def test_publish_details_survives_bars_crash(tmp_path, monkeypatch):
    sd = _mk_staging(tmp_path)
    out = tmp_path / "pub"
    out.mkdir()
    def boom(c, ds, today):
        raise RuntimeError("lake down")
    monkeypatch.setattr("autoresearch.scan.price_claims.bars_for", boom)
    assert assemble._publish_details(sd, out) == 1     # 不炸,卡照发(对账段缺席)
```

- [ ] **Step 2: 跑测确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_assemble_wave1.py -q`
Expected: FAIL(`价格断言对账` not in body)

- [ ] **Step 3: 改 `_publish_details`**

在 intel 附录写完之后、`n += 1` 之前插入(注意 `body` 变量名已被 intel 用过,新变量取 `card_txt`):

```python
        # ── 价格断言对账(Wave1 ⑤-2,advisory;含 intel 附录一起对——pr_006 的捏造在 intel 侧)──
        with contextlib.suppress(Exception):
            from autoresearch.scan import price_claims
            card_txt = dst.read_text(encoding="utf-8")
            res = price_claims.audit_card_text(
                card_txt, name=str(fr.get("name", "") or ""), code6=code,
                date=scan_dir.name)
            if res["n_claims"]:
                bad = res["mismatches"]
                if bad:
                    det = ";".join(f"{b['date'][4:6]}-{b['date'][6:]} 称"
                                   f"{('涨停' if b['kind'] == 'limit' else str(b['claimed']) + '%')}"
                                   f" 实为{b['actual']}%" for b in bad[:3])
                    line = (f"\n\n---\n_🔎 价格断言对账(确定性·advisory):{res['n_claims']} 条可对账,"
                            f"**{len(bad)} 条不符** → {det}_\n")
                else:
                    line = (f"\n\n---\n_🔎 价格断言对账(确定性·advisory):{res['n_claims']} 条可对账,"
                            f"0 条不符_\n")
                with dst.open("a", encoding="utf-8") as fh:
                    fh.write(line)
```

文件顶部确认已有 `import contextlib`(assemble.py 若无则在 import 区加一行)。

- [ ] **Step 4: 跑测确认通过 + 回归**

Run: `uv run --no-sync python -m pytest tests/scan/test_assemble_wave1.py tests/scan/test_assemble.py -q`
Expected: 全 passed(存量 assemble 测试不受影响)

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/assemble.py tests/scan/test_assemble_wave1.py
git commit -m "feat(scan): 发布副本卡尾追加价格断言对账行(advisory,staging 不动)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: product_shape_lint 两个新探针(引用密度 + 价格对账聚合)

**Files:**
- Modify: `autoresearch/learning/self_review.py`(`product_shape_lint` 函数尾部,`return out` 之前)
- Test: `tests/learning/test_shape_probes_wave1.py`(新)

**Interfaces:**
- Consumes: Task 1 `audit_card_text`
- Produces: lint 记录 `{"check": "citation_density", "severity": "warn", ...}` 与 `{"check": "price_claim_mismatch", "severity": "warn", ...}`(assemble 827 行既有接线自动收编,零改动)

- [ ] **Step 1: 写失败测试**

```python
# tests/learning/test_shape_probes_wave1.py
from pathlib import Path

from autoresearch.learning.self_review import product_shape_lint


def _mk(tmp_path: Path, card: str, code: str = "688689") -> Path:
    sd = tmp_path / "2026-07-21"
    (sd / "details").mkdir(parents=True)
    (sd / "details" / f"{code}.md").write_text(card, encoding="utf-8")
    (sd / "finalists.csv").write_text(f"code,name,sector,lane\n{code},银河微电,半导体,\n",
                                      encoding="utf-8")
    return sd


FULL_SPARSE = ("# 决策卡 — 688689 银河微电 @ 2026-07-21\n进入P4倾向: Hold\n"
               "07-19 一条引用\n**Rating**: Underweight\n")
EARLY_STOP = ("# 决策卡 — 688689 银河微电 @ 2026-07-21  ·  〔早停·表面 DD〕\n"
              "**Rating**: Hold\n")
FULL_RICH = ("# 决策卡 — 688689 银河微电 @ 2026-07-21\n进入P4倾向: Hold\n"
             + "\n".join(f"2026-07-{d:02d} 引用{d}" for d in range(10, 17))
             + "\n**Rating**: Hold\n")


def _hits(out, check):
    return [o for o in out if o["check"] == check]


def test_citation_density_warns_sparse_full_card(tmp_path):
    sd = _mk(tmp_path, FULL_SPARSE)
    out = product_shape_lint(sd, "2026-07-21")
    hits = _hits(out, "citation_density")
    assert len(hits) == 1 and hits[0]["severity"] == "warn" and hits[0]["code"] == "688689"


def test_citation_density_exempts_early_stop_and_rich(tmp_path):
    assert not _hits(product_shape_lint(_mk(tmp_path, EARLY_STOP), "2026-07-21"),
                     "citation_density")


def test_citation_density_rich_card_clean(tmp_path):
    assert not _hits(product_shape_lint(_mk(tmp_path, FULL_RICH), "2026-07-21"),
                     "citation_density")


def test_price_claim_mismatch_probe(tmp_path, monkeypatch):
    card = ("# 决策卡 — 688689 银河微电 @ 2026-07-21\n进入P4倾向: Hold\n"
            "银河微电 07-21 大涨 9.5%\n" + FULL_RICH.split("进入P4倾向: Hold\n")[1])
    sd = _mk(tmp_path, card)
    monkeypatch.setattr("autoresearch.scan.price_claims.bars_for",
                        lambda c, ds, today: {"20260721": 1.0})
    hits = _hits(product_shape_lint(sd, "2026-07-21"), "price_claim_mismatch")
    assert len(hits) == 1 and hits[0]["severity"] == "warn"
```

- [ ] **Step 2: 跑测确认失败**

Run: `uv run --no-sync python -m pytest tests/learning/test_shape_probes_wave1.py -q`
Expected: FAIL(无 citation_density 记录)

- [ ] **Step 3: 在 `product_shape_lint` 尾部(`return out` 前)加探针 7/8**

沿用函数内既有风格(presence-gated + `add()`;`fin_rows`/`reused` 变量已在前文就绪):

```python
    # ── 7. 引用密度(Wave1 ⑤-5):满卡带日期引用 <6 行 → warn;早停/♻️复用卡豁免 ──
    _DATED = re.compile(r"20\d{2}-\d{1,2}-\d{1,2}|\b\d{1,2}[-/]\d{1,2}\b|20\d{6}")
    cards: dict[str, str] = {}
    with contextlib.suppress(Exception):
        for p in (scan_dir / "details").glob("*.md"):
            cards[p.stem.split(".")[0].zfill(6)] = p.read_text(encoding="utf-8")
    for code, text in sorted(cards.items()):
        if "〔早停" in text or code in reused:
            continue
        n_cited = sum(1 for ln in text.splitlines() if _DATED.search(ln))
        if n_cited < 6:
            add("citation_density", "warn",
                f"满卡带日期引用仅 {n_cited} 行(<6)——研究底料偏薄(07-21 银河微电 4 行病)",
                code=code)

    # ── 8. 价格断言对账聚合(Wave1 ⑤-2):任一卡有不符断言 → warn(逐码) ──
    name_by_code = {r["code"]: "" for r in fin_rows}
    with contextlib.suppress(Exception):
        import pandas as pd
        fin = pd.read_csv(scan_dir / "finalists.csv", dtype={"code": str})
        for _, r in fin.iterrows():
            c = str(r.get("code", "") or "").split(".")[0].zfill(6)
            name_by_code[c] = "" if pd.isna(r.get("name")) else str(r.get("name"))
    for code, text in sorted(cards.items()):
        with contextlib.suppress(Exception):
            from autoresearch.scan import price_claims
            res = price_claims.audit_card_text(
                text, name=name_by_code.get(code, ""), code6=code, date=date_str)
            if res["mismatches"]:
                b = res["mismatches"][0]
                add("price_claim_mismatch", "warn",
                    f"{len(res['mismatches'])} 条价格断言与 OHLCV 不符(首条 {b['date']} "
                    f"称 {'涨停' if b['kind'] == 'limit' else str(b['claimed']) + '%'} "
                    f"实 {b['actual']}%)——pr_20260714_006 型",
                    code=code)
```

注意:函数内已 `import re` / `import contextlib`(头部局部 import 区),无需重复;`date_str` 形如 `2026-07-21`(与 `audit_card_text` 的 `date` 参数约定一致)。

- [ ] **Step 4: 跑测确认通过 + lint 相关回归**

Run: `uv run --no-sync python -m pytest tests/learning/test_shape_probes_wave1.py tests/learning/test_self_review.py tests/learning/test_self_review_drift.py -q`
Expected: 全 passed

- [ ] **Step 5: Commit**

```bash
git add autoresearch/learning/self_review.py tests/learning/test_shape_probes_wave1.py
git commit -m "feat(learning): product_shape_lint 探针7引用密度+探针8价格断言对账聚合(advisory)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: dispatch-plan meta 带 pinned 旗

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(`dispatch_plan`,712–750 行)
- Test: `tests/scan/test_l4_dispatch_pinned.py`(新)

**Interfaces:**
- Produces: `dispatch_plan(...)["meta"][code6]` 增键 `"pinned": bool`(lane=="pinned";主会话把它透传给每股 l4-stock args,Task 5 消费)

- [ ] **Step 1: 写失败测试**

```python
# tests/scan/test_l4_dispatch_pinned.py
from pathlib import Path

from autoresearch.scan.agents.l4_card import dispatch_plan


def test_dispatch_meta_carries_pinned_flag(tmp_path: Path):
    sd = tmp_path / "2026-07-21"
    sd.mkdir(parents=True)
    (sd / "finalists.csv").write_text(
        "code,name,sector,lane\n300857,协创数据,消费电子,pinned\n002926,华西证券,证券Ⅱ,healthy\n",
        encoding="utf-8")
    for c in ("300857", "002926"):
        (sd / f"_l4_prompt_{c}.md").write_text("pkg", encoding="utf-8")
    plan = dispatch_plan("2026-07-21", root=tmp_path)
    assert plan["meta"]["300857"]["pinned"] is True
    assert plan["meta"]["002926"]["pinned"] is False
```

- [ ] **Step 2: 跑测确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_dispatch_pinned.py -q`
Expected: FAIL,KeyError `'pinned'`

- [ ] **Step 3: 改 `dispatch_plan`**

l4_card.py:749 处,meta 赋值行改为:

```python
            meta[code6] = {"name": _cell(r, "name"), "sector": _cell(r, "sector"),
                           "pinned": _cell(r, "lane").strip() == "pinned"}
```

- [ ] **Step 4: 跑测确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_l4_dispatch_pinned.py -q`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add autoresearch/scan/agents/l4_card.py tests/scan/test_l4_dispatch_pinned.py
git commit -m "feat(scan): dispatch-plan meta 带 pinned 旗(SELL 双复核前置)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: l4-stock.js SELL 双复核 + SKILL 文案

**Files:**
- Modify: `.claude/workflows/l4-stock.js`(Verify 段整段替换 + CARD schema + args 解析)
- Modify: `.claude/skills/scan-market/SKILL.md`(步骤 4 的 args 说明一行)
- Test: `tests/test_agent_defs.py`(新增一条 workflow 文本锚测试,防漂移)

**Interfaces:**
- Consumes: Task 4 的 `meta[code].pinned`(主会话组 args 时透传为 `args.pinned`)
- Produces: `_ensemble_<code>.json` 记录增 `"trigger": "ow_review" | "sell_review"` 键(Task 6 assemble 按方向折)

- [ ] **Step 1: 加 workflow 文本锚测试(先失败)**

在 `tests/test_agent_defs.py` 末尾追加:

```python
def test_l4_stock_workflow_sell_review_anchors():
    """l4-stock.js 的 SELL 双复核契约锚(Wave1 ⑤-3):trigger 字段 + pinned 消费在场。"""
    js = (ROOT / ".claude" / "workflows" / "l4-stock.js").read_text(encoding="utf-8")
    for a in ("sell_review", "ow_review", "A.pinned", "proposal"):
        assert a in js, f"l4-stock.js 缺 SELL 双复核锚「{a}」"
```

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py::test_l4_stock_workflow_sell_review_anchors -q`
Expected: FAIL(缺锚)

- [ ] **Step 2: 改 l4-stock.js**

(a) CARD schema 增 proposal(required 不变):

```js
const CARD = { type: 'object', required: ['code', 'rating'],
  properties: { code: { type: 'string' }, rating: { type: 'string' },
    conviction: { type: 'number' }, proposal: { type: 'string' } } }
```

(b) args 解析区(`const cfg = A.cfg || {}` 之后)加:

```js
const pinned = !!A.pinned   // dispatch-plan meta 透传;缺省 false = 现行为(parity)
```

(c) Card 段派发 prompt 的最后一句改为(让 agent 把 FINAL 行也回传):

```
最后返回该卡最终五档评级与 FINAL 行(code / rating / conviction / proposal=FINAL TRANSACTION PROPOSAL 的值,如 "SELL")。
```

(d) Verify 段整段替换为(保持原缩进风格):

```js
// ── Verify:≥OW 双复核(防追高误买)∥ pinned 卖出双复核(防误卖持仓,Wave1 ⑤-3)──
// 取中位;ow_review 只向下折、sell_review 只向温和折(assemble 侧 _apply_ensemble_fold 按 trigger 再折一遍=权威)。
const isOW = (r) => /(overweight|\bbuy\b|增持|买入)/i.test(r || '')
const isSellish = (card) => /sell/i.test(card.rating || '') || /sell/i.test(card.proposal || '')
const trigger = isOW(card.rating) ? 'ow_review' : (pinned && isSellish(card) ? 'sell_review' : null)
let final = card.rating
if (trigger) {
  phase('Verify')
  log(trigger === 'ow_review'
    ? `🎭 买单复核:${code} 追加 2 独立 run 取中位(只向下折回)`
    : `🎭 持仓卖出复核:${code} 追加 2 独立 run 取中位(只向温和折回,卖错持仓代价不对称)`)
  const RANK = { 'sell': 0, 'underweight': 1, 'hold': 2, 'overweight': 3, 'buy': 4 }
  const tier = (r) => RANK[String(r || '').toLowerCase()] ?? 2
  const reruns = (await parallel([2, 3].map((i) => () => agent(
    `独立复核 run${i}(不知道其它 run 结论):执行 ${SD}/_l4_prompt_${code}.md 的任务包,按人设走渐进深度 DD,决策卡写到 ${SD}/ensemble/${code}.run${i}.md(先自行创建 ensemble/ 目录),返回 code/rating/conviction/proposal。`,
    { agentType: 'l4-card', effort: cfg.agents?.l4_card?.effort ?? 'xhigh',
      label: `ens${i}:${code}`, phase: 'Verify', schema: CARD })))).filter(Boolean)
  const ratings = [card.rating, ...reruns.map((r) => r.rating)]
  const sorted = ratings.map(tier).sort((a, b) => a - b)
  // N<3(复核 run 失败)→ 不折回原判 + degraded 标记强制人裁展示(sell_review 不因缺 run 软化卖出)
  const degraded = ratings.length < 3
  const medianTier = sorted[Math.floor(sorted.length / 2)]
  const names = ['Sell', 'Underweight', 'Hold', 'Overweight', 'Buy']
  const rec = { code, ratings, median: names[medianTier],
    spread: sorted[sorted.length - 1] - sorted[0], degraded, trigger }
  await agent(
    `在仓库根目录精确执行下面这条命令,然后只回报退出码。不要做别的、不要判断。\n\n\`\`\`\ncat > ${SD}/_ensemble_${code}.json << 'EOF'\n${JSON.stringify(rec)}\nEOF\n\`\`\``,
    { agentType: 'general-purpose', effort: 'low', label: `ens-dump:${code}`, phase: 'Verify' })
  if (!degraded) {
    if (trigger === 'ow_review' && tier(rec.median) < tier(card.rating)) final = rec.median
    if (trigger === 'sell_review' && tier(rec.median) > tier(card.rating)) final = rec.median
  }
  log(`🎭 复核 ✓ ${code} [${trigger}] runs=${JSON.stringify(ratings)} → 终评 ${final}${degraded ? '(degraded,报告强制人裁展示)' : ''}`)
}
```

注意:原 `const isOW` 独立声明行被并入本段,勿留重复声明;`degraded` 时 ow_review 原先"退化取更偏空"改为"不折回+人裁"——sell/ow 对称,折回只在 3 run 齐时做(assemble 权威折回同规则,见 Task 6)。

(e) SKILL.md 步骤 4 的派发行下方注释行(Task 7 之外的顺手同步,`(cfg = 步骤 0.5 …)` 那行)改为:

```
   (**cfg = 步骤 0.5 frame 回显的 `user_config` 块原样透传,勿传 `{}`**——空 cfg 静默关 intel/降 effort,见 0.5 节 07-21 事故注;**pinned = dispatch 返回的 `meta[code].pinned` 原样透传**——缺了 SELL 双复核不触发)
```

- [ ] **Step 3: 跑锚测试确认通过**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`
Expected: 全 passed(含新锚测试)

- [ ] **Step 4: Commit**

```bash
git add .claude/workflows/l4-stock.js .claude/skills/scan-market/SKILL.md tests/test_agent_defs.py
git commit -m "feat(scan): 持仓 SELL 双复核(sell_review trigger,取中位只向温和折)+ pinned 透传契约

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: assemble 权威折回按 trigger 分方向

**Files:**
- Modify: `autoresearch/scan/assemble.py`(`_apply_ensemble_fold`,140–151 行)
- Test: `tests/scan/test_assemble_wave1.py`(追加)

**Interfaces:**
- Consumes: `_ensemble_<code>.json` 的 `trigger` 键(Task 5);缺键 = 旧记录 → 保持只向下(parity)

- [ ] **Step 1: 追加失败测试**

```python
# tests/scan/test_assemble_wave1.py 追加
def test_ensemble_fold_ow_only_down():
    assert assemble._apply_ensemble_fold("Overweight", {"median": "Hold"}) == "Hold"
    assert assemble._apply_ensemble_fold("Hold", {"median": "Overweight"}) == "Hold"


def test_ensemble_fold_sell_review_only_milder():
    rec = {"median": "Hold", "trigger": "sell_review"}
    assert assemble._apply_ensemble_fold("Sell", rec) == "Hold"          # 复核救回误卖
    rec2 = {"median": "Sell", "trigger": "sell_review"}
    assert assemble._apply_ensemble_fold("Underweight", rec2) == "Underweight"  # 不向更狠折


def test_ensemble_fold_degraded_noop():
    rec = {"median": "Hold", "trigger": "sell_review", "degraded": True}
    assert assemble._apply_ensemble_fold("Sell", rec) == "Sell"
```

Run: `uv run --no-sync python -m pytest tests/scan/test_assemble_wave1.py -q`
Expected: 新增 3 条 FAIL

- [ ] **Step 2: 改 `_apply_ensemble_fold`**

```python
def _apply_ensemble_fold(rating: str, rec: dict | None) -> str:
    """复核折回:ow_review(默认)只向下(更靠 Sell)折;sell_review 只向温和折(救误卖持仓,
    Wave1 ⑤-3)。degraded(复核 run 不齐)→ 原样不折,交 ens_flag 人裁。
    median/rating 不在五档词表(脏数据)→ 原样不动,不报错。
    """
    if not rec or rec.get("degraded"):
        return rating
    median = rec.get("median")
    if median not in TIER_RANK or rating not in TIER_RANK:
        return rating
    if rec.get("trigger") == "sell_review":
        return median if TIER_RANK[median] < TIER_RANK[rating] else rating
    return median if TIER_RANK[median] > TIER_RANK[rating] else rating
```

- [ ] **Step 3: 跑测确认通过 + assemble 回归**

Run: `uv run --no-sync python -m pytest tests/scan/test_assemble_wave1.py tests/scan/ -q`
Expected: 全 passed

- [ ] **Step 4: Commit**

```bash
git add autoresearch/scan/assemble.py tests/scan/test_assemble_wave1.py
git commit -m "feat(scan): ensemble 权威折回按 trigger 分方向(sell_review 只向温和折,degraded 不折人裁)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: l4-card / lite-playbook 契约增强(断言分级 + 原文引句 + 一致预期差 + 席位史)

**Files:**
- Modify: `.claude/agents/l4-card.md`
- Modify: `.claude/skills/stock-research/lite-playbook.md`(同步同一改动)
- Modify: `tests/test_agent_defs.py`(锚表扩)

**Interfaces:**
- Produces: 卡片新契约锚 `断言分级`、`一致预期差`(test_agent_defs 双侧锁定)

- [ ] **Step 1: 扩锚测试(先失败)**

`tests/test_agent_defs.py` 的 `test_l4_card_contract_anchors_synced` 中 anchors 列表追加两项:

```python
    anchors = ["进入P4倾向", "FINAL TRANSACTION PROPOSAL", "**Rating**",
               "断言分级", "一致预期差",
               ...]  # 其余原有项保持不动
```

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`
Expected: FAIL(两份文件都缺新锚)

- [ ] **Step 2: 两份文件同步加契约**

**(a) 铁律节**(l4-card.md「## 铁律(内化)」与 lite-playbook 对应节)追加一条 bullet:

```
- **断言分级(准确度契约,Wave1)**:叙事段(一段话研判/裁决表/催化)的每个关键事实断言分三级——**已核**(出自 slim/简报数字,默认级不标)/**网查**(必须`「原文引句≤30字」+来源名+日期`,禁止转述标题当事实)/**推断**(明写"推断")。价格/涨跌类断言只允许已核级(出自 verified OHLCV);intel 稿里的价格断言须与 slim OHLCV 对上才可引用,对不上写"intel 称 X 未对账"。发布层有确定性对账兜底,但第一责任在写卡时。
```

**(b) 满卡模板**的 `**(A股)**:` 行改为:

```
**(A股)**:主力净流入(10日)/获利比例/多头排列·RSI·MACD/北向/股东户数/预告快报/质押红旗/涨跌停可交易性/龙虎榜席位史(简报带席位行时必引:机构/游资净额与方向)/**一致预期差**(fwd-EPS·fwd-PE vs TTM 实际,一行差值方向;简报无机构面行=写"无一致预期数据")
```

**(c) 压缩纪律节**追加一句:

```
带日期引用是硬底:满卡正文带日期引用行 ≥6(早停卡豁免)——lint citation_density 会逐卡查。
```

- [ ] **Step 3: 跑锚测试确认通过**

Run: `uv run --no-sync python -m pytest tests/test_agent_defs.py -q`
Expected: 全 passed

- [ ] **Step 4: Commit**

```bash
git add .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md tests/test_agent_defs.py
git commit -m "feat(agents): l4-card 契约增强——断言分级/原文引句/一致预期差/席位史/引用密度硬底(双侧锚同步)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 全量回归 + 07-21 真数据冒烟

**Files:** 无新改动(验证任务)

- [ ] **Step 1: 全套件回归**

Run: `uv run --no-sync python -m pytest -q 2>&1 | tail -3; echo "exit=$?"`
Expected: 约 1320+新增 ≈ 1340 级全绿,`exit=0`(勿用管道吞退出码——echo 行必须打出 exit=0)

- [ ] **Step 2: 07-21 staging 冒烟(确定性,零 LLM)**

```bash
uv run --no-sync python -c "
from autoresearch.learning.self_review import product_shape_lint
out = product_shape_lint('context/scan/2026-07-21', '2026-07-21')
for o in out:
    if o['check'] in ('citation_density', 'price_claim_mismatch'):
        print(o['check'], o['code'], o['detail'][:60])
"
```
Expected: `citation_density 688689 …`(银河微电 4 行病被逮)与 `300434` 也可能亮;price_claim_mismatch 视湖数据可能 0 条(无失败=正常,有失败=对账在工作)。

```bash
uv run --no-sync python -m autoresearch.scan.assemble 2026-07-21 2>&1 | tail -3
ls -t reports/scan/ | head -1
```
然后对最新目录抽查一张发布卡:
```bash
grep -l "价格断言对账" reports/scan/$(ls -t reports/scan/ | head -1)/details/*.md | head -3
```
Expected: 至少含带日期价格断言的卡(协创数据/长飞光纤)尾部出现对账行。

- [ ] **Step 3: 冒烟结论写回 plan**

在本文件末尾追加一段 `## 冒烟实录(YYYY-MM-DD)`:citation_density 命中哪些码、price 对账行落在哪几张卡、全套件计数。

- [ ] **Step 4: Commit(收尾)**

```bash
git add docs/plans/2026-07-22-wave1-accuracy-card-enrichment-plan.md
git commit -m "docs(plan): Wave1 冒烟实录回填

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 计划自审(已跑)

- **Spec 覆盖**:⑤-1(Task 7 断言分级)/⑤-2(Task 1/2/3)/⑤-3(Task 4/5/6)/⑤-5(Task 3)/④所有卡增强(Task 7 席位史+一致预期差+原文引句)——Wave 1 范围全覆盖;⑤-4 预测留档对账属 Wave 3(档案就位后),不在本 plan。
- **占位扫描**:无 TBD/伪码;所有 Step 带真代码/真命令/预期输出。
- **类型一致**:`audit_card_text` 签名在 Task 1 定义、Task 2/3 消费一致;`trigger` 键 Task 5 产、Task 6 消费一致;`meta[].pinned` Task 4 产、Task 5 消费一致;js RANK(sell=0..buy=4,小=差)与 python TIER_RANK(Buy=0..Sell=4,大=差)方向相反——两处折回代码各自用本侧词表,已按各自方向写对,勿互抄。
- **已知边界**:早停卡引用密度豁免;`sell_review` 触发依赖主会话透传 `args.pinned`(SKILL 已写死契约);对账 nodata 不算失败(周末/停牌日期);发布副本追加不动 staging(retro/parse_rating 口径不变)。

## 冒烟实录(2026-07-23)

**Task 8 执行(HEAD 95c90ab,Tasks 1-7 全落)。验证任务,零代码改动;仅本节写回。**

- **Step 1 全套件回归**:`1346 passed, 2 warnings in 52.97s`(复跑校验 `54.02s`),`exit=0`。约超预期(1320+新增≈1340 级)6 只,全绿。2 条 warning 均为 pandas `FutureWarning`(`fillna` downcasting / 空列 concat),pre-existing 与本 wave 改动无关,非新增。

- **Step 2-a citation_density**:**0 命中**(与 brief 预期"至少银河微电"不符,已查明非探针故障)。根因两条独立且都成立:
  1. `context/scan/2026-07-21/details/688689.md`、`300434.md` 标题均含〔早停(P3 表面 DD 早停),命中 `product_shape_lint` 已文档化的豁免边界(`早停卡引用密度豁免`,`self_review.py:393`)。
  2. 即使不豁免,实测 12 张发布卡 `n_cited`(带日期行数)全部 ∈ `[8,19]`,无一低于 <6 门槛(688689=8、300434=9,其余 002371=13/002926=11/300857=15/301282=9/600188=9/600350=9/600521=10/601336=14/601869=19/688766=18)。12 张卡 mtime 均为 07-21 22:24–22:26,早于 citation_density 探针本身诞生(commit `6e74dea` @ 07-22 22:53)与 Task 7 卡契约增强(commit `95c90ab` @ 07-23 00:00)——这批卡从未被新契约"喂过",纯粹是旧卡本身引用密度已达标,非新尺子失灵。docstring 里"07-21 银河微电仅 4 行"的例子应是设计期对该卡另一种(更严口径的)人工计数,与当前实现的宽松正则(任何含日期 token 的行,含叙事性价格时间线如"7/6 冲到 80.75")不是同一把尺子。
  - 结论:探针机制本身健康(逻辑走查确认早停豁免生效、门槛判断正确),只是 07-21 这批真实数据不含它要抓的缺陷模式——真实空集,不是假阴性。

- **Step 2-b price_claim_mismatch(product_shape_lint 直接探针)**:**2 码命中**,对账机制在工作且抓到真不符(比"0 条不符"的默认预期更强的证据):
  - `600521` 华海药业:`1 条价格断言与 OHLCV 不符(首条 20260609 称 20.0% 实 -0.94%)`
  - `688766` 普冉股份:`1 条价格断言与 OHLCV 不符(首条 20260721 称 8.5% 实 17.5%)`

- **Step 2-c assemble 发布卡尾部对账行**(`reports/scan/20260723_0018/`,数据日 2026-07-21,12 张卡+trace 34 件,assemble 全程无报错):**4/12 张卡**携带 `🔎 价格断言对账` 尾行(其余 8 张 `n_claims=0` 未挂尾行,属"对账 nodata 不算失败"边界,非故障):
  - `北方华创.md`(002371):`_🔎 价格断言对账(确定性·advisory):2 条可对账,0 条不符_`
  - `华海药业.md`(600521):`_🔎 价格断言对账(确定性·advisory):1 条可对账,**1 条不符** → 06-09 称20.0% 实为-0.94%_`
  - `协创数据.md`(300857):`_🔎 价格断言对账(确定性·advisory):1 条可对账,0 条不符_`
  - `普冉股份.md`(688766):`_🔎 价格断言对账(确定性·advisory):1 条可对账,**1 条不符** → 07-21 称8.5% 实为17.5%_`
  - brief 预测的"协创数据/长飞光纤最可能"命中一半:协创数据✓(0 条不符,干净);长飞光纤(601869)未挂尾行——卡文内无可对账的日期价格断言(n_claims=0),不算失败。
  - 与 Step 2-b 的 `product_shape_lint` 独立读数完全交叉印证(同 2 码、同不符明细),两条实现路径(assemble 内联 vs lint 聚合)一致。

- **三判据小结**:①citation_density 未命中(有据可查的真实空集,非故障)②price 对账行落卡✓(4 张,超"至少一张"门槛)③price_claim_mismatch 非零命中且为真实不符(强于"0 条"默认预期的验证)。全套件零失败,assemble 零报错。整体 **DONE**——①的"未达预期"已定位到与已知设计边界(早停豁免)及数据本身(全卡达标)完全吻合的根因,不构成产品缺陷,故不裁 DONE_WITH_CONCERNS。

## 冒烟修正(2026-07-23·终审 C-1 修后)

> **本节修正上方 `## 冒烟实录(2026-07-23)` Step 2-b/2-c 的判据②结论。既有实录不改写——留作错误留痕。**
> 终审复核裁定:原实录把 600521/688766 两条"catch"当成"抓到真不符(强于 0 条默认预期)",**实为 2/2 假阳性**。抽取器把「累计区间涨幅」与「前向情景目标%」当成了某日单日已实现移动。C-1 收紧抽取器后重跑,两条假阳全部归零。

**根因(承认原 2 条 catch 为假阳)**:
- **600521 华海药业**:原实录 `**1 条不符** → 06-09 称20.0% 实为-0.94%`。真身是卡内 `本股 6/09 14.64→7/16 17.63(+20%)非超卖` = **累计区间涨幅**(6/09→7/16 累计 +20%,分析师正确),抽取器错取区间起点 6/09 + 累计 +20% 当单日 → 对账 06-09 单日 −0.94% 报假不符。**分析师从没说 06-09 涨 20%。**
- **688766 普冉股份**:原实录 `**1 条不符** → 07-21 称8.5% 实为17.5%`。真身是同句里 `延续至 510(**+8.5%**)` = **Bull 情景目标**(前向),而同句 `7/21 单日已实测 +17.5%` 才是已实现单日移动。抽取器把前向 +8.5% 挂到 7/21 → 报假不符。

**修法(`autoresearch/scan/price_claims.py` `extract_price_claims`,零 assemble 改动=对账器修好即全链修好)**:抽取器只认「某日已实现单日股价移动」,按每个 % 匹配点的局部语境三类排除——(a) 日期簇与数字间有区间标记(→/至/到/从)⇒ 弃(华海);(b) 数字前局部窗有情景/目标语境(Bull/延续至/目标/p60/EV…)⇒ 弃该 %(普冉 +8.5%);(c) 数字前后 8 字内有基本面名词(营收/净利/同比…)⇒ 弃。语境检查锚在**数字位**而非 match.start(verb 支路会从很靠左的动词起匹配),区间检查用**日期簇**(相邻≤16 字的日期并簇)才能逮到落在两日期之间的 →。

**修后各卡对账行实测**(数据日 2026-07-21,发布 run `reports/scan/20260723_0104/`,assemble exit=0):

| 卡 | 修前(原实录) | 修后(实测) | 判定 |
|---|---|---|---|
| 华海药业 600521 | `**1 条不符** → 06-09 称20.0% 实为-0.94%` | **对账行整行消失**(n_claims=0:+20% 区间被弃,卡内无其它可对账单日断言) | 假阳消除 ✓ |
| 普冉股份 688766 | `**1 条不符** → 07-21 称8.5% 实为17.5%` | `1 条可对账,0 条不符`(+8.5% 情景弃、**+17.5% 已实测被正确抽取并对账通过**) | 假阳消除 ✓(理想:保真) |
| 北方华创 002371 | `2 条可对账,0 条不符` | `2 条可对账,0 条不符`(涨停 +10.00% 真断言) | 不变 ✓ |
| 协创数据 300857 | `1 条可对账,0 条不符` | `1 条可对账,0 条不符` | 不变(见残留)✓ |

- **残留取舍(不许静默)**:300857 卡内 `7-19 中报预告 +247%~+340%` 仍被抽为 pct=247%(基本面预告%,非本 C-1 三向量之一;`预告` 不在 brief 明列的基本面词表)。但 07-19 是**周日**→ 湖无 bar → nodata → **0 不符**,不产生对读者的「不符」误指控,不达 C-1 严重度阈值(=对外假阳)。故按 brief 明列词表**保守不扩**,如实记账于此。

**probe 归零证据**(`product_shape_lint('context/scan/2026-07-21','2026-07-21')`,真湖对账):
- `price_claim_mismatch` **0 命中**(全 12 卡),600521/688766 均不在其中 → **对 600521/688766 归零** ✓。
- 交叉印证:assemble 内联对账(发布卡尾)与 lint 聚合对账(probe 8)两条独立路径读数一致(华海无尾行、普冉 0 不符)。

**顺带活体验收 I-2(probe 9)**:同一 lint 跑出 `sell_review_missing` **2 命中 → 300857/601869**(两只均 lane=pinned + `**Rating**: Underweight` + 无 `_ensemble_*.json`)= 招牌 SELL 双复核在 07-21 未跑的**真实留痕**(07-21 run 早于 ⑤-3)。tripwire 按设计对真数据开火,防呆到位。

**全套件**:`1353 passed, 2 warnings`(baseline 1346 + 新增 7),`exit=0`;2 warning 均为 pre-existing pandas FutureWarning(temperature.py concat),与本次改动无关。ruff 4 改动文件全过。

**判据②改写**:原"机制响但落卡的 catch 为假阳"→ 修后 **price 对账在真数据上 0 假阳、且对普冉保真(realized +17.5% 正确抽取通过)**,判据②由 FAIL-in-spirit 升为 **PASS**。

**R-1 追记(round2 复核·2026-07-23 当日闭合)**:预告%/累计%/`X%~Y%` 百分区间三条同类旁路已闭合(`_FUND` 扩 `预告/预增/预盈/中报/年报/季报/归母`、`_RANGE_MARKS` 扩 `累计`、新增 `_PCT_TILDE_RANGE` 排除;`tests/scan/test_price_claims.py` 新增 3 测,全套件 1356 passed)。详见 `.superpowers/sdd/wave1-r1-fix-report.md`。
协创 300857 卡内 `7-19 中报预告 +247%~+340%` 现为**抽取级排除**(`extract_price_claims` 直接判 0 claims),不再依赖 07-19 周日 nodata 的偶然守卫;真 staging `product_shape_lint` 的 `price_claim_mismatch` 仍 0。
