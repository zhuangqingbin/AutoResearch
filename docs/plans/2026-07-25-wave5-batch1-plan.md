# Wave5 批 1 实施计划(过程直播 + 免费两修 + 早停记账 + 守护)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 scan-market 在跑动中把已有确定性产物转播给用户(8 检查点),同时修好两处「写了没在跑」的优化(prewarm 未装、共享指令稿无生产者),并把「为什么 0 买」从未经证实的叙事变成可审计的早停账本。

**Architecture:** 三条腿互不耦合。①**直播**:新增零 LLM 的 `autoresearch/scan/render.py`(四个 view),prelude 汇总屏双写文件,workflow 逐只 log 入围名单,SKILL.md 定死 8 个检查点契约——全部只转播现成产物,零新计算。②**免费两修**:装 launchd prewarm;给 `_l4_shared_instructions.md` 补确定性生产者(它当前无人生产,当日校准行从未到达任何卡片)。③**早停记账**:卡模板加一行机读 `**早停**` 契约 → 解析落 `_early_stop.json` → 新账本 `earlystop_ledger` + t1 早停桶扩样 + 0买叙事按真机制分桶重写。

**Tech Stack:** Python 3.12 + pandas(`uv run --no-sync`);pytest;workflow = JS 脚本(`.claude/workflows/*.js`);agent def = markdown(`.claude/agents/*.md`)。

## Global Constraints

- 一切命令在**仓库根目录**用 `uv run --no-sync` 跑(不误删 venv-only 的 akshare/tushare/lightgbm)。
- **不放松买入门**:`assemble.py:558` 的 `rating in ("Buy","Overweight")` 一字不动;本批不改任何评级判据、不改早停触发条件(只记账)。
- **确定性层零 LLM**:本批新增代码全是 pandas/正则/文件 IO,不得引入 LLM 调用。
- **B 级降级必留痕**:新增读盘一律 presence-gated,缺文件返回空串/空 dict,**但不得静默**——该显示「缺」的地方显式写「缺」。
- **agent def / playbook 改动下 session 才生效**(会话启动装载)。批 1 完成后的首次验收扫描必须在**新 session** 跑。
- **编辑 SKILL.md / lite-playbook.md 前必须先 Read 当前内容**(这些文档会被外部改动,凭记忆编辑会覆盖别人的修改)。
- **测试必须能变红**:每个新守卫写完后自查一句「把被守的内容删掉/改坏,这条测试会红吗」;不会红的断言就是没有的断言。
- 跑测试**不要管道接 tail/head**(会吞掉 pytest 退出码);要看少量输出用 `-q`。
- 提交信息用中文 + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` 结尾。

**设计依据**:`docs/specs/2026-07-25-scan-wave5-live-mainruler-macro-metering-design.md`(章 ①、②C、②D、④B)。

---

### Task 1: render CLI(四 view)+ `_gate_histogram` 提公共

**Files:**
- Create: `autoresearch/scan/render.py`
- Modify: `autoresearch/scan/assemble.py:355`(`_gate_histogram` → 公共 `gate_histogram` + 别名)
- Test: `tests/scan/test_render.py`

**Interfaces:**
- Consumes: `autoresearch.scan.menu.menu_health(scan_dir) -> str`;`autoresearch.scan.assemble.gate_histogram(scan_dir, rows) -> str`;`autoresearch.scan.health.final_ratings(scan_dir) -> dict[str,str]`;`autoresearch.scan.stage_timing.ensure_stage_timing(det) -> dict`;`autoresearch.scan.assemble._funnel_rows(meta, n_l2, n_l3, n_cards, n_pinned) -> list[str]`
- Produces: `autoresearch.scan.render.render_view(date, view, root=None) -> str`;CLI `python -m autoresearch.scan.render <date> --view menu_health|gate_hist|timing|funnel`

- [ ] **Step 1: 写失败测试**

创建 `tests/scan/test_render.py`:

```python
"""render CLI:把 L5 才渲染的确定性表提前到跑动中随时可调(Wave5 ①)。"""
from __future__ import annotations

import json

import pytest

from autoresearch.scan import render


def _mk(tmp_path, date="2026-07-25"):
    d = tmp_path / date
    (d / "details").mkdir(parents=True)
    return d


def test_menu_health_view(tmp_path):
    d = _mk(tmp_path)
    (d / "L2_gbdt_top200.csv").write_text(
        "code,industry,pct_60d,main_pos,cmf_20,pe\n"
        "000001,银行,5.0,1,0.1,6.0\n000002,地产,-30.0,0,-0.1,8.0\n", encoding="utf-8")
    (d / "L1_scored_full.csv").write_text(
        "code,industry,pct_60d,main_pos,cmf_20,pe\n"
        "000001,银行,5.0,1,0.1,6.0\n000002,地产,-30.0,0,-0.1,8.0\n"
        "000003,银行,-25.0,0,-0.2,9.0\n", encoding="utf-8")
    out = render.render_view("2026-07-25", "menu_health", root=tmp_path)
    assert "L2 菜单体检" in out
    assert "落刀面" in out


def test_gate_hist_view_counts_cards(tmp_path):
    d = _mk(tmp_path)
    (d / "finalists.csv").write_text("code,name,lane\n000651,格力电器,composite\n", encoding="utf-8")
    (d / "details" / "000651.md").write_text(
        "# 决策卡 — 000651\n"
        "**Rubric建议**: 6 维净分 +1/6 ｜ OW三门 主力真在 ✓·业绩真兑现 ✗·估值不透支 ✓ → **建议 Hold**\n"
        "**Rating**: Hold\n", encoding="utf-8")
    out = render.render_view("2026-07-25", "gate_hist", root=tmp_path)
    assert "OW三门失守分布" in out
    assert "业绩真兑现✗ 1" in out
    assert "评级分布" in out and "Hold 1" in out


def test_timing_view_reads_stage_timing(tmp_path):
    d = _mk(tmp_path)
    (d / "_stage_timing.json").write_text(
        json.dumps({"L0L1L2": {"wall_s": 505}, "L3精排": {"wall_s": 1077}}), encoding="utf-8")
    out = render.render_view("2026-07-25", "timing", root=tmp_path)
    assert "L3精排" in out
    assert "17m57s" in out          # 1077s = 17m57s


def test_missing_artifacts_say_so_not_silent(tmp_path):
    """B 级降级必留痕:产物缺失时显式说「缺」,不返回空串装作没事。"""
    _mk(tmp_path)
    out = render.render_view("2026-07-25", "menu_health", root=tmp_path)
    assert "缺" in out


def test_unknown_view_raises(tmp_path):
    _mk(tmp_path)
    with pytest.raises(ValueError):
        render.render_view("2026-07-25", "nope", root=tmp_path)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoresearch.scan.render'`

- [ ] **Step 3: `_gate_histogram` 提为公共名**

在 `autoresearch/scan/assemble.py` 把 `def _gate_histogram(scan_dir: Path, rows: list[dict]) -> str:` 改名为 `def gate_histogram(scan_dir: Path, rows: list[dict]) -> str:`,并在该函数体结束后紧接一行别名(保持既有调用点与测试不破):

```python
_gate_histogram = gate_histogram   # 向后兼容别名(Wave5 ①:render CLI 复用公共名)
```

确认内部调用点仍可用(别名指向同一对象,无需改调用处)。

- [ ] **Step 4: 写 render.py**

创建 `autoresearch/scan/render.py`:

```python
#!/usr/bin/env python3
"""scan 过程直播渲染器(确定性,零 LLM)——把 L5 才渲染的表提前到跑动中随时可调。

design: docs/specs/2026-07-25-scan-wave5-live-mainruler-macro-metering-design.md §①

为什么单独一个模块:菜单体检 / 门直方图 / 耗时表 / 漏斗表全都是现成的确定性渲染器,
但此前只在 assemble(L5)里被调用一次 —— 用户在 L2 完成后想知道"今天菜单什么成色"、
在 L4 完成后想知道"门柱什么形状",都得等一小时后的 summary.md。本模块零新计算,
只是把同样的渲染器接到一个可随时调用的 CLI 上。

  uv run --no-sync python -m autoresearch.scan.render 2026-07-25 --view menu_health
  uv run --no-sync python -m autoresearch.scan.render 2026-07-25 --view gate_hist
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

VIEWS = ("menu_health", "gate_hist", "timing", "funnel")


def _scan_dir(date: str, root: Path | str | None = None) -> Path:
    return Path(root or "context/scan") / date


def _fmt_wall(v) -> str:
    if isinstance(v, dict):
        v = v.get("wall_s")
    if not v:
        return "—"
    v = int(v)
    return f"{v // 60}m{v % 60:02d}s" if v >= 60 else f"{v}s"


def _view_menu_health(det: Path) -> str:
    from autoresearch.scan.menu import menu_health
    out = menu_health(det)
    return out or "(菜单体检:staging 缺 L2_gbdt_top200.csv 或 L1_scored_full.csv —— 未跑或跑挂)"


def _view_gate_hist(det: Path) -> str:
    from autoresearch.scan.assemble import gate_histogram
    from autoresearch.scan.health import final_ratings
    ratings = final_ratings(det)
    if not ratings:
        return "(门直方图:details/ 无决策卡 —— L4 未跑或全失败)"
    order = ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
    cnt = {k: sum(1 for r in ratings.values() if r == k) for k in order}
    dist = " · ".join(f"{k} {cnt[k]}" for k in order if cnt[k])
    lines = [f"**评级分布**({len(ratings)} 卡):{dist or '—'}"]
    hist = gate_histogram(det, [{"code": c} for c in ratings])
    lines.append(hist or "(OW三门:无可解析卡 —— 多为早停卡,早停卡按定义不写三门段)")
    return "\n".join(lines)


def _view_timing(det: Path) -> str:
    from autoresearch.scan.stage_timing import ensure_stage_timing
    tmap = ensure_stage_timing(det)
    if not tmap:
        return "(耗时表:_stage_timing.json 缺且无可推导锚 —— 本次尚无产物落盘)"
    lines = ["| 阶段 | 墙钟 |", "|---|---:|"]
    lines += [f"| {k} | {_fmt_wall(v)} |" for k, v in tmap.items()]
    return "\n".join(lines)


def _view_funnel(det: Path) -> str:
    import pandas as pd

    from autoresearch.scan.assemble import _funnel_rows

    def _n(name: str) -> int:
        p = det / name
        if not p.is_file():
            return 0
        try:
            return int(len(pd.read_csv(p)))
        except Exception:  # noqa: BLE001 — 半截文件不该炸掉直播
            return 0

    meta: dict = {}
    mp = det / "meta.json"
    if mp.is_file():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
    n_cards = len(list((det / "details").glob("*.md"))) if (det / "details").is_dir() else 0
    if not meta and not _n("L2_gbdt_top200.csv"):
        return "(漏斗表:meta.json 与 L2 staging 均缺 —— 前奏未跑)"
    return "\n".join(_funnel_rows(meta, _n("L2_gbdt_top200.csv"), _n("finalists.csv"), n_cards))


def render_view(date: str, view: str, root: Path | str | None = None) -> str:
    """按 view 名渲染一块 markdown;产物缺失 → 显式说明缺什么(不静默返回空串)。"""
    if view not in VIEWS:
        raise ValueError(f"未知 view「{view}」,可选:{'/'.join(VIEWS)}")
    det = _scan_dir(date, root)
    fn = {"menu_health": _view_menu_health, "gate_hist": _view_gate_hist,
          "timing": _view_timing, "funnel": _view_funnel}[view]
    return fn(det)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scan 过程直播渲染器(零 LLM)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--view", required=True, choices=VIEWS)
    ap.add_argument("--root", default=None, help="scan staging 根(默认 context/scan)")
    a = ap.parse_args(argv)
    print(render_view(a.date, a.view, root=a.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_render.py -q`
