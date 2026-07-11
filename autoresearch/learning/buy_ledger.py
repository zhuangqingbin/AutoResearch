#!/usr/bin/env python3
"""买单 ledger —— 买后管理度量 + 评级基率(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-portfolio-memory-design.md §2

系统推了买单之后从没人记账:T+1/T+5/T+10 走成什么样?目标价现实吗?开盘 gap 吃掉多少
edge?本模块逐买单落账(来源=attribution 已实现 fwd + 卡片目标价 + L1 收盘),并聚出
**评级基率**("本系统 OW 历史 T+5 胜率 X%")——样本 ≥min_n 后注入 skeptic/PM 当先验。

  uv run --no-sync python -m autoresearch.learning.buy_ledger   # → reports/learning/buy_ledger.md
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

_COLS = ["date", "code", "name", "rating", "gap_open", "fwd_1", "fwd_2", "fwd_5", "fwd_10",
         "hi_10", "hi_2", "target_ret", "target_hit"]
_TARGET_RE = re.compile(r"(\d+(?:\.\d+)?)")
_SCHEMA_SWITCH = "2026-07-10"   # 卡契约 v3(超短)生效日:此前卡=10日语义按 hi_10 判,此后按 hi_2
_HI2_MIN_N = 10   # hi2 校准分组样本门槛(⚠禁注惯例:n<此值的分组丢弃/禁注,all/by_regime 共用)


def _hi_col_for(day: str) -> str:
    """日期分界唯一出处:switch 起 v3 超短卡按 `hi_2_oc`,旧 swing 卡按 `hi_10_oc`。"""
    return "hi_2_oc" if str(day) >= _SCHEMA_SWITCH else "hi_10_oc"


def target_hit_for(day: str, tr: float | None, row) -> bool | None:
    """日期分界触价命中:目标幅(close_D 基)rebase 到 o1 基,与对应窗口 MFE 比。

    switch 日(`_SCHEMA_SWITCH`)起卡契约 v3(超短)生效,窗口收窄到 2 日 → 按 `hi_2_oc` 判;
    之前的卡是 10 日语义 → 按 `hi_10_oc` 判(不拿新窗口冤枉旧卡)。缺值 → None(诚实标未成熟)。
    """
    if tr is None:
        return None
    col = _hi_col_for(day)
    hi = pd.to_numeric(pd.Series([row.get(col)]), errors="coerce").iloc[0]
    if pd.isna(hi):
        return None
    gap = pd.to_numeric(pd.Series([row.get("gap_d1")]), errors="coerce").iloc[0]
    t_entry = (1 + tr) / (1 + gap) - 1 if not pd.isna(gap) else tr
    return bool(hi >= t_entry)


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
            hi10, hi2, gap = _a("hi_10_oc"), _a("hi_2_oc"), _a("gap_d1")
            hit = (target_hit_for(d.name, tr, attr.loc[code])
                   if (attr is not None and code in attr.index) else None)
            rows.append({"date": d.name, "code": code, "name": names.get(code, ""),
                         "rating": rating, "gap_open": gap, "fwd_1": _a("fwd_1_oo"),
                         "fwd_2": _a("fwd_2_oc"),
                         "fwd_5": f5, "fwd_10": f10, "hi_10": hi10, "hi_2": hi2,
                         "target_ret": tr, "target_hit": hit})
    return pd.DataFrame(rows, columns=_COLS)


def target_calibration(scan_root: Path | str | None = None, window: int = 30,
                       min_n: int = 10) -> dict | None:
    """全卡目标触达统计(近 window 个 scan 日,**全评级**非只 ≥OW —— 0 买期样本不断供)。

    只统计**看多目标**(tr>0;UW 向下目标负幅任何上涨都"触达",会稀释过乐观读数——
    07-05 真数据冒烟发现)+ 已成熟行(有对应窗口 MFE 列);触价口径与 roll 同款 helper
    (`target_hit_for`):目标幅(close_D 基)rebase 到 o1 基再与窗口最高比;日期分界:
    v3 起 `hi_2_oc`(2日 MFE),旧卡 `hi_10_oc`。返回 None = 无现场。spec 2026-07-05 §6。
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
            if attr is None or code not in attr.index:
                continue
            hit = target_hit_for(d.name, tr, attr.loc[code])
            if hit is None:
                continue
            col = _hi_col_for(d.name)
            hi = pd.to_numeric(pd.Series([attr.at[code, col]]), errors="coerce").iloc[0]
            targets.append(tr)
            mfes.append(float(hi))
            hits.append(hit)
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
    return (f"📐 目标价校准:近{stats['window']}scan日全卡触达率(v3 起 2 日窗) "
            f"{stats['hit_rate']:.0%}(成熟 n={stats['n_mature']};中位目标 "
            f"{stats['med_target']:+.0%} vs 中位MFE {stats['med_mfe']:+.0%})"
            f"——目标幅>{stats['med_mfe']:+.0%} 需给出超额理由")


# ---------------- 目标价 hi_2_oc 基率锚(全 universe 分布,非仅买单;task-6-brief) ----------------


