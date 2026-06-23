#!/usr/bin/env python3
"""跨日聚合各 scan 日的 retro/channel_eval.csv → 每路滚动边际超额(单日是噪声,跨日才是信号)。零 LLM。

用法:
  uv run --no-sync python -m autoresearch.learning.channel_ledger     # 滚动 → reports/learning/channel_ledger.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_COLS = ["channel", "n_days", "sum_unique", "mean_unique_excess_t5", "mean_excess_t5", "mean_hit_rate_t5"]


def roll(scan_root: Path | None = None) -> pd.DataFrame:
    """聚合 context/scan/*/retro/channel_eval.csv 跨日 → 每路滚动汇总(按边际超额降序)。"""
    scan_root = scan_root or Path("context/scan")
    frames = []
    for p in sorted(scan_root.glob("*/retro/channel_eval.csv")):
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        if "channel" in df.columns and len(df):
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=_COLS)
    alld = pd.concat(frames, ignore_index=True)
    for c in ("unique_excess_t5", "mean_excess_t5", "hit_rate_t5", "n_unique"):
        alld[c] = pd.to_numeric(alld.get(c), errors="coerce")
    out = alld.groupby("channel").agg(
        n_days=("channel", "size"),
        sum_unique=("n_unique", "sum"),
        mean_unique_excess_t5=("unique_excess_t5", "mean"),
        mean_excess_t5=("mean_excess_t5", "mean"),
        mean_hit_rate_t5=("hit_rate_t5", "mean"),
    ).reset_index()
    for c in ("mean_unique_excess_t5", "mean_excess_t5", "mean_hit_rate_t5"):
        out[c] = out[c].round(4)
    return out.sort_values("mean_unique_excess_t5", ascending=False, na_position="last").reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    """ledger → markdown 表(每路近 N 日边际超额 + 命中;n_days<3 标 ⚠样本少)。"""
    out = ["# 召回各路前向边际超额(跨日 ledger)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无 channel_eval 数据(需先 retro 评估出 fwd)_"]
    out += ["| 路 | 天数 | Σunique | 边际超额T5 | membership超额T5 | 命中率T5 |",
            "|---|---|---|---|---|---|"]

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.1f}%"

    for r in ledger.itertuples(index=False):
        thin = " ⚠样本少" if (r.n_days or 0) < 3 else ""
        out.append(f"| {r.channel}{thin} | {int(r.n_days)} | {int(r.sum_unique)} | "
                   f"{f(r.mean_unique_excess_t5)} | {f(r.mean_excess_t5)} | {f(r.mean_hit_rate_t5)} |")
    return out


def main() -> int:
    led = roll()
    body = "\n".join(render(led))
    outp = Path("reports/learning/channel_ledger.md")
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(body, encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