Expected: PASS(5 passed)

- [ ] **Step 6: 变异探针自查(必做)**

把 `render.py` 的 `_view_gate_hist` 里 `lines.append(hist or ...)` 整行删掉,重跑测试:
Run: `uv run --no-sync pytest tests/scan/test_render.py -q`
Expected: FAIL(`test_gate_hist_view_counts_cards` 红)。**确认会红后把删掉的行改回来**,再跑一次确认 PASS。若删掉后仍全绿 → 说明断言没咬住,补断言再来。

- [ ] **Step 7: 真产物冒烟**

Run: `uv run --no-sync python -m autoresearch.scan.render 2026-07-21 --view gate_hist`
Expected: 打印评级分布 + OW三门行(2026-07-21 有真实 staging);无异常退出。
再跑 `--view menu_health`、`--view timing`、`--view funnel` 各一次,均应打印内容或明确的「缺」说明。

- [ ] **Step 8: 提交**

```bash
git add autoresearch/scan/render.py autoresearch/scan/assemble.py tests/scan/test_render.py
git commit -m "feat(scan): render CLI 四 view(菜单体检/门直方图/耗时/漏斗)——确定性表提前到跑动中可调

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: prelude 汇总屏落盘 `_prelude_summary.md` + prewarm 状态行

**Files:**
- Modify: `autoresearch/scan/prelude.py:294-317`(汇总屏打印块 → 提函数 + 双写)
- Test: `tests/scan/test_prelude_summary.py`

**Interfaces:**
- Consumes: `_run_steps` 的返回 `[{"step","ok","note"}]`;`calib_suggestion_lines()`;`_retro_input_nag()`
- Produces: `autoresearch.scan.prelude.render_summary(date, results, scan_root=None) -> str`;副产物文件 `context/scan/<date>/_prelude_summary.md`;`autoresearch.scan.prelude.prewarm_line(date, scan_root=None) -> str`

**背景**:`scan-market.js:28` 的 bash-agent 只回报「stdout 末 15 行」,而汇总屏有 12 步 ✓/✗ + 建议行 + 下一步,结构性被截断。落盘后 workflow 改为指路该文件,主会话 Read 全量转播。

- [ ] **Step 1: 写失败测试**

创建 `tests/scan/test_prelude_summary.py`:

```python
"""prelude 汇总屏双写:12 步 ✓/✗ 屏必须完整落盘(scan-market.js 末15行截断的解药)。"""
from __future__ import annotations

from autoresearch.scan import prelude


def _results():
    return [{"step": "universe", "ok": True, "note": "L0 4100 → L1 1000 → L2 200"},
            {"step": "menu", "ok": False, "note": "RuntimeError: staging 缺"}]


def test_render_summary_has_every_step_with_mark():
    out = prelude.render_summary("2026-07-25", _results())
    assert "✓ universe" in out
    assert "✗ menu" in out
    assert "L0 4100 → L1 1000 → L2 200" in out
    assert "prelude 汇总" in out


def test_render_summary_includes_prewarm_state(tmp_path):
    (tmp_path / "2026-07-25").mkdir(parents=True)
    out = prelude.render_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert "预热" in out
    assert "✗" in out                      # 无 _prewarm.json → 明说没跑


def test_prewarm_line_detects_artifact(tmp_path):
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    assert "✗" in prelude.prewarm_line("2026-07-25", scan_root=tmp_path)
    (d / "_prewarm.json").write_text('{"started_at": 1, "ended_at": 2}', encoding="utf-8")
    assert "✓" in prelude.prewarm_line("2026-07-25", scan_root=tmp_path)


def test_write_summary_file(tmp_path):
    (tmp_path / "2026-07-25").mkdir(parents=True)
    p = prelude.write_summary("2026-07-25", _results(), scan_root=tmp_path)
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "✓ universe" in text and "✗ menu" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_prelude_summary.py -q`
Expected: FAIL — `AttributeError: module 'autoresearch.scan.prelude' has no attribute 'render_summary'`

- [ ] **Step 3: 实现 render_summary / prewarm_line / write_summary**

在 `autoresearch/scan/prelude.py` 的 `run_prelude` **之前**插入三个函数(放在 `dossier_reconcile_nag` 之后即可):

```python
def prewarm_line(date: str, scan_root: Path | str | None = None) -> str:
    """夜间预热是否真跑过(Wave5 ④B:写了没装的优化必须当天可见,不靠事后考古)。"""
    import datetime as _dt
    p = Path(scan_root or "context/scan") / date / "_prewarm.json"
    if not p.is_file():
        return ("预热(夜间):✗ 未跑 —— L0/L1/L2 本次全额取数(~8-10m)。"
                "装载检查:`launchctl list | grep scan-prewarm`")
    ts = _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
    return f"预热(夜间):✓ 已跑({ts})—— universe/evidence 应全湖命中"


def render_summary(date: str, results: list[dict], scan_root: Path | str | None = None) -> str:
    """prelude 汇总屏文本(纯函数,可单测 + 可落盘)。

    此前这段是 run_prelude 里的一串 print —— 结果被 scan-market.js「只回报 stdout 末 15 行」
    结构性截断(12 步 ✓/✗ + 建议行 + 下一步 > 15 行)。提成纯函数后既能照常打印,也能落盘
    供 workflow 指路、主会话全量转播。
    """
    out = ["═" * 30 + f" prelude 汇总 · {date} " + "═" * 30]
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        out.append(f"  {mark} {r['step']}: {r['note']}")
    out.append(f"  {prewarm_line(date, scan_root)}")
    pend = next((r["note"] for r in results
                 if r["step"] == "retro_pending" and "待诊断" in r["note"]), None)
    if pend:
        out.append(f"  ⚠️  {pend}")
    try:
        stalled = _retro_input_nag()
        if stalled:
            out.append(f"  ⚠️  {stalled}")
    except Exception as e:  # noqa: BLE001 — nag 可选,缺了不挡前奏
        print(f"[prelude] ✗ retro_input_nag: {e}", file=sys.stderr)
    try:
        clines = calib_suggestion_lines()
        if clines:
            out.append("  当日件建议行(📐 贴 _l4_shared_instructions.md;🔁 贴 L3 校准块旁;"
                       "🚪 贴 skeptic/PM 先验;**含「禁注」的行勿贴**):")
            out += [f"    {ln}" for ln in clines]
    except Exception as e:  # noqa: BLE001 — 建议行可选,缺了不挡前奏
        print(f"[prelude] ✗ calib_lines: {e}", file=sys.stderr)
    out.append("  下一步(LLM 段):哨兵档 → 直接 assemble(日历已跑);"
               "全扫 → 策略师 → L3 → L4(见 SKILL 流程)")
    return "\n".join(out)


def write_summary(date: str, results: list[dict],
                  scan_root: Path | str | None = None) -> Path:
    """汇总屏落 `context/scan/<date>/_prelude_summary.md`,返回路径(目录缺则建)。"""
    det = Path(scan_root or "context/scan") / date
    det.mkdir(parents=True, exist_ok=True)
    p = det / "_prelude_summary.md"
    p.write_text(render_summary(date, results, scan_root=scan_root) + "\n", encoding="utf-8")
    return p
```

- [ ] **Step 4: 把 run_prelude 的打印块换成调用**

把 `autoresearch/scan/prelude.py` 中从 `print("\n" + "═" * 30 + f" prelude 汇总 · {date} " + "═" * 30)` 开始、到 `print("  下一步(LLM 段):…")` 为止的整段(现 :294-316),替换为:

```python
    print("\n" + render_summary(date, results))
    import contextlib
    with contextlib.suppress(Exception):      # 落盘可选,写不了不挡前奏(stdout 仍有全文)
        print(f"  (汇总屏已落盘:{write_summary(date, results)})")
    return results
```

注意:`return results` 是原函数末行,替换后只保留一份。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_prelude_summary.py -q`
Expected: PASS(5 passed)

- [ ] **Step 6: 变异探针自查**

把 `render_summary` 里 `out.append(f"  {prewarm_line(date, scan_root)}")` 删掉,重跑:
Expected: FAIL(`test_render_summary_includes_prewarm_state` 红)。改回后重跑 PASS。

- [ ] **Step 7: 真跑冒烟(不落生产日,用旧日期幂等重跑汇总)**

Run: `uv run --no-sync python -c "from autoresearch.scan import prelude; print(prelude.render_summary('2026-07-21', [{'step':'universe','ok':True,'note':'冒烟'}]))"`
Expected: 打印带 ✓ universe + 预热状态行 + 下一步行的完整屏。

- [ ] **Step 8: 提交**