def hi2_calibration(scan_root: Path | str | None = None, window: int = 30) -> dict:
    """全 universe(非仅买单)`hi_2_oc` 分布基率锚:近 window 个 scan 日 attribution.csv 全量
    `hi_2_oc` 有值行 concat,按当日 `meta.json` 的 regime 分组(缺文件/缺键该日只进 all,
    不进分组)。分位用 `series.quantile(0.5/0.6)`;`touch8_rate` = hi_2_oc≥8% 占比(即"旧
    中位目标在 2 日窗的真实触达率")。

    动机:全卡目标触达 43%、中位目标 +8% vs 中位 MFE +4% = 目标价系统性 2× 过乐观。本函数
    给 L4 卡目标价一个**基于真实 2 日 MFE 分布**的基率锚(p60),而非拍脑袋。`by_regime`
    分组 n<`_HI2_MIN_N` 直接丢弃(⚠禁注惯例);`all` 组恒返回(即便 n=0/n<阈值),是否
    据此注入简报由调用方 `target_calib_line` 按同一阈值把关。
    """
    scan_root = Path(scan_root or "context/scan")
    days: list[Path] = []
    if scan_root.exists():
        days = sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20")
        days = days[-window:]

    def _stats(vals: list[float]) -> dict:
        s = pd.Series(vals, dtype=float)
        n = len(s)
        return {"n": n,
                "hi2_p50": round(float(s.quantile(0.5)), 4) if n else None,
                "hi2_p60": round(float(s.quantile(0.6)), 4) if n else None,
                "touch8_rate": round(float((s >= 0.08).mean()), 4) if n else None}

    all_vals: list[float] = []
    regime_vals: dict[str, list[float]] = {}
    for d in days:
        attr = _read_attr(d)
        if attr is None or "hi_2_oc" not in attr.columns:
            continue
        s = pd.to_numeric(attr["hi_2_oc"], errors="coerce").dropna()
        if not len(s):
            continue
        vals = s.tolist()
        all_vals.extend(vals)
        regime = None
        mp = d / "meta.json"
        if mp.exists():
            try:
                regime = json.loads(mp.read_text(encoding="utf-8")).get("regime")
            except Exception:  # noqa: BLE001 — 坏 meta 不阻校准,该日只进 all
                regime = None
        if regime:
            regime_vals.setdefault(str(regime), []).extend(vals)

    by_regime = {r: _stats(v) for r, v in regime_vals.items() if len(v) >= _HI2_MIN_N}
    return {"all": _stats(all_vals), "by_regime": by_regime}


def write_target_calib(scan_root: Path | str | None = None, window: int = 30,
                       out_dir: Path | str | None = None) -> dict:
    """`hi2_calibration()` 落盘 → `<out_dir>/target_calib.json`(presence-gated 消费方:
    `l4_card._target_calib_mark` 读此文件组简报 📐 行)。`out_dir` 缺省 = `context/learning`。
    一次性:`python -c "from autoresearch.learning.buy_ledger import write_target_calib as w; w()"`。
    """
    calib = hi2_calibration(scan_root=scan_root, window=window)
    out = Path(out_dir) if out_dir else Path("context/learning")
    out.mkdir(parents=True, exist_ok=True)
    (out / "target_calib.json").write_text(
        json.dumps(calib, ensure_ascii=False, indent=2), encoding="utf-8")
    return calib


def target_calib_line(calib: dict | None, regime: str | None,
                      min_n: int = _HI2_MIN_N) -> str | None:
    """L4 逐卡块 📐 行(`hi_2_oc` 全 universe 分布基率锚)。presence-gated:全体 n<min_n →
    None(⚠禁注惯例,整行不注);同 regime 分组若在 `by_regime` 出现(`hi2_calibration` 已按
    `_HI2_MIN_N` 过滤)才追加第二段,否则只报全体。
    """
    if not calib:
        return None
    allg = calib.get("all") or {}
    n_all = allg.get("n") or 0
    p60 = allg.get("hi2_p60")
    if n_all < min_n or p60 is None:
        return None
    t8 = allg.get("touch8_rate")
    line = f"📐 目标校准:全体 2 日 MFE p60={p60:+.1%}(n={n_all}"
    line += f"·+8%目标历史触达 {t8:.0%})" if t8 is not None else ")"
    rg = (calib.get("by_regime") or {}).get(regime) if regime else None
    if rg and rg.get("hi2_p60") is not None:
        line += f"·同 regime p60={rg['hi2_p60']:+.1%}(n={rg['n']})"
    return line + "——目标价超 p60 须在卡内给硬理由"


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
            f"- 有目标价卡 n={calib['n']},成熟(有对应窗口MFE)n={calib['n_mature']};"
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

    out += ["| 日期 | 股票 | 评级 | gap开盘 | fwd_1 | fwd_2 | fwd_5 | fwd_10 | 触价hi10 | hi_2 | 目标幅 | 命中 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in ledger.itertuples(index=False):
        hit = "—" if r.target_hit is None or pd.isna(r.target_hit) else ("✅" if r.target_hit else "✗")
        out.append(f"| {r.date} | {r.name}({r.code}) | {r.rating} | {f(r.gap_open)} "
                   f"| {f(r.fwd_1)} | {f(r.fwd_2)} | {f(r.fwd_5)} | {f(r.fwd_10)} | {f(r.hi_10)} "
                   f"| {f(r.hi_2)} | {f(r.target_ret)} | {hit} |")
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
