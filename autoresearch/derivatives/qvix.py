#!/usr/bin/env python3
"""F1 二期 · QVIX 序列契约 —— 「函数存在 ≠ 有效覆盖」(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §2.2 二期 / OPEN-Q-2

## 2026-08-04 复核实测

    50ETF / 300ETF QVIX   有历史
    科创 QVIX 接口         可达
    1000 指数 QVIX         **末行关键列全 NaN**

最后一条正是本模块存在的理由:**接口返回 200、有行、列也在,但那一行全是 NaN**。
`akshare` 的函数存在不等于这条序列可用。所以每条序列都要过契约:
日期单调、关键列非空率、极值合理、末行不是空壳。

## 能力边界(必须写在读数旁边)

QVIX 只能提供**波动率指数/分位**,**不能替代 25Δ skew 或期限结构** —— 那两个需要
逐合约的隐含波动率曲面。自算 IV 时优先用 put-call parity 推隐含远期/分红,
**不得固定 `q=0`**;同时做 no-arbitrage、到期天数、流动性、solver 失败率与极值过滤。

  uv run --no-sync python -m autoresearch.derivatives.qvix check
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = 1
RULE_VERSION = "qvix.v1"

# 已知序列(2026-08-04 复核)。`known_status` 是**当时的实测**,不是永久断言 ——
# 契约每次都真跑,这里只是给读的人一个先验。
KNOWN_SERIES = {
    "50etf": {"label": "50ETF QVIX", "known_status": "有历史"},
    "300etf": {"label": "300ETF QVIX", "known_status": "有历史"},
    "kcb": {"label": "科创 QVIX", "known_status": "接口可达"},
    "1000index": {"label": "中证1000 指数 QVIX",
                  "known_status": "**末行关键列全 NaN**(2026-08-04 实测)"},
}

CAPABILITY_BOUNDARY = (
    "QVIX 只提供波动率指数/分位,**不能替代 25Δ skew 或期限结构**;"
    "自算 IV 须用 put-call parity 推隐含远期/分红,**不得固定 q=0**,"
    "并做 no-arbitrage / 到期天数 / 流动性 / solver 失败率 / 极值过滤")

# IV 的合理区间(年化)。超出即不是波动率,是数据错位。
IV_MIN, IV_MAX = 0.01, 3.0
MIN_NONNULL_RATE = 0.9


@dataclass
class SeriesVerdict:
    series: str
    status: str                # OK | DEGRADED | UNUSABLE | EMPTY
    n_rows: int
    problems: list
    nonnull_rate: float | None
    last_row_all_nan: bool
    date_monotonic: bool | None
    value_range: tuple | None

    def as_dict(self) -> dict:
        return asdict(self)


def check_series(name: str, frame: pd.DataFrame | None, *,
                 value_columns: tuple = ("close", "open", "high", "low"),
                 date_column: str = "date") -> SeriesVerdict:
    """逐序列契约。**末行全 NaN 单列一个字段** —— 那是 1000 指数序列的真实症状,
    而它最容易被「有 500 行数据」的表象盖过去。"""
    if frame is None or not len(frame):
        return SeriesVerdict(name, "EMPTY", 0, ["空帧(0 行)"], None, False, None, None)

    problems: list[str] = []
    present = [c for c in value_columns if c in frame.columns]
    if not present:
        return SeriesVerdict(name, "UNUSABLE", int(len(frame)),
                             [f"关键列全缺 {list(value_columns)}"], None, True, None, None)

    values = frame[present].apply(pd.to_numeric, errors="coerce")
    nonnull = float(values.notna().any(axis=1).mean())
    if nonnull < MIN_NONNULL_RATE:
        problems.append(f"有效行占比 {nonnull:.3f} < {MIN_NONNULL_RATE}")

    last_all_nan = bool(values.iloc[-1].isna().all())
    if last_all_nan:
        problems.append("**末行关键列全 NaN** —— 接口返回了行,但那一行是空壳"
                        "(1000 指数序列的已知症状)")

    monotonic = None
    if date_column in frame.columns:
        dates = pd.to_datetime(frame[date_column].astype(str), errors="coerce")
        monotonic = bool(dates.is_monotonic_increasing or dates.is_monotonic_decreasing)
        if not monotonic:
            problems.append("日期非单调 —— 多半是拼接乱序")
        if dates.isna().any():
            problems.append(f"{int(dates.isna().sum())} 行日期不可解析")

    clean = values.stack().dropna()
    value_range = None
    if len(clean):
        lo, hi = float(clean.min()), float(clean.max())
        value_range = (round(lo, 4), round(hi, 4))
        # QVIX 常以百分点计(如 18.5 = 18.5%)→ 两种量纲都容忍,超出即数据错位
        scaled = clean / 100.0 if hi > IV_MAX else clean
        if float(scaled.min()) < IV_MIN or float(scaled.max()) > IV_MAX:
            problems.append(f"极值越界 [{lo}, {hi}] —— 这不像波动率,像数据错位")

    status = ("UNUSABLE" if last_all_nan or nonnull == 0
              else ("DEGRADED" if problems else "OK"))
    return SeriesVerdict(name, status, int(len(frame)), problems,
                         round(nonnull, 6), last_all_nan, monotonic, value_range)


def check_all(frames: dict) -> dict:
    """多序列体检 → 汇总。**逐序列判**:一条坏不该让另一条也不能用。"""
    verdicts = {name: check_series(name, frame).as_dict()
                for name, frame in frames.items()}
    usable = [n for n, v in verdicts.items() if v["status"] == "OK"]
    return {
        "schema_version": SCHEMA_VERSION, "rule_version": RULE_VERSION,
        "series": verdicts,
        "usable": sorted(usable),
        "n_usable": len(usable),
        "capability_boundary": CAPABILITY_BOUNDARY,
        "known_series": KNOWN_SERIES,
    }


def percentile_of_latest(frame: pd.DataFrame, *, column: str = "close",
                         min_history: int = 60) -> dict:
    """最新值的历史分位 —— 用**它之前**的历史,不含自己。

    历史不足 `min_history` → `None`(拿 10 个点定分位是伪精确)。
    """
    if frame is None or column not in (frame.columns if frame is not None else []):
        return {"percentile": None, "n_history": 0, "reason": "无数据/无该列"}
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if len(series) < min_history + 1:
        return {"percentile": None, "n_history": int(len(series)),
                "reason": f"历史 {len(series)} < {min_history + 1} —— 不足以定分位"}
    latest, history = float(series.iloc[-1]), series.iloc[:-1]
    return {"percentile": round(float((history < latest).mean()), 6),
            "n_history": int(len(history)), "latest": latest, "reason": None}


def render(report: dict) -> str:
    lines = ["# QVIX 序列契约(F1 二期)", "",
             f"> {report['capability_boundary']}", "",
             f"- 可用序列 **{report['n_usable']}/{len(report['series'])}**"
             f":{'、'.join(report['usable']) or '无'}", "",
             "| 序列 | 状态 | 行数 | 有效率 | 末行全NaN | 日期单调 | 问题 |",
             "|---|---|---:|---:|---|---|---|"]
    for name, v in report["series"].items():
        lines.append(
            f"| `{name}` | {v['status']} | {v['n_rows']} | "
            + ("—" if v["nonnull_rate"] is None else f"{v['nonnull_rate']:.3f}")
            + f" | {'🚨 是' if v['last_row_all_nan'] else '否'} "
            + f"| {'—' if v['date_monotonic'] is None else ('是' if v['date_monotonic'] else '否')} "
            + f"| {'、'.join(v['problems']) or '—'} |")
    lines += ["", "## 已知先验(2026-08-04 实测,非永久断言)", "",
              "| 序列 | 当时状态 |", "|---|---|"]
    lines += [f"| {v['label']} | {v['known_status']} |"
              for v in report["known_series"].values()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="QVIX 序列契约(§2.2 二期)")
    ap.add_argument("cmd", choices=["check"])
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args(argv)
    # 生产取数留给接线波;这里只跑契约本体(消费者默认关闭,不主动联网)
    report = check_all({})
    if a.json_out:
        out = Path(a.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