```bash
git add autoresearch/scan/prelude.py tests/scan/test_prelude_summary.py
git commit -m "feat(scan): prelude 汇总屏提纯函数并落盘 _prelude_summary.md(解 workflow 末15行截断)+ 预热状态行

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 安装 launchd prewarm(ops,无代码)

**Files:**
- Read: `scripts/com.tradingagents.scan-prewarm.plist`、`scripts/prewarm.sh`
- Create(系统侧): `~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist`

**背景**:`launchctl list | grep scan-prewarm` 当前无输出,`_prewarm.json` 全历史仅 07-10/07-13 两次 —— 优化写了但从没在跑,每次扫描白付 8–10min 取数。SKILL.md:57 记着安装命令,**先跑通再改文档**(操作建议未验证就写进文档是本仓踩过的坑)。

- [ ] **Step 1: 读 plist 确认占位与调度时刻**

Run: `cat scripts/com.tradingagents.scan-prewarm.plist`
确认:`__REPO__` 占位在场;`StartCalendarInterval` 为 19:30;`ProgramArguments` 指向 `scripts/prewarm.sh`。

- [ ] **Step 2: 确认 prewarm 脚本可执行且能跑**

Run: `ls -l scripts/prewarm.sh && uv run --no-sync python -m autoresearch.scan.prewarm --help`
Expected: 脚本存在;CLI 打印用法(若 `--help` 不支持则跳过第二段,只确认模块可导入:`uv run --no-sync python -c "import autoresearch.scan.prewarm"`)。

- [ ] **Step 3: 安装并装载**

```bash
sed "s|__REPO__|$PWD|" scripts/com.tradingagents.scan-prewarm.plist > ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist
```

若 bootstrap 报 `Bootstrap failed: 5: Input/output error`(已装载过),先 `launchctl bootout gui/$(id -u)/com.tradingagents.scan-prewarm` 再重跑 bootstrap。

- [ ] **Step 4: 验证装载**

Run: `launchctl list | grep scan-prewarm`
Expected: 一行输出,含 label `com.tradingagents.scan-prewarm`(第一列 PID 或 `-`,第二列退出码应为 0 或 `-`)。
**若无输出 = 没装上**,不要跳过:检查 plist 里 `__REPO__` 是否替换成功(`grep __REPO__ ~/Library/LaunchAgents/com.tradingagents.scan-prewarm.plist` 应无输出)。

- [ ] **Step 5: 手动触发一次验证端到端**

```bash
launchctl kickstart -p gui/$(id -u)/com.tradingagents.scan-prewarm
```
等待 2–3 分钟后确认落盘:`ls -l context/scan/*/_prewarm.json | tail -3`
Expected: 出现今日或最近交易日的 `_prewarm.json`。若 kickstart 在非交易日不产出,记录实际观察到的行为(不要假设成功)。

- [ ] **Step 6: 把实测结果写回 SKILL.md**

Read `.claude/skills/scan-market/SKILL.md`(先读!),在 0. 节「夜间预热」条目末尾追加一句实测状态,例如:
`已装载并实测(2026-07-25:launchctl list 可见 / kickstart 落 _prewarm.json ✓);prelude 汇总屏首行会显示当日预热 ✓/✗。`
**只写你在 Step 4/5 真实看到的结果**;若 kickstart 未产出就如实写「装载 ✓,首次自动触发待 07-28 19:30 验证」。

- [ ] **Step 7: 提交**

```bash
git add .claude/skills/scan-market/SKILL.md
git commit -m "ops(scan): 安装 launchd 夜间预热并实测装载(此前写了没装,每跑白付 8-10min 取数)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `_l4_shared_instructions.md` 确定性生产者

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(新增 `write_shared_instructions`;`write_dispatch_pack` 内 t1 块拼接改由生产者负责;CLI 加 `shared` 子命令)
- Modify: `.claude/workflows/scan-market.js:146-153`(l4-prep 链首加 `shared` 一步)
- Test: `tests/scan/test_l4_shared_instructions.py`

**Interfaces:**
- Consumes: `autoresearch.scan.prelude.calib_suggestion_lines() -> list[str]`;`autoresearch.learning.t1_review.render_t1_calibration_block(stage="L4") -> str`
- Produces: `autoresearch.scan.agents.l4_card.write_shared_instructions(scan_dir) -> int`(写入字节数);产物 `context/scan/<date>/_l4_shared_instructions.md`;CLI `python -m autoresearch.scan.agents.l4_card shared <date>`

**背景(实测)**:`grep -rn "_l4_shared_instructions"` 全仓只有**读者**(`l4_card.py:662,727`)、**测试写者**、文档提及——**没有任何生产者**。07-17/07-21 该文件均不存在,每张卡的共享块位置只落了一句「共享指令稿缺」。后果:prelude 每天算出的 📐/🔁/🚪 当日校准行**从未到达任何一张决策卡**(STAGES.md:215 写的「落 `_l4_shared_instructions.md`(只放当日件)」是一个从来没人执行的手工步骤)。这是准确度 bug,不是 cache 优化——不要在提交信息里夸大成 cache 收益。

**纪律**:prelude 的建议行里**含「禁注」字样的行禁止贴**(样本不足的行贴进 prompt = 用坏先验污染判断)。生产者必须过滤。

- [ ] **Step 1: 写失败测试**

创建 `tests/scan/test_l4_shared_instructions.py`:

```python
"""共享指令稿生产者:此前全仓无生产者,当日校准行从未到达任何一张卡(Wave5 ④B)。"""
from __future__ import annotations

from autoresearch.scan.agents import l4_card


def test_writes_file_with_calib_lines(tmp_path, monkeypatch):
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    monkeypatch.setattr("autoresearch.scan.prelude.calib_suggestion_lines",
                        lambda *a, **k: ["📐 目标价校准:触达率 44%——目标幅>+4% 需超额理由"])
    monkeypatch.setattr("autoresearch.learning.t1_review.render_t1_calibration_block",
                        lambda *a, **k: "🔁 T+1 校准:近 10 日方向票 4 准 2 不准")
    n = l4_card.write_shared_instructions(d)
    assert n > 0
    text = (d / "_l4_shared_instructions.md").read_text(encoding="utf-8")
    assert "目标价校准" in text
    assert "T+1 校准" in text


def test_banned_lines_are_filtered(tmp_path, monkeypatch):
    """含「禁注」的行是样本不足的自我标注,贴进 prompt = 用坏先验污染判断。"""
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    monkeypatch.setattr("autoresearch.scan.prelude.calib_suggestion_lines",
                        lambda *a, **k: ["📐 好行:触达率 44%",
                                         "🚪 门柱:n=3 样本不足,禁注 skeptic 先验"])
    monkeypatch.setattr("autoresearch.learning.t1_review.render_t1_calibration_block",
                        lambda *a, **k: "")
    l4_card.write_shared_instructions(d)
    text = (d / "_l4_shared_instructions.md").read_text(encoding="utf-8")
    assert "好行" in text
    assert "禁注" not in text


def test_empty_sources_still_write_stable_header(tmp_path, monkeypatch):
    """无校准行也要落一份稳定标头:文件在场 = 逐卡共享块 byte-identical(缺文件才是 cache 断裂)。"""
    d = tmp_path / "2026-07-25"
    d.mkdir(parents=True)
    monkeypatch.setattr("autoresearch.scan.prelude.calib_suggestion_lines", lambda *a, **k: [])
    monkeypatch.setattr("autoresearch.learning.t1_review.render_t1_calibration_block",
                        lambda *a, **k: "")
    n = l4_card.write_shared_instructions(d)
    assert n > 0
    assert "当日共享块" in (d / "_l4_shared_instructions.md").read_text(encoding="utf-8")


def test_prompts_pick_up_shared_block(tmp_path, monkeypatch):
    """端到端:生产者写的内容必须出现在逐卡 prompt 里(生产者接线了消费者没接=本仓 FN-1 家族)。"""
    d = tmp_path / "2026-07-25"
    (d / "details").mkdir(parents=True)
    (d / "finalists.csv").write_text("code,name,conviction,lane\n000651,格力电器,70,composite\n",
                                     encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.prelude.calib_suggestion_lines",
                        lambda *a, **k: ["📐 独有标记 ZZZ9"])
    monkeypatch.setattr("autoresearch.learning.t1_review.render_t1_calibration_block",
                        lambda *a, **k: "")
    l4_card.write_shared_instructions(d)
    l4_card.write_dispatch_pack(d)
    prompt = (d / "_l4_prompt_000651.md").read_text(encoding="utf-8")
    assert "ZZZ9" in prompt
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_l4_shared_instructions.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'write_shared_instructions'`

- [ ] **Step 3: 实现生产者**

在 `autoresearch/scan/agents/l4_card.py` 里 `write_dispatch_pack` 定义**之前**加:

```python
def write_shared_instructions(scan_dir: Path | str) -> int:
    """落 `_l4_shared_instructions.md`(当日共享块,逐卡 byte-identical)。返回写入字节数。

    Wave5 ④B:该文件此前**全仓无生产者**(只有读者 + 测试写者),07-17/07-21 实测均不存在
    —— prelude 每天算出的 📐/🔁/🚪 当日校准行从未到达任何一张决策卡。这里把 STAGES.md:215
    描述的手工步骤变成确定性生产。

    纪律:prelude 建议行里**含「禁注」的行不贴**(样本不足的自我标注,贴进 prompt = 用坏
    先验污染判断);两个来源任一异常都不阻断(写出只含标头的稳定文件,好过没有文件)。
    """
    import contextlib

    scan_dir = Path(scan_dir)
    scan_dir.mkdir(parents=True, exist_ok=True)
    lines = ["## 当日共享块(全卡一致;确定性生成,勿逐卡改写)"]
    calib: list[str] = []
    with contextlib.suppress(Exception):
        from autoresearch.scan.prelude import calib_suggestion_lines
        calib = [ln for ln in calib_suggestion_lines() if "禁注" not in ln]
    if calib:
        lines += ["", "### 当日校准锚(据实调用,不作评级指令)"] + [f"- {ln}" for ln in calib]
    t1_blk = ""
    with contextlib.suppress(Exception):
        from autoresearch.learning.t1_review import render_t1_calibration_block
        t1_blk = render_t1_calibration_block(stage="L4")
    if t1_blk:
        lines += ["", t1_blk.strip()]
    text = "\n".join(lines).strip() + "\n"
    p = scan_dir / "_l4_shared_instructions.md"
    p.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))
```

- [ ] **Step 4: 去掉 write_dispatch_pack 里的重复 t1 拼接**

在 `write_dispatch_pack` 中,删除这段(现 :663-669,读 shared 文件之后的那个 `contextlib.suppress` 块):

```python
    with contextlib.suppress(Exception):   # 快环校准(L4/intel 相关观察;2026-07-17 自我迭代腿,
        from autoresearch.learning.t1_review import render_t1_calibration_block
        t1_blk = render_t1_calibration_block(stage="L4")     # 空账本=零字节 parity)
        if t1_blk:
            shared = (shared + "\n\n" + t1_blk).strip()
```

理由:t1 块现在由生产者写进文件,消费侧再拼一次会重复。**删除后共享块的唯一事实源 = 文件本身**,这正是 byte-identical 契约要的。

- [ ] **Step 5: CLI 加 `shared` 子命令**

在 `l4_card.py` 的 `main()` 里,`choices=[...]` 列表加 `"shared"`,并在分派处加一条:

```python
    if a.cmd == "shared":
        n = write_shared_instructions(base / a.date)
        print(json.dumps({"ok": True, "bytes": n}, ensure_ascii=False))
        return 0
```

(照抄同文件其他子命令的 `base`/`a.date` 取法与打印风格;若现有分派是 dict 映射则按同样风格加键。)

- [ ] **Step 6: 跑测试确认通过 + 回归 l4 相关**

Run: `uv run --no-sync pytest tests/scan/test_l4_shared_instructions.py tests/scan/test_l4_dispatch_pack.py tests/scan/test_l4_prompt_cache_prefix.py -q`
Expected: 全部 PASS。若 `test_l4_prompt_cache_prefix.py` 因删掉 t1 拼接而红,读该测试再判:它锁的是「共享块在固定标头之后、逐卡块之前」的顺序,不该受影响;真红了就是删多了,回看 Step 4。

- [ ] **Step 7: 变异探针自查**

把 `write_shared_instructions` 里 `if "禁注" not in ln` 改成 `if True`,重跑:
Expected: FAIL(`test_banned_lines_are_filtered` 红)。改回后 PASS。

- [ ] **Step 8: workflow 接线**

编辑 `.claude/workflows/scan-market.js`,把 l4-prep 的 bash 链(现 :146-153)首行 `${R} autoresearch.scan.l4_reuse ${date} --apply; ` **之前**插入:

```
`${R} autoresearch.scan.agents.l4_card shared ${date}; ` +
```

即链变为 `shared → l4_reuse → 四生产者并行 → prompts`。

- [ ] **Step 9: 真产物冒烟**

Run: `uv run --no-sync python -m autoresearch.scan.agents.l4_card shared 2026-07-21 && head -20 context/scan/2026-07-21/_l4_shared_instructions.md`
Expected: 打印 `{"ok": true, "bytes": N}`(N>0)且文件含「当日共享块」标头。

- [ ] **Step 10: 提交**

```bash
git add autoresearch/scan/agents/l4_card.py .claude/workflows/scan-market.js tests/scan/test_l4_shared_instructions.py
git commit -m "fix(scan): 补 _l4_shared_instructions.md 生产者——当日校准行此前从未到达任何决策卡(全仓零生产者)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 卡片 `**早停**` 机读契约 + `_early_stop.json`

**Files:**
- Modify: `.claude/agents/l4-card.md`(早停卡模板 A)
- Modify: `.claude/skills/stock-research/lite-playbook.md`(早停卡模板,真值源)
- Modify: `tests/test_agent_defs.py:41`(anchors 加 `"停因:"`)
- Modify: `autoresearch/scan/assemble.py`(新增 `parse_early_stop` + 落 `_early_stop.json`)
- Test: `tests/scan/test_early_stop_parse.py`

**Interfaces:**
- Produces: `autoresearch.scan.assemble.parse_early_stop(text) -> dict | None`(`{"phase": "P3", "reason": "涨停追高"}`);`autoresearch.scan.assemble.write_early_stop(scan_dir) -> dict[str, dict]`;产物 `context/scan/<date>/_early_stop.json`

**背景(实测)**:07-21 的 12 张卡里只有 2 张能被 `gate_status` 解析,6 张是早停卡——早停卡按定义压 ≤Hold 且**从不写 OW三门段**。所以「N 只 finalist 深核后无一过 ≥OW 三门」这句每天打印的判词**不被自己的数据支持**,真机制是早停,而早停零计量。本任务只记账,**不改任何早停触发条件**。

**枚举**(七档,写死在 agent def 与解析器两侧):`数据不足`、`涨停追高`、`题材透支`、`资金流出`、`估值透支`、`基本面恶化`、`其他`。

- [ ] **Step 1: 写失败测试**

创建 `tests/scan/test_early_stop_parse.py`:

```python
"""早停记账:0买真机制是早停(07-21 实测 12 卡中 6 张早停、仅 2 张可解析三门),此前零计量。"""
from __future__ import annotations

import json

from autoresearch.scan import assemble

_EARLY = """# 决策卡 — 000651 格力电器 @ 2026-07-25  ·  〔早停·表面 DD〕
**Rubric建议**: 表面4维净分 -1/4 ｜ 早停因:资金派发无催化 → **建议 Hold**
**Rating**: Hold
**早停**: 停于 P3 ｜ 停因:资金流出
FINAL TRANSACTION PROPOSAL: **HOLD**
"""

_FULL = """# 决策卡 — 300857 协创数据 @ 2026-07-25
**Rubric建议**(评分卡派生): 6 维净分 +2/6 ｜ OW三门 主力真在 ✓·业绩真兑现 ✓·估值不透支 ✓ → **建议 Overweight**
**Rating**: Overweight
FINAL TRANSACTION PROPOSAL: **BUY**
"""


def test_parse_early_stop_reads_phase_and_reason():
    got = assemble.parse_early_stop(_EARLY)
    assert got == {"phase": "P3", "reason": "资金流出"}


def test_full_card_has_no_early_stop():
    assert assemble.parse_early_stop(_FULL) is None


def test_unknown_reason_falls_back_to_other():
    text = _EARLY.replace("停因:资金流出", "停因:老板长得不行")
    assert assemble.parse_early_stop(text) == {"phase": "P3", "reason": "其他"}


def test_write_early_stop_json(tmp_path):
    d = tmp_path / "2026-07-25"
    (d / "details").mkdir(parents=True)
    (d / "details" / "000651.md").write_text(_EARLY, encoding="utf-8")
    (d / "details" / "300857.md").write_text(_FULL, encoding="utf-8")
    got = assemble.write_early_stop(d)
    assert got == {"000651": {"phase": "P3", "reason": "资金流出"}}
    on_disk = json.loads((d / "_early_stop.json").read_text(encoding="utf-8"))
    assert on_disk == got
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_early_stop_parse.py -q`
Expected: FAIL — `AttributeError: module 'autoresearch.scan.assemble' has no attribute 'parse_early_stop'`

- [ ] **Step 3: 实现解析器与落盘**

在 `autoresearch/scan/assemble.py` 的 `gate_histogram` 定义**之前**加:

```python
_EARLYSTOP_RE = re.compile(r"\*\*早停\*\*[:：]\s*停于\s*(P[0-9])\s*[｜|]\s*停因[:：]\s*([^\s｜|]+)")
_STOP_REASONS = ("数据不足", "涨停追高", "题材透支", "资金流出",
                 "估值透支", "基本面恶化", "其他")


def parse_early_stop(text: str) -> dict | None:
    """决策卡的机读早停行 → {"phase","reason"};满卡无此行 → None。

    Wave5 ②C:0 买的真机制是早停(07-21 实测 12 卡 6 张早停),而早停卡按定义不写 OW三门段
    —— 门直方图看不见它们,账本也从来没数过。枚举外的自由文本归入「其他」(不丢样本、
    也不让写卡人用自造词绕开分桶)。
    """
    m = _EARLYSTOP_RE.search(text or "")
    if not m:
        return None
    reason = m.group(2).strip()
    return {"phase": m.group(1), "reason": reason if reason in _STOP_REASONS else "其他"}


def write_early_stop(scan_dir: Path | str) -> dict[str, dict]:
    """逐卡解析早停行 → `_early_stop.json`({code: {phase,reason}});无早停卡则写空对象。"""
    scan_dir = Path(scan_dir)
    out: dict[str, dict] = {}
    base = scan_dir / "details"
    if base.is_dir():
        for p in sorted(base.glob("*.md")):
            got = parse_early_stop(p.read_text(encoding="utf-8"))
            if got:
                code = p.stem
                out[code.zfill(6) if code.isdigit() else code] = got
    (scan_dir / "_early_stop.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out
```

- [ ] **Step 4: 在 assemble 主流程调用**

在 `autoresearch/scan/assemble.py` 里,紧挨着现有写 `_final_ratings.json` 的位置(现 :180-183 那段 `out = {...}` / `(Path(scan_dir) / "_final_ratings.json").write_text(...)`)之后加一行:

```python
        write_early_stop(scan_dir)      # Wave5 ②C:早停分桶落盘(0买真机制记账)
```

若该处 `scan_dir` 变量名不同,按上下文取同一个 scan 目录变量。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_early_stop_parse.py -q`
Expected: PASS(4 passed)

- [ ] **Step 6: 改早停卡模板(两侧同步)**

先 `Read .claude/skills/stock-research/lite-playbook.md`,在早停卡模板里 `**Rating**: <Hold|Underweight|Sell> ← 必须 = Rubric建议` 的**下一行**插入:

```
**早停**: 停于 P<1|3> ｜ 停因:<数据不足|涨停追高|题材透支|资金流出|估值透支|基本面恶化|其他>   ← 机读契约(勿改词表);满卡不写此行
```

再 `Read .claude/agents/l4-card.md`,在模板 A 的 `**Rating**: <Hold|Underweight|Sell>` 下一行插入**同一行文本**(两侧必须逐字一致,`test_agent_defs` 会校验)。

同时在 l4-card.md 的「铁律(内化)」节末尾加一条:

```
- **早停必须机读留痕**:出早停卡时 `**早停**` 行必填,停因只从词表七选一(数据不足/涨停追高/题材透支/资金流出/估值透支/基本面恶化/其他)——它进当日账本用来回答「今天为什么没买」,写自由文本等于把自己从统计里删掉。
```

- [ ] **Step 7: anchors 加锚**

编辑 `tests/test_agent_defs.py:41-47` 的 `anchors` 列表,在 `"早停卡短格式"` 之后加 `"停因:"`:

```python
               "早停卡短格式", "停因:", "卡契约 v3·超短 1~2 日", "超短口径",
```

- [ ] **Step 8: 跑契约测试**

Run: `uv run --no-sync pytest tests/test_agent_defs.py -q`
Expected: PASS。若红且提示「lite-playbook 缺契约锚」→ Step 6 两侧文本没对齐,逐字比对。

- [ ] **Step 9: 变异探针自查**

把 `.claude/agents/l4-card.md` 里刚加的 `**早停**` 行删掉,重跑 `uv run --no-sync pytest tests/test_agent_defs.py -q`:
Expected: FAIL。改回后 PASS。

- [ ] **Step 10: 提交**

```bash
git add .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md tests/test_agent_defs.py autoresearch/scan/assemble.py tests/scan/test_early_stop_parse.py
git commit -m "feat(scan): 卡片加 **早停** 机读契约 + _early_stop.json 落盘(0买真机制此前零计量)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: `earlystop_ledger` 账本 + 接入 prelude

**Files:**
- Create: `autoresearch/learning/earlystop_ledger.py`
- Modify: `autoresearch/scan/prelude.py`(`_ledgers` 步骤加入新模块)
- Test: `tests/learning/test_earlystop_ledger.py`

**Interfaces:**
- Consumes: `context/scan/<date>/_early_stop.json`(Task 5 产物);`context/scan/<date>/retro/attribution.csv` 的 `fwd_2_oc` 列
- Produces: `autoresearch.learning.earlystop_ledger.roll(scan_root=None) -> pd.DataFrame`;`render(df) -> list[str]`;`main() -> int`;产物 `reports/learning/earlystop_ledger.md`

**用途**:≥10 交易日后,用「强势票停因桶(涨停追高/题材透支)的 fwd_2_oc 均值与 t 值」裁决 playbook 是否该改——**本任务只攒数据,不改规则**。

- [ ] **Step 1: 写失败测试**

创建 `tests/learning/test_earlystop_ledger.py`:

```python
"""早停账本:按停因桶累计 fwd_2_oc,为「强势票早停是不是误杀」攒裁决样本(Wave5 ②C)。"""
from __future__ import annotations

import json

from autoresearch.learning import earlystop_ledger as el


def _day(root, date, stops: dict, attribution: str):
    d = root / date
    (d / "retro").mkdir(parents=True)
    (d / "_early_stop.json").write_text(json.dumps(stops, ensure_ascii=False), encoding="utf-8")
    (d / "retro" / "attribution.csv").write_text(attribution, encoding="utf-8")


def test_roll_joins_stops_to_forward_returns(tmp_path):
    _day(tmp_path, "2026-07-21",
         {"000651": {"phase": "P3", "reason": "涨停追高"},
          "300857": {"phase": "P3", "reason": "资金流出"}},
         "code,fwd_2_oc\n000651,0.05\n300857,-0.02\n")
    df = el.roll(scan_root=tmp_path)
    assert len(df) == 2
    row = df[df["code"] == "000651"].iloc[0]
    assert row["reason"] == "涨停追高"
    assert abs(float(row["fwd_2_oc"]) - 0.05) < 1e-9


def test_render_buckets_by_reason(tmp_path):
    _day(tmp_path, "2026-07-21",
         {"000651": {"phase": "P3", "reason": "涨停追高"},
          "000002": {"phase": "P3", "reason": "涨停追高"}},
         "code,fwd_2_oc\n000651,0.05\n000002,0.03\n")
    md = "\n".join(el.render(el.roll(scan_root=tmp_path)))
    assert "涨停追高" in md
    assert "n=2" in md
    assert "样本不足" in md          # n<10 必须自标禁裁决


def test_empty_root_renders_placeholder(tmp_path):
    md = "\n".join(el.render(el.roll(scan_root=tmp_path)))
    assert "无早停记录" in md
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/learning/test_earlystop_ledger.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'autoresearch.learning.earlystop_ledger'`

- [ ] **Step 3: 实现账本**

创建 `autoresearch/learning/earlystop_ledger.py`:

```python
#!/usr/bin/env python3
"""早停账本 —— 回答"早停到底杀对了没有"(确定性,零 LLM)。

design: docs/specs/2026-07-25-scan-wave5-live-mainruler-macro-metering-design.md §②C

背景:0 买的主导机制是早停(07-21 实测 12 卡中 6 张早停),但早停卡按定义压 ≤Hold、
不写 OW三门段 —— 门直方图看不见,任何账本也没数过。本表把 `_early_stop.json` 的停因
桶 join 上 retro attribution 的 fwd_2_oc(超短主尺),让"强势票早停是误杀还是纪律"
在 ≥10 日后可裁决。**本表只攒数据,不改任何早停规则。**

  uv run --no-sync python -m autoresearch.learning.earlystop_ledger  # → reports/learning/earlystop_ledger.md
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_COLS = ["date", "code", "phase", "reason", "fwd_2_oc"]
_MIN_N = 10          # 停因桶 n<10 一律自标"样本不足",禁止据此改规则


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    """聚合 context/scan/*/_early_stop.json × retro/attribution.csv → 逐票早停行。"""
    scan_root = Path(scan_root or "context/scan")
    rows: list[dict] = []
    for p in sorted(scan_root.glob("*/_early_stop.json")):
        try:
            stops = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 半截文件跳过,不阻断整表
            continue
        if not stops:
            continue
        date = p.parent.name
        fwd: dict[str, float] = {}
        ap = p.parent / "retro" / "attribution.csv"
        if ap.is_file():
            try:
                adf = pd.read_csv(ap, dtype={"code": str})
                if "code" in adf.columns and "fwd_2_oc" in adf.columns:
                    adf["code"] = adf["code"].astype(str).str.zfill(6)
                    fwd = dict(zip(adf["code"],
                                   pd.to_numeric(adf["fwd_2_oc"], errors="coerce"), strict=True))
            except Exception:  # noqa: BLE001
                fwd = {}
        for code, meta in stops.items():
            c = str(code).zfill(6)
            v = fwd.get(c)
            rows.append({"date": date, "code": c,
                         "phase": (meta or {}).get("phase", ""),
                         "reason": (meta or {}).get("reason", "其他"),
                         "fwd_2_oc": None if v is None or pd.isna(v) else float(v)})
    return pd.DataFrame(rows, columns=_COLS).sort_values(["date", "code"]).reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    """ledger → markdown(停因桶汇总 + 逐日计数);空 → 占位行。"""
    out = ["# 早停账本(早停杀对了没有 · 主尺 fwd_2_oc)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无早停记录(卡片 `**早停**` 行未落或尚未跑过带早停的扫描)_"]
    mature = ledger[ledger["fwd_2_oc"].notna()]
    out += [f"- 累计早停 {len(ledger)} 张(其中 fwd 已成熟 {len(mature)} 张)", "",
            "| 停因 | n | fwd_2_oc 均值 | 已成熟 n | 裁决资格 |", "|---|---:|---:|---:|---|"]
    for reason, g in ledger.groupby("reason"):
        gm = g[g["fwd_2_oc"].notna()]
        mean = f"{gm['fwd_2_oc'].mean() * 100:+.2f}%" if len(gm) else "—"
        ok = "可裁决" if len(gm) >= _MIN_N else f"样本不足(需 ≥{_MIN_N})"
        out.append(f"| {reason} | n={len(g)} | {mean} | {len(gm)} | {ok} |")
    out += ["", "_停因桶均值显著为正 = 该桶早停在误杀(据此提案改 playbook,须用户点头);"
            "为负 = 早停是纪律。n<10 的桶一律不作结论。_"]
    return out


def main() -> int:
    df = roll()
    p = Path("reports/learning/earlystop_ledger.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(render(df)) + "\n", encoding="utf-8")
    print(f"[earlystop_ledger] {len(df)} 行 → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/learning/test_earlystop_ledger.py -q`
Expected: PASS(3 passed)

- [ ] **Step 5: 接进 prelude 的 `_ledgers` 步骤**

编辑 `autoresearch/scan/prelude.py` 的 `_ledgers()`:import 列表加 `earlystop_ledger`,循环元组加进去:

```python
        from autoresearch.learning import (
            buy_ledger,
            catalyst_ledger,
            changelog_ledger,
            channel_ledger,
            cross_calib,
            earlystop_ledger,
            gate_ledger,
            journal,
            paper_nav,
            zero_buy_ledger,
        )
```

```python
        for mod in (journal, buy_ledger, cross_calib, catalyst_ledger, paper_nav,
                    channel_ledger, gate_ledger, zero_buy_ledger, changelog_ledger,
                    earlystop_ledger):
```

并把该函数返回串末尾加 ` + earlystop`:

```python
        return ("journal + buy_ledger + cross_calib + catalyst + paper_nav + "
                "channel + gate + zero_buy + changelog + earlystop 已刷新")
```

- [ ] **Step 6: 真跑冒烟**

Run: `uv run --no-sync python -m autoresearch.learning.earlystop_ledger && cat reports/learning/earlystop_ledger.md`
Expected: 打印行数 + 文件内容(现存历史卡无 `**早停**` 行,预期是「无早停记录」占位——**这是正确结果**,不要为了让它有数而回填历史)。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/learning/earlystop_ledger.py autoresearch/scan/prelude.py tests/learning/test_earlystop_ledger.py
git commit -m "feat(learning): earlystop_ledger 按停因桶累计 fwd_2_oc + 接进 prelude 日刷新

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 0买叙事纠偏(按真机制分桶)

**Files:**
- Modify: `autoresearch/scan/market.py:426-428`(`render_funnel_readout` 的 0 买判词)
- Modify: `autoresearch/scan/render.py`(`gate_hist` view 加早停分桶行)
- Test: `tests/scan/test_zero_buy_narrative.py`

**Interfaces:**
- Consumes: `context/scan/<date>/_early_stop.json`(Task 5)
- Produces: 修改后的 `render_funnel_readout`(签名不变)

**背景**:现文案「{n} 只 finalist 深核后无一过 ≥OW 三门」在 07-21 是**不实陈述**——12 张卡里 6 张是早停卡(从不写三门段)、只有 2 张能解析三门。报告不能继续讲一个自己数据不支持的故事。

- [ ] **Step 1: 写失败测试**

创建 `tests/scan/test_zero_buy_narrative.py`:

```python
"""0买判词必须按真机制分桶:07-21 的「无一过 ≥OW 三门」是不实陈述(12 卡中 6 张早停)。"""
from __future__ import annotations

import json

from autoresearch.scan.market import render_funnel_readout

_HOLD = "**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"


def _day(tmp_path, stops: dict, n_cards: int = 3):
    d = tmp_path / "2026-07-25"
    (d / "details").mkdir(parents=True)
    # regime 取自 market_pack:判词里要拼「risk_off regime 下的纪律空仓」,缺文件会走回退口径
    (d / "market_pack.json").write_text(
        json.dumps({"regime": {"label": "risk_off"}}, ensure_ascii=False), encoding="utf-8")
    codes = ["000651", "300857", "000002"][:n_cards]
    (d / "finalists.csv").write_text(
        "code,name,lane\n" + "".join(f"{c},票{c},composite\n" for c in codes), encoding="utf-8")
    for c in codes:
        (d / "details" / f"{c}.md").write_text(f"# 决策卡 — {c}\n{_HOLD}", encoding="utf-8")
    (d / "_early_stop.json").write_text(json.dumps(stops, ensure_ascii=False), encoding="utf-8")
    return d


def test_zero_buy_reports_early_stop_bucket(tmp_path):
    d = _day(tmp_path, {"000651": {"phase": "P3", "reason": "资金流出"},
                        "300857": {"phase": "P3", "reason": "涨停追高"}})
    out = render_funnel_readout(d)
    assert "0 买" in out
    assert "早停 2" in out
    assert "满卡" in out
    assert "无一过" not in out          # 旧的不实判词必须消失


def test_zero_buy_without_early_stop_file_is_honest(tmp_path):
    """无 _early_stop.json(旧日/未落)→ 明说口径未知,不得倒退回「无一过三门」。"""
    d = _day(tmp_path, {})
    (d / "_early_stop.json").unlink()
    out = render_funnel_readout(d)
    assert "0 买" in out
    assert "无一过" not in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_zero_buy_narrative.py -q`
Expected: FAIL(断言 `"早停 2" in out` 不成立;且 `"无一过" not in out` 也会红)

- [ ] **Step 3: 改判词**

编辑 `autoresearch/scan/market.py` 的 `render_funnel_readout`,把 else 分支(现 :425-428):

```python
    else:
        reg = (market_pack(scan_dir).get("regime") or {}).get("label")
        zh = _REGIME_ZH.get(reg, reg or "")
        lines.append(f"- **0 买**:{len(final)} 只 finalist 深核后无一过 ≥OW 三门 —— "
                     f"{zh} regime 下的纪律空仓观望,非漏斗故障。")
```

替换为:

```python
    else:
        reg = (market_pack(scan_dir).get("regime") or {}).get("label")
        zh = _REGIME_ZH.get(reg, reg or "")
        lines.append(f"- **0 买**:{len(final)} 只 finalist 深核后无一进买单 —— "
                     f"{zh} regime 下的纪律空仓观望,非漏斗故障。")
        lines.append(f"  - 机制拆分:{_zero_buy_mechanism(scan_dir, len(final))}")
```

并在同文件 `render_funnel_readout` **之前**加辅助函数:

```python
def _zero_buy_mechanism(scan_dir: Path, n_cards: int) -> str:
    """0 买的真机制分桶(Wave5 ②D)。

    旧判词写死「无一过 ≥OW 三门」——07-21 实测 12 卡里 6 张是早停卡(按定义压 ≤Hold 且
    从不写三门段)、仅 2 张能被 gate_status 解析。报告不能继续讲一个自己数据不支持的
    故事:这里按 `_early_stop.json` 把 0 买拆成「早停 / 满卡未达 OW」两桶并列出停因。
    """
    import json as _json
    p = Path(scan_dir) / "_early_stop.json"
    if not p.is_file():
        return "口径未知(`_early_stop.json` 未落——本日卡片早于早停记账上线,或 assemble 未跑完)"
    try:
        stops = _json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return "口径未知(`_early_stop.json` 解析失败)"
    n_early = len(stops)
    buckets: dict[str, int] = {}
    for meta in stops.values():
        r = (meta or {}).get("reason", "其他")
        buckets[r] = buckets.get(r, 0) + 1
    detail = "、".join(f"{k} {v}" for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]))
    return (f"早停 {n_early} 张({detail or '—'})· 满卡未达 OW {max(n_cards - n_early, 0)} 张"
            f"——早停卡按定义压 ≤Hold 且不写 OW三门段,不计入门柱直方图")
```

确认 `market.py` 顶部已 import `Path`(未导入则加 `from pathlib import Path`)。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_zero_buy_narrative.py -q`
Expected: PASS(2 passed)

- [ ] **Step 5: render gate_hist 同步加早停行**

编辑 `autoresearch/scan/render.py` 的 `_view_gate_hist`,在 `lines.append(hist or ...)` **之前**插入:

```python
    from autoresearch.scan.market import _zero_buy_mechanism
    lines.append(f"**停因分桶**:{_zero_buy_mechanism(det, len(ratings))}")
```

- [ ] **Step 6: 回归 + 提交**

Run: `uv run --no-sync pytest tests/scan/test_render.py tests/scan/test_zero_buy_narrative.py tests/scan/test_assemble_slim0buy.py -q`
Expected: 全 PASS。若 `test_assemble_slim0buy.py` 红且断言的是旧文案,读该测试后同步更新它的期望字符串(旧文案本身就是被纠正的对象)。

```bash
git add autoresearch/scan/market.py autoresearch/scan/render.py tests/scan/test_zero_buy_narrative.py
git commit -m "fix(scan): 0买判词按真机制分桶(早停/满卡未达OW)——「无一过≥OW三门」不被自身数据支持

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: t1_review 早停桶扩样

**Files:**
- Modify: `autoresearch/learning/t1_review.py`(`build_scorecard` 加 `early_stop` 列;`render_scorecard_md` 加早停桶段)
- Test: `tests/learning/test_t1_early_stop_bucket.py`

**Interfaces:**
- Consumes: `context/scan/<t>/_early_stop.json`
- Produces: `build_scorecard` 返回的 `scorecard` DataFrame 新增 `early_stop` 列(值 = 停因或空串)

**背景**:最近 5 次 t1 里 3 次真选**全 Hold**、verdict 全为「—」——无方向主张就无从判准不准,快环样本饥饿。早停卡不判准/不准(Hold 无方向),但它们的 cc1 分布是「早停杀对了没有」的第一手证据,必须进表。

- [ ] **Step 1: 写失败测试**

创建 `tests/learning/test_t1_early_stop_bucket.py`:

```python
"""t1 记分卡带早停桶:最近 5 次里 3 次真选全 Hold、verdict 全「—」= 快环无样本可评。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning import t1_review


