#!/usr/bin/env python3
"""`brief.md` 确定性生成器(Wave12 T25 / 批C C1)—— 报告双层的**核心速读**层。

design: `docs/specs/2026-08-08-wave12-seven-topics-design.md` §C1 + §E5(用户 2026-08-08 裁定)。

## 为什么是模板而不是 agent(R-C1 已裁)

brief 的内容全是**结构化结论、计数、评级、tripwire 与账本状态**。让 LLM 再压缩一遍只会
新增三样东西:编数面、对账 lint、一次调用成本——却不增加任何决策信息。所以本模块
**零 LLM、零联网、只读结构化产物**,同一 run 重放 byte 稳定(无时间戳、无随机、无无序遍历)。
将来若要润色,只能生成非权威 commentary,**不得改写 BUY、数字或风险结论**。

## 六节骨架(硬预算 ≤3,000 字节)

  ① 市场一句(regime + 温度 + 策略师定调)
  ② 漏斗一行(L0→finalists)
  ③ **BUY 结论区**——影子期**双行**:旧生产结论(近期恒 0 买)+「影子 relative BUY」行
     (显式标**非正式·不执行**);activate 后换成正式 BUY 行。
     (2026-08-21 learning 层退役:原第三行「旧 OW 基率」分账行随 `buy_ledger` 删除。)
  ④ 持仓动作表(pinned 逐票:评级 + tripwire)
  ⑤ 风险哨(自检 fail/warn + 降级字段 + 卡覆盖)
  ⑥ 昨日 delta(finalist 重叠 + 评级变动)
  (原 ⑦ 欠账红行〔待裁决提案 / 未决反馈〕随 learning 层退役删除 —— 素材出自
   `feedback_store` 的提案看板,闭环一走没有欠账这回事了。)

## 语义纪律(硬性,测试钉死)

1. **相对 BUY 不得写成「预计绝对上涨」**(`BANNED_RELATIVE_PHRASES`)。它的承诺只有一句:
   「当日可交易全集里相对最优」——这与「当天亏钱」可以同时为真。
2. **绝对 gap 为负 → 固定写 `弱市相对最优`**,不许用别的说法糊过去。
3. **每个数字都能在白名单输入里找到**:生成器同时输出 `sources` 边表(逐行 `field/value/
   file/locator/text`),供 T27 的 `self_review.brief_lint` 逐项对账;`text` 是该数字在
   brief 正文里的**完整渲染片段**,篡改 brief 里任何一个评级/读数都会让对应行失配。

  uv run --no-sync python -m autoresearch.scan.brief <date> [--out <dir>]
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import re
import sys
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.ruler import MAIN_RULER, REL_MARKET
from autoresearch.contracts import artifacts as _contract_artifacts
from autoresearch.scan.relative_buy import DECISION_FILENAME, MODE_SHADOW
from autoresearch.scan.relative_facts import (  # P0 低位转强波:读模型/禁词单一事实源(summary 同源)
    BANNED_RELATIVE_PHRASES,  # noqa: F401 — 再导出契约,测试锁 `brief.X is relative_facts.X`,勿删
    COMPOSITE_EXPECTATION,
    DECISION_POOL_LABEL,
    REL_MARKET_POPULATION,
    WEAK_MARKET_PHRASE,
    relative_facts,
)

SCHEMA_VERSION = 1
#: 硬预算(字节)。T27 的 lint 用同一个常量量,不另写一份字面量。
MAX_BYTES = 3000

#: B-1(2026-08-09 全支终审)—— `rel_gap_market` 的**评分人口**短语。
#: 决策文档的 `benchmark.market.eval_population` 是权威原文(往往一整句),brief ③ 只有
#: ~3KB 预算装不下,所以正文渲染这个短语、边表 `value` 仍记原文。老决策文档(T23 的 I-2
#: 把 `definition`/`eval_population` 拆开**之前**写的)没有该键 → 回落到本常量;
#: **绝不回落到那个 L0 的 n**,否则病只是换了个地方复发。
#: (2026-08-21 起真身在 `scan/relative_facts.py`,与 summary 侧同源;此处为 re-export。)
BRIEF_FILENAME = "brief.md"
SOURCES_FILENAME = "_brief_sources.json"

#: 输入白名单 —— 生成器只准从这些结构化产物取数,**禁读 `details/` 全文与 `trace/` 大文件**。
#: 四个非文件项是**确定性派生**(零 LLM),各自注明真身:
#:   `menu_health`     = `scan.menu.menu_health(scan_dir)` over `L1_scored_full.csv`+`L2_gbdt_top200.csv`
#:   `temperature.csv` = `context/learning/temperature.csv`(prelude 增量落盘)
#:
#: ⚠️ **这张表是双向不变量,不是许愿单**(fix-1,复核 I-2/M-2):
#:   ⊇ 方向 —— 模块里实际读到的每个文件都必须在表内(`test_whitelist_covers_every_file_read`
#:      用 AST 抽本文件的文件名字面量核对);
#:   ⊆ 方向 —— 表内每一项都必须真被读过(`test_whitelist_has_no_dead_entry`)。
#:      少了 ⊆,一条从没接线的「许愿项」会永远躺在表里冒充契约(`market_view.md` 就这么
#:      躺了一轮,复核 M-2 逮到)。
#:
#: 2026-08-29(Task 9c / spec §2.4 A1):**路径不在这里再写一份** —— 凡是已登记的产物
#: 一律 `("artifact", <登记名>)`,路径由 `contracts.artifacts` 给;登记表改名而这里没跟上
#: (或名字打错)= **导入即 `KeyError`**,不会像今天这样安静地漂(K1 的病:一个产物名散在
#: 40 个文件里)。
#:
#: ⚠️ 白名单是**许可**表,比登记表窄得多(登记表 80+ 项,brief 只准读下面 10 项)——
#: 所以这里是**逐项点名**,不是 `for_root("staging")` 之类的宽过滤器。用过滤器等于把
#: 「brief 能读什么」交给别人以后往登记表里加什么,那是放宽许可,不是派生。
#:
#: `("literal", …)` 的三项没进登记表,各有各的理由(它们**不是**漏登记):
#:   `_tripwire_conflicts.json` / `temperature.csv` —— `contracts.NON_ARTIFACT_LITERALS`
#:      审计判定「不是流水线产物」(后者在引擎根 `context_<engine>/learning/` 下增量落盘);
#:   `menu_health` —— 虚拟项,根本不是文件(见上面的真身注释)。
_WHITELIST_SPEC: tuple[tuple[str, str], ...] = (
    ("artifact", "funnel_meta"),               # meta.json
    ("artifact", "finalists"),
    ("artifact", "final_ratings"),
    ("artifact", "decision_records"),
    ("artifact", "run_mode"),
    ("artifact", "run_health"),
    ("artifact", "gate_fires"),
    ("literal", "_tripwire_conflicts.json"),
    ("artifact", "market_view"),
    ("artifact", "relative_buy_decision"),     # == relative_buy.DECISION_FILENAME(读点用后者)
    ("literal", "temperature.csv"),
    ("literal", "menu_health"),
    ("artifact", "overseas_calendar"),  # D-2:隔夜窗海外事件(⑤ 风险哨一句;风险可见性,不喂判断层)
    ("artifact", "manifest"),  # Task 3:跨 run 昨日 delta 读点(`_prev_published` 挑上一场发布 run)
    ("artifact", "recommendations"),  # Task 4:E6 BUY/席位实测读点(`_e6_realized_stats`)
    ("artifact", "buyability"),  # Task 21:不可买归因(`_buyability.json`),③ 附加一行
)

#: 白名单里**已登记**产物的登记名(顺序同上)。
REGISTERED_INPUTS: tuple[str, ...] = tuple(n for kind, n in _WHITELIST_SPEC if kind == "artifact")
#: 白名单里**未登记**的三项(理由见 `_WHITELIST_SPEC` 注释)。
UNREGISTERED_INPUTS: tuple[str, ...] = tuple(n for kind, n in _WHITELIST_SPEC if kind == "literal")

INPUT_WHITELIST = tuple(
    _contract_artifacts.by_name(n).path if kind == "artifact" else n
    for kind, n in _WHITELIST_SPEC
)

#: 本模块**产出**(不是输入)的文件名 —— 白名单不变量测试的豁免集。
OUTPUT_FILENAMES = (BRIEF_FILENAME, SOURCES_FILENAME)

#: 相对 BUY 区禁词与「弱市相对最优」措辞:真身在 `scan/relative_facts.py`(brief/summary
#: 共用同一张表,见文件顶部 import)——两处各写一份字面量 = 两处各漂,是 brief↔summary
#: 「一起错一起绿」的同族前科。

_REGIME_ZH = {"trend": "趋势", "range": "震荡", "risk_off": "避险"}
_RATING_ORDER = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
_BUY_RATINGS = ("Buy", "Overweight")


# ────────────────────────────── 读取原语(容忍缺文件) ──────────────────────────────

def _json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def _code6(value) -> str:
    return str(value or "").strip().split(".")[0].zfill(6)


def _pct(value, digits: int = 2) -> str:
    return "—" if value is None else f"{value * 100:+.{digits}f}%"


# ────────────────────────────── 事实收集 ──────────────────────────────

def _gate_counts(scan_dir: Path) -> dict:
    """self_review 落的 `gate_fires.csv` → fail/warn 计数(无 severity 的行不计入)。"""
    fail = warn = 0
    for row in _rows(scan_dir / "gate_fires.csv"):
        sev = str(row.get("severity") or "").strip()
        fail += sev == "fail"
        warn += sev == "warn"
    return {"n_fail": fail, "n_warn": warn}


def _why_no_buy(scan_dir: Path) -> dict:
    """「今天为什么没买」的机读分桶 —— 早停停因 + OW 三门失守,全取自 `decision_records.json`
    的结构化字段(不读卡片自由文本)。

    CP7 播报纪律的同一事实源:0买日**必须播停因分桶**,不许说「无一过 ≥OW 三门」——早停卡
    按定义压 ≤Hold 且根本不写三门段,把它们算进门柱是把两类原因搅成一类(STAGES『运维细节』)。
    """
    doc = _json(scan_dir / "decision_records.json")
    records = (doc or {}).get("records") if isinstance(doc, dict) else None
    stops: dict[str, int] = {}
    gates: dict[str, int] = {}
    n_full = 0
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        early = rec.get("early_stop")
        if isinstance(early, dict) and early.get("reason"):
            stops[str(early["reason"])] = stops.get(str(early["reason"]), 0) + 1
            continue
        n_full += 1
        for gate, state in sorted((rec.get("gate_states") or {}).items()):
            if str(state) == "FAIL":
                gates[str(gate)] = gates.get(str(gate), 0) + 1
    return {"early_stop": dict(sorted(stops.items())), "gate_fail": dict(sorted(gates.items())),
            "n_early": sum(stops.values()), "n_full": n_full}


def _is_live_scan(scan: Path) -> bool:
    """只有生产现场(run 分区 staging 或活着的 scan_root 子目录)才去翻已发布 run;
    测试的 tmp 目录不是,行为逐字不变(也不会被开发机上的真实 run 污染)。"""
    try:
        return "scan_runs" in scan.resolve().parts or scan.parent.resolve() == ws.scan_root().resolve()
    except OSError:
        return False


def _prev_published(date: str) -> tuple[str | None, dict[str, str], set[str]]:
    """上一场**已发布** run(数据日 < date)的终评级与 finalist 码集。

    run 分区后 `run_health.churn` 恒 None(09-09→09-17 六场全印「无上一扫描日」),因为
    `health.finalist_churn` 只看 `scan_dir.parent`,那里只有本场自己的日期。改从
    `reports_<engine>/scan/` 的已发布 run 里找(`outcome.published_runs`)。找不到 → (None, {}, set())。
    """
    try:
        from autoresearch.scan.outcome import published_runs
        runs = published_runs()
    except Exception:  # noqa: BLE001 — 跨 run 读者是可选层,坏了只影响 ⑥ 那一行
        return None, {}, set()
    best: tuple[str, Path] | None = None
    for run in runs:
        manifest = _json(run / "manifest.json") or {}
        d = str(manifest.get("analysis_date") or "")
        if d and d < date and (best is None or d > best[0]):
            best = (d, run)
    if best is None:
        return None, {}, set()
    staging = best[1] / "trace" / "staging"
    ratings = _json(staging / "_final_ratings.json") or {}
    codes = {_code6(r.get("code")) for r in _rows(staging / "finalists.csv") if r.get("code")}
    return best[0], {_code6(k): v for k, v in ratings.items()}, codes


def _tripwire_counts(scan_dir: Path) -> dict[str, int]:
    """`_tripwire_conflicts.json` → 逐票盯梢线冲突条数(白名单项;fix-1 补进 `INPUT_WHITELIST`)。

    复核 I-2:第一版读了这份文件却把它渲染出来的 `⚠️tripwire N` 标成「来自
    `_final_ratings.json`」—— sources 误标比单纯漏标更糟,T27 的白名单腿会对着**错误的来源**
    比对、绿了也不代表数对。现在它有自己的边表行(`pinned.<code>.tripwire`)。
    """
    doc = _json(scan_dir / "_tripwire_conflicts.json")
    if not isinstance(doc, dict):
        return {}
    out: dict[str, int] = {}
    for code, entry in doc.items():
        hits = entry.get("all_hits") if isinstance(entry, dict) else None
        out[_code6(code)] = len(hits) if isinstance(hits, list) else 1
    return out


#: ① 定调句的字符上限(`market_view.md` 首段 = 策略师「一句话定调」)。
TONE_CHARS = 34
_TONE_RE = re.compile(r"^\s*1\.\s*\*\*一句话定调\*\*\s*[::]\s*(.+)$", re.M)


def _market_tone(scan_dir: Path) -> str:
    """`market_view.md` 的「一句话定调」→ 截断短句(白名单项 `market_view.md` 的**唯一**读点)。

    复核 M-2:这一项此前在白名单里挂着却从没被读过 = 死条目冒充契约。现在真接线,并由
    `test_whitelist_has_no_dead_entry` 的 ⊆ 方向守住不再退回死条目。
    缺文件 / 缺该行 → ""(presence-gated,不猜)。
    """
    path = scan_dir / "market_view.md"
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = _TONE_RE.search(text)
    if not m:
        return ""
    body = re.sub(r"[*`]", "", m.group(1)).strip()
    for stop in ("——", "。", ";", ";"):
        idx = body.find(stop)
        if 0 < idx <= TONE_CHARS:
            body = body[:idx]
            break
    body = body.replace("\n", " ").strip()
    return (body[:TONE_CHARS] + "…") if len(body) > TONE_CHARS else body


def _menu_sick(scan_dir: Path) -> bool | None:
    """L2 菜单病旗(白名单虚拟项 `menu_health`)—— 健康上涨断供 = 当天「没得挑」的结构性原因。

    只取这一个 bool:`menu_health()` 的整块 markdown 已经在 summary §2,brief 要的是那面旗。
    缺 staging → None(presence-gated,与 False「查过没病」区分)。
    """
    try:
        from autoresearch.scan.menu import menu_health
        block = menu_health(scan_dir)
    except Exception:  # noqa: BLE001 — 菜单体检是可选层
        return None
    if not block:
        return None
    return "⚠️菜单病" in block


def _overseas_line(scan_dir: Path) -> str:
    """D-2:隔夜窗海外事件一句(`scan/overseas.py`;缺文件 / 无事件 → "")。

    **风险可见性,不是决策输入**:brief ⑤ 只印一句 + 计数,不改 BUY/仓位/评级
    (设计稿 §0 边界最后一行)。判断层接入(B-2/B-3)受 09-中冻结,与本行无关。
    """
    try:
        from autoresearch.scan.overseas import brief_line
        return brief_line(scan_dir)
    except Exception:  # noqa: BLE001 — 可选层,坏日历不挡 30 秒入口
        return ""


def collect_facts(scan_dir: Path | str, *, analysis_date: str | None = None,
                  run_folder: str | None = None, decision: dict | None = None,
                  scan_root: Path | None = None) -> dict:
    """白名单输入 → 结构化事实字典(纯读,不写盘、不联网、不调 LLM)。

    `decision` 显式传入时**不读盘**(历史回放用:`_relative_buy_decision.json` 是 T24 之后
    才挂进 `post_run.observe` 的,更早的 run 目录里没有这份文件,回放只能现算并注入)。
    """
    scan = Path(scan_dir)
    date = analysis_date or scan.name
    root = Path(scan_root) if scan_root else scan.parent

    meta = _json(scan / "meta.json") or {}
    health = _json(scan / "run_health.json") or {}
    ratings = _json(scan / "_final_ratings.json") or {}
    finals = _rows(scan / "finalists.csv")
    mode_doc = _json(scan / "run_mode.json") or {}
    if decision is None:
        decision = _json(scan / DECISION_FILENAME)

    counts = health.get("counts") or {}
    pinned_rows = [r for r in finals if str(r.get("lane") or "").strip() == "pinned"]
    genuine_rows = [r for r in finals if str(r.get("lane") or "").strip() != "pinned"]
    rating_by_code = {_code6(k): v for k, v in ratings.items()}
    dist: dict[str, int] = {}
    for value in rating_by_code.values():
        dist[str(value)] = dist.get(str(value), 0) + 1

    temp = None
    with contextlib.suppress(Exception):
        from autoresearch.scan import temperature as _temp
        temp = _temp.show(date)

    tw = _tripwire_counts(scan)
    pinned = [{"code": _code6(r.get("code")), "name": str(r.get("name") or ""),
               "rating": rating_by_code.get(_code6(r.get("code")), "—"),
               "tripwire": tw.get(_code6(r.get("code")), 0)}
              for r in pinned_rows]

    churn = health.get("churn") or {}
    prev_date = churn.get("prev_date")
    prev_by_code: dict[str, str] = {}
    n_repeat, n_today = churn.get("n_repeat"), churn.get("n_today")
    prev_file: str | None = None
    if prev_date:
        prev = _json(root / str(prev_date) / "_final_ratings.json") or {}
        prev_by_code = {_code6(k): v for k, v in prev.items()}
    elif _is_live_scan(scan):
        prev_date, prev_by_code, prev_codes = _prev_published(date)
        if prev_date:
            prev_file = "manifest.json"
            today_codes = {_code6(r.get("code")) for r in finals if r.get("code")}
            n_repeat, n_today = len(prev_codes & today_codes), len(today_codes)
    changes: list[dict] = []
    for code in sorted(set(prev_by_code) & set(rating_by_code)):
        if prev_by_code[code] != rating_by_code[code]:
            changes.append({"code": code, "from": prev_by_code[code],
                            "to": rating_by_code[code]})

    return {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "run_folder": run_folder or "",
        "ruler": MAIN_RULER,
        "market": {
            "regime": meta.get("regime") or health.get("regime"),
            "temperature": (temp or {}).get("score"),
            "phase": (temp or {}).get("phase"),
            "tone": _market_tone(scan),
        },
        "funnel": {
            "universe_raw": meta.get("universe_raw"),
            "universe": meta.get("universe") or counts.get("l1_full"),
            "recall": meta.get("recall_n") or counts.get("recall"),
            "l2": meta.get("l2_n") or counts.get("l2"),
            "l3_genuine": len(genuine_rows),
            "l3_pinned": len(pinned_rows),
            "cards": counts.get("cards"),
        },
        "buys": {
            "production_n": sum(1 for v in rating_by_code.values() if v in _BUY_RATINGS),
            "dist": dist,
            "run_mode": str(mode_doc.get("mode") or "—"),
            **_why_no_buy(scan),
        },
        "relative": _relative_facts(decision),
        "buyability": _json(scan / "_buyability.json") or {},
        "e6_realized": _e6_realized_stats(),
        "pinned": pinned,
        "risk": {**_gate_counts(scan),
                 "degraded": list(health.get("degraded_fields") or []),
                 "menu_sick": _menu_sick(scan),
                 "overseas": _overseas_line(scan),
                 "cards": counts.get("cards"), "finalists": len(finals)},
        "delta": {"prev_date": prev_date, "n_repeat": n_repeat, "n_today": n_today,
                  "changes": changes, "prev_file": prev_file},
    }


#: 真身在 `scan/relative_facts.py`(brief ③ 与 summary「📉 今日漏斗读数」共用同一个解析器,
#: 2026-08-21 低位转强波 P0)。保留旧名给本模块内部调用点与既有测试。
_relative_facts = relative_facts


# ────────────────────────────── 渲染(七节;text 即 sources 的锚) ──────────────────────────────

def _src(rows: list[dict], field: str, value, file: str, locator: str, text: str) -> str:
    rows.append({"field": field, "value": "—" if value is None else str(value),
                 "file": file, "locator": locator, "text": text})
    return text


def _sections(facts: dict, *, pinned_cap: int, delta_cap: int) -> tuple[list[str], list[dict]]:
    src: list[dict] = []
    out: list[str] = []
    date, run = facts["date"], facts["run_folder"]
    out.append(f"# 速读 · {date}" + (f"(run `{run}`)" if run else ""))
    out.append("")

    # ① 市场
    mk = facts["market"]
    regime = mk.get("regime")
    bits = [_src(src, "market.regime", regime, "meta.json", "regime",
                 f"{_REGIME_ZH.get(str(regime), str(regime or '—'))}({regime or '—'})")]
    if mk.get("temperature") is not None:
        bits.append(_src(src, "market.temperature", mk["temperature"], "temperature.csv",
                         f"date={date}",
                         f"🌡 {float(mk['temperature']):.0f}({mk.get('phase') or '—'})"))
    if mk.get("tone"):
        bits.append(_src(src, "market.tone", mk["tone"], "market_view.md",
                         "§1 一句话定调", f"定调「{mk['tone']}」"))
    out.append("**① 市场**:" + " · ".join(bits))

    # ② 漏斗
    fn = facts["funnel"]
    funnel_text = (f"{fn.get('universe_raw') or '—'}→L0 {fn.get('universe') or '—'}"
                   f"→L1 {fn.get('recall') or '—'}→L2 {fn.get('l2') or '—'}"
                   f"→L3 {fn['l3_genuine']}(+{fn['l3_pinned']}📌)"
                   f"→L4 {fn.get('cards') if fn.get('cards') is not None else '—'} 卡")
    _src(src, "funnel.universe", fn.get("universe"), "meta.json", "universe", funnel_text)
    _src(src, "funnel.l2", fn.get("l2"), "meta.json", "l2_n", funnel_text)
    _src(src, "funnel.finalists", fn["l3_genuine"], "finalists.csv", "lane!=pinned",
         funnel_text)
    out.append("**② 漏斗**:" + funnel_text)

    # ③ BUY 结论区
    out.append("**③ 结论**")
    out += _buy_lines(facts, src)

    # ④ 持仓动作
    out.append("**④ 持仓**:" + _pinned_text(facts, src, pinned_cap))

    # ⑤ 风险哨
    rk = facts["risk"]
    menu = {True: "⚠️菜单病(健康上涨断供)", False: "菜单健康", None: "菜单 —"}[rk.get("menu_sick")]
    risk_text = (f"自检 fail {rk['n_fail']} / warn {rk['n_warn']}"
                 f" · 降级 {'、'.join(rk['degraded']) if rk['degraded'] else '无'}"
                 f" · {menu}"
                 f" · 卡 {rk.get('cards') if rk.get('cards') is not None else '—'}"
                 f"/{rk['finalists']}")
    _src(src, "risk.n_fail", rk["n_fail"], "gate_fires.csv", "severity==fail", risk_text)
    _src(src, "risk.n_warn", rk["n_warn"], "gate_fires.csv", "severity==warn", risk_text)
    _src(src, "risk.degraded", ",".join(rk["degraded"]), "run_health.json",
         "degraded_fields", risk_text)
    # D-2:隔夜窗海外事件一句(presence-gated;文件缺 / 无事件 → 整段不出现,parity 不破)。
    # **风险可见性**:它不改 BUY、不改仓位、不改评级,只让「买之前/持仓期间有什么已知外部
    # 事件」在 30 秒入口里可见 —— 08-26 真跑漏掉 NVDA 盘后财报正是这条腿缺席。
    _ov = str(rk.get("overseas") or "")
    if _ov:
        risk_text = f"{risk_text} · {_ov}"
        _src(src, "risk.overseas", _ov, "overseas_calendar.csv", "window+subject", risk_text)
    _src(src, "risk.menu_sick", rk.get("menu_sick"), "menu_health",
         "健康上涨断供旗", risk_text)
    out.append("**⑤ 风险哨**:" + risk_text)

    # ⑥ 昨日 delta
    out.append("**⑥ 昨日 delta**:" + _delta_text(facts, src, delta_cap))

    out.append("")
    out.append(f"_确定性生成(零 LLM);主尺 {facts['ruler']};详细版见 `summary.md`。"
               f"仅供研究,非投资建议。_")
    return out, src


_REALIZED_MIN_N = 20


def _e6_realized_stats(reports_root: Path | None = None) -> dict:
    """账本 `recommendations.csv` 里 E6 的**实测**:BUY 行(mode=active ∧ e6_buy)与席位行
    (role=composite_seat),都限 MATURE ∧ ACTIONABLE。n<20 只报 n(账本自己的 `ledger_line`
    纪律同款);n≥20 报均值 pp 与胜率。账本缺 → n=0。"""
    try:
        from autoresearch.scan.outcome import load_ledger
        rows = load_ledger(reports_root)
    except Exception:  # noqa: BLE001 — 账本是可选层
        rows = []

    def _stat(pred) -> dict:
        vals = []
        for r in rows:
            if not pred(r):
                continue
            if str(r.get("outcome_status")) != "MATURE" or str(r.get("actionability")) != "ACTIONABLE":
                continue
            try:
                vals.append(float(r.get("gap_c1_o2")))
            except (TypeError, ValueError):
                continue
        n = len(vals)
        if n < _REALIZED_MIN_N:
            return {"n": n, "mean_pp": None, "win": None}
        return {"n": n, "mean_pp": round(100 * sum(vals) / n, 2),
                "win": round(sum(1 for v in vals if v > 0) / n, 2)}

    return {
        "buy": _stat(lambda r: str(r.get("e6_buy")).lower() == "true" and str(r.get("mode")) == "active"),
        "seat": _stat(lambda r: str(r.get("role")) == "composite_seat"),
    }


def _realized_text(stat: dict, label: str) -> str:
    """n<20:只给 n;为负 → 固定 `弱市相对最优`(语义纪律②)。"""
    if stat.get("mean_pp") is None:
        return f"账本 {label} 实测 n={stat.get('n', 0)},不足 {_REALIZED_MIN_N} 不给区间"
    body = f"账本 {label} 实测 {stat['mean_pp']:+.2f}pp(n={stat['n']},胜率 {stat['win']:.0%},未扣成本)"
    if stat["mean_pp"] < 0:
        body += f" → **{WEAK_MARKET_PHRASE}**(相对 BUY 从不承诺绝对收益为正)"
    return body


def _buy_lines(facts: dict, src: list[dict]) -> list[str]:
    lines: list[str] = []
    buys = facts["buys"]
    rel = facts["relative"]
    # Task 21(2026-09-24 §2.7 + 2026-09-25 controller 追加裁定):不可买归因,三个出口
    # (present=False/blocked/正常)共用同一份渲染 —— 算一次,哪个分支 return 都带上它,
    # 不许两份拷贝各自漂移。blocked 日尤其要紧:它是这一行存在的理由(八个真实扫描日
    # 六个 blocked),present=False 时 `_buyability.json` 几乎必然也不存在,靠
    # `_buyability_line` 内部的 `if not ba.get("wall")` 天然不出线,不必再加一层判断。
    ba_line = _buyability_line(facts, src)
    # E3(task-2.4):active 期这一行**不再叫「生产 BUY」** —— 那个名字会让读者把研究评级
    # 的张数读成买入建议,而 active 期 BUY 由下一行的决策文件独家拥有。口径源同 `tag`
    # (决策文件的 `mode`,brief 渲染时它已定稿),两行不会一个说影子一个说正式。
    active = rel.get("mode", MODE_SHADOW) == "active"
    dist = "、".join(f"{r} {buys['dist'][r]}" for r in _RATING_ORDER if r in buys["dist"])
    prod_text = (
        (f"**研究评级分布**(≥Overweight {buys['production_n']} 只 —— "
         f"**证据不是决策**,BUY 见下一行;评级分布 {dist or '—'};run_mode {buys['run_mode']})")
        if active else
        (f"**生产 BUY {buys['production_n']} 只**(旧绝对门 ≥Overweight;"
         f"评级分布 {dist or '—'};run_mode {buys['run_mode']})"))
    _src(src, "buys.production_n", buys["production_n"], "_final_ratings.json",
         "count(rating in Buy/Overweight)", prod_text)
    _src(src, "buys.run_mode", buys["run_mode"], "run_mode.json", "mode", prod_text)
    lines.append("- " + prod_text)
    why = _why_text(buys, active=active)
    if why:
        _src(src, "buys.why_no_buy", buys.get("n_early"), "decision_records.json",
             "records[].early_stop.reason + gate_states==FAIL", why)
        lines.append("  " + why)

    tag = ("🕶 **影子 relative BUY(非正式·不执行)**"
           if not active else "✅ **relative BUY**")
    # Task 20:E6 v4.0 的 A/R 分级(裁定①)—— A=卡面自己允许入场,R=卡面没给买点、
    # 靠「每天至少一只」的相对硬规则强出。缺 `tier`(v3.0 决策书 / tiering=False)不挂标,
    # 逐字兼容;present=False 或 blocked=True 时 `rel.get("tier")` 恒 None,同样不挂标。
    # fix round 1(reviewer minor):R 级不能只在 ✅ 后面追加说明——brief 是被快速略读的,
    # 先入眼的字形才是真正落地的信号,追加在后面读者仍先看见绿勾。R 级改**替换**前导
    # 字形(✅→🟥,行首即转红),标签里原来的 🟥 随之去重(否则会双红)。影子期前导
    # 本来就不是 ✅ 而是 🕶,`str.replace` 找不到 ✅ 是无操作的 no-op——不补红也不留双
    # 标,那句「非正式·不执行」本身已经是限定语,不需要额外的红色标记。
    tier = rel.get("tier")
    if tier == "A":
        tag += " · **A 级·卡面允许入场**"
    elif tier == "R":
        tag = tag.replace("✅", "🟥") + " · **R 级·卡面无买点·强制相对(裁定①)**"
    if not rel.get("present"):
        lines.append(f"- {tag}:—(`{DECISION_FILENAME}` 未生成 —— 缺证据不等于没候选)")
        if ba_line:
            lines.append(ba_line)
        return lines
    if rel.get("blocked"):
        why = "、".join(rel.get("blocked_reasons") or []) or "无分桶"
        text = (f"{tag}:**BLOCKED**(全部候选被硬资格否决:{why};"
                f"候选 {rel.get('n_candidates')} / 合格 {rel.get('n_eligible')})")
        _src(src, "relative.blocked", True, DECISION_FILENAME, "blocked", text)
        lines.append("- " + text)
        # blocked 日是不可买归因这一行存在的理由(八个真实扫描日六个 blocked)——
        # 上面那行「BLOCKED(...hard_gate.no_redflag×7...)」把六个否决因揉成一个数,
        # 下面这行才是把它拆开的地方(2026-09-25 controller 追加裁定:不改 blocked_reasons
        # 本身,两套词汇会打架;归因专用本行,所以必须紧跟着渲染)。
        if ba_line:
            lines.append(ba_line)
        return lines

    gap_txt = _realized_text(facts["e6_realized"]["buy"], "BUY")
    pool_txt = (f" · 池={rel.get('pool_label')}"
                + (f"({rel.get('n_pool')} 只)" if rel.get("n_pool") is not None else ""))
    text = (f"{tag}:{rel.get('name') or '—'} {rel.get('code') or '—'}"
            f" · basis={rel.get('basis')}{pool_txt} · 合格内 #{rel.get('rank')}"
            f"/{rel.get('n_eligible')}(候选 {rel.get('n_candidates')})"
            f" · 卡面 {rel.get('research_rating') or '—'}"
            f" · 基准 {rel.get('market_column')}(人口={REL_MARKET_POPULATION}等权;"
            f"{DECISION_POOL_LABEL} {rel.get('decision_pool_n')} = 决策层分位/流动性门分母,"
            f"非本列人口)"
            f"+{rel.get('sector_column')}"
            f" · {gap_txt} · 硬否决 {rel.get('hard_reject')}"
            f" · 主尺 {rel.get('ruler')}"
            f" —— 只承诺「当日全集内相对最优」,**不承诺绝对收益为正**")
    _src(src, "relative.code", rel.get("code"), DECISION_FILENAME, "buys[0].code", text)
    _src(src, "relative.tier", tier, DECISION_FILENAME, "buys[0].tier", text)
    _src(src, "relative.rank", rel.get("rank"), DECISION_FILENAME,
         "candidates[code].rank", text)
    # B-1:**两行,不是一行** —— 边表必须能把这两个 n/人口分开,否则 T27 的对账 lint
    # 比对的是同一个错源(它锚的正是这段 `text`),错得再离谱也永远绿灯。
    _src(src, "relative.decision_pool_n", rel.get("decision_pool_n"), DECISION_FILENAME,
         f"benchmark.market.n({DECISION_POOL_LABEL} = 决策层分位/流动性门的分母)", text)
    _src(src, "relative.eval_population", rel.get("eval_population"), DECISION_FILENAME,
         f"benchmark.market.eval_population({REL_MARKET} 的真分母;评分时由 "
         "relative_ledger 另算,不在本产物里)", text)
    _src(src, "relative.realized_buy_n", facts["e6_realized"]["buy"]["n"], "recommendations.csv",
         "rows[e6_buy ∧ mode=active ∧ MATURE ∧ ACTIONABLE]", text)
    _src(src, "relative.pool", rel.get("pool"), DECISION_FILENAME, "pool", text)
    lines.append("- " + text)
    if rel.get("pool") == "composite":
        # v3.0 的诚实呈现(A5):证据是什么、期望多大、执行条件是什么 —— 三样都写在
        # BUY 行下面,免得读者把「相对最优」读成「明天会涨」。
        exec_txt = ("  ↳ 证据:当日 composite 分位最高的证据席 · L4 否决检查通过 · "
                    + _realized_text(facts["e6_realized"]["seat"], "席位")
                    + f" · {COMPOSITE_EXPECTATION}")
        _src(src, "relative.pool_expectation", rel.get("pool"), DECISION_FILENAME,
             "pool==composite", exec_txt)
        lines.append(exec_txt)
        lines.append("  ↳ 执行线(T+1 尾盘,机器可检):当日涨幅 ≤3% ∧ 收盘不在当日区间上 30% "
                     "∧ 未封涨停 —— 追强在隔夜尺上四年逐年为负(−0.13~−0.27pp)")
    if ba_line:
        lines.append(ba_line)
    return lines


def _buyability_line(facts: dict, src: list[dict]) -> str | None:
    """不可买归因(Task 21,2026-09-24 §2.7 + 2026-09-25 controller 追加裁定)③ 附加行。

    单点渲染:`_buy_lines` 的三个出口(present=False / blocked / 正常)各自在
    `return lines` 前调用本函数一次,不许各写一份各自漂 —— `ba.get("wall")` 为空
    (falsy)时自然返回 `None`,调用方不必另加判断。这个 falsy 有两种成因:
    `_buyability.json` 整份文件缺席(如决策文件本就没生成),或文件在场但
    `wall` 字段本身是 `null`(fix round 1 finding ①:`build_buyability` 读不到决策文档
    时诚实吐出 `wall=None`,而不是编一个「看起来合理」的墙)——两种成因在这里天然
    同一处理,不必分辨。blocked 日尤其要紧:上面那行「BLOCKED(...hard_gate.no_redflag
    ×N...)」把六个否决因揉成一个数,这一行才是把「卡面入场=禁止」从中拆出来的地方。
    """
    ba = facts.get("buyability") or {}
    if not ba.get("wall"):
        return None
    m, c, g = ba.get("menu") or {}, ba.get("cards") or {}, ba.get("gates") or {}
    pct = lambda v: "—" if v is None else f"{v:.0%}"   # noqa: E731
    # fix round 1 finding ②:`sector_seats`/`composite_seats` 现在缺源时是 `None`(不是
    # 假 0),渲染必须跟着用「—」而不是让 f-string 直接吐出字面量 "None"。
    cnt = lambda v: "—" if v is None else str(v)   # noqa: E731
    wall = ba["wall"]
    # P22 的两个新值在中文行里是孤立的英文标识符,加一句短注让读者不必跳去查 wall 词表;
    # 判断力不能全指望后面那串「允许/条件/禁止/未知/盲」计数 —— 两支世界能落在完全相同的
    # 立场分布上(silent/refused 是 entry_source 维度,和立场分布是正交的两件事),所以
    # 光靠计数认不出 silent 还是 refused,glosses 不是可省的装饰。
    gloss = {"cards_silent": "(没有卡写入场行)", "cards_refused": "(卡写了,不允许)"}.get(wall, "")
    text = (f"不可买归因:**{wall}**{gloss} ｜ 菜单 落刀 L2 {pct(m.get('l2_knife'))}/L0 {pct(m.get('l0_knife'))}"
            f" · 健康 {pct(m.get('l2_healthy'))}/{pct(m.get('l0_healthy'))}"
            f" · 席位 行业 {cnt(m.get('sector_seats'))}/证据 {cnt(m.get('composite_seats'))}"
            f" ｜ 卡 允许 {c.get('allowed', 0)}/条件 {c.get('conditional', 0)}/禁止 {c.get('prohibited', 0)}"
            f"/未知 {c.get('unknown', 0)}/盲 {c.get('blind', 0)}"
            f" ｜ 早停 {c.get('earlystop', 0)}/满卡 {c.get('full', 0)}"
            f" ｜ 门 data_a 日级 {g.get('data_a_day', 0)}·票级 {g.get('data_a_ticker', 0)}"
            f" · contract {g.get('contract', 0)}"
            f" · redflag {g.get('no_redflag', 0)}(卡禁 {g.get('no_redflag_card_prohibited', 0)})")
    _src(src, "buyability.wall", wall, "_buyability.json", "wall", text)
    _src(src, "buyability.l2_knife", m.get("l2_knife"), "_buyability.json", "menu.l2_knife", text)
    _src(src, "buyability.allowed", c.get("allowed"), "_buyability.json", "cards.allowed", text)
    return "  " + text


def _why_text(buys: dict, *, active: bool = False) -> str:
    """0 买日的「为什么」一行:**早停分桶与门柱分列**(两类原因不搅在一起)。有买日不出。

    active 期措辞改成「为什么没有 ≥OW 卡」:那时「没买」是决策文件说了算,而这一行讲的
    是研究评级为什么没到 ≥OW —— 两件事,不能共用「为什么没买」这个名字(否则 BUY 在场
    的日子会同屏出现「✅ relative BUY 600000」和「为什么没买」)。
    """
    if buys.get("production_n"):
        return ""
    stops = buys.get("early_stop") or {}
    gates = buys.get("gate_fail") or {}
    if not stops and not gates:
        return ""
    bits = [f"早停 {buys.get('n_early', 0)} 张"
            + (f"({'、'.join(f'{k} {v}' for k, v in stops.items())})" if stops else "")]
    bits.append(f"满卡 {buys.get('n_full', 0)} 张 OW三门失守 "
                + ("、".join(f"{k} {v}" for k, v in gates.items()) if gates else "无"))
    # 口径必须自报(复核 I-5):summary 的「OW三门失守分布」由 `gate_histogram` 解析**卡片
    # 自由文本**得来,与这一行的**结构化** `decision_records.gate_states` 是两个生产者、数会不等。
    # T26 把本行注进 summary 的 🧭 仪表盘后两者首次同屏,不打标读者会随机相信一个。
    head = "└ 为什么没有 ≥OW 卡:" if active else "└ 为什么没买:"
    return (head + " · ".join(bits)
            + "(口径:`decision_records.gate_states` 结构化读数;早停卡不写三门段,两类不混算)")


def _pinned_text(facts: dict, src: list[dict], cap: int) -> str:
    pinned = facts["pinned"]
    if not pinned:
        return "无保送持仓(finalists 无 lane=pinned 行)"
    shown = pinned[:cap]
    parts = []
    for row in shown:
        tw = f"·⚠️tripwire {row['tripwire']}" if row["tripwire"] else ""
        text = f"{row['name']} {row['code']} **{row['rating']}**{tw}"
        _src(src, f"pinned.{row['code']}", row["rating"], "_final_ratings.json",
             row["code"], text)
        # I-2:tripwire 数**不是**来自 _final_ratings.json —— 它有自己的来源,必须单独记一行。
        # 合在评级那一行里 = sources 误标,T27 的白名单腿会对着错误的来源比对(绿了也不代表数对)。
        _src(src, f"pinned.{row['code']}.tripwire", row["tripwire"],
             "_tripwire_conflicts.json", f"{row['code']}.all_hits", text)
        parts.append(text)
    tail = f" …(+{len(pinned) - len(shown)} 只见 summary)" if len(pinned) > len(shown) else ""
    return " ; ".join(parts) + tail


def _delta_text(facts: dict, src: list[dict], cap: int) -> str:
    """⑥ 昨日 delta。**整行都要进边表**(fix-1,复核 M-1):第一版只锚 `head` 前缀,于是把
    「同名票评级无变动」改成任意文本、或把 `600188 Hold→Underweight` 改成别的评级,T27 三条腿
    全绿 —— 边表只覆盖半句话就等于半条对账。现在 `delta.changes` 锚**完整**渲染文本。"""
    delta = facts["delta"]
    if not delta.get("prev_date"):
        text = "—(无上一扫描日)"
        _src(src, "delta.changes", 0, "run_health.json", "churn.prev_date=None", text)
        return text
    head = (f"vs {delta['prev_date']} — finalist 重叠 {delta.get('n_repeat')}"
            f"/{delta.get('n_today')}")
    changes = delta.get("changes") or []
    if not changes:
        text = head + " · 同名票评级无变动"
    else:
        shown = changes[:cap] if cap else []
        if not shown:
            text = head + f" · 评级变动 {len(changes)} 只(明细见 summary)"
        else:
            body = "、".join(f"{c['code']} {c['from']}→{c['to']}" for c in shown)
            more = f" 等 {len(changes)} 只" if len(changes) > len(shown) else ""
            text = head + f" · 评级变动:{body}{more}"
    if delta.get("prev_file"):
        # 新分支(run 分区/无 churn/读已发布 run):prev_date 真身来自 manifest.json 的
        # analysis_date,不是 run_health.json 的 churn.prev_date —— 单独锚一行,不与老分支
        # 共用下面那条 `delta.n_repeat` 引用,防止「sources 误标」(I-2 同族)。
        _src(src, "delta.prev_date", delta["prev_date"], delta["prev_file"],
             "analysis_date", text)
    _src(src, "delta.n_repeat", delta.get("n_repeat"), "run_health.json",
         "churn.n_repeat", text)
    _src(src, "delta.changes", len(changes), "_final_ratings.json",
         f"diff vs {delta['prev_date']}/_final_ratings.json", text)
    return text


# ────────────────────────────── 组装 / 落盘 ──────────────────────────────

#: 超预算时的**确定性**降级梯度(pinned 展示上限, 评级变动展示上限)。
#: 顺序即优先级:先砍 ⑥ 的逐只变动明细,再砍 ④ 的持仓条目(持仓是决策件,最后才动)。
_FIT_LADDER = ((99, 6), (99, 3), (99, 0), (16, 0), (12, 0), (8, 0), (6, 0), (4, 0),
               (3, 0), (2, 0), (1, 0))


def build(scan_dir: Path | str, *, analysis_date: str | None = None,
          run_folder: str | None = None, decision: dict | None = None,
          scan_root: Path | None = None, facts: dict | None = None) -> dict:
    """→ `{"markdown", "sources", "facts", "n_bytes"}`。同输入 byte 稳定。"""
    facts = facts or collect_facts(scan_dir, analysis_date=analysis_date,
                                   run_folder=run_folder, decision=decision,
                                   scan_root=scan_root)
    lines, sources = _sections(facts, pinned_cap=_FIT_LADDER[0][0],
                               delta_cap=_FIT_LADDER[0][1])
    md = "\n".join(lines) + "\n"
    for pinned_cap, delta_cap in _FIT_LADDER[1:]:
        if len(md.encode("utf-8")) <= MAX_BYTES:
            break
        lines, sources = _sections(facts, pinned_cap=pinned_cap, delta_cap=delta_cap)
        md = "\n".join(lines) + "\n"
    return {"markdown": md, "sources": sources, "facts": facts,
            "n_bytes": len(md.encode("utf-8"))}


#: 仪表盘块 = brief 的 ①②③④ 四节(T26 §C2「仪表盘 = brief 同源渲染」)。
#: 切片锚用渲染出来的节标记本身,不另维护一份节名表(两处各写一遍必走漂)。
_DASHBOARD_FROM, _DASHBOARD_TO = "**① 市场**", "**⑤ 风险哨**"


def dashboard_block(built: dict) -> str:
    """brief 成品 → summary 的 🧭 仪表盘正文(①②③④,逐字同源)。

    **同源**是硬要求而不是修辞:T27 的「brief↔summary BUY 一致」lint 若两边各渲染一次,
    对的就只是两个渲染器,不是两份事实。这里直接从 brief 的 markdown 里切 ①→⑤ 之间的段。
    """
    lines = built["markdown"].splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith(_DASHBOARD_FROM))
        stop = next(i for i, ln in enumerate(lines) if ln.startswith(_DASHBOARD_TO))
    except StopIteration:
        return ""
    return "\n".join(lines[start:stop]).strip()


def write(scan_dir: Path | str, out_dir: Path | str, *, built: dict | None = None,
          **kwargs) -> Path:
    """brief.md → `out_dir`;`sources` 边表 → staging `_brief_sources.json`(供 T27 lint)。

    `built` 显式传入时**不重算** —— 调用方(publisher)同一份成品既要落 brief.md 又要
    切仪表盘注回 summary,重算两次等于给「两边可能不一致」开一道口子。
    """
    out = built or build(scan_dir, **kwargs)
    target = Path(out_dir) / BRIEF_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(out["markdown"], encoding="utf-8")
    payload = {"schema_version": SCHEMA_VERSION, "date": out["facts"]["date"],
               "run_folder": out["facts"]["run_folder"], "n_bytes": out["n_bytes"],
               "max_bytes": MAX_BYTES, "rows": out["sources"]}
    (Path(scan_dir) / SOURCES_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def safe_write(scan_dir: Path | str, out_dir: Path | str, **kwargs) -> Path | None:
    """brief 失败不得阻断发布(summary 仍是完整产物;缺 brief 由 T27 lint 报 fail)。"""
    try:
        return write(scan_dir, out_dir, **kwargs)
    except Exception as exc:  # noqa: BLE001
        print(f"[brief] 生成失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def safe_publish(scan_dir: Path | str, out_dir: Path | str, summary_path: Path | str,
                 **kwargs) -> Path | None:
    """一次算、两处用:落 brief.md + 把 ①②③④ 注回 summary 的 🧭 managed 块。

    E3b(task-2.4)起**同一次注入**还回填另两个 managed 块(组合视角 / 仓位 overlay):
    它们与仪表盘同病同治 —— BUY 数出自 `_relative_buy_decision.json`,而那份文件由
    writer-1 在 `publisher.py:325` 才写,比 `build_summary`(`:305`)晚一站。本函数跑在
    `publisher.py:387`(决策文件已在盘),是全流程里第一个能同时看到"报告"和"决策"的点,
    所以三块必须在这里一起回填 —— 分两次注入 = 两个时刻 = 又一个"两边可能不一致"的口子。
    影子期报告里根本没有那两个标记,`inject_deferred_blocks` 因此是结构性 no-op(parity)。

    失败不阻断发布(summary 保留占位文案,自己会说「注入未跑」;缺 brief 由 T27 lint 报 fail)。
    """
    try:
        from autoresearch.scan.relative_buy import load_decision
        from autoresearch.scan.report_sections import (
            inject_dashboard,
            inject_deferred_blocks,
        )
        built = build(scan_dir, **kwargs)
        target = write(scan_dir, out_dir, built=built)
        summary = Path(summary_path)
        if summary.exists():
            text = inject_dashboard(summary.read_text(encoding="utf-8"),
                                    dashboard_block(built))
            # 盘读、不现算:brief ③ 印的与这里注入的必须是**同一份**决策文件
            # (现算会再造一个"记账与发布分家"的写者)。
            text = inject_deferred_blocks(text, scan_dir,
                                          load_decision(scan_dir,
                                                        date=built["facts"].get("date")))
            summary.write_text(text, encoding="utf-8")
        return target
    except Exception as exc:  # noqa: BLE001
        print(f"[brief] 发布失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="确定性 brief.md 生成器(零 LLM)")
    ap.add_argument("date", help="分析日(YYYY-MM-DD)或 scan 目录")
    ap.add_argument("--out", default=None, help="落盘目录(缺省只打印)")
    ap.add_argument("--run-folder", default="")
    args = ap.parse_args(argv)
    explicit = Path(args.date)
    scan = explicit if explicit.exists() else ws.scan_root() / args.date
    out = build(scan, run_folder=args.run_folder)
    if args.out:
        write(scan, args.out, run_folder=args.run_folder)
    print(out["markdown"])
    print(f"[brief] {out['n_bytes']}B / 预算 {MAX_BYTES}B · sources {len(out['sources'])} 行",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
