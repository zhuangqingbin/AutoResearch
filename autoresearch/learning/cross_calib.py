#!/usr/bin/env python3
"""跨层校准环 —— 层间一致性读数(确定性,零 LLM)。

design: docs/specs/2026-07-05-scan-metering-calibration-wave-design.md §7

两张 join 报表:① **L3→L4 翻案率 per lane**(L3 高确信被 L4 压 ≤UW 的历史倾向,建议行贴
L3 校准块旁);② **rubric 门柱级拦对/错杀**(binding gate = 唯一✗门 × attribution 前向,
机会成本红队的确定性对账面;口径对齐 gate_ledger:ex = 被拦票 fwd − 全市场均值,主口径 T+2
(ex2<0=拦对,错杀 = ex2>0 且触价命中卡内目标——日期分界:v3 起 hi_2_oc,旧卡 hi_10_oc;
ex5 保留供参考)。**校准不改门/权重/评级**——只给判断层"你自己的历史倾向"数字。

`flip_stats` 的 `flip_rate` 是**收缩估计**(design 2026-07-12-selflearning-optimization-
brainstorm.md §4 P0-3,C9-C12):p̂=(n·p_桶+k·p_全局)/(n+k),n_hiconv<3(`shrink.MIN_N_INJECT`)
仍绝对禁注(`flip_rate=None`);`min_n`(默认10)降级为 ⚠ 薄样本标记,不再是排除门槛——
裁决门槛(改不改机制)与注入锚(此处)双轨语义不混,见 brainstorm §4 排序原则。

  uv run --no-sync python -m autoresearch.learning.cross_calib  # → reports/learning/cross_calib.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.learning.shrink import MIN_N_INJECT, n_tag, shrink as _shrink_fn, shrink_config

_FLIP_COLS = ["lane", "n", "n_hiconv", "flip_rate", "triage_n", "triage_hit", "thin"]
_GATE_COLS = ["gate", "n_blocked", "n_realized", "mean_ex2", "mean_ex5", "block_ok_rate",
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
               min_n: int = 10, shrink: bool | None = None,
               k: float | None = None) -> pd.DataFrame:
    """L3_judged × L4 最终评级(verify 折回后)→ 每 lane 高确信翻案率。无卡行不入分母。

    `flip_rate` = 收缩估计 p̂=(n·p_桶+k·p_全局)/(n+k)(`p_全局` = 全部 lane 池化的高确信
    翻案率)。`shrink`/`k` 缺省(`None`)→ 读 `scan_config.json` 的 `learning.{shrink,shrink_k}`
    (缺配置 → shrink=True,k=15 新基线);显式传参可覆盖(供测试/回放用)。n_hiconv<3
    (`shrink.MIN_N_INJECT`)→ `flip_rate=None`,不受 `shrink`/`k` 影响(绝对禁注)。
    `thin`(n_hiconv<`min_n`,默认10)只是 ⚠ 薄样本标记,不再是排除条件——双轨语义:
    `min_n` 仍是"这一行数据薄不薄"的既有阈值文化,`MIN_N_INJECT` 才是"还展不展示"的硬 floor。
    """
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

    if shrink is None or k is None:
        cfg_on, cfg_k = shrink_config()
        shrink = cfg_on if shrink is None else shrink
        k = cfg_k if k is None else k

    conv_all = pd.to_numeric(matched.get("conviction"), errors="coerce")
    hi_all = matched[conv_all >= _HICONV]
    p_global = float(hi_all["final"].isin(_LOW).mean()) if len(hi_all) else None

    out = []
    for lane, g in matched.groupby("lane"):
        conv = pd.to_numeric(g.get("conviction"), errors="coerce")
        hi = g[conv >= _HICONV]
        tg = g[g["triage_lean"] == "回避"] if "triage_lean" in g.columns else g.iloc[0:0]
        n_hi = len(hi)
        raw = float(hi["final"].isin(_LOW).mean()) if n_hi else None
        if raw is None or n_hi < MIN_N_INJECT:
            flip_rate = None
        elif shrink:
            shrunk = _shrink_fn(raw, n_hi, p_global, k)
            flip_rate = round(float(shrunk), 3) if shrunk is not None else None
        else:
            flip_rate = round(raw, 3)
        out.append({"lane": lane, "n": len(g), "n_hiconv": n_hi,
                    "flip_rate": flip_rate,
                    "triage_n": len(tg),
                    "triage_hit": round(float(tg["final"].isin(_LOW).mean()), 3) if len(tg) else None,
                    "thin": n_hi < min_n})
    return (pd.DataFrame(out, columns=_FLIP_COLS)
            .sort_values("n_hiconv", ascending=False).reset_index(drop=True))


def gate_stats(scan_root: Path | str | None = None, window: int = 30,
               min_n: int = 10) -> pd.DataFrame:
    """Gate bucket × attribution → 每门拦对率/错杀率。

    主口径 T+2(`ex2`,fwd_2_oc);T+5(`ex5`)保留供参考。触价命中走卡契约日期分界
    (`buy_ledger.target_hit_for`:switch 日起按 hi_2_oc 判,旧卡按 hi_10_oc 判)。
    DecisionRecord 存在时以结构化事实为准:唯一 FAIL、多个 FAIL、UNKNOWN 分别计入
    本门、"多门"、"不可判";仅历史日缺 DecisionRecord 时才解析 Markdown。
    """
    from autoresearch.learning.buy_ledger import (  # lazy 防环
        _read_attr,
        _target_ret,
        target_hit_for,
    )
    from autoresearch.learning.rejection_attribution import decision_gate_bucket
    from autoresearch.scan.l4.parsers import gate_status
    from autoresearch.scan.decision_read_model import read_decisions
    from autoresearch.scan.health import final_ratings
    rows = []
    for d in _days(scan_root, window):
        attr = _read_attr(d)
        m2 = (pd.to_numeric(attr["fwd_2_oc"], errors="coerce").mean()
              if attr is not None and "fwd_2_oc" in attr.columns else None)
        m5 = (pd.to_numeric(attr["fwd_5_oc"], errors="coerce").mean()
              if attr is not None and "fwd_5_oc" in attr.columns else None)
        if (d / "decision_records.json").exists():
            gate_rows = [
                (code, gate)
                for code, decision in read_decisions(d).items()
                if (gate := decision_gate_bucket(decision)) is not None
            ]
        else:
            gate_rows = []
            for code in final_ratings(d):
                p = d / "details" / f"{code}.md"
                if not p.exists():
                    continue
                st = gate_status(p.read_text(encoding="utf-8"))
                if not st:
                    continue
                failed = [gate for gate, is_failed in st.items() if is_failed]
                if failed:
                    gate_rows.append(
                        (code, failed[0] if len(failed) == 1 else "多门")
                    )
        for code, gate in gate_rows:
            ex2 = ex5 = hit = None
            if attr is not None and code in attr.index:
                def _num(col, code=code, attr=attr):
                    if col not in attr.columns:
                        return None
                    v = pd.to_numeric(pd.Series([attr.at[code, col]]), errors="coerce").iloc[0]
                    return None if pd.isna(v) else float(v)
                f2, f5 = _num("fwd_2_oc"), _num("fwd_5_oc")
                if f2 is not None and m2 is not None and not pd.isna(m2):
                    ex2 = f2 - float(m2)
                if f5 is not None and m5 is not None and not pd.isna(m5):
                    ex5 = f5 - float(m5)
                tr = _target_ret(d, code)
                hit = target_hit_for(d.name, tr, attr.loc[code])
            rows.append({"gate": gate, "ex2": ex2, "ex5": ex5, "hit": hit})
    if not rows:
        return pd.DataFrame(columns=_GATE_COLS)
    out = []
    for gate, g in pd.DataFrame(rows).groupby("gate"):
        ex2c = pd.to_numeric(g["ex2"], errors="coerce").dropna()
        ex5c = pd.to_numeric(g["ex5"], errors="coerce").dropna()
        mk = g.dropna(subset=["ex2", "hit"])              # 错杀列:缺目标价/未成熟票剔除
        miss = (pd.to_numeric(mk["ex2"], errors="coerce") > 0) & mk["hit"].astype(bool)
        out.append({"gate": gate, "n_blocked": len(g), "n_realized": len(ex2c),
                    "mean_ex2": round(float(ex2c.mean()), 4) if len(ex2c) else None,
                    "mean_ex5": round(float(ex5c.mean()), 4) if len(ex5c) else None,
                    "block_ok_rate": round(float((ex2c < 0).mean()), 3) if len(ex2c) else None,
                    "misskill_n": len(mk),
                    "misskill_rate": round(float(miss.mean()), 3) if len(mk) else None,
                    "thin": len(g) < min_n})
    return (pd.DataFrame(out, columns=_GATE_COLS)
            .sort_values("n_blocked", ascending=False).reset_index(drop=True))


def suggestion_lines(flips: pd.DataFrame, gates: pd.DataFrame,
                     min_n: int = 10) -> list[str]:
    """两条建议行(编排层手贴:🔁 → L3 校准块旁;🚪 → skeptic/PM 先验旁);thin → 禁注。

    🔁 行:`flip_stats` 已把 `flip_rate` 收缩且 n_hiconv<3 禁注(`flip_rate=None`),本函数
    只挑 flip_rate 最高的 lane 报一行,`(n=X)` 尾标经 `n_tag` 按 `min_n` 判 ⚠(P0-3:薄样本
    不再二值排除,只标记)。全体 lane 皆 `flip_rate` 缺失(即全 <3)才落回"先积累"占位文案。
    """
    lines: list[str] = []
    if flips is not None and len(flips):
        ok = flips[flips["flip_rate"].notna()]
        if len(ok):
            w = ok.sort_values("flip_rate", ascending=False).iloc[0]
            lines.append(f"🔁 L3校准:{w['lane']} lane 高确信(conviction≥{_HICONV})被 L4 "
                         f"翻案 {w['flip_rate']:.0%}{n_tag(w['n_hiconv'], min_n)}"
                         f"——该 lane 论点请先自证翻案主因")
        else:
            lines.append(f"🔁 L3校准:各 lane 高确信样本 <{MIN_N_INJECT} ⚠样本少·禁注,先积累")
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
    out += ["", "## 🚪 rubric 门柱级拦对/错杀(binding=唯一✗门;ex2<0=拦对(主口径,T+2);"
            "错杀=ex2>0 且触价命中卡内目标——日期分界:v3 起 hi_2,旧卡 hi_10;ex5 列供参考)", ""]
    if gates is None or not len(gates):
        out.append("_无门柱 × attribution 数据_")
    else:
        out += ["| 门 | 拦次 | 已实现 | 被拦ex2(主) | 被拦ex5(参考) | 拦对率 | 错杀n | 错杀率 | |",
                "|---|---|---|---|---|---|---|---|---|"]
        for r in gates.itertuples(index=False):
            thin = "⚠样本少" if r.thin else ""
            ex2 = "—" if r.mean_ex2 is None or pd.isna(r.mean_ex2) else f"{r.mean_ex2 * 100:+.2f}%"
            ex5 = "—" if r.mean_ex5 is None or pd.isna(r.mean_ex5) else f"{r.mean_ex5 * 100:+.2f}%"
            out.append(f"| {r.gate} | {r.n_blocked} | {r.n_realized} | {ex2} | {ex5} "
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