def _setup(tmp_path):
    d = tmp_path / "2026-07-21"
    d.mkdir(parents=True)
    (d / "finalists.csv").write_text(
        "code,name,lane,conviction\n000651,格力电器,composite,70\n"
        "300857,协创数据,composite,65\n", encoding="utf-8")
    (d / "details").mkdir()
    for c in ("000651", "300857"):
        (d / "details" / f"{c}.md").write_text("**Rating**: Hold\n", encoding="utf-8")
    (d / "_early_stop.json").write_text(
        json.dumps({"000651": {"phase": "P3", "reason": "涨停追高"}}, ensure_ascii=False),
        encoding="utf-8")
    return d


def _prices():
    return pd.DataFrame({"code": ["000651", "300857", "000002"],
                         "industry": ["家电", "消费电子", "地产"],
                         "close_t": [40.0, 20.0, 10.0], "close_t1": [42.0, 19.0, 10.1],
                         "cc1": [0.05, -0.05, 0.01], "oc1": [0.04, -0.04, 0.01],
                         "hi_oc": [0.06, 0.01, 0.02]})


def test_scorecard_carries_early_stop_column(tmp_path):
    _setup(tmp_path)
    res = t1_review.build_scorecard("2026-07-21", scan_root=tmp_path,
                                    prices=_prices(), cal=["2026-07-21", "2026-07-22"])
    sc = res["scorecard"]
    assert "early_stop" in sc.columns
    row = sc[sc["code"] == "000651"].iloc[0]
    assert row["early_stop"] == "涨停追高"
    assert sc[sc["code"] == "300857"].iloc[0]["early_stop"] == ""


