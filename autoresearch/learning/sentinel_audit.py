#!/usr/bin/env python3
"""哨兵档校准账本 —— 它到底是既省了钱又没漏掉机会,还是只是省了钱(确定性,零 LLM)。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §A12 / §C4

哨兵档目前**只有省钱这一半证据**:07-31 判材料枯竭、被 `force_full` 拉满后花了 $34.48。
省下的钱好算,漏掉的机会没人算过 —— 而一个只报省钱不报漏肉的开关,会一路把自己调得越来
越激进。本账本把两边并排放,目标是形成 **cost–miss frontier**。

**false-negative 的口径必须收窄**(§A12):只认**预注册 shadow scope 内**的可交易候选
`excess2 ≥ +2pp`,**不看全市场任一赢家**。全市场口径在 5,000 只票里近乎恒真 ——
abstention v1 已经用 1,299/5,528 的实测证明过那条路会退化成"天天有漏"(见
`abstention_ledger` 的 v1/v2 之争)。同一个错误不该在这里再犯一次。

本模块**不自动改 sentinel 阈值**(R4/§C4):只有 A12 攒够固定成熟门,才允许提阈值实验。

  uv run --no-sync python -m autoresearch.learning.sentinel_audit
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pandas as pd

from autoresearch.common.ruler import MAIN_RULER

SCHEMA_VERSION = 1
UNMEASURED = "UNMEASURED"
FALSE_NEGATIVE_MIN_EXCESS = 0.02      # +2pp,与 A11 的 FALSE_KILL 同一把尺
# 单日全扫的成本量级 —— 07-31 实测 $34.48 / 113m44s。它是**容量估算**,不是长期收益承诺
# (§C4 明写这一点):哨兵日省下的不等于这个数,只是同量级参照。
FULL_SCAN_COST_USD = 34.48

_COLS = ["date", "run_mode", "sentinel_reason", "pinned_n", "cost_saved_estimate_usd",
         "shadow_scope_n", "false_negative_n", "false_negative_codes",
         "max_shadow_excess2", "market_fwd2", "next_regime", "false_negative_status"]


def _run_mode(scan_dir: Path) -> tuple[str | None, str | None, int]:
    from autoresearch.scan.run_mode import load

    mode = load(scan_dir)
    if mode is None:
        return None, None, 0
    return mode.mode, mode.sentinel_reason, len(mode.pinned_codes)


def _shadow_scope(date: str, path: Path | str | None = None) -> list[str]:
    """**预注册**的 shadow scope = 当日 `shadow_buys`(系统最想买的 K 只)。

    这就是 false-negative 的全部人口 —— 不是全市场。
    """
    from autoresearch.learning.abstention_ledger import shadow_codes_for

    return shadow_codes_for(date, path)


def _excess_map(scan_dir: Path) -> dict[str, float]:
    """逐票 T+2 超额(可交易且成熟);缺 retro → {}。"""
    path = scan_dir / "retro" / "rejection_attribution.csv"
    if not path.exists():
        return {}
    with contextlib.suppress(Exception):
        frame = pd.read_csv(path, dtype={"code": str})
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        ok = frame[
            frame["buyable"].astype(str).str.lower().isin({"true", "1"})
            & frame["mature"].astype(str).str.lower().isin({"true", "1"})
        ]
        values = pd.to_numeric(ok["excess_2"], errors="coerce")
        return {c: float(v) for c, v in zip(ok["code"], values, strict=False)
                if pd.notna(v)}
    return {}


def _market_fwd2(scan_dir: Path) -> float | None:
    path = scan_dir / "retro" / "attribution.csv"
    if not path.exists():
        return None
    with contextlib.suppress(Exception):
        frame = pd.read_csv(path)
        values = pd.to_numeric(frame.get(MAIN_RULER), errors="coerce").dropna()
        if len(values):
            return round(float(values.mean()), 6)
    return None


def _regime(scan_dir: Path) -> str | None:
    path = scan_dir / "meta.json"
    if not path.exists():
        return None
    with contextlib.suppress(Exception):
        return json.loads(path.read_text(encoding="utf-8")).get("regime")
    return None


def audit_day(scan_dir: Path | str, *, next_scan_dir: Path | None = None,
              shadow_path: Path | str | None = None) -> dict | None:
    """单个哨兵日的审计行;非哨兵日 → None(不入账)。"""
    scan = Path(scan_dir)
    mode, reason, pinned_n = _run_mode(scan)
    if mode is None or not mode.startswith("SENTINEL"):
        return None

    scope = _shadow_scope(scan.name, shadow_path)
    excess = _excess_map(scan)
    measured = {c: excess[c] for c in scope if c in excess}
    misses = sorted(c for c, v in measured.items() if v >= FALSE_NEGATIVE_MIN_EXCESS)

    if not scope:
        status = f"{UNMEASURED}(无预注册 shadow scope)"
    elif not measured:
        status = f"{UNMEASURED}(scope 内无可交易且成熟的候选)"
    else:
        status = "MISS" if misses else "CLEAN"

    return {
        "date": scan.name,
        "run_mode": mode,
        "sentinel_reason": reason,
        "pinned_n": pinned_n,
        # 中间档仍跑持仓 L4,省的是"全扫减去持仓那几只"那部分,不是全额
        "cost_saved_estimate_usd": round(
            FULL_SCAN_COST_USD * (1 - min(pinned_n, 10) / 10), 2),
        "shadow_scope_n": len(scope),
        "false_negative_n": len(misses),
        "false_negative_codes": "|".join(misses),
        "max_shadow_excess2": (round(max(measured.values()), 6) if measured else None),
        "market_fwd2": _market_fwd2(scan),
        "next_regime": _regime(next_scan_dir) if next_scan_dir else None,
        "false_negative_status": status,
    }


def roll(scan_root: Path | str | None = None,
         shadow_path: Path | str | None = None) -> pd.DataFrame:
    root = Path(scan_root or "context/scan")
    if not root.exists():
        return pd.DataFrame(columns=_COLS)
    days = sorted(p for p in root.iterdir() if p.is_dir() and p.name[:2] == "20")
    rows = []
    for i, day in enumerate(days):
        nxt = days[i + 1] if i + 1 < len(days) else None
        row = audit_day(day, next_scan_dir=nxt, shadow_path=shadow_path)
        if row:
            rows.append(row)
    return pd.DataFrame(rows, columns=_COLS)


def frontier(ledger: pd.DataFrame) -> dict:
    """cost–miss frontier 的汇总读数(§C4)。分母永远同屏。"""
    if ledger is None or not len(ledger):
        return {"sentinel_days": 0, "cost_saved_usd": 0.0, "false_negative_n": 0,
                "unmeasured_n": 0, "measured_n": 0}
    unmeasured = int(ledger["false_negative_status"].astype(str)
                     .str.startswith(UNMEASURED).sum())
    return {
        "sentinel_days": int(len(ledger)),
        "cost_saved_usd": round(float(
            pd.to_numeric(ledger["cost_saved_estimate_usd"], errors="coerce").sum()), 2),
        "false_negative_n": int(
            pd.to_numeric(ledger["false_negative_n"], errors="coerce").fillna(0).sum()),
        "unmeasured_n": unmeasured,
        "measured_n": int(len(ledger)) - unmeasured,
    }


def render(ledger: pd.DataFrame) -> list[str]:
    out = ["# 哨兵档校准账本(省了多少 × 漏了什么)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无哨兵日(需 run_mode.json;A2 之前的运行没有这份事实)_"]

    stats = frontier(ledger)
    out += [
        f"- **cost–miss frontier**:{stats['sentinel_days']} 个哨兵日 · "
        f"省约 ${stats['cost_saved_usd']} · 漏肉 {stats['false_negative_n']} 次 · "
        f"不可测 {stats['unmeasured_n']}/{stats['sentinel_days']} 日",
        "",
        "| 日期 | 模式 | 持仓 | 省≈$ | scope | 漏肉 | 最大shadow超额 | 市场fwd2 | 次日regime | 判定 |",
        "|---|---|---:|---:|---:|---:|---|---|---|---|",
    ]

    def pct(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"

    for r in ledger.itertuples(index=False):
        out.append(
            f"| {r.date} | {r.run_mode} | {r.pinned_n} | {r.cost_saved_estimate_usd} "
            f"| {r.shadow_scope_n} | {r.false_negative_n} | {pct(r.max_shadow_excess2)} "
            f"| {pct(r.market_fwd2)} | {r.next_regime or '—'} | {r.false_negative_status} |")
    out += [
        "",
        f"_漏肉只认**预注册 shadow scope 内**可交易候选 `excess2 ≥ +{FALSE_NEGATIVE_MIN_EXCESS:.0%}`,"
        "**不看全市场任一赢家** —— 全市场口径在 5,000 只票里近乎恒真(abstention v1 实测"
        "命中 1,299/5,528),那条路会退化成「天天有漏」。_",
        f"_省钱是**容量估算**(单日全扫 ${FULL_SCAN_COST_USD} 量级),不是长期收益承诺;"
        "本账本**不自动改 sentinel 阈值**,攒够固定成熟门才允许提阈值实验。_",
    ]
    return out


def main(argv: list[str] | None = None) -> int:
    ledger = roll()
    target = Path("reports/learning/sentinel_audit.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(render(ledger)) + "\n", encoding="utf-8")
    print(f"[sentinel_audit] {len(ledger)} 哨兵日 → {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
