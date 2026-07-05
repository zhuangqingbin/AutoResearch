#!/usr/bin/env python3
"""跨层校准环 —— 层间一致性读数(确定性,零 LLM)。

design: docs/specs/2026-07-05-scan-metering-calibration-wave-design.md §7

两张 join 报表:① **L3→L4 翻案率 per lane**(L3 高确信被 L4 压 ≤UW 的历史倾向,建议行贴
L3 校准块旁);② **rubric 门柱级拦对/错杀**(binding gate = 唯一✗门 × attribution 前向,
机会成本红队的确定性对账面;口径对齐 gate_ledger:ex = 被拦票 fwd − 全市场均值,ex5<0=拦对,
错杀 = ex5>0 且 hi_10 触达卡内目标)。**校准不改门/权重/评级**——只给判断层"你自己的
历史倾向"数字;n<min_n thin 禁注。

  uv run --no-sync python -m autoresearch.learning.cross_calib  # → reports/learning/cross_calib.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

_FLIP_COLS = ["lane", "n", "n_hiconv", "flip_rate", "triage_n", "triage_hit", "thin"]
_GATE_COLS = ["gate", "n_blocked", "n_realized", "mean_ex5", "block_ok_rate",
              "misskill_n", "misskill_rate", "thin"]
_LOW = ("Underweight", "Sell")
_HICONV = 70


def _days(scan_root: Path | str | None, window: int) -> list[Path]:
    scan_root = Path(scan_root or "context/scan")
    if not scan_root.exists():
        return []
    days = sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20")
    return days[-window:]


def flip_stats(scan_root: Path | str | None = None, window: int = 30,
               min_n: int = 10) -> pd.DataFrame:
    """L3_judged × L4 最终评级(verify 折回后)→ 每 lane 高确信翻案率。无卡行不入分母。"""
    from autoresearch.scan.health import final_ratings  # lazy 防环
    rows = []
    for d in _days(scan_root, window):
        jp = d / "L3_judged_full.csv"
        if not jp.exists():
            continue
        try:
            j = pd.read_csv(jp, dtype={"code": str})
        except Exception:  # noqa: BLE001
            continue
        if "code" not in j.columns or "lane" not in j.columns:
            continue
        j["code"] = j["code"].astype(str).str.zfill(6)
        j["final"] = j["code"].map(final_ratings(d))
        rows.append(j)
    if not rows:
        return pd.DataFrame(columns=_FLIP_COLS)
    matched = pd.concat(rows, ignore_index=True)
    matched = matched[matched["final"].notna()]
    out = []
    for lane, g in matched.groupby("lane"):
        conv = pd.to_numeric(g.get("conviction"), errors="coerce")
        hi = g[conv >= _HICONV]
        tg = g[g["triage_lean"] == "回避"] if "triage_lean" in g.columns else g.iloc[0:0]
        out.append({"lane": lane, "n": len(g), "n_hiconv": len(hi),
                    "flip_rate": round(float(hi["final"].isin(_LOW).mean()), 3) if len(hi) else None,
                    "triage_n": len(tg),
                    "triage_hit": round(float(tg["final"].isin(_LOW).mean()), 3) if len(tg) else None,
                    "thin": len(hi) < min_n})
    return (pd.DataFrame(out, columns=_FLIP_COLS)
            .sort_values("n_hiconv", ascending=False).reset_index(drop=True))


def gate_stats(scan_root: Path | str | None = None, window: int = 30,
               min_n: int = 10) -> pd.DataFrame:
    """binding gate(唯一✗门;≥2✗ 计"多门")× attribution → 每门拦对率/错杀率。"""
    from autoresearch.learning.buy_ledger import _read_attr, _target_ret  # lazy 防环
    from autoresearch.scan.assemble import gate_status
    from autoresearch.scan.health import final_ratings
    rows = []
    for d in _days(scan_root, window):
        attr = _read_attr(d)
        m5 = (pd.to_numeric(attr["fwd_5_oc"], errors="coerce").mean()
              if attr is not None and "fwd_5_oc" in attr.columns else None)
        for code in final_ratings(d):
            p = d / "details" / f"{code}.md"
            if not p.exists():
                continue
            st = gate_status(p.read_text(encoding="utf-8"))
            if not st:
                continue
            failed = [g for g, f in st.items() if f]
            if not failed:
                continue
            gate = failed[0] if len(failed) == 1 else "多门"
            ex5 = hit = None
            if attr is not None and code in attr.index:
                def _num(col, code=code, attr=attr):
                    if col not in attr.columns:
                        return None
                    v = pd.to_numeric(pd.Series([attr.at[code, col]]), errors="coerce").iloc[0]
                    return None if pd.isna(v) else float(v)
                f5, hi10, gap = _num("fwd_5_oc"), _num("hi_10_oc"), _num("gap_d1")
                if f5 is not None and m5 is not None and not pd.isna(m5):
                    ex5 = f5 - float(m5)
                tr = _target_ret(d, code)
                if tr is not None and hi10 is not None:   # 触价口径同 buy_ledger(o1 基 rebase)
                    t_entry = (1 + tr) / (1 + gap) - 1 if gap is not None else tr
                    hit = bool(hi10 >= t_entry)
            rows.append({"gate": gate, "ex5": ex5, "hit": hit})
    if not rows:
        return pd.DataFrame(columns=_GATE_COLS)
    out = []
    for gate, g in pd.DataFrame(rows).groupby("gate"):
        ex = pd.to_numeric(g["ex5"], errors="coerce").dropna()
        mk = g.dropna(subset=["ex5", "hit"])              # 错杀列:缺目标价/未成熟票剔除
        miss = (pd.to_numeric(mk["ex5"], errors="coerce") > 0) & mk["hit"].astype(bool)
        out.append({"gate": gate, "n_blocked": len(g), "n_realized": len(ex),
                    "mean_ex5": round(float(ex.mean()), 4) if len(ex) else None,
                    "block_ok_rate": round(float((ex < 0).mean()), 3) if len(ex) else None,
                    "misskill_n": len(mk),
                    "misskill_rate": round(float(miss.mean()), 3) if len(mk) else None,
                    "thin": len(g) < min_n})
    return (pd.DataFrame(out, columns=_GATE_COLS)
            .sort_values("n_blocked", ascending=False).reset_index(drop=True))


def suggestion_lines(flips: pd.DataFrame, gates: pd.DataFrame,
                     min_n: int = 10) -> list[str]:
    """两条建议行(编排层手贴:🔁 → L3 校准块旁;🚪 → skeptic/PM 先验旁);thin → 禁注。"""
    lines: list[str] = []
    if flips is not None and len(flips):
        ok = flips[(pd.to_numeric(flips["n_hiconv"], errors="coerce") >= min_n)
                   & flips["flip_rate"].notna()]
        if len(ok):
            w = ok.sort_values("flip_rate", ascending=False).iloc[0]
            lines.append(f"🔁 L3校准:{w['lane']} lane 高确信(conviction≥{_HICONV})被 L4 "
                         f"翻案 {w['flip_rate']:.0%}(n={int(w['n_hiconv'])})"
                         f"——该 lane 论点请先自证翻案主因")
        else:
            lines.append(f"🔁 L3校准:各 lane 高确信样本 <{min_n} ⚠样本少·禁注,先积累")
    if gates is not None and len(gates):
        ok = gates[(pd.to_numeric(gates["n_blocked"], errors="coerce") >= min_n)
                   & gates["misskill_rate"].notna()]
        if len(ok):
            w = ok.sort_values("misskill_rate", ascending=False).iloc[0]
            bo = "—" if pd.isna(w["block_ok_rate"]) else format(w["block_ok_rate"], ".0%")
            lines.append(f"🚪 门校准:{w['gate']} 拦 {int(w['n_blocked'])} 次,拦对率 {bo},"
                         f"错杀率 {w['misskill_rate']:.0%}——供 skeptic/红队先验,不改门")
        else:
            lines.append(f"🚪 门校准:各门样本 <{min_n} ⚠样本少·禁注,先积累")
    return lines


def render(flips: pd.DataFrame, gates: pd.DataFrame) -> list[str]:
    out = ["# 跨层校准环(L3→L4 翻案 + rubric 门柱级拦对/错杀;只读数,不改门/权重/评级)", ""]

    def f(x, pct=True):
        if x is None or pd.isna(x):
            return "—"
        return format(x, ".0%") if pct else str(x)

    out += ["## 🔁 L3→L4 翻案率(per lane;高确信=conviction≥70,翻案=L4 ≤Underweight)", ""]
    if flips is None or not len(flips):
        out.append("_无 L3_judged × 卡片数据_")
    else:
        out += ["| lane | n | 高确信n | 翻案率 | 回避n | 回避命中 | |", "|---|---|---|---|---|---|---|"]
        for r in flips.itertuples(index=False):
            thin = "⚠样本少" if r.thin else ""
            out.append(f"| {r.lane} | {r.n} | {r.n_hiconv} | {f(r.flip_rate)} "
                       f"| {r.triage_n} | {f(r.triage_hit)} | {thin} |")
    out += ["", "## 🚪 rubric 门柱级拦对/错杀(binding=唯一✗门;ex5<0=拦对;"
            "错杀=ex5>0 且 hi_10 触达卡内目标)", ""]
    if gates is None or not len(gates):
        out.append("_无门柱 × attribution 数据_")
    else:
        out += ["| 门 | 拦次 | 已实现 | 被拦ex5 | 拦对率 | 错杀n | 错杀率 | |",
                "|---|---|---|---|---|---|---|---|"]
        for r in gates.itertuples(index=False):
            thin = "⚠样本少" if r.thin else ""
            ex = "—" if r.mean_ex5 is None or pd.isna(r.mean_ex5) else f"{r.mean_ex5 * 100:+.2f}%"
            out.append(f"| {r.gate} | {r.n_blocked} | {r.n_realized} | {ex} "
                       f"| {f(r.block_ok_rate)} | {r.misskill_n} | {f(r.misskill_rate)} | {thin} |")
    out += ["", "_拦对/错杀不互补(中间地带=拦了但未触达目标);错杀持续高的门 → 走 proposal "
            "人拍板,本报表不自动动门。_"]
    return out


def main() -> int:
    flips, gates = flip_stats(), gate_stats()
    out = Path("reports/learning/cross_calib.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(flips, gates)) + "\n", encoding="utf-8")
    lines = suggestion_lines(flips, gates)
    print(f"[cross_calib] lanes={len(flips)} gates={len(gates)} → {out}"
          + ("".join(f"\n{ln}" for ln in lines)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
