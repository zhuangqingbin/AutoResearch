#!/usr/bin/env python3
"""买单 ledger —— 买后管理度量 + 评级基率(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-portfolio-memory-design.md §2

系统推了买单之后从没人记账:T+1/T+5/T+10 走成什么样?目标价现实吗?开盘 gap 吃掉多少
edge?本模块逐买单落账(来源=attribution 已实现 fwd + 卡片目标价 + L1 收盘),并聚出
**评级基率**("本系统 OW 历史 T+5 胜率 X%")——样本 ≥min_n 后注入 skeptic/PM 当先验。

  uv run --no-sync python -m autoresearch.learning.buy_ledger   # → reports/learning/buy_ledger.md
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

_COLS = ["date", "code", "name", "rating", "gap_open", "fwd_1", "fwd_2", "fwd_5", "fwd_10",
         "hi_10", "target_ret", "target_hit"]
_TARGET_RE = re.compile(r"(\d+(?:\.\d+)?)")


def _target_ret(scan_dir: Path, code: str) -> float | None:
    """卡片目标价(仪表盘『目标/EV目标』首个数字)÷ 当日收盘 − 1;缺 → None。"""
    p = scan_dir / "details" / f"{code}.md"
    if not p.exists():
        return None
    from autoresearch.scan.assemble import _get, _parse_dashboard
    dash = _parse_dashboard(p.read_text(encoding="utf-8"))
    m = _TARGET_RE.search(_get(dash, "EV目标", "目标") or "")
    if not m:
        return None
    target = float(m.group(1))
    lp = scan_dir / "L1_scored_full.csv"
    if not lp.exists():
        lp = scan_dir / "L1_recall_top1000.csv"
    if not lp.exists():
        return None
    try:
        l1 = pd.read_csv(lp, dtype={"code": str})
        sub = l1[l1["code"].astype(str).str.zfill(6) == code]
        close = pd.to_numeric(sub.iloc[0]["close"], errors="coerce") if len(sub) else None
    except Exception:  # noqa: BLE001
        return None
    if close is None or pd.isna(close) or not close:
        return None
    return round(float(target / close - 1.0), 4)


def _read_attr(d: Path) -> pd.DataFrame | None:
    """读该 scan 日的 attribution(code 索引);缺/坏 → None。"""
    ap = d / "retro" / "attribution.csv"
    if not ap.exists():
        return None
    try:
        attr = pd.read_csv(ap, dtype={"code": str})
        attr["code"] = attr["code"].astype(str).str.zfill(6)
        return attr.set_index("code")
    except Exception:  # noqa: BLE001
        return None


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    """逐 scan 日抽 ≥OW 买单 × attribution 已实现 fwd → ledger 帧。无买单日自然无行。"""
    from autoresearch.scan.health import final_ratings  # lazy 防环
    scan_root = Path(scan_root or "context/scan")
    rows = []
    if not scan_root.exists():
        return pd.DataFrame(columns=_COLS)
    for d in sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        ratings = {c: r for c, r in final_ratings(d).items() if r in ("Buy", "Overweight")}
        if not ratings:
            continue
        attr = _read_attr(d)
        names = {}
        fp = d / "finalists.csv"
        if fp.exists():
            try:
                fin = pd.read_csv(fp, dtype={"code": str})
                names = dict(zip(fin["code"].astype(str).str.zfill(6),
                                 fin.get("name", ""), strict=False))
            except Exception:  # noqa: BLE001
                pass
        for code, rating in ratings.items():
            def _a(col, code=code, attr=attr):
                if attr is None or code not in attr.index or col not in attr.columns:
                    return None
                v = pd.to_numeric(pd.Series([attr.at[code, col]]), errors="coerce").iloc[0]
                return None if pd.isna(v) else round(float(v), 6)
            tr = _target_ret(d, code)
            f10, f5 = _a("fwd_10_oc"), _a("fwd_5_oc")
            hi10, gap = _a("hi_10_oc"), _a("gap_d1")
            hit = None
            if tr is not None:
                if hi10 is not None:                # 触价口径:目标幅(close_D 基)换算到 o1 基与最高价比
                    t_entry = (1 + tr) / (1 + gap) - 1 if gap is not None else tr
                    hit = bool(hi10 >= t_entry)
                elif f10 is not None or f5 is not None:   # 缺 hi → 回退收盘口径
                    hit = bool((f10 if f10 is not None else f5) >= tr)
            rows.append({"date": d.name, "code": code, "name": names.get(code, ""),
                         "rating": rating, "gap_open": gap, "fwd_1": _a("fwd_1_oo"),
                         "fwd_2": _a("fwd_2_oc"),
                         "fwd_5": f5, "fwd_10": f10, "hi_10": hi10,
                         "target_ret": tr, "target_hit": hit})
    return pd.DataFrame(rows, columns=_COLS)


def target_calibration(scan_root: Path | str | None = None, window: int = 30,
                       min_n: int = 10) -> dict | None:
    """全卡目标触达统计(近 window 个 scan 日,**全评级**非只 ≥OW —— 0 买期样本不断供)。

    只统计**看多目标**(tr>0;UW 向下目标负幅任何上涨都"触达",会稀释过乐观读数——
    07-05 真数据冒烟发现)+ 已成熟行(attribution 有 hi_10_oc);触价口径与 roll 同:
    目标幅(close_D 基)rebase 到 o1 基再与 10 日最高比。返回 None = 无现场。spec 2026-07-05 §6。
    """
    from autoresearch.scan.health import final_ratings  # lazy 防环
    scan_root = Path(scan_root or "context/scan")
    if not scan_root.exists():
        return None
    days = sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20")
    days = days[-window:]
    if not days:
        return None
    n = 0
    targets, mfes, hits = [], [], []
    for d in days:
        attr = _read_attr(d)
        for code in final_ratings(d):
            tr = _target_ret(d, code)
            if tr is None or tr <= 0:        # 只看多目标:向下目标不入过乐观统计
                continue
            n += 1
            if attr is None or code not in attr.index or "hi_10_oc" not in attr.columns:
                continue
            hi10 = pd.to_numeric(pd.Series([attr.at[code, "hi_10_oc"]]), errors="coerce").iloc[0]
            if pd.isna(hi10):
                continue
            gap = pd.to_numeric(pd.Series([attr.at[code, "gap_d1"]]), errors="coerce").iloc[0] \
                if "gap_d1" in attr.columns else None
            t_entry = (1 + tr) / (1 + gap) - 1 if gap is not None and not pd.isna(gap) else tr
            targets.append(tr)
            mfes.append(float(hi10))
            hits.append(bool(hi10 >= t_entry))
    n_mature = len(hits)
    return {"n": n, "n_mature": n_mature, "window": window, "min_n": min_n,
            "hit_rate": round(sum(hits) / n_mature, 3) if n_mature else None,
            "med_target": round(float(pd.Series(targets).median()), 4) if targets else None,
            "med_mfe": round(float(pd.Series(mfes).median()), 4) if mfes else None,
            "thin": n_mature < min_n}


def calibration_line(stats: dict | None) -> str | None:
    """当日件建议行(编排层贴 `_l4_shared_instructions.md`);thin → 禁注文案。"""
    if stats is None:
        return None
    if stats["thin"]:
        return (f"📐 目标价校准:成熟样本不足(n={stats['n_mature']}<{stats['min_n']})"
                f"⚠样本少·禁注,先积累")
    return (f"📐 目标价校准:近{stats['window']}scan日全卡10日触达率 "
            f"{stats['hit_rate']:.0%}(成熟 n={stats['n_mature']};中位目标 "
            f"{stats['med_target']:+.0%} vs 中位MFE {stats['med_mfe']:+.0%})"
            f"——目标幅>{stats['med_mfe']:+.0%} 需给出超额理由")


def rating_base_rates(ledger: pd.DataFrame, min_n: int = 10) -> list[dict]:
    """按评级聚基率:n / T+2 胜率&均值(主)/ T+5 胜率&均值(参考)/ 目标命中率。

    n_realized/thin 样本口径按主尺 fwd_2 已实现数(仅当 ledger 无 fwd_2 列即旧数据时回退 fwd_5)。
    """
    if ledger is None or not len(ledger):
        return []
    out = []
    for rating, g in ledger.groupby("rating"):
        has_f2 = "fwd_2" in g.columns
        f2 = pd.to_numeric(g["fwd_2"], errors="coerce").dropna() if has_f2 else pd.Series(dtype=float)
        f5 = pd.to_numeric(g["fwd_5"], errors="coerce").dropna()
        th = g["target_hit"].dropna()
        n_realized = len(f2) if has_f2 else len(f5)
        out.append({"rating": rating, "n": len(g), "n_realized": n_realized,
                    "win2": round(float((f2 > 0).mean()), 3) if len(f2) else None,
                    "mean2": round(float(f2.mean()), 4) if len(f2) else None,
                    "win5": round(float((f5 > 0).mean()), 3) if len(f5) else None,
                    "mean5": round(float(f5.mean()), 4) if len(f5) else None,
                    "target_hit": round(float(th.mean()), 3) if len(th) else None,
                    "thin": n_realized < min_n})
    return sorted(out, key=lambda r: r["rating"])


def _calib_section(calib: dict | None) -> list[str]:
    """『全卡目标校准』节(calib=None → 不加节,presence-gated)。"""
    if calib is None:
        return []
    line = calibration_line(calib)
    return ["", f"## 📐 全卡目标校准(近{calib['window']} scan 日,全评级)",
            f"- 有目标价卡 n={calib['n']},成熟(有 hi_10)n={calib['n_mature']};"
            f"触达率 {'—' if calib['hit_rate'] is None else format(calib['hit_rate'], '.0%')},"
            f"中位目标 {'—' if calib['med_target'] is None else format(calib['med_target'], '+.0%')} "
            f"vs 中位MFE {'—' if calib['med_mfe'] is None else format(calib['med_mfe'], '+.0%')}",
            f"- 当日件建议行:{line}"]


def render(ledger: pd.DataFrame, calib: dict | None = None) -> list[str]:
    out = ["# 买单 ledger(买后 T+1/5/10 + 目标命中 + 开盘 gap;评级基率供 skeptic 先验)", ""]
    if ledger is None or not len(ledger):
        return out + ["_尚无 ≥OW 买单入账(0 买期,机制就绪等首单)_"] + _calib_section(calib)

    def f(x, pct=True):
        if x is None or pd.isna(x):
            return "—"
        return f"{x * 100:+.2f}%" if pct else str(x)

    out += ["| 日期 | 股票 | 评级 | gap开盘 | fwd_1 | fwd_2 | fwd_5 | fwd_10 | 触价hi10 | 目标幅 | 命中 |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in ledger.itertuples(index=False):
        hit = "—" if r.target_hit is None or pd.isna(r.target_hit) else ("✅" if r.target_hit else "✗")
        out.append(f"| {r.date} | {r.name}({r.code}) | {r.rating} | {f(r.gap_open)} "
                   f"| {f(r.fwd_1)} | {f(r.fwd_2)} | {f(r.fwd_5)} | {f(r.fwd_10)} | {f(r.hi_10)} "
                   f"| {f(r.target_ret)} | {hit} |")
    br = rating_base_rates(ledger)
    if br:
        out += ["", "## 评级基率(n≥10 才可注入 skeptic/PM 当先验)"]
        for b in br:
            thin = " ⚠样本少" if b["thin"] else ""
            out.append(
                f"- **{b['rating']}**:n={b['n']}(已实现 {b['n_realized']}),"
                f"**T+2 胜率 {'—' if b['win2'] is None else format(b['win2'], '.0%')}(主)**"
                f"/均值 {f(b['mean2'])},"
                f"T+5 胜率 {'—' if b['win5'] is None else format(b['win5'], '.0%')}(参考)"
                f"/均值 {f(b['mean5'])},目标命中 "
                f"{('—' if b['target_hit'] is None else format(b['target_hit'], '.0%'))}{thin}")
    out += _calib_section(calib)
    out += ["", "> fwd 列 `—` = 该日 attribution 在 fwd 成熟前写盘(retro 一次性落账)。刷新:对已成熟老日"
            "手动 `retro.attribute('<date>')` 重写 attribution 再重跑本 ledger(拉数走 factor_lab cache,幂等)。"]
    return out


def main() -> int:
    ledger = roll()
    calib = target_calibration()
    out = Path("reports/learning/buy_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(ledger, calib=calib)) + "\n", encoding="utf-8")
    line = calibration_line(calib)
    print(f"[buy_ledger] {len(ledger)} 单 → {out}" + (f"\n{line}" if line else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
