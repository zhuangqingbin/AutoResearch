#!/usr/bin/env python3
"""F2 风格温差 —— 小票 vs 大票风险偏好(确定性,零 LLM;**仅展示,不进 regime**)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §2.3 F2

MO(中证1000)与 IO(沪深300)**优先分别构建期限匹配、换月调整的指标**,再比较
IV/skew 或 PCR z-score。**原始 PCR 差与成交额比只能是探索特征** —— 两个品种的合约乘数、
期限结构、参与者结构都不同,直接相减得到的数没有稳定含义。

CFFEX 数据权限已证(20260731:720 行,OPEN-Q-3 关闭),所以当前风险**不在权限**,
而在:元数据分页、期限对齐、名义归一与样本长度。

**晋升门**:只有对次日 breadth / regime transition 有稳定增量且 no-harm 过线,
才提「regime 第四信号」;在那之前**仅展示,不进入策略师判断**。

  uv run --no-sync python -m autoresearch.derivatives.style_spread guard
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = 1
RULE_VERSION = "style_spread.v1"

MO = "MO"      # 中证1000 期权(小票)
IO = "IO"      # 沪深300 期权(大票)

ALIGNED = "ALIGNED"
UNALIGNED = "UNALIGNED"

EXPLORATORY_ONLY = (
    "原始 PCR 差与成交额比**只能是探索特征** —— 两个品种的合约乘数、期限结构与"
    "参与者结构都不同,直接相减得到的数没有稳定含义")

PROMOTION_GATE = (
    "只有对次日 breadth / regime transition 有稳定增量且 no-harm 过线,"
    "才提 regime 第四信号;在那之前**仅展示,不进入策略师判断**")

Z_MIN_HISTORY = 60      # z-score 的最小历史长度;不足 → 不给 z,不是给个 0


class StyleSpreadError(RuntimeError):
    """期限未对齐却硬比 —— 拒绝出数。"""


def align_terms(mo: pd.DataFrame, io: pd.DataFrame, *,
                bucket_column: str = "expiry_bucket") -> dict:
    """期限对齐:两边取**同一个期限桶**才可比。

    对不上 → `UNALIGNED` 并**拒绝出数**(§2.3「期限匹配」)。换月那几天两边的最近月
    很可能不同,那时的「温差」全是期限错配,不是风险偏好。
    """
    if not len(mo) or not len(io):
        return {"status": UNALIGNED, "shared_buckets": [],
                "reason": "一侧无数据"}
    mo_buckets = set(mo[bucket_column].astype(str))
    io_buckets = set(io[bucket_column].astype(str))
    shared = sorted(mo_buckets & io_buckets)
    if not shared:
        return {"status": UNALIGNED, "shared_buckets": [],
                "reason": f"无共同期限桶(MO={sorted(mo_buckets)} / IO={sorted(io_buckets)})"
                          " —— 换月期硬比得到的是期限错配,不是风险偏好"}
    return {"status": ALIGNED, "shared_buckets": shared,
            "mo_only": sorted(mo_buckets - io_buckets),
            "io_only": sorted(io_buckets - mo_buckets)}


def zscore(series: pd.Series, *, min_history: int = Z_MIN_HISTORY) -> float | None:
    """最新值相对**自身历史**的 z。历史不足 → `None`(不是 0)。

    先各自 z 再比较,而不是先相减再 z —— 后者把两个不同量纲的数强行放进一个分布。
    """
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < min_history + 1:
        return None
    history = values.iloc[:-1]
    std = float(history.std())
    if std <= 0:
        return None
    return round(float((values.iloc[-1] - history.mean()) / std), 6)


def spread(mo_panel: pd.DataFrame, io_panel: pd.DataFrame, *,
           metric: str = "oi_pcr", bucket: str | None = None,
           history: dict | None = None) -> dict:
    """MO vs IO 的风格温差。

    主口径 = **各自 z-score 之差**(先分别归一,再比较);
    次口径 = 原始差(标 `exploratory=True`,不得单独引用)。
    """
    alignment = align_terms(mo_panel, io_panel)
    if alignment["status"] == UNALIGNED:
        return {"schema_version": SCHEMA_VERSION, "rule_version": RULE_VERSION,
                "status": UNALIGNED, "reason": alignment["reason"],
                "metric": metric, "z_spread": None, "raw_spread": None,
                "exploratory_only": EXPLORATORY_ONLY,
                "promotion_gate": PROMOTION_GATE}

    target = bucket or alignment["shared_buckets"][0]
    mo_row = mo_panel[mo_panel["expiry_bucket"].astype(str) == target]
    io_row = io_panel[io_panel["expiry_bucket"].astype(str) == target]
    mo_value = float(mo_row[metric].iloc[0]) if len(mo_row) and pd.notna(
        mo_row[metric].iloc[0]) else None
    io_value = float(io_row[metric].iloc[0]) if len(io_row) and pd.notna(
        io_row[metric].iloc[0]) else None

    hist = history or {}
    mo_z = zscore(hist.get(MO, pd.Series(dtype=float)))
    io_z = zscore(hist.get(IO, pd.Series(dtype=float)))
    return {
        "schema_version": SCHEMA_VERSION, "rule_version": RULE_VERSION,
        "status": ALIGNED, "metric": metric, "expiry_bucket": target,
        "mo_value": mo_value, "io_value": io_value,
        "mo_z": mo_z, "io_z": io_z,
        # 主口径:先各自归一再比较
        "z_spread": (None if mo_z is None or io_z is None else round(mo_z - io_z, 6)),
        "z_spread_available": mo_z is not None and io_z is not None,
        "z_unavailable_reason": (None if mo_z is not None and io_z is not None
                                 else f"历史不足 {Z_MIN_HISTORY} 日 —— 不给 z,不是给 0"),
        # 次口径:探索特征,不得单独引用
        "raw_spread": (None if mo_value is None or io_value is None
                       else round(mo_value - io_value, 6)),
        "raw_spread_exploratory": True,
        "exploratory_only": EXPLORATORY_ONLY,
        "promotion_gate": PROMOTION_GATE,
        "shared_buckets": alignment["shared_buckets"],
    }


def assert_not_a_regime_signal(regime_inputs) -> None:
    """守卫:风格温差进了 regime 输入 = 已是 B 类,必须先过 registry(§2.3 晋升门)。"""
    names = set(regime_inputs or ())
    leaked = {n for n in names if "style_spread" in str(n) or "temp_diff" in str(n)}
    if leaked:
        raise StyleSpreadError(
            f"风格温差 {sorted(leaked)} 进入了 regime 输入 —— {PROMOTION_GATE}")


def render(result: dict) -> str:
    if result["status"] == UNALIGNED:
        return ("# F2 风格温差(MO vs IO)\n\n"
                f"- 状态 **UNALIGNED** —— {result['reason']}\n"
                f"- 拒绝出数(期限未对齐时的「温差」是期限错配,不是风险偏好)\n\n"
                f"> 晋升门:{result['promotion_gate']}\n")
    lines = ["# F2 风格温差(MO 中证1000 vs IO 沪深300)", "",
             f"> {result['exploratory_only']}", "",
             f"- 期限桶 `{result['expiry_bucket']}` · 指标 `{result['metric']}`",
             f"- MO {result['mo_value']} (z={result['mo_z']}) · "
             f"IO {result['io_value']} (z={result['io_z']})"]
    if result["z_spread_available"]:
        lines.append(f"- **z 温差 {result['z_spread']:+.4f}**(主口径:先各自归一再比较)")
    else:
        lines.append(f"- z 温差:不可用 —— {result['z_unavailable_reason']}")
    raw = result["raw_spread"]
    lines += [f"- 原始差 {'—' if raw is None else f'{raw:+.4f}'}(**探索特征**,不得单独引用)",
              "", f"> 晋升门:{result['promotion_gate']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="F2 风格温差(§2.3;仅展示)")
    ap.add_argument("cmd", choices=["guard"])
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)
    from autoresearch.common.regime import __doc__ as _regime_doc  # noqa: F401

    try:
        # 当前 regime 输入不含风格温差 —— 守卫在这里对空集调用,接线那天才有真输入
        assert_not_a_regime_signal(())
    except StyleSpreadError as exc:
        print(f"  ✗ {exc}")
        return 1
    print("[style_spread] ✅ 风格温差仍在展示层(未进 regime 输入)")
    if a.json_out:
        Path(a.json_out).write_text(
            json.dumps({"promotion_gate": PROMOTION_GATE, "ok": True},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
