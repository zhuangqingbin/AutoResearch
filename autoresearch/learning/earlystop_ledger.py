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

_COLS = ["date", "code", "phase", "reason", "fwd_2_oc"]
_MIN_N = 10          # 停因桶 n<10 一律自标"样本不足",禁止据此改规则


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    """聚合 context/scan/*/_early_stop.json × retro/attribution.csv → 逐票早停行。"""
    scan_root = Path(scan_root or "context/scan")
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
                if "code" in adf.columns and "fwd_2_oc" in adf.columns:
                    adf["code"] = adf["code"].astype(str).str.zfill(6)
                    fwd = dict(zip(adf["code"],
                                   pd.to_numeric(adf["fwd_2_oc"], errors="coerce"), strict=True))
            except Exception:  # noqa: BLE001
                fwd = {}
        for code, meta in stops.items():
            c = str(code).zfill(6)
            v = fwd.get(c)
            rows.append({"date": date, "code": c,
                         "phase": (meta or {}).get("phase", ""),
                         "reason": (meta or {}).get("reason", "其他"),
                         "fwd_2_oc": None if v is None or pd.isna(v) else float(v)})
    return pd.DataFrame(rows, columns=_COLS).sort_values(["date", "code"]).reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    """ledger → markdown(停因桶汇总 + 逐日计数);空 → 占位行。"""
    out = ["# 早停账本(早停杀对了没有 · 主尺 fwd_2_oc)", ""]
    if ledger is None or not len(ledger):
        return out + ["_无早停记录(卡片 `**早停**` 行未落或尚未跑过带早停的扫描)_"]
    mature = ledger[ledger["fwd_2_oc"].notna()]
    out += [f"- 累计早停 {len(ledger)} 张(其中 fwd 已成熟 {len(mature)} 张)", "",
            "| 停因 | n | fwd_2_oc 均值 | 已成熟 n | 裁决资格 |", "|---|---:|---:|---:|---|"]
    for reason, g in ledger.groupby("reason"):
        gm = g[g["fwd_2_oc"].notna()]
        mean = f"{gm['fwd_2_oc'].mean() * 100:+.2f}%" if len(gm) else "—"
        ok = "可裁决" if len(gm) >= _MIN_N else f"样本不足(需 ≥{_MIN_N})"
        out.append(f"| {reason} | n={len(g)} | {mean} | {len(gm)} | {ok} |")
    out += ["", "_停因桶均值显著为正 = 该桶早停在误杀(据此提案改 playbook,须用户点头);"
            "为负 = 早停是纪律。n<10 的桶一律不作结论。_"]
    return out


def main() -> int:
    df = roll()
    p = Path("reports/learning/earlystop_ledger.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(render(df)) + "\n", encoding="utf-8")
    print(f"[earlystop_ledger] {len(df)} 行 → {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