def test_render_has_early_stop_section(tmp_path):
    _setup(tmp_path)
    res = t1_review.build_scorecard("2026-07-21", scan_root=tmp_path,
                                    prices=_prices(), cal=["2026-07-21", "2026-07-22"])
    md = t1_review.render_scorecard_md(res)
    assert "早停桶" in md
    assert "涨停追高" in md
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/learning/test_t1_early_stop_bucket.py -q`
Expected: FAIL — `assert 'early_stop' in sc.columns`

- [ ] **Step 3: build_scorecard 加列**

在 `autoresearch/learning/t1_review.py` 的 `build_scorecard` 里,紧跟 `rows["rating"] = rows["code"].map(ratings).fillna("无卡")` 之后加:

```python
    # Wave5 ②C:早停卡不判准/不准(Hold 无方向主张),但它们的 cc1 分布是「早停杀对了没有」
    # 的第一手证据 —— 进表进桶,解快环"真选全 Hold 无从评"的样本饥饿。
    import json as _json
    _esp = sdir / "_early_stop.json"
    _es: dict = {}
    if _esp.is_file():
        try:
            _es = _json.loads(_esp.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — 缺/坏文件只退化为空列
            _es = {}
    rows["early_stop"] = rows["code"].map(lambda c: (_es.get(str(c).zfill(6)) or {}).get("reason", ""))
```

并把 `keep` 列表加上 `"early_stop"`:

```python
    keep = ["code", "name", "lane", "rating", "conviction", "close_t", "close_t1",
            "cc1", "oc1", "hi_oc", "excess", "excess_ind", "z", "verdict", "surprise",
            "sealed", "needs_diag", "limit", "early_stop"]
```

- [ ] **Step 4: render 加早停桶段**

在 `render_scorecard_md` 的返回前(逐票表渲染之后)加:

```python
    if "early_stop" in sc.columns and (sc["early_stop"].astype(str) != "").any():
        es = sc[sc["early_stop"].astype(str) != ""]
        lines += ["", f"**早停桶**({len(es)} 张;不判准/不准——Hold 无方向主张,只记分布):"]
        for reason, g in es.groupby("early_stop"):
            cc = pd.to_numeric(g["cc1"], errors="coerce")
            avg = "—" if not cc.notna().any() else f"{cc.mean() * 100:+.1f}%"
            lines.append(f"- {reason} ×{len(g)}:T+1 cc1 均值 {avg}"
                         f"({'、'.join(str(c) for c in g['code'])})")
```

(按该函数现有 `lines` 变量名与返回方式接;若它用 `"\n".join(lines)` 返回,插在 join 之前。)

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/learning/test_t1_early_stop_bucket.py -q`
Expected: PASS(2 passed)

- [ ] **Step 6: 变异探针 + 回归**

把 Step 3 里 `rows["early_stop"] = ...` 一行删掉重跑 → 应 FAIL;改回 → PASS。
Run: `uv run --no-sync pytest tests/learning -q`
Expected: 全 PASS。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/learning/t1_review.py tests/learning/test_t1_early_stop_bucket.py
git commit -m "feat(learning): t1 记分卡带早停桶(真选全 Hold 时快环也有样本可评)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: pinned 派发硬化(派发时刻可见)

**Files:**
- Modify: `.claude/workflows/scan-market.js`(handoff 前加 pinned 清单 log)
- Modify: `.claude/skills/scan-market/SKILL.md`(步骤 4 的 pinned 传参要求提为显式检查项)
- Test: `tests/test_agent_defs.py`(workflow 锚测试加 pinned 清单锚)

**Interfaces:**
- Consumes: `dispatch_plan` 返回的 `meta[code].pinned`(**已在生产**,`l4_card.py:784-785,795-796`)
- Produces: workflow 日志行 `📌 保送票 N 只:…—— 派发这些 l4-stock 必须传 args.pinned:true`

**背景**:07-21 实测 300857/601869 评级偏空但 `_ensemble_*.json` 缺失(漏传 `args.pinned` → SELL 双复核断链)。`meta[code].pinned` 生产侧没问题、`self_review` 探针 9 `sell_review_missing` 也在(`autoresearch/learning/self_review.py:250,464`,warn 级)——**断的是主会话派发时刻的记忆**。解药是把契约摆在派发的那一秒眼前,而不是事后 warn。

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_defs.py` 末尾加:

```python
def test_scan_market_workflow_pinned_roster_log():
    """派发前必须逐只列出 pinned 名单(07-21 漏传 args.pinned → 持仓 SELL 双复核断链)。

    探针 9 sell_review_missing 是事后 warn;真正断的是派发那一秒的记忆,所以契约必须
    出现在 handoff 日志里。删掉那行 log,本测试应变红。
    """
    js = (ROOT / ".claude" / "workflows" / "scan-market.js").read_text(encoding="utf-8")
    assert "📌 保送票" in js, "scan-market.js 缺 pinned 名单 log(派发时刻不可见)"
    assert "args.pinned" in js, "scan-market.js 的 pinned log 未点名 args.pinned 传参要求"
```

若该文件里 `ROOT` 变量名不同(如 `REPO`/`SKILLS` 的父路径),照抄同文件既有 workflow 锚测试(`test_l4_stock_workflow_sell_review_anchors`)的路径取法。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/test_agent_defs.py -q -k pinned_roster`
Expected: FAIL — `scan-market.js 缺 pinned 名单 log`

- [ ] **Step 3: workflow 加名单 log**

编辑 `.claude/workflows/scan-market.js`,在最后一行 `return { date, mode: 'l4-handoff', ... }` **之前**、紧跟现有 `log(\`L4 交接:…\`)` 之后插入:

```js
// 📌 保送票在派发那一秒必须可见:07-21 漏传 args.pinned → 300857/601869 的持仓 SELL 双复核
// 整段没跑(self_review 探针 9 sell_review_missing 只能事后 warn)。名单是给主会话看的硬提示。
const metaAll = plan.meta || g2.meta || {}
const pinnedCodes = dispatch.filter((c) => metaAll[c] && metaAll[c].pinned)
if (pinnedCodes.length) {
  log(`📌 保送票 ${pinnedCodes.length} 只:${pinnedCodes.join('/')} —— 派发这些 l4-stock 必须传 args.pinned:true(漏传=持仓 SELL 双复核断链)`)
} else {
  log('📌 保送票 0 只(本次 dispatch 无 pinned)')
}
```

- [ ] **Step 4: 语法检查(用 AsyncFunction 探针,不用 node --check)**

Run:
```bash
uv run --no-sync python - <<'PY'
import subprocess, pathlib
src = pathlib.Path('.claude/workflows/scan-market.js').read_text(encoding='utf-8')
js = "new (Object.getPrototypeOf(async function(){}).constructor)(%s)" % __import__('json').dumps(src)
r = subprocess.run(['node', '-e', js], capture_output=True, text=True)
print('OK' if r.returncode == 0 else 'SYNTAX-ERR\n' + r.stderr)
PY
```
Expected: `OK`。
**为什么不用 `node --check`**:该文件是 ESM(顶层 `export const` + 顶层 `await`/`return`),`node --check` 对它零鉴别力——写坏了照样 exit 0(Wave3.5 实测)。AsyncFunction 构造探针才会真的报错。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/test_agent_defs.py -q`
Expected: PASS

- [ ] **Step 6: SKILL.md 提为显式检查项**

先 `Read .claude/skills/scan-market/SKILL.md`,把步骤 4 里那句
`pinned` 取自 dispatch-plan 的 `meta[code].pinned`——漏传→保送票 SELL 双复核断链(probe 9 `sell_review_missing` 会逮)。
改为:

```
`pinned` 取自 dispatch-plan 的 `meta[code].pinned`。**派发前对照 workflow 打印的「📌 保送票 N 只」行逐一核对**:名单里的每只必须带 `pinned: true`。漏传 = 持仓 SELL 双复核整段不跑(2026-07-21 实测 300857/601869 中招);probe 9 `sell_review_missing` 只能事后 warn,拦不住。
```

- [ ] **Step 7: 提交**

```bash
git add .claude/workflows/scan-market.js .claude/skills/scan-market/SKILL.md tests/test_agent_defs.py
git commit -m "fix(scan): 派发时刻打印 pinned 名单 + SKILL 核对项(07-21 漏传 args.pinned 断持仓 SELL 双复核)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: workflow 直播接线(prelude 指路 + GATE2 逐只名单 + 检查点 log)

**Files:**
- Modify: `.claude/workflows/scan-market.js`(:28 bash 回报口径、:105 行业 brief、:136 GATE2、各相位末 log)
- Test: `tests/test_agent_defs.py`(workflow 锚测试加直播锚)

**Interfaces:**
- Consumes: Task 2 的 `_prelude_summary.md`;Task 1 的 render CLI;`g2.meta`(`gates.py:95-98` 已带 name/sector)
- Produces: workflow 日志中的 CP1/CP3/CP4 素材行

**背景**:`g2.meta` 已经带着每只 finalist 的 name/sector,而 `scan-market.js:136` 只 log 了 `finalists=${g2.n}`——整条漏斗最高光的一刻(选出了哪几只)被扔掉了。

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_defs.py` 末尾加:

```python
def test_scan_market_workflow_live_anchors():
    """直播锚:GATE2 必须逐只列名单(g2.meta 已带 name/sector,此前只 log 计数)。"""
    js = (ROOT / ".claude" / "workflows" / "scan-market.js").read_text(encoding="utf-8")
    assert "_prelude_summary.md" in js, "prelude 汇总屏未指路(末15行截断依旧)"
    assert "L3入围" in js, "GATE2 未逐只 log 入围名单"
    assert "g2.meta" in js or "metaAll" in js, "GATE2 名单未读 meta(name/sector 白算)"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/test_agent_defs.py -q -k live_anchors`
Expected: FAIL — `prelude 汇总屏未指路`

- [ ] **Step 3: prelude 指路(解截断)**

编辑 `.claude/workflows/scan-market.js`,把 prelude 那一步(现 :56)的 bash 调用改为在命令后补一句指路,并在 GATE1 通过后 log 文件路径。具体:把

```js
    () => bash(`${R} autoresearch.scan.prelude ${date}`, 'prelude/universe', 'Prelude'),
```

改为

```js
    () => bash(`${R} autoresearch.scan.prelude ${date} && echo "SUMMARY_FILE=${SD}/_prelude_summary.md"`,
      'prelude/universe', 'Prelude'),
```

并在 `log(\`GATE1 ✓ sentinel=...\`)` 之后加一行:

```js
log(`📋 前奏汇总屏全文:${SD}/_prelude_summary.md(主会话 Read 后全量转播给用户 —— bash 回报只有末 15 行,12 步 ✓/✗ 屏放不下)`)
```

- [ ] **Step 4: GATE2 逐只名单**

把 `log(\`GATE2 ✓ finalists=${g2.n}\`)`(现 :136)替换为:

```js
// 整条漏斗最高光的一刻是"选出了哪几只",而不是"选出了几只"。g2.meta 早就带着
// name/sector(gates.py:95),此前被整段扔掉 —— Wave5 ① CP3。
log(`GATE2 ✓ finalists=${g2.n}`)
const fmeta = g2.meta || {}
;(g2.finalists || []).forEach((c, i) => {
  const m = fmeta[c] || {}
  log(`  L3入围 ${i + 1}/${g2.n} ${c} ${m.name || ''}${m.sector ? `(${m.sector})` : ''}`)
})
```

- [ ] **Step 5: L4-prep 检查点 log(CP4)**

在 `log(\`L4 交接:新派 ${dispatch.length} 股…\`)` **之前**加:

```js
log(`🔎 门直方图/菜单体检可随时调:\`${R} autoresearch.scan.render ${date} --view menu_health\`(L2 成色)· \`--view gate_hist\`(L4 完成后看门柱与停因分桶)`)
```

- [ ] **Step 6: 语法探针**

Run:(同 Task 9 Step 4 的 AsyncFunction 探针命令)
Expected: `OK`

- [ ] **Step 7: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/test_agent_defs.py -q`
Expected: PASS

- [ ] **Step 8: 变异探针自查**

删掉 Step 4 里的 `forEach` 整块,重跑 → `test_scan_market_workflow_live_anchors` 应红;改回 → 绿。

- [ ] **Step 9: 提交**

```bash
git add .claude/workflows/scan-market.js tests/test_agent_defs.py
git commit -m "feat(scan): workflow 直播接线——prelude 汇总屏指路 + GATE2 逐只入围名单 + render CLI 提示

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: SKILL.md 过程直播契约(8 检查点 + L4 滚动表)

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`(流程节顶部新增「过程直播契约」小节;步骤 4 加滚动表指令)
- Test: `tests/test_agent_defs.py`(SKILL 契约锚)

**Interfaces:**
- Consumes: Task 1 render CLI、Task 2 `_prelude_summary.md`、Task 10 的 workflow log
- Produces: 主会话必守的 8 检查点播报契约

- [ ] **Step 1: 写失败测试**

在 `tests/test_agent_defs.py` 末尾加:

```python
def test_scan_market_skill_live_contract():
    """8 检查点直播契约必须在 SKILL.md 里(主会话从 L0 到 L5 静默是 Wave5 ① 的起因)。"""
    md = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    assert "过程直播契约" in md
    for cp in ("CP0", "CP1", "CP3", "CP5", "CP7"):
        assert cp in md, f"SKILL.md 缺检查点 {cp}"
    assert "autoresearch.scan.render" in md, "SKILL.md 未告诉主会话怎么调 render CLI"
    assert "_prelude_summary.md" in md, "SKILL.md 未要求转播前奏汇总屏全文"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/test_agent_defs.py -q -k skill_live_contract`
Expected: FAIL — `assert '过程直播契约' in md`

- [ ] **Step 3: 写契约节**

先 `Read .claude/skills/scan-market/SKILL.md`。在「## 流程(6 段)」小节的**进度可视化**引用块之后、`0. 前奏一键` 之前,插入:

```markdown
> ### 过程直播契约(必做,2026-07-25 用户反馈"各环节展示不够优雅完整")
>
> Monitor 只是兜底(它靠文件存在性反推、有误报前科)。**主会话必须在下列 8 个检查点主动向用户播报**——素材全是零 LLM 的现成产物,只转播不加工,别自己编数:
>
> | # | 时机 | 播什么 | 怎么拿 |
> |---|---|---|---|
> | CP0 | Stage0 完 | regime + 温度 + 策略师定调句 | `market_pack.json` + `market_view.md` §1 首句 |
> | CP1 | GATE1 过 | **前奏汇总屏全文**(12 步 ✓/✗ + 预热状态 + 当日件建议行) | Read `context/scan/<date>/_prelude_summary.md` **全量转播** |
> | CP2 | 行业 brief 齐 | 每个行业一句地形定调 | 各 `sector_briefs/*.md` 地形段首句 |
> | CP3 | GATE2 过 | **入围名单逐只**(代码/名称/行业)+ 被 pass1 切掉的影子名单 | workflow 的 `L3入围` 日志 + `_l3_pass1_cut.csv` |
> | CP4 | L4 派发 | 派发 N 股 + 预算旗 + intel 开关 + 📌保送名单 | workflow 日志(含 `📌 保送票` 行) |
> | CP5 | L4 进行中 | **每出一张卡播一行**:k/N 代码 名称 评级 conv | 轮询 `details/*.md`(见下方滚动表) |
> | CP6 | L4 全完 | 评级分布 + 停因分桶 + OW三门直方图 | `uv run --no-sync python -m autoresearch.scan.render <date> --view gate_hist` |
> | CP7 | GATE4 过 | 买单/0买判词 + 产物路径 + 分段耗时 | `summary.md` 摘录 + `--view timing` |
>
> 随时可调(零 LLM,几秒):`uv run --no-sync python -m autoresearch.scan.render <date> --view menu_health|gate_hist|timing|funnel`。
>
> **CP5 滚动表做法**:l4-stock 全部拉起后,主会话每 60–90s 跑一次
> `ls -t context/scan/<date>/details/*.md 2>/dev/null | head -20`,对**新出现**的卡 grep 其 `**Rating**` 行,播一行 `k/N <代码> <名称> → <评级>`;N 张齐或收到 workflow 完成通知即停。卡文件存在 = 该股确实完成(卡就是产物),这与 `progress.py` 按存在性猜阶段不同——后者分不清"在跑/被跳过/挂了",别拿它当阶段断言。
```

同时在步骤 5(L5 整合)的命令块之后,把「**汇报**:漏斗 + buy-list(评级/目标)+ 诚实局限。」扩为:

```
**汇报**(CP7):漏斗 + buy-list(评级/目标)+ 分段耗时表(`--view timing`)+ 诚实局限;0 买日必须播**停因分桶**(早停 N 张〔按停因〕/ 满卡未达 OW M 张),不要再说"无一过 ≥OW 三门"——早停卡按定义不写三门段,那句话不被数据支持。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/test_agent_defs.py -q`
Expected: PASS

- [ ] **Step 5: 变异探针自查**

把契约节里的 `| CP5 |` 整行删掉重跑 → 应红;改回 → 绿。

- [ ] **Step 6: 提交**

```bash
git add .claude/skills/scan-market/SKILL.md tests/test_agent_defs.py
git commit -m "docs(scan): SKILL 加过程直播契约 8 检查点 + L4 出卡滚动表(主会话此前 L0→L5 全程静默)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: stderr 计数归位 + 全量回归

**Files:**
- Modify: `autoresearch/scan/universe.py:427,458,496`、`autoresearch/scan/frame.py:176-183`(计数行同时进 stdout)
- Test: `tests/scan/test_universe_stdout_counts.py`

**背景**:L0/L1/L2 的关键计数行现在只进 stderr,bash-agent 回报「stdout 末 15 行」时白打。改为**双写**(stderr 保留给日志管道,stdout 供直播),不改任何计数逻辑。

- [ ] **Step 1: 读现状**

Run: `sed -n '420,432p;452,462p;490,500p' autoresearch/scan/universe.py && sed -n '172,186p' autoresearch/scan/frame.py`
确认这些 print 都带 `file=sys.stderr`,记下每行原文。

- [ ] **Step 2: 写失败测试**

创建 `tests/scan/test_universe_stdout_counts.py`:

```python
"""L0/L1/L2 计数行必须进 stdout:bash-agent 只回报 stdout,进 stderr 的等于白打(Wave5 ①)。"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path("autoresearch/scan/universe.py").read_text(encoding="utf-8")


def test_no_stderr_only_count_lines():
    """计数类 print 不得只写 stderr —— 要么不带 file=,要么另有一条同内容的 stdout print。"""
    bad = [ln.strip() for ln in SRC.splitlines()
           if re.search(r"print\(.*(L0|L1|L2|召回|粗排|选集).*file=sys\.stderr", ln)
           and "_echo" not in ln]
    assert not bad, "这些计数行只进 stderr,bash-agent 看不到:\n" + "\n".join(bad)
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run --no-sync pytest tests/scan/test_universe_stdout_counts.py -q`
Expected: FAIL,列出当前只进 stderr 的计数行。

- [ ] **Step 4: 加双写辅助并改调用点**

在 `autoresearch/scan/universe.py` 顶部(import 之后)加:

```python
def _echo(msg: str) -> None:
    """计数行双写:stdout 供过程直播(bash-agent 只回报 stdout),stderr 保留给日志管道。"""
    import sys as _sys
    print(msg)
    print(msg, file=_sys.stderr)
```

把 Step 1 记下的那几行 `print(f"...", file=sys.stderr)` 改为 `_echo(f"...")`(内容一字不改)。`frame.py:176-183` 同法(该文件自带一份 `_echo`,或从 universe import)。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run --no-sync pytest tests/scan/test_universe_stdout_counts.py -q`
Expected: PASS

- [ ] **Step 6: 全量回归**

Run: `uv run --no-sync pytest -q`
Expected: 全绿(基线 1448+ 通过;本批新增约 20 条)。**红了不要跳过**:逐条读失败断言,判断是被本批改动打破的旧期望(如旧 0 买文案)还是真回归;前者改测试期望并在提交信息里说明,后者修代码。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/universe.py autoresearch/scan/frame.py tests/scan/test_universe_stdout_counts.py
git commit -m "fix(scan): L0/L1/L2 计数行双写 stdout(此前只进 stderr,bash-agent 回报看不到)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: 端到端验收(新 session 跑一次真扫描)

**Files:** 无代码改动;验收记录写入 `docs/plans/2026-07-25-wave5-batch1-plan.md` 末尾的验收表。

**前置**:agent def 与 playbook 的改动**下 session 才生效**——本任务必须在**新开的 Claude Code session** 里做,否则 l4-card 用的还是旧模板(不会写 `**早停**` 行),验收会假绿。

- [ ] **Step 1: 开新 session,跑一次完整扫描**

按 SKILL.md 流程正常跑当日扫描(`frame --json` 取 user_config → `scan-market.js` → N×`l4-stock.js` → `assemble` + `gates gate4`)。
**注意传参**:`args.config` 必须是 frame 回显的 `user_config`(不是 `{}`);pinned 名单按 workflow 打印的 `📌 保送票` 行逐只传 `args.pinned: true`。

- [ ] **Step 2: 逐项对照验收**

| 验收项 | 怎么看 | 通过标准 |
|---|---|---|
| CP1 汇总屏 | 主会话是否转播了 12 步 ✓/✗ 全文 | 12 步全可见,含预热状态行 |
| 预热生效 | 汇总屏预热行 | `✓ 已跑`;若 ✗ 回 Task 3 查装载 |
| CP3 名单 | workflow 日志 | 逐只 `L3入围 k/N 代码 名称(行业)` |
| CP5 滚动表 | L4 期间主会话输出 | 每张卡出炉 ≤2min 内播一行 |
| 共享块 | `ls -l context/scan/<date>/_l4_shared_instructions.md` | 文件存在且 >0 字节 |
| 校准行到卡 | `grep -c "当日共享块" context/scan/<date>/_l4_prompt_*.md` | 等于 prompt 数(全部命中) |
| 早停记账 | `cat context/scan/<date>/_early_stop.json` | 早停卡数 = 卡里带 `**早停**` 行的数;非空则 `render --view gate_hist` 显示停因分桶 |
| 0买判词 | `summary.md`(若当日 0 买) | 出现「早停 N 张(…)· 满卡未达 OW M 张」,**无**「无一过 ≥OW 三门」 |
| pinned 复核 | `ls context/scan/<date>/_ensemble_*.json` | pinned 且评级偏空的票都有对应文件 |
| GATE4 | `gates gate4` 输出 | `ok:true`;若有 `sell_review_missing` warn 则记录并回查派发 |

- [ ] **Step 3: 把实测结果写进本文件**

在本计划末尾追加「## 验收记录(YYYY-MM-DD 实跑)」节,逐项写**实际观察到的**结果(含未通过项与原因)。**不要写"应该通过"**——只写看到的。

- [ ] **Step 4: 提交验收记录**

```bash
git add docs/plans/2026-07-25-wave5-batch1-plan.md
git commit -m "docs(plan): Wave5 批1 端到端验收记录

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 批 1 完成后的状态

- 主会话在 8 个检查点有话说;render CLI 让任何时刻都能重看菜单/门柱/耗时/漏斗。
- prewarm 真在跑(每次省 8–10min);共享指令稿真在生产(当日校准行第一次到达决策卡)。
- 早停有了机读契约、日账本、t1 桶,10 个交易日后可裁决「强势票早停是误杀还是纪律」——这是批 3 的输入。
- 0 买判词说的是它真实做过的事。

**不在本批**(见 spec):批 2 = 宏观接线 + macro full 修通 + usage 真计量;批 3 = ic_by_regime 裁决与板块动量路 replay;触发式 = playbook 修订、权重条件化、第二刀。

---

## 实施记录(2026-07-25 实跑)

12 个代码任务全部完成,全量测试 **1571 passed**(基线 1448 + 本批新增 ~25)。逐 commit:

| commit | 内容 |
|---|---|
| `97e5228` | render CLI 四 view + `_gate_histogram` 提公共 |
| `ee83aef` | prelude 汇总屏提纯函数 + 落盘 + 预热状态行 |
| `a52a26a` | `_l4_shared_instructions.md` 生产者 + workflow JS 语法探针 |
| `dd7fbb9` | 卡片 `**早停**` 机读契约 + `_early_stop.json` |
| `caab3ae` | earlystop_ledger + 接进 prelude |
| `9bd0e97` | 0买判词按真机制分桶 |
| `06e4f6d` | t1 记分卡早停桶 |
| `2f65b1a` | 直播接线(SKILL 8检查点 + GATE2 名单 + pinned 名单) |
| `d1c1f89` | 计数行改走 stdout(AST 守卫) |
| `4a3f2c7` | prewarm 安装实测 |

### 与计划的偏差(三处,均为实施中发现的事实修正)

1. **frame.py 的计数行不改**(计划 Task 12 原写"一并改"):`frame --json` 的 **stdout 就是 `market_pack.json` 的 payload**(`scan-market.js:53` 重定向),往那里多打一行计数会直接产出非法 JSON、毁掉整条下游。改为只动 `universe.py`,并新增一条**反向测试**钉死 frame 必须留 stderr,防后人"顺手统一"。
2. **早停落独立文件 `_early_stop.json`**(spec 原写"扩展进 `_final_ratings.json`"):后者的 `{code: rating}` 契约有既有消费者(retro `_buylist` 等),改形状风险不对称。独立文件零破坏。
3. **`write_shared_instructions` 顺带接管 t1 校准块**:原先 `write_dispatch_pack` 会在读完文件后再拼一次 t1 块;生产者落地后那样会重复,故把拼接删掉——**共享块的唯一事实源 = 那个文件**,这正是 byte-identical 契约要的。

### 契约锚踩坑记(同一个病,一波逮三次)

「绿灯不等于有灯」在本批**连中三次**,病因完全相同:**锚串被承重行之外的文字满足**。

| 锚(第一版) | 为什么零鉴别力 | 改成 |
|---|---|---|
| `"停因:"` | 既有模板行 `早停因:<≤20字>` 里就含这五个字 | `"**早停**: 停于"` |
| `"📌 保送票"` / `"args.pinned"` | 上方**注释**里两串都有 → 删掉整条 log 照绿 | `"${pinnedCodes.join('/')}"` / `"args.pinned:true"` |
| `"CP5"` | 正文散文 `**CP5 滚动表做法**` 满足它 → 删掉表行照绿 | `"| CP5 |"`(表格行) |

**配方**:写完锚立刻问「把承重行删掉,它会红吗」,并**真的删一次跑一次**。三次里有两次是靠这个动作逮到的——不做这一步,本批会交付三个装饰性绿灯。

另外两个探针相关的实测:
- **`node --check` 对 workflow JS 零鉴别力复验成立**:往 `scan-market.js` 追加 `const broken = {{{` 后 `node --check` 仍 **exit 0**;新的 AsyncFunction 探针(`scripts/check_workflow_js.py`,已接进 pytest)exit 1。注意探针要先剥掉 `export ` 关键字,否则连好文件都会报 `Unexpected token 'export'`(第一版就是这么写错的)。
- **AST > 逐行 grep**:`test_universe_stdout_counts` 第一版按行匹配 `file=sys.stderr`,把所有**跨行** print 误判成"没带 file=" —— 只会误报的守卫和不会报的守卫一样没用。改用 `ast.walk` 找 `print` 调用后正常。

### 真产物冒烟读数(2026-07-21 / 07-24 staging)

- `render --view gate_hist`(07-21):评级分布 12 卡 Hold 7 · UW 5;OW三门 6 卡可解析(主力真在✗3 · 业绩真兑现✗2 · 估值不透支✗3);停因分桶诚实报「口径未知」(该日卡片早于早停记账上线)。
- `render --view timing`(07-21):L0L1L2 8m25s · 策略师 1m39s · 行业brief 4m03s · **L3精排 17m57s** · L4slim 1m28s · L4研究 5m35s。
- `l4_card shared 2026-07-24`:716 字节,含 📐 目标价校准 / 🔁 L3校准 / 🚪 门校准 三条真行 + T+1 快环块 —— **这些行此前从未到达任何一张决策卡**。
- prelude 汇总屏(07-21 冒烟):完整 12 步屏可见,顺带暴露真实欠账 —— **retro_input 已备料未收尾:07-16、07-17、07-21 三天**。
- prewarm:`launchctl list` 可见;kickstart 实跑 ~12min 落 `_prewarm.json`(帧 3975 入湖 / 21 次端点预拉 / 温度 1 行 / 档案池预取 30/30)。

### 剩余(Task 13,须新 session)

agent def 与 playbook 改动**下 session 才装载**,故端到端验收必须新开 session 跑一次真扫描,按 Task 13 的验收表逐项对照。首要看:早停卡真的会写 `**早停**` 行吗(这是 ②C 整条链的入口)。
