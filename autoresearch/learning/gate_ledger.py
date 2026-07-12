#!/usr/bin/env python3
"""门的跨日 mark-to-market ledger —— 每道 self_review 硬门"拦得对不对"(确定性,零 LLM)。

design: docs/specs/2026-07-02-learning-mtm-design.md §R3

聚合各 scan 日 gate_fires.csv × retro/attribution.csv 的已实现 fwd → 每门 n_fires/被拦票
excess 均值/拦对率(**主尺 T+2**,fwd_1/fwd_5 保留参考)。某门持续 excess>0(拦的票反而跑赢)→
第 4 步提松阈/退役建议(人批)。门拦对了是功劳簿,拦错了是退役依据——没有这本账,门只会累积成保守棘轮。

  uv run --no-sync python -m autoresearch.learning.gate_ledger   # → reports/learning/gate_ledger.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.learning.shrink import MIN_N_INJECT, n_tag, shrink as _shrink_fn, shrink_config

_COLS = ["check", "n_days", "n_fires", "mean_ex1", "mean_ex2", "mean_ex5", "hit_rate",
         "tail_rate", "tail_n", "tail_rate_raw"]
_TAIL_THIN_N = 5   # 既有 render() "⚠样本少"阈值(n_fires<5);tail_rate 的 ⚠ 沿用同一数字,测的是 tail_n


def roll(scan_root: Path | None = None, shrink: bool | None = None,
        k: float | None = None) -> pd.DataFrame:
    """聚合 context/scan/*/{gate_fires.csv × retro/attribution.csv} → 每门跨日汇总。

    `tail_rate` 是**收缩估计**(design 2026-07-12-selflearning-optimization-brainstorm.md
    §4 P0-3,C9-C12——spec 原文点名此处"现 n=2-3 天最急需"):p̂=(n·p_门+k·p_全局)/(n+k),
    `p_全局`=全部门池化左尾率,`n`=`tail_n`(该门 `fwd2_raw` 非空观测数——денominator 与
    `n_fires` 可能不同:未成熟票 fwd_2_oc 尚缺时计入 n_fires 但不计入 tail_n)。`tail_n`<3
    (`shrink.MIN_N_INJECT`)→ `tail_rate=None`(绝对禁注,不受 `shrink` 开关影响)。
    `tail_rate_raw` 保留原始比例供审计/回放对照,不收缩。`shrink`/`k` 缺省 → 读
    `scan_config.json` 的 `learning.{shrink,shrink_k}`。
    """
    scan_root = Path(scan_root or "context/scan")
    rows = []
    for gf in sorted(scan_root.glob("*/gate_fires.csv")):
        attr_p = gf.parent / "retro" / "attribution.csv"
        if not attr_p.exists():
            continue
        try:
            fires = pd.read_csv(gf, dtype={"code": str})
            attr = pd.read_csv(attr_p, dtype={"code": str})
        except Exception:
            continue
        fires = fires[fires["code"].astype(str).str.len() > 0]
        if not len(fires) or "fwd_1_oo" not in attr.columns:
            continue
        fires["code"] = fires["code"].astype(str).str.zfill(6)
        attr["code"] = attr["code"].astype(str).str.zfill(6)
        f1 = pd.to_numeric(attr["fwd_1_oo"], errors="coerce")
        f2 = pd.to_numeric(attr.get("fwd_2_oc"), errors="coerce") if "fwd_2_oc" in attr.columns \
            else pd.Series(dtype=float)
        f5 = pd.to_numeric(attr.get("fwd_5_oc"), errors="coerce") if "fwd_5_oc" in attr.columns \
            else pd.Series(dtype=float)
        m1 = f1.mean()
        m2 = f2.mean() if len(f2) else float("nan")
        m5 = f5.mean() if len(f5) else float("nan")
        j = fires.merge(attr[[c for c in ("code", "fwd_1_oo", "fwd_2_oc", "fwd_5_oc") if c in attr.columns]],
                        on="code", how="left")
        j["fwd2_raw"] = pd.to_numeric(j.get("fwd_2_oc"), errors="coerce")   # 原始值(非超额)→ 左尾 KPI
        j["ex1"] = pd.to_numeric(j.get("fwd_1_oo"), errors="coerce") - m1
        j["ex2"] = (pd.to_numeric(j.get("fwd_2_oc"), errors="coerce") - m2) if "fwd_2_oc" in j.columns else None
        j["ex5"] = (pd.to_numeric(j.get("fwd_5_oc"), errors="coerce") - m5) if "fwd_5_oc" in j.columns else None
        j["date"] = gf.parent.name
        rows.append(j)
    if not rows:
        return pd.DataFrame(columns=_COLS)
    alld = pd.concat(rows, ignore_index=True)
    out = alld.groupby("check").agg(
        n_days=("date", "nunique"), n_fires=("code", "size"),
        mean_ex1=("ex1", "mean"), mean_ex2=("ex2", "mean"), mean_ex5=("ex5", "mean"),
        hit_rate=("ex2", lambda s: float((s.dropna() < 0).mean()) if s.notna().any() else None),
        tail_n=("fwd2_raw", lambda s: int(s.notna().sum())),
        tail_rate_raw=("fwd2_raw", lambda s: float((s.dropna() <= -0.05).mean()) if s.notna().any() else None),
    ).reset_index()
    for c in ("mean_ex1", "mean_ex2", "mean_ex5", "hit_rate", "tail_rate_raw"):
        out[c] = pd.to_numeric(out[c], errors="coerce").round(4)

    if shrink is None or k is None:
        cfg_on, cfg_k = shrink_config()
        shrink = cfg_on if shrink is None else shrink
        k = cfg_k if k is None else k
    fwd2_all = pd.to_numeric(alld["fwd2_raw"], errors="coerce").dropna()
    p_global = float((fwd2_all <= -0.05).mean()) if len(fwd2_all) else None

    def _tail(row):
        n = row["tail_n"]
        raw = row["tail_rate_raw"]
        if n < MIN_N_INJECT or raw is None or pd.isna(raw):
            return None
        if not shrink:
            return raw
        v = _shrink_fn(raw, n, p_global, k)
        return round(float(v), 4) if v is not None else raw

    out["tail_rate"] = pd.to_numeric(out.apply(_tail, axis=1), errors="coerce")
    return out.sort_values("n_fires", ascending=False).reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    out = ["# 门审计 ledger(被拦票 vs 市场;ex<0 = 拦对)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无 gate_fires × attribution 数据(需 assemble 留痕 + retro 归因)_"]

    def f(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"

    out += ["| 门 | 天数 | 拦次 | 被拦ex1(参考) | 被拦ex2(主尺) | 被拦ex5(参考) | 拦对率(按ex2) | 拦对率(左尾≤-5%) |",
            "|---|---|---|---|---|---|---|---|"]
    for r in ledger.itertuples(index=False):
        thin = " ⚠样本少" if (r.n_fires or 0) < 5 else ""
        hr = "—" if pd.isna(r.hit_rate) else f"{r.hit_rate:.0%}"
        tr = "—" if pd.isna(r.tail_rate) else f"{r.tail_rate:.0%}{n_tag(r.tail_n, _TAIL_THIN_N)}"
        out.append(f"| {r.check}{thin} | {int(r.n_days)} | {int(r.n_fires)} | "
                   f"{f(r.mean_ex1)} | {f(r.mean_ex2)} | {f(r.mean_ex5)} | {hr} | {tr} |")
    out += ["", "_某门持续 ex>0 → 提松阈/退役建议(proposals,人批);别让门无问责地累积。_"]
    return out


def main() -> int:
    ledger = roll()
    out = Path("reports/learning/gate_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(ledger)) + "\n", encoding="utf-8")
    print(f"[gate_ledger] {len(ledger)} 门 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
