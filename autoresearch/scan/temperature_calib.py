#!/usr/bin/env python3
"""S1 情绪温度计 · 校准报告 —— phase × 市场 fwd_1/fwd_2 条件分布 + phase×regime 交叉表(零 LLM)。

design: docs/specs/2026-07-07-memory-astrategy-optimization-design.md §S1(验收①);
        docs/specs/2026-07-11-funnel-p0p1-wave-plan.md Task 5。

温度是**择时/regime 变量,不是选股因子**(按 regime 校准口径,非 IC)——本报告只读数:
① 温度分段(phase)与全市场次日(fwd_1)/2 日累计(fwd_2)等权收益的条件分布,看分段是否
真的带来收益差异;② phase × regime(scan 日 `meta.json` 的 risk_off/range/trend)交叉表,
看温度是否提供 regime 三块之外的正交信息。**不改 score 权重/phase 阈值/门/菜单**——
v1 权重与分段阈值仍是待校准先验(见 `temperature.py` 模块 docstring),本报告只给证据。

样本量 n<10 的行标 ⚠样本少(与 `cross_calib.py`/`buy_ledger.py` 同款 thin 禁注惯例)。

  uv run --no-sync python -m autoresearch.scan.temperature_calib   # → reports/research/temperature_calib.md
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_MIN_N = 10
_PHASE_ORDER = ["冰点", "修复", "发酵", "高潮", "退潮", "未知"]


def _phase_sort_key(phase: str) -> int:
    return _PHASE_ORDER.index(phase) if phase in _PHASE_ORDER else len(_PHASE_ORDER)


def forward_returns(dates_iso: list[str]) -> pd.DataFrame:
    """temperature.csv 各日('YYYY-MM-DD')→ 全市场等权 fwd_1(次日)/fwd_2(2 日累计)收益。

    复用 `paper_nav.market_nav`(累乘 NAV)+ `trade_days`(湖 parquet 文件名即交易日历);
    `market_nav` 累乘性质 → 任意两日间累计收益 = nav[j]/nav[i]-1,不必额外拆解单日收益。
    日历 `start` 早于 temperature.csv 最早日一天不差(否则该日之前的种子/首日会被裁掉)。
    lake 无该日/日历覆盖不到未来两个交易日 → 对应 fwd 置 None(数据尚未成熟,非缺陷)。
    """
    cols = ["date", "fwd_1", "fwd_2"]
    if not dates_iso:
        return pd.DataFrame(columns=cols)
    from autoresearch.learning.paper_nav import market_nav, trade_days
    start = min(dates_iso).replace("-", "")
    days = trade_days(start=start)
    if not days:
        return pd.DataFrame(columns=cols)
    nav = market_nav(days)
    idx = {d: i for i, d in enumerate(days)}
    rows = []
    for d in dates_iso:
        i = idx.get(d.replace("-", ""))
        f1 = f2 = None
        if i is not None:
            if i + 1 < len(days):
                f1 = float(nav.iloc[i + 1] / nav.iloc[i] - 1)
            if i + 2 < len(days):
                f2 = float(nav.iloc[i + 2] / nav.iloc[i] - 1)
        rows.append({"date": d, "fwd_1": f1, "fwd_2": f2})
    return pd.DataFrame(rows, columns=cols)


def regime_by_date(scan_root: Path | str = "context/scan") -> pd.DataFrame:
    """各 scan 日('YYYY-MM-DD' 目录名)→ `meta.json` 里的 regime(缺 meta/regime 字段 → 跳过)。"""
    root = Path(scan_root)
    cols = ["date", "regime"]
    if not root.exists():
        return pd.DataFrame(columns=cols)
    rows = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        meta = d / "meta.json"
        if not meta.exists():
            continue
        try:
            reg = json.loads(meta.read_text(encoding="utf-8")).get("regime")
        except Exception:  # noqa: BLE001 — 坏 meta.json 跳过,不阻断整表
            continue
        if reg:
            rows.append({"date": d.name, "regime": reg})
    return pd.DataFrame(rows, columns=cols)


def phase_table(temp: pd.DataFrame, fwd: pd.DataFrame, min_n: int = _MIN_N) -> pd.DataFrame:
    """phase 分组:n / 市场次日均值(fwd_1)/ fwd_2 均值;n<min_n → thin=True(⚠样本少)。"""
    cols = ["phase", "n", "mean_fwd_1", "mean_fwd_2", "thin"]
    if not len(temp):
        return pd.DataFrame(columns=cols)
    m = temp.merge(fwd, on="date", how="left")
    rows = []
    for ph, g in m.groupby("phase"):
        f1 = pd.to_numeric(g["fwd_1"], errors="coerce").dropna()
        f2 = pd.to_numeric(g["fwd_2"], errors="coerce").dropna()
        rows.append({"phase": ph, "n": len(g),
                     "mean_fwd_1": round(float(f1.mean()), 4) if len(f1) else None,
                     "mean_fwd_2": round(float(f2.mean()), 4) if len(f2) else None,
                     "thin": len(g) < min_n})
    return (pd.DataFrame(rows, columns=cols)
            .sort_values("phase", key=lambda s: s.map(_phase_sort_key))
            .reset_index(drop=True))


def phase_regime_table(temp: pd.DataFrame, fwd: pd.DataFrame, regime: pd.DataFrame,
                       min_n: int = _MIN_N) -> pd.DataFrame:
    """phase × regime 交叉表(inner join scan 日 regime;无重叠 → 空表)。是否提供 regime 三块
    之外的正交信息 = 同 regime 内不同 phase 的 fwd_2 均值是否仍有区分度。"""
    cols = ["phase", "regime", "n", "mean_fwd_2", "thin"]
    if not len(temp) or not len(regime):
        return pd.DataFrame(columns=cols)
    m = temp.merge(fwd, on="date", how="left").merge(regime, on="date", how="inner")
    if not len(m):
        return pd.DataFrame(columns=cols)
    rows = []
    for (ph, reg), g in m.groupby(["phase", "regime"]):
        f2 = pd.to_numeric(g["fwd_2"], errors="coerce").dropna()
        rows.append({"phase": ph, "regime": reg, "n": len(g),
                     "mean_fwd_2": round(float(f2.mean()), 4) if len(f2) else None,
                     "thin": len(g) < min_n})
    return (pd.DataFrame(rows, columns=cols)
            .sort_values(["phase", "regime"], key=lambda s: s.map(_phase_sort_key) if s.name == "phase" else s)
            .reset_index(drop=True))


def _pct(x) -> str:
    return "—" if x is None or x != x else f"{x * 100:+.2f}%"


def render(phase_tbl: pd.DataFrame, cross_tbl: pd.DataFrame, n_days: int) -> list[str]:
    out = ["# S1 情绪温度计校准报告(phase × 市场 fwd_1/fwd_2 条件分布 + phase×regime 交叉表)", "",
           f"_样本:`temperature.csv` {n_days} 日;市场收益复用 `paper_nav.market_nav`"
           "(全市场等权日收益累乘,任两日间取比值即累计收益)。n<10 标 ⚠样本少。"
           "**只读数,不改 score 权重/phase 阈值/门/菜单**(S1 spec 拍板边界:本波展示先行)。_", ""]

    out += ["## ① phase × 市场 fwd_1/fwd_2 条件分布", "",
            "| phase | n | 市场次日均值(fwd_1) | fwd_2 均值 | |", "|---|---:|---:|---:|---|"]
    if not len(phase_tbl):
        out.append("| _无数据_ | | | | |")
    else:
        for r in phase_tbl.itertuples(index=False):
            thin = "⚠样本少" if r.thin else ""
            out.append(f"| {r.phase} | {r.n} | {_pct(r.mean_fwd_1)} | {_pct(r.mean_fwd_2)} | {thin} |")

    out += ["", "## ② phase × regime 交叉表(温度是否提供 regime 三块之外的正交信息)", "",
            "_regime 取各 scan 日 `meta.json`(仅覆盖已跑过 scan 的日子,样本天然薄于 ①)。_", ""]
    if not len(cross_tbl):
        out.append("_无 scan 日 meta.json regime 数据(尚无足够 scan 现场与 temperature.csv 日期重叠)_")
    else:
        out += ["| phase | regime | n | fwd_2 均值 | |", "|---|---|---:|---:|---|"]
        for r in cross_tbl.itertuples(index=False):
            thin = "⚠样本少" if r.thin else ""
            out.append(f"| {r.phase} | {r.regime} | {r.n} | {_pct(r.mean_fwd_2)} | {thin} |")

    out += ["", "_S1 spec(`docs/specs/2026-07-07-memory-astrategy-optimization-design.md` §S1)"
            "验收①的报告端。v1 权重/分段阈值仍是待校准先验;样本积累后再谈调权重/接菜单。_"]
    return out


def main() -> int:
    from autoresearch.scan import temperature as T
    path = Path(T.CSV_PATH)
    out_path = Path("reports/research/temperature_calib.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        out_path.write_text(
            "# S1 情绪温度计校准报告\n\n_`temperature.csv` 缺,未回填"
            "(先跑 `python -m autoresearch.scan.temperature backfill <start>`)。_\n",
            encoding="utf-8")
        print(f"[temperature_calib] {path} 缺 → 空稿 → {out_path}")
        return 0
    temp = pd.read_csv(path, dtype={"date": str})
    if not len(temp):
        out_path.write_text("# S1 情绪温度计校准报告\n\n_`temperature.csv` 空表_\n", encoding="utf-8")
        print(f"[temperature_calib] temperature.csv 空表 → 空稿 → {out_path}")
        return 0
    fwd = forward_returns(sorted(temp["date"].astype(str)))
    regime = regime_by_date()
    phase_tbl = phase_table(temp, fwd)
    cross_tbl = phase_regime_table(temp, fwd, regime)
    out_path.write_text("\n".join(render(phase_tbl, cross_tbl, len(temp))) + "\n", encoding="utf-8")
    print(f"[temperature_calib] {len(temp)} 日 → phase 分组 {len(phase_tbl)} 档 · "
          f"phase×regime 交叉 {len(cross_tbl)} 格 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
