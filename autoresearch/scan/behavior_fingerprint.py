#!/usr/bin/env python3
"""零 LLM 的行为指纹(2026-10-03 A6):这一场研究层「长什么样」,以及它和最近的常态差多少。

只读本 run 已在盘的产物:决策记录 / `_final_ratings.json`(评级)、`_early_stop.json`(早停与停因)、
`_relative_buy_decision.json` 的 `card_context`(入场立场、EV、R:R)、`details/*.md`(卡长)、
`_l4_intel_*.md`(情报引用的链接数)。每类 agent 的 token / 轮次读数在 `run_drift.usage_shape`,
这里不重复。

为什么要有它:09-22 前后评级分布从 UW 居多变成 Hold 居多,卡与卡之间的 EV、R:R 几乎没有方差
(复盘稿 §3.6「输出常数化」),同一周里契约改动、菜单换档、模型换代三件事叠在一起 —— 没有任何
监控会对「分布变了」或「方差消失」报警。本模块只产事实与标签,不拥有任何门。

偏离规则(`observability.fingerprint`):占比类指标与基线中位相差 ≥ `share_delta`;正值标量
(卡长、链接数、离散度)与基线中位之比 ≥ `ratio_warn` 或 ≤ 1/`ratio_warn`(后者就是常数化)。
基线 = 最近 `window` 场有指纹的已发布真实扫描;不足 `min_runs` 场 → NO_BASELINE。
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_POLICY = {"window": 10, "min_runs": 3, "share_delta": 0.25, "ratio_warn": 1.5}

#: 占比类指标(值域 0–1,按绝对差比较)与它们在报告里的名字。
SHARE_LABELS = {
    "rating_ow_plus": "增持及以上占比",
    "rating_hold": "持有占比",
    "rating_uw_minus": "减持及以下占比",
    "early_stop": "早停率",
    "entry_allowed": "入场允许占比",
    "entry_conditional": "入场条件占比",
    "entry_prohibited": "入场禁止占比",
}
#: 正值标量(按比值比较;离散度掉到 1/ratio_warn 以下 = 常数化)。`ev_median` 可正可负,只记不比。
SCALAR_LABELS = {
    "card_bytes_median": "卡长中位",
    "intel_urls_median": "情报链接数中位",
    "ev_std": "EV 离散度",
    "rr_median": "R:R 中位",
    "rr_std": "R:R 离散度",
}
_URL_RE = re.compile(r"https?://")


def policy(cfg: dict | None = None) -> dict:
    """`observability.fingerprint` → 校验后的完整组;非法即 ValueError。"""
    from autoresearch.scan.user_config import knob

    raw = knob("observability", "fingerprint", None, None, cfg) or {}
    merged = {**DEFAULT_POLICY, **(raw if isinstance(raw, dict) else {})}
    window, min_runs = merged["window"], merged["min_runs"]
    share_delta, ratio_warn = merged["share_delta"], merged["ratio_warn"]
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (window, min_runs)) \
            or min_runs > window:
        raise ValueError("observability.fingerprint.window / min_runs 须为正整数且 min_runs <= window")
    if not 0 < float(share_delta) < 1 or not float(ratio_warn) > 1:
        raise ValueError("observability.fingerprint 须满足 0 < share_delta < 1 且 ratio_warn > 1")
    return {"window": window, "min_runs": min_runs, "share_delta": float(share_delta),
            "ratio_warn": float(ratio_warn)}


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


_PCT = r"([+\-−]?\d+(?:\.\d+)?)\s*%"
_EV_PCT_RE = re.compile(r"EV\s*[≈=:：]?\s*" + _PCT)
_ANY_PCT_RE = re.compile(_PCT)
_RR_RE = re.compile(r"^\s*≈?\s*(\d+(?:\.\d+)?)\s*(?:/\s*(\d+(?:\.\d+)?))?\s*$")


def _ev_pct(text) -> float | None:
    """卡面 EV 目标格(散文)→ 预期收益百分比:`EV ≈ x%` 优先;否则全格恰好一个百分比才取;
    多个百分比又没标 EV(区间)→ None,不猜。真实写法:`315.4(−0.3%)·带 310–322`、
    `302–308(−0.8%~+1.2%,EV ≈ −0.1%)`(复审 M-1:此前直接 float() 散文,10/10 读不出)。"""
    if not isinstance(text, str):
        return _num(text)
    found = _EV_PCT_RE.search(text)
    if found:
        return float(found.group(1).replace("−", "-"))
    values = _ANY_PCT_RE.findall(text)
    return float(values[0].replace("−", "-")) if len(values) == 1 else None


def _rr(text) -> float | None:
    """R:R 格:`0.8/1` → 0.8,`1.0` → 1.0;其它写法 → None。"""
    if not isinstance(text, str):
        return _num(text)
    found = _RR_RE.match(text)
    if not found:
        return None
    reward, risk = float(found.group(1)), found.group(2)
    return reward / float(risk) if risk and float(risk) > 0 else (None if risk else reward)


def _median(values: list[float]):
    if not values:
        return None
    value = statistics.median(values)
    return int(value) if float(value).is_integer() else round(float(value), 4)


def _ratings(scan: Path) -> dict[str, str]:
    from autoresearch.scan.decision_read_model import read_final_ratings

    try:
        return {str(code): str(rating) for code, rating in read_final_ratings(scan).items()}
    except Exception:  # noqa: BLE001 - 读不动的事实书 = 没量到,不猜
        return {}


def fingerprint(scan_dir: Path | str) -> dict:
    """本场指纹。没有任何卡 → `measured=False`;某项输入缺席 → 该项 None(没看过 ≠ 零)。"""
    scan = Path(scan_dir)
    ratings = _ratings(scan)
    n = len(ratings)
    if not n:
        return {"schema_version": SCHEMA_VERSION, "measured": False, "n_cards": 0,
                "shares": {}, "stop_reasons": {}, "scalars": {}}
    counts = Counter(ratings.values())
    shares: dict[str, float | None] = {
        "rating_ow_plus": (counts["Buy"] + counts["Overweight"]) / n,
        "rating_hold": counts["Hold"] / n,
        "rating_uw_minus": (counts["Underweight"] + counts["Sell"]) / n,
    }
    early = _json(scan / "_early_stop.json")
    stop_reasons: dict[str, int] = {}
    if isinstance(early, dict):
        stopped = {code: doc for code, doc in early.items() if code in ratings}
        shares["early_stop"] = len(stopped) / n
        stop_reasons = dict(sorted(Counter(
            str((doc or {}).get("reason") or "—") if isinstance(doc, dict) else "—"
            for doc in stopped.values()).items()))
    else:
        shares["early_stop"] = None
    decision = _json(scan / "_relative_buy_decision.json")
    contexts = [c.get("card_context") for c in (decision or {}).get("candidates") or []
                if isinstance(c, dict) and isinstance(c.get("card_context"), dict)]
    for stance in ("ALLOWED", "CONDITIONAL", "PROHIBITED"):
        shares[f"entry_{stance.lower()}"] = (
            sum(1 for ctx in contexts if ctx.get("entry_stance") == stance) / len(contexts)
            if contexts else None)
    ev = [v for v in (_ev_pct(ctx.get("ev_target")) for ctx in contexts) if v is not None]
    rr = [v for v in (_rr(ctx.get("rr")) for ctx in contexts) if v is not None]
    cards = sorted((scan / "details").glob("*.md")) if (scan / "details").is_dir() else []
    intel = sorted(scan.glob("_l4_intel_*.md"))
    scalars = {
        "card_bytes_median": _median([p.stat().st_size for p in cards]),
        "intel_urls_median": _median([len(_URL_RE.findall(p.read_text(encoding="utf-8",
                                                                       errors="replace")))
                                      for p in intel]),
        "ev_median": _median(ev),
        "ev_std": round(statistics.pstdev(ev), 4) if len(ev) >= 2 else None,
        "rr_median": _median(rr),
        "rr_std": round(statistics.pstdev(rr), 4) if len(rr) >= 2 else None,
    }
    return {"schema_version": SCHEMA_VERSION, "measured": True, "n_cards": n,
            "shares": {k: (None if v is None else round(v, 4)) for k, v in shares.items()},
            "stop_reasons": stop_reasons, "scalars": scalars}


def compare(current: dict, history: list[dict], rules: dict | None = None) -> dict:
    """本场指纹 vs 最近 `window` 场已发布真实扫描的指纹;返回 `{status, n_baseline, deviations}`。"""
    rules = {**DEFAULT_POLICY, **(rules or {})}
    window, min_runs = int(rules["window"]), int(rules["min_runs"])
    base = {"n_baseline": 0, "deviations": []}
    if not (current or {}).get("measured"):
        return {"status": "UNMEASURED", **base}
    baseline = [row["fingerprint"] for row in history
                if row.get("real_scan") is True and isinstance(row.get("fingerprint"), dict)
                and row["fingerprint"].get("measured")][-window:]
    if len(baseline) < min_runs:
        return {"status": "NO_BASELINE", **base, "n_baseline": len(baseline)}
    deviations: list[dict] = []
    for metric in SHARE_LABELS:
        value = (current.get("shares") or {}).get(metric)
        past = [b["shares"][metric] for b in baseline
                if (b.get("shares") or {}).get(metric) is not None]
        if value is None or len(past) < min_runs:
            continue
        median = _median(past)
        if abs(value - median) >= float(rules["share_delta"]):
            deviations.append({"metric": metric, "kind": "share", "value": value,
                               "baseline_median": median, "delta": round(value - median, 4)})
    for metric in SCALAR_LABELS:
        value = (current.get("scalars") or {}).get(metric)
        past = [b["scalars"][metric] for b in baseline
                if (b.get("scalars") or {}).get(metric) is not None]
        if value is None or len(past) < min_runs:
            continue
        median = _median(past)
        if not median or median <= 0:
            continue
        ratio = round(float(value) / float(median), 2)
        if ratio >= float(rules["ratio_warn"]) or ratio <= 1 / float(rules["ratio_warn"]):
            deviations.append({"metric": metric, "kind": "ratio", "value": value,
                               "baseline_median": median, "ratio": ratio})
    return {"status": "DEVIATION" if deviations else "NORMAL", "n_baseline": len(baseline),
            "deviations": deviations}


def _label(metric: str) -> str:
    return SHARE_LABELS.get(metric) or SCALAR_LABELS.get(metric) or metric


def summary_fragment(observation: dict) -> str:
    """summary 紧凑行里的一段:`行为:…`;没量到印 `—`。"""
    block = observation.get("behavior") or {}
    status = block.get("status") or "UNMEASURED"
    if status == "UNMEASURED":
        return "行为:—"
    deviations = block.get("deviations") or []
    if not deviations:
        return f"行为:{status}"
    names = "、".join(_label(d["metric"]) for d in deviations[:3])
    return f"行为:{status}({len(deviations)} 项:{names}{'…' if len(deviations) > 3 else ''})"


def detail_lines(observation: dict) -> list[str]:
    """appendix E:本场指纹一行 + 逐项偏离。"""
    fp = observation.get("fingerprint")
    block = observation.get("behavior") or {}
    if not isinstance(fp, dict):
        return ["- 行为指纹:—(未计量)"]          # 观测腿没跑到 / 失败:不替它编原因(复审 M-4)
    if not fp.get("measured"):
        return ["- 行为指纹:—(本场没有可读的卡)"]
    shares, scalars = fp.get("shares") or {}, fp.get("scalars") or {}

    def fmt(value, digits=2):
        return "—" if value is None else f"{value:.{digits}f}"

    head = (f"- 行为指纹:{block.get('status') or 'UNMEASURED'}"
            f"(近 {block.get('n_baseline', 0)} 场已发布真实扫描)· 卡 {fp.get('n_cards')} 张"
            f" · 持有 {fmt(shares.get('rating_hold'))} · 早停 {fmt(shares.get('early_stop'))}"
            f" · 入场禁止 {fmt(shares.get('entry_prohibited'))}"
            f" · EV 离散度 {fmt(scalars.get('ev_std'), 3)} · R:R 离散度 {fmt(scalars.get('rr_std'), 3)}")
    lines = [head]
    for d in block.get("deviations") or []:
        if d["kind"] == "share":
            lines.append(f"  - {_label(d['metric'])}:{d['value']:.2f} vs 中位 {d['baseline_median']:.2f}"
                         f"(差 {d['delta']:+.2f})")
        else:
            lines.append(f"  - {_label(d['metric'])}:{d['value']} vs 中位 {d['baseline_median']}"
                         f" = ×{d['ratio']:.2f}")
    return lines
