#!/usr/bin/env python3
"""差一点(near-miss)报告出口 —— 让漏肉可见,但不让它看起来像买单(确定性,零 LLM)。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §C3

三条纪律,缺一条这个特性就会变成"绕门冲动放大器":

1. **主报告只给聚合,个股进附录**(§R6)。只展示"这只差一点"而不同屏给分母,读者会
   系统性高估漏肉、进而想绕门。所以 summary 一行是门柱计数 + 历史同口径的
   `FALSE_KILL x/n`,逐只读数放「影子观察附录」,且固定标注**未过门、非建议、
   shadow 整体仍输真实线**。
2. **前视强制隔离**(§C3-3)。所有"历史战绩"只认**在本报告日之前就已经成熟**的样本。
   历史回放尤其危险:重放 07-16 的报告时,ledger 里躺着 07-29 才知道的结果。本模块用
   扫描日历做保守代理(信号日与报告日之间至少隔 2 个扫描日),缺输入 → 显式
   `UNMEASURED`,**不静默消失**(静默消失会被读成"历史上没错杀过")。
3. **CORRECT/NEUTRAL 不逐案刷屏,但分母始终同屏**(§C3-5)。

  uv run --no-sync python -m autoresearch.scan.near_miss 2026-07-31
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.learning.gate_attribution import COHORT_V3, SINGLE_GATES

MATURITY_SCAN_DAYS = 2      # T+2 主尺:信号日与报告日之间至少隔 2 个扫描日才算已成熟
UNMEASURED = "UNMEASURED"
DISCLAIMER = ("_未过门,非建议;影子组合整体仍**输给**真实线 —— "
              "这一节的用途是让漏肉可查,不是给绕门找理由。_")


@dataclass
class NearMissFacts:
    date: str
    is_zero_buy: bool
    shadow: list[dict] = field(default_factory=list)
    gate_counts: dict[str, int] = field(default_factory=dict)
    gate_history: dict[str, dict] = field(default_factory=dict)
    abstention: dict | None = None
    as_of_days: list[str] = field(default_factory=list)

    @property
    def has_shadow(self) -> bool:
        return bool(self.shadow)


def _scan_days(scan_root: Path) -> list[str]:
    if not scan_root.exists():
        return []
    return sorted(p.name for p in scan_root.iterdir()
                  if p.is_dir() and p.name[:2] == "20")


def mature_before(signal_date: str, report_date: str, days: list[str]) -> bool:
    """信号日的 T+2 结果在报告日之前是否**已经**可知。

    用扫描日历做保守代理而不是自然日:自然日会把周末算进去,把还没成熟的样本放进历史战绩
    ——那就是从未来读数。日历不全时宁可判"未成熟"(少展示),不宁可多展示。
    """
    if signal_date >= report_date:
        return False
    between = [d for d in days if signal_date < d < report_date]
    return len(between) >= MATURITY_SCAN_DAYS


def _shadow_rows(date: str, path: Path | str | None = None,
                 scan_dir: Path | None = None) -> list[dict]:
    """当日 shadow 候选 + 各自被哪道门拦。

    `binding` 列**优先从卡面现算**,只在算不出时回退存盘值:2026-08-01 查出
    `gate_status` 读不懂 markdown 加粗的 `**✗**`(全语料 42/241 卡受影响),那期间落的
    `shadow_buys.binding` 全是空串。历史产物是当时的事实、不该改写,但**渲染时重算**
    既不改历史也能让这一节说真话。
    """
    src = Path(path or ws.learning_root() / "shadow_buys.csv")
    if not src.exists():
        return []
    try:
        frame = pd.read_csv(src, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return []
    if "date" not in frame.columns:
        return []
    hit = frame[frame["date"].astype(str) == str(date)]
    rows = []
    for row in hit.itertuples(index=False):
        code = str(row.code).zfill(6)
        stored = getattr(row, "binding", "")
        stored = "" if stored is None or pd.isna(stored) else str(stored)
        binding = _binding_from_card(scan_dir, code) or [g for g in stored.split("|") if g]
        rows.append({
            "code": code,
            "name": "" if pd.isna(getattr(row, "name", "")) else str(row.name),
            "conviction": getattr(row, "conviction", None),
            "binding": binding,
            "close": getattr(row, "close", None),
        })
    return rows


def _binding_from_card(scan_dir: Path | None, code: str) -> list[str]:
    """卡面『OW三门』段里 ✗ 的门名;卡缺/无门柱段 → []。"""
    if scan_dir is None:
        return []
    card = Path(scan_dir) / "details" / f"{code}.md"
    if not card.exists():
        return []
    with contextlib.suppress(Exception):
        from autoresearch.scan.l4.parsers import gate_status
        gates = gate_status(card.read_text(encoding="utf-8")) or {}
        return [gate for gate, failed in gates.items() if failed]
    return []


def _gate_history(report_date: str, scan_root: Path,
                  days: list[str]) -> dict[str, dict]:
    """每道门的历史 `FALSE_KILL x/n`,**只用报告日之前已成熟的样本**(A11 v3 契约)。"""
    from autoresearch.learning import gate_attribution as ga

    out: dict[str, dict] = {}
    try:
        rows = ga.roll_participation(scan_root)
    except Exception:  # noqa: BLE001 — 历史战绩缺了只降级成 UNMEASURED,不阻断报告
        rows = pd.DataFrame()
    usable = (
        rows[rows["date"].map(lambda d: mature_before(str(d), report_date, days))]
        if len(rows) else rows
    )
    for gate in SINGLE_GATES:
        subset = usable[usable["gate"] == gate] if len(usable) else usable
        measured = (
            subset[subset["outcome"] != "UNMEASURED"] if len(subset) else subset
        )
        if not len(measured):
            out[gate] = {"status": UNMEASURED, "reason": "报告日前无已成熟样本"}
            continue
        false_kill = int((measured["outcome"] == "FALSE_KILL").sum())
        out[gate] = {
            "status": "OK",
            "false_kill": false_kill,
            "measured": int(len(measured)),
            "as_of": str(measured["date"].max()),
            "cohort_version": COHORT_V3,
        }
    return out


def _abstention(report_date: str, scan_root: Path, days: list[str]) -> dict | None:
    """最近一条**已成熟**的 v2 FALSE 裁决 + 滚动成熟窗分布(§C3-4)。"""
    from autoresearch.learning.abstention_ledger import roll as abstention_roll

    try:
        ledger = abstention_roll(scan_root)
    except Exception:  # noqa: BLE001
        return None
    if not len(ledger):
        return None
    judged = ledger[ledger["status_v2"].notna()]
    usable = judged[judged["date"].map(
        lambda d: mature_before(str(d), report_date, days))]
    if not len(usable):
        return None
    counts = usable["status_v2"].value_counts().to_dict()
    false_rows = usable[usable["status_v2"] == "FALSE"]
    latest = false_rows.iloc[-1].to_dict() if len(false_rows) else None
    payload = {
        "window_n": int(len(usable)),
        "counts": {k: int(counts.get(k, 0))
                   for k in ("CORRECT", "FALSE", "NEUTRAL", "IMMATURE")},
        "as_of": str(usable["date"].max()),
    }
    if latest is None:
        return payload
    codes = [c for c in str(latest.get("shadow_opportunity_codes", "")).split("|") if c]
    payload["latest_false"] = {
        "signal_date": str(latest["date"]),
        "codes": codes,
        "excess_2": _excess_for(scan_root, str(latest["date"]), codes),
    }
    return payload


def _excess_for(scan_root: Path, date: str, codes: list[str]) -> float | None:
    """那只 shadow 机会票的实际 T+2 超额(逐票因果账的原始读数);取不到 → None。"""
    if not codes:
        return None
    path = Path(scan_root) / date / "retro" / "rejection_attribution.csv"
    if not path.exists():
        return None
    with contextlib.suppress(Exception):
        frame = pd.read_csv(path, dtype={"code": str})
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        hit = frame[frame["code"].isin(codes)]
        values = pd.to_numeric(hit.get("excess_2"), errors="coerce").dropna()
        if len(values):
            return round(float(values.max()), 6)
    return None


def build(scan_dir: Path | str, *, report_date: str | None = None,
          n_buys: int | None = None,
          shadow_path: Path | str | None = None) -> NearMissFacts:
    """一次扫描的 near-miss 事实。`n_buys` 缺省 → 从 `_final_ratings.json` 数。"""
    scan = Path(scan_dir)
    root = scan.parent
    date = scan.name
    report = str(report_date or date)
    days = _scan_days(root)

    if n_buys is None:
        n_buys = _count_buys(scan)
    shadow = _shadow_rows(date, shadow_path, scan_dir=scan)
    counts = dict.fromkeys(SINGLE_GATES, 0)
    for row in shadow:
        for gate in row["binding"]:
            if gate in counts:
                counts[gate] += 1
    return NearMissFacts(
        date=date, is_zero_buy=(n_buys == 0), shadow=shadow, gate_counts=counts,
        gate_history=_gate_history(report, root, days),
        abstention=_abstention(report, root, days), as_of_days=days)


def _count_buys(scan: Path) -> int:
    import json
    path = scan / "_final_ratings.json"
    if not path.exists():
        return 0
    with contextlib.suppress(Exception):
        raw = json.loads(path.read_text(encoding="utf-8"))
        return sum(1 for v in raw.values() if v in ("Buy", "Overweight"))
    return 0


# ────────────────────────── 渲染(只读事实,不重判) ──────────────────────────

def summary_line(facts: NearMissFacts) -> str:
    """主报告里的**一行**:门柱计数 + 历史同口径分母。个股读数不在这里(§R6)。"""
    if not facts.is_zero_buy or not facts.has_shadow:
        return ""
    gates = " / ".join(f"{gate} {facts.gate_counts.get(gate, 0)}"
                       for gate in SINGLE_GATES)
    total_binding = sum(facts.gate_counts.values())
    head = (f"🎯 差一点:shadow top{len(facts.shadow)} 中 {gates}"
            if total_binding
            else f"🎯 差一点:shadow top{len(facts.shadow)} 均非 OW 三门拦下"
                 f"(早停/rubric 压的评级)")
    history = "；".join(_history_fragment(gate, facts.gate_history.get(gate, {}))
                        for gate in SINGLE_GATES)
    return f"{head};历史同口径(截至本报告日前已成熟):{history}"


def _history_fragment(gate: str, record: dict) -> str:
    if not record or record.get("status") != "OK":
        return f"{gate} {UNMEASURED}"
    return (f"{gate} FALSE_KILL {record['false_kill']}/{record['measured']}"
            f"(截至 {record['as_of']})")


def abstention_line(facts: NearMissFacts) -> str:
    """§C3-4:FALSE 弃权在**首次可用的下一份报告**上浮,不写死"次日/昨日"。"""
    payload = facts.abstention
    if not payload:
        return ""
    counts = payload["counts"]
    window = (f"滚动成熟窗 FALSE {counts['FALSE']}/{payload['window_n']}"
              f",NEUTRAL {counts['NEUTRAL']}/{payload['window_n']}"
              f",CORRECT {counts['CORRECT']}/{payload['window_n']}")
    latest = payload.get("latest_false")
    if not latest:
        return f"✅ 最近成熟弃权中无 FALSE;{window}"
    codes = "·".join(latest["codes"]) or "—"
    excess = latest.get("excess_2")
    excess_txt = f"excess2 {excess * 100:+.1f}pp" if excess is not None else f"excess2 {UNMEASURED}"
    return (f"⚠️ 最近成熟弃权 FALSE:信号日 {latest['signal_date']}·{codes}·{excess_txt}"
            f";{window}")


def appendix_lines(facts: NearMissFacts) -> list[str]:
    """影子观察附录:逐只门柱读数。**主报告不把它与 buy-list 排成同视觉层级**。"""
    if not facts.is_zero_buy or not facts.has_shadow:
        return []
    lines = [
        "### 🕯️ 影子观察附录(未过门的 top 候选)",
        "",
        DISCLAIMER,
        "",
        "| 代码 | 名称 | conviction | 被哪道门拦 | 收盘 |",
        "|---|---|---:|---|---:|",
    ]
    for row in facts.shadow:
        binding = "/".join(row["binding"]) or "非三门(早停/rubric)"
        conv = "—" if row["conviction"] is None or pd.isna(row["conviction"]) \
            else f"{float(row['conviction']):.0f}"
        close = "—" if row["close"] is None or pd.isna(row["close"]) \
            else f"{float(row['close']):.2f}"
        lines.append(f"| {row['code']} | {row['name'] or '—'} | {conv} | {binding} | {close} |")
    return lines


def section(facts: NearMissFacts) -> list[str]:
    """完整 C3 出口:summary 一行 + 弃权 banner + 附录。非 0买日 → 只可能有 banner。"""
    out: list[str] = []
    head = summary_line(facts)
    if head:
        out += ["", head]
    banner = abstention_line(facts)
    if banner:
        out += ["", banner]
    appendix = appendix_lines(facts)
    if appendix:
        out += [""] + appendix
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="near-miss 报告出口(确定性)")
    ap.add_argument("date")
    ap.add_argument("--scan-root", default=str(ws.scan_root()))
    ap.add_argument("--report-date", default=None,
                    help="模拟在该日发布(前视隔离验收用);缺省 = 扫描日当天")
    args = ap.parse_args(argv)

    facts = build(Path(args.scan_root) / args.date, report_date=args.report_date)
    print(f"[near_miss] {facts.date} · 0买={facts.is_zero_buy} · "
          f"shadow={len(facts.shadow)} · 门柱={facts.gate_counts}")
    print("\n".join(section(facts)) or "_(无 near-miss 节)_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
