#!/usr/bin/env python3
"""`brief.md` 确定性生成器(Wave12 T25 / 批C C1)—— 报告双层的**核心速读**层。

design: `docs/specs/2026-08-08-wave12-seven-topics-design.md` §C1 + §E5(用户 2026-08-08 裁定)。

## 为什么是模板而不是 agent(R-C1 已裁)

brief 的内容全是**结构化结论、计数、评级、tripwire 与账本状态**。让 LLM 再压缩一遍只会
新增三样东西:编数面、对账 lint、一次调用成本——却不增加任何决策信息。所以本模块
**零 LLM、零联网、只读结构化产物**,同一 run 重放 byte 稳定(无时间戳、无随机、无无序遍历)。
将来若要润色,只能生成非权威 commentary,**不得改写 BUY、数字或风险结论**。

## 七节骨架(硬预算 ≤3,000 字节)

  ① 市场一句(regime + 温度 + 两尺分歧日提示 R-X1)
  ② 漏斗一行(L0→finalists)
  ③ **BUY 结论区**——影子期**双行**:旧生产结论(近期恒 0 买)+「影子 relative BUY」行
     (显式标**非正式·不执行**);activate 后换成正式 BUY 行。第三行常驻
     **「旧 OW 基率」分账行**,与 relative 账**分列并置、不连成趋势线**(定义断层:
     决策对象/人口/尺三处都不同,见 `docs/research/2026-08-09-e6-replay-baseline.md` §4)。
  ④ 持仓动作表(pinned 逐票:评级 + tripwire)
  ⑤ 风险哨(自检 fail/warn + 降级字段 + 卡覆盖)
  ⑥ 昨日 delta(finalist 重叠 + 评级变动)
  ⑦ 欠账红行(待裁决提案 / 未决反馈)

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
import sys
from pathlib import Path

from autoresearch.common.ruler import MAIN_RULER, REL_MARKET, REL_SECTOR
from autoresearch.scan.relative_buy import DECISION_FILENAME, MODE_SHADOW

SCHEMA_VERSION = 1
#: 硬预算(字节)。T27 的 lint 用同一个常量量,不另写一份字面量。
MAX_BYTES = 3000
BRIEF_FILENAME = "brief.md"
SOURCES_FILENAME = "_brief_sources.json"

#: 输入白名单 —— 生成器只准从这些结构化产物取数,**禁读 `details/` 全文与 `trace/` 大文件**。
#: 三个非文件项是**确定性派生**(零 LLM),各自注明真身:
#:   `buy_ledger`      = `learning.buy_ledger.roll(scan_root)` over `context/scan/*/retro/attribution.csv`
#:   `temperature.csv` = `context/learning/temperature.csv`(prelude 增量落盘)
#:   `feedback_store`  = `context/knowledge/`(提案/未决反馈看板)
INPUT_WHITELIST = (
    "meta.json",
    "finalists.csv",
    "_final_ratings.json",
    "decision_records.json",
    "run_mode.json",
    "run_health.json",
    "gate_fires.csv",
    "market_view.md",
    DECISION_FILENAME,
    "retro/attribution.csv",
    "temperature.csv",
    "buy_ledger",
    "feedback_store",
)

#: 相对 BUY 区禁词 —— 出现任意一个即语义越权(它从不承诺绝对方向)。
BANNED_RELATIVE_PHRASES = ("预计上涨", "预计绝对上涨", "预期上涨", "看涨", "必涨", "稳赚")
#: 绝对 gap 为负时**固定**用这句话,不许换措辞。
WEAK_MARKET_PHRASE = "弱市相对最优"

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

def _two_ruler_divergence(scan_root: Path, date: str) -> dict | None:
    """R-X1 两尺分歧日提示:**严格早于今日**的最近一个已成熟日,若市场等权
    `gap_c1_o2`(隔夜,主尺)与 `fwd_2_oc`(含日内)符号相反 → 一句话提示。

    今日自己的两尺读数在决策当晚**根本不存在**(要 T+2 才成熟),所以这里只报最近一个
    已成熟日,并把日期写进正文——不写死「昨日」,也不假装量的是今天(缺输入 → 不出行)。
    """
    if not scan_root.is_dir():
        return None
    days = sorted((p for p in scan_root.iterdir()
                   if p.is_dir() and p.name[:2] == "20" and p.name < date),
                  reverse=True)
    for day in days:
        attr = day / "retro" / "attribution.csv"
        if not attr.exists():
            continue
        try:
            import pandas as pd
            frame = pd.read_csv(attr, usecols=lambda c: c in (MAIN_RULER, "fwd_2_oc"))
        except Exception:  # noqa: BLE001 — 坏表按无读数处理,绝不阻断出报
            continue
        if MAIN_RULER not in frame.columns or "fwd_2_oc" not in frame.columns:
            continue
        import pandas as pd
        gap = pd.to_numeric(frame[MAIN_RULER], errors="coerce").dropna()
        oc = pd.to_numeric(frame["fwd_2_oc"], errors="coerce").dropna()
        if not len(gap) or not len(oc):
            continue
        g, o = round(float(gap.mean()), 6), round(float(oc.mean()), 6)
        if (g > 0) == (o > 0):
            return None                     # 同向 = 无分歧,不占字节
        return {"date": day.name, "gap": g, "oc": o, "n": int(min(len(gap), len(oc)))}
    return None


def _ow_base_rate(scan_root: Path) -> dict | None:
    """旧 OW 买单账基率(≥Overweight 绝对门,主尺 T+2)。**样本随 `buy_ledger` 自动更新**,
    不写死 9 笔 —— 下一单进账这行自己会变。缺依赖 → None(presence-gated)。"""
    try:
        from autoresearch.learning.buy_ledger import rating_base_rates, roll
        ledger = roll(scan_root)
        rows = [r for r in rating_base_rates(ledger) if r["rating"] in _BUY_RATINGS]
    except Exception:  # noqa: BLE001 — 账本层可选,坏了不阻发布
        return None
    if not rows:
        return {"n": 0, "n_realized": 0, "win2": None, "mean2": None}
    n = sum(r["n"] for r in rows)
    n_realized = sum(r["n_realized"] for r in rows)
    wins = [r for r in rows if r["win2"] is not None]
    win2 = (sum(r["win2"] * r["n_realized"] for r in wins) / n_realized) \
        if (wins and n_realized) else None
    means = [r for r in rows if r["mean2"] is not None]
    mean2 = (sum(r["mean2"] * r["n_realized"] for r in means) / n_realized) \
        if (means and n_realized) else None
    return {"n": n, "n_realized": n_realized,
            "win2": None if win2 is None else round(win2, 4),
            "mean2": None if mean2 is None else round(mean2, 6)}


def _debt(scan_root: Path) -> dict:
    """⑦ 欠账:待裁决提案数 + 未决反馈数(feedback_store;缺 → 0,不猜)。"""
    out = {"proposals": 0, "open_feedback": 0}
    with contextlib.suppress(Exception):
        from autoresearch.learning.feedback_store import proposals_nag_lines
        out["proposals"] = len(proposals_nag_lines())
    with contextlib.suppress(Exception):
        import autoresearch.learning.feedback_store as fs
        out["open_feedback"] = sum(1 for f in fs._read_jsonl(fs._FEEDBACK)
                                   if f.get("status") == "open")
    return out


def _gate_counts(scan_dir: Path) -> dict:
    """self_review 落的 `gate_fires.csv` → fail/warn 计数(`dump_ow_gate_fires` 追加的
    binding 行没有 severity 列,不计入——那是门审计账,不是自检结论)。"""
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


def _tripwire_counts(scan_dir: Path) -> dict[str, int]:
    doc = _json(scan_dir / "_tripwire_conflicts.json")
    if not isinstance(doc, dict):
        return {}
    out: dict[str, int] = {}
    for code, entry in doc.items():
        hits = entry.get("all_hits") if isinstance(entry, dict) else None
        out[_code6(code)] = len(hits) if isinstance(hits, list) else 1
    return out


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
    changes: list[dict] = []
    if prev_date:
        prev = _json(root / str(prev_date) / "_final_ratings.json") or {}
        prev_by_code = {_code6(k): v for k, v in prev.items()}
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
            "divergence": _two_ruler_divergence(root, date),
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
        "ow_base": _ow_base_rate(root),
        "pinned": pinned,
        "risk": {**_gate_counts(scan),
                 "degraded": list(health.get("degraded_fields") or []),
                 "cards": counts.get("cards"), "finalists": len(finals)},
        "delta": {"prev_date": prev_date, "n_repeat": churn.get("n_repeat"),
                  "n_today": churn.get("n_today"), "changes": changes},
        "debt": _debt(root),
    }


def _relative_facts(decision: dict | None) -> dict:
    """`_relative_buy_decision.json` → ③ 影子/正式 BUY 行需要的字段。缺文件 → `present=False`
    (**显式说「未生成」**,不静默省行——静默会被读成「今天没有相对 BUY」)。"""
    if not isinstance(decision, dict):
        return {"present": False, "mode": None}
    bench = decision.get("benchmark") or {}
    market, sector = bench.get("market") or {}, bench.get("sector") or {}
    buys = decision.get("buys") or []
    by_code = {row.get("code"): row for row in (decision.get("candidates") or [])
               if isinstance(row, dict)}
    top = by_code.get(buys[0]["code"]) if buys else None
    gap = (top or {}).get("expected_abs_gap") or {}
    hard_reject = sum(1 for row in (decision.get("candidates") or [])
                      if isinstance(row, dict) and not row.get("eligible"))
    reasons = [f"{r.get('reason')}×{r.get('n')}"
               for r in (decision.get("blocked_reasons") or []) if isinstance(r, dict)]
    return {
        "present": True,
        "mode": decision.get("mode"),
        "rule_version": decision.get("rule_version"),
        "blocked": bool(decision.get("blocked")),
        "blocked_reasons": reasons,
        "code": (buys[0]["code"] if buys else None),
        "basis": (buys[0].get("basis") if buys else None),
        "name": (top or {}).get("name"),
        "rank": (top or {}).get("rank"),
        "score": (top or {}).get("relative_decision_score"),
        "research_rating": (top or {}).get("research_rating"),
        "n_eligible": (decision.get("counts") or {}).get("eligible"),
        "n_candidates": (decision.get("counts") or {}).get("candidates"),
        "market_column": market.get("column") or REL_MARKET,
        "market_n": market.get("n"),
        "sector_column": sector.get("column") or REL_SECTOR,
        "n_sectors": market.get("n_sectors") or sector.get("n_sectors"),
        "abs_gap_status": gap.get("status", "UNMEASURED"),
        "abs_gap_value": gap.get("value"),
        "abs_gap_n": gap.get("n", 0),
        "hard_reject": hard_reject,
        "ruler": decision.get("ruler") or MAIN_RULER,
    }


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
    dv = mk.get("divergence")
    if dv:
        bits.append(_src(src, "market.divergence", dv["date"], "retro/attribution.csv",
                         f"{dv['date']}:mean({MAIN_RULER}) vs mean(fwd_2_oc)",
                         f"⚖️ 两尺分歧({dv['date']} 已成熟):隔夜 {MAIN_RULER} "
                         f"{_pct(dv['gap'])} vs 含日内 fwd_2_oc {_pct(dv['oc'])} 符号相反"
                         f"——日内那段不在本系统授权内"))
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
    risk_text = (f"自检 fail {rk['n_fail']} / warn {rk['n_warn']}"
                 f" · 降级 {'、'.join(rk['degraded']) if rk['degraded'] else '无'}"
                 f" · 卡 {rk.get('cards') if rk.get('cards') is not None else '—'}"
                 f"/{rk['finalists']}")
    _src(src, "risk.n_fail", rk["n_fail"], "gate_fires.csv", "severity==fail", risk_text)
    _src(src, "risk.n_warn", rk["n_warn"], "gate_fires.csv", "severity==warn", risk_text)
    _src(src, "risk.degraded", ",".join(rk["degraded"]), "run_health.json",
         "degraded_fields", risk_text)
    out.append("**⑤ 风险哨**:" + risk_text)

    # ⑥ 昨日 delta
    out.append("**⑥ 昨日 delta**:" + _delta_text(facts, src, delta_cap))

    # ⑦ 欠账
    debt = facts["debt"]
    debt_text = f"待裁决提案 {debt['proposals']} · 未决反馈 {debt['open_feedback']}"
    _src(src, "debt.proposals", debt["proposals"], "feedback_store", "proposals(open)",
         debt_text)
    _src(src, "debt.open_feedback", debt["open_feedback"], "feedback_store",
         "feedback(status=open)", debt_text)
    out.append("**⑦ 欠账**:" + debt_text)

    out.append("")
    out.append(f"_确定性生成(零 LLM);主尺 {facts['ruler']};详细版见 `summary.md`。"
               f"仅供研究,非投资建议。_")
    return out, src


def _buy_lines(facts: dict, src: list[dict]) -> list[str]:
    lines: list[str] = []
    buys = facts["buys"]
    dist = "、".join(f"{r} {buys['dist'][r]}" for r in _RATING_ORDER if r in buys["dist"])
    prod_text = (f"**生产 BUY {buys['production_n']} 只**(旧绝对门 ≥Overweight;"
                 f"评级分布 {dist or '—'};run_mode {buys['run_mode']})")
    _src(src, "buys.production_n", buys["production_n"], "_final_ratings.json",
         "count(rating in Buy/Overweight)", prod_text)
    _src(src, "buys.run_mode", buys["run_mode"], "run_mode.json", "mode", prod_text)
    lines.append("- " + prod_text)
    why = _why_text(buys)
    if why:
        _src(src, "buys.why_no_buy", buys.get("n_early"), "decision_records.json",
             "records[].early_stop.reason + gate_states==FAIL", why)
        lines.append("  " + why)

    rel = facts["relative"]
    tag = ("🕶 **影子 relative BUY(非正式·不执行)**"
           if rel.get("mode", MODE_SHADOW) != "active" else "✅ **relative BUY**")
    if not rel.get("present"):
        lines.append(f"- {tag}:—(`{DECISION_FILENAME}` 未生成 —— 缺证据不等于没候选)")
        return lines + [_ow_line(facts, src)]
    if rel.get("blocked"):
        why = "、".join(rel.get("blocked_reasons") or []) or "无分桶"
        text = (f"{tag}:**BLOCKED**(全部候选被硬资格否决:{why};"
                f"候选 {rel.get('n_candidates')} / 合格 {rel.get('n_eligible')})")
        _src(src, "relative.blocked", True, DECISION_FILENAME, "blocked", text)
        lines.append("- " + text)
        return lines + [_ow_line(facts, src)]

    gap_txt = _abs_gap_text(rel)
    text = (f"{tag}:{rel.get('name') or '—'} {rel.get('code') or '—'}"
            f" · basis={rel.get('basis')} · 合格内 #{rel.get('rank')}"
            f"/{rel.get('n_eligible')}(候选 {rel.get('n_candidates')})"
            f" · 卡面 {rel.get('research_rating') or '—'}"
            f" · 基准 {rel.get('market_column')}(L0 可交易 {rel.get('market_n')} 等权)"
            f"+{rel.get('sector_column')}"
            f" · 绝对 gap {gap_txt} · 硬否决 {rel.get('hard_reject')}"
            f" · 主尺 {rel.get('ruler')}"
            f" —— 只承诺「当日全集内相对最优」,**不承诺绝对收益为正**")
    _src(src, "relative.code", rel.get("code"), DECISION_FILENAME, "buys[0].code", text)
    _src(src, "relative.rank", rel.get("rank"), DECISION_FILENAME,
         "candidates[code].rank", text)
    _src(src, "relative.market_n", rel.get("market_n"), DECISION_FILENAME,
         "benchmark.market.n", text)
    _src(src, "relative.abs_gap_status", rel.get("abs_gap_status"), DECISION_FILENAME,
         "candidates[code].expected_abs_gap.status", text)
    lines.append("- " + text)
    return lines + [_ow_line(facts, src)]


def _why_text(buys: dict) -> str:
    """0 买日的「为什么」一行:**早停分桶与门柱分列**(两类原因不搅在一起)。有买日不出。"""
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
    return "└ 为什么没买:" + " · ".join(bits) + "(早停卡不写三门段,两类不混算)"


def _abs_gap_text(rel: dict) -> str:
    """绝对 gap 口径。**为负 → 固定 `弱市相对最优`**(语义纪律②,不许换措辞)。"""
    status, value = rel.get("abs_gap_status"), rel.get("abs_gap_value")
    if value is None:
        return f"{status}(n={rel.get('abs_gap_n', 0)},样本不足禁止拍数)"
    if value < 0:
        return (f"{_pct(value)}(n={rel.get('abs_gap_n', 0)})"
                f" → **{WEAK_MARKET_PHRASE}**(相对 BUY 从不承诺绝对收益为正)")
    return f"{_pct(value)}(n={rel.get('abs_gap_n', 0)})"


def _ow_line(facts: dict, src: list[dict]) -> str:
    """旧 OW 基率**分账行**(常驻)。与上面的 relative 账**分列并置、不连成趋势线**:
    决策对象(绝对『值得买』vs 相对『最值得买』)、人口(≥OW 的卡 vs 当日全部 L4 候选)、
    尺(旧行跨越换尺、口径混存 vs 本账钉死主尺)三处都不同。"""
    head = "📊 **旧 OW 基率(分账·定义断层·不连线)**:"
    ow = facts.get("ow_base")
    if not ow:
        return "- " + head + "—(buy_ledger 不可用)"
    win = "—" if ow["win2"] is None else f"{ow['win2']:.0%}"
    mean = "—" if ow["mean2"] is None else _pct(ow["mean2"])
    text = (f"{head}{ow['n']} 笔(已实现 {ow['n_realized']})"
            f"· T+2 胜率 {win} · 均值 {mean}")
    _src(src, "ow_base.n", ow["n"], "buy_ledger", "roll().shape[0]", text)
    _src(src, "ow_base.win2", ow["win2"], "buy_ledger",
         "rating_base_rates().win2", text)
    return "- " + text


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
        parts.append(text)
    tail = f" …(+{len(pinned) - len(shown)} 只见 summary)" if len(pinned) > len(shown) else ""
    return " ; ".join(parts) + tail


def _delta_text(facts: dict, src: list[dict], cap: int) -> str:
    delta = facts["delta"]
    if not delta.get("prev_date"):
        return "—(无上一扫描日)"
    head = (f"vs {delta['prev_date']} — finalist 重叠 {delta.get('n_repeat')}"
            f"/{delta.get('n_today')}")
    _src(src, "delta.n_repeat", delta.get("n_repeat"), "run_health.json",
         "churn.n_repeat", head)
    changes = delta.get("changes") or []
    if not changes:
        return head + " · 同名票评级无变动"
    shown = changes[:cap] if cap else []
    if not shown:
        return head + f" · 评级变动 {len(changes)} 只(明细见 summary)"
    body = "、".join(f"{c['code']} {c['from']}→{c['to']}" for c in shown)
    more = f" 等 {len(changes)} 只" if len(changes) > len(shown) else ""
    return head + f" · 评级变动:{body}{more}"


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


def write(scan_dir: Path | str, out_dir: Path | str, **kwargs) -> Path:
    """brief.md → `out_dir`;`sources` 边表 → staging `_brief_sources.json`(供 T27 lint)。"""
    out = build(scan_dir, **kwargs)
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="确定性 brief.md 生成器(零 LLM)")
    ap.add_argument("date", help="分析日(YYYY-MM-DD)或 scan 目录")
    ap.add_argument("--out", default=None, help="落盘目录(缺省只打印)")
    ap.add_argument("--run-folder", default="")
    args = ap.parse_args(argv)
    explicit = Path(args.date)
    scan = explicit if explicit.exists() else Path("context/scan") / args.date
    out = build(scan, run_folder=args.run_folder)
    if args.out:
        write(scan, args.out, run_folder=args.run_folder)
    print(out["markdown"])
    print(f"[brief] {out['n_bytes']}B / 预算 {MAX_BYTES}B · sources {len(out['sources'])} 行",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
