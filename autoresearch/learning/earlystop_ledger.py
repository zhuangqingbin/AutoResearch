#!/usr/bin/env python3
"""早停账本 —— 回答"早停到底杀对了没有"(确定性,零 LLM)。

design: docs/specs/2026-07-25-scan-wave5-live-mainruler-macro-metering-design.md §②C

背景:0 买的主导机制是早停(07-21 实测 12 卡中 6 张早停),但早停卡按定义压 ≤Hold、
不写 OW三门段 —— 门直方图看不见,任何账本也没数过。本表把 `_early_stop.json` 的停因
桶 join 上 retro attribution 的 fwd_2_oc(超短主尺),让"强势票早停是误杀还是纪律"
在 ≥10 日后可裁决。**本表只攒数据,不改任何早停规则。**

  uv run --no-sync python -m autoresearch.learning.earlystop_ledger  # → reports/learning/earlystop_ledger.md
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.common.ruler import MAIN_RULER

_COLS = ["date", "code", "phase", "reason", "fwd_2_oc", "ruler"]
# fwd_2_oc:参考尺,固定列名,勿随主尺漂移(持久化 ledger 列);取值来自 attribution.csv 的
# MAIN_RULER 列,故 ruler 与它同行入账,标记这一刻(roll() 调用时)MAIN_RULER 的真值。
_MIN_N = 10          # 停因桶 n<10 一律自标"样本不足",禁止据此改规则


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    """聚合 context/scan/*/_early_stop.json × retro/attribution.csv → 逐票早停行。"""
    scan_root = Path(scan_root or ws.scan_root())
    rows: list[dict] = []
    for p in sorted(scan_root.glob("*/_early_stop.json")):
        try:
            stops = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 半截文件跳过,不阻断整表
            continue
        if not stops:
            continue
        date = p.parent.name
        fwd: dict[str, float] = {}
        ap = p.parent / "retro" / "attribution.csv"
        if ap.is_file():
            try:
                adf = pd.read_csv(ap, dtype={"code": str})
                if "code" in adf.columns and MAIN_RULER in adf.columns:
                    adf["code"] = adf["code"].astype(str).str.zfill(6)
                    fwd = dict(zip(adf["code"],
                                   pd.to_numeric(adf[MAIN_RULER], errors="coerce"), strict=True))
            except Exception:  # noqa: BLE001
                fwd = {}
        for code, meta in stops.items():
            c = str(code).zfill(6)
            v = fwd.get(c)
            rows.append({"date": date, "code": c,
                         "phase": (meta or {}).get("phase", ""),
                         "reason": (meta or {}).get("reason", "其他"),
                         "fwd_2_oc": None if v is None or pd.isna(v) else float(v),
                         "ruler": MAIN_RULER})
    return pd.DataFrame(rows, columns=_COLS).sort_values(["date", "code"]).reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    """ledger → markdown(停因桶汇总 + 逐日计数);空 → 占位行。

    按 (停因, ruler) 分段汇总 —— T16 flip 前后的行混在同一停因桶时,`fwd_2_oc` 均值绝不能
    跨尺合并算(fwd_2_oc 尺与 gap_c1_o2 尺是两把不同的尺,揉在一起是算混合物,这条 task
    的灵魂)。roll() 产出的行必带 ruler;防御性兜底给非 roll() 来源的旧结构。
    """
    out = [f"# 早停账本(早停杀对了没有 · 主尺 {MAIN_RULER})", ""]
    if ledger is None or not len(ledger):
        return out + ["_无早停记录(卡片 `**早停**` 行未落或尚未跑过带早停的扫描)_"]
    ledger = ledger.copy()
    if "ruler" in ledger.columns:
        ledger["ruler"] = ledger["ruler"].fillna("fwd_2_oc")
    else:
        ledger["ruler"] = "fwd_2_oc"
    # 下面读的是本 ledger 自己 `_COLS` 定的固定列 "fwd_2_oc"(见上),不是 attribution 源列,
    # 勿改成 MAIN_RULER —— roll() 写入时已固定该名,ledger 历史行永远叫这个名字。
    mature = ledger[ledger["fwd_2_oc"].notna()]
    out += [f"- 累计早停 {len(ledger)} 张(其中 fwd 已成熟 {len(mature)} 张)", ""]
    rulers = sorted(ledger["ruler"].unique())
    if len(rulers) > 1:
        out.append(f"- ⚠️ 本表跨 {len(rulers)} 种尺({'、'.join(rulers)}),下表已按尺分段,"
                    "禁止跨尺合并均值。")
    out += ["", "| 停因 | 尺 | n | fwd_2_oc 均值 | 已成熟 n | 裁决资格 |",
            "|---|---|---:|---:|---:|---|"]
    for (reason, ruler), g in ledger.groupby(["reason", "ruler"]):
        gm = g[g["fwd_2_oc"].notna()]
        mean = f"{gm['fwd_2_oc'].mean() * 100:+.2f}%" if len(gm) else "—"
        ok = "可裁决" if len(gm) >= _MIN_N else f"样本不足(需 ≥{_MIN_N})"
        out.append(f"| {reason} | {ruler} | n={len(g)} | {mean} | {len(gm)} | {ok} |")
    out += ["", "_停因桶均值显著为正 = 该桶早停在误杀(据此提案改 playbook,须用户点头);"
            "为负 = 早停是纪律。n<10 的桶一律不作结论。_"]
    return out


def main() -> int:
    df = roll()
    p = ws.reports_root() / "learning/earlystop_ledger.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(render(df)) + "\n", encoding="utf-8")
    print(f"[earlystop_ledger] {len(df)} 行 → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
