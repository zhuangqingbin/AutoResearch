#!/usr/bin/env python3
"""sector_ledger —— 行业嘴的 MTM(Phase 4):brief 研判段方向 × 行业成分中位已实现收益。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §7 Phase 4。

- `record_calls(scan_dir, date)`:当日 sector_briefs 的 `**行业方向**` keyed 行 →
  `context/knowledge/sector_calls.jsonl`(按 date+industry 幂等;♻️复用 brief 照记——嘴每天都在说话)。
  assemble 发布时自动调(失败不阻报告)。
- `mature_call(frame0, frame1, industry)`:两日成分帧(code/industry/close,如两日 L1_scored_full)
  → 行业**同代码交集**中位 close→close 收益(纯函数;retro 侧数据成熟后 `backfill` 回填)。
- CLI 报告:按方向聚合命中率;**已成熟 n<10 只记账不下结论**(薄样本先验比没有更坏——与评级基率同纪律)。
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

LEDGER_PATH = Path("context/knowledge/sector_calls.jsonl")


def _load(path: Path | str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[dict] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001 — 坏行跳过
            continue
    return out


def record_calls(scan_dir: Path | str, date: str, path: Path | str = LEDGER_PATH) -> int:
    """当日 briefs 的方向行 → jsonl(date+industry 幂等)。无 briefs/无方向行 → 0。"""
    from autoresearch.sector.brief import extract_view, parse_direction
    d = Path(scan_dir) / "sector_briefs"
    if not d.is_dir():
        return 0
    path = Path(path)
    seen = {(c.get("date"), c.get("industry")) for c in _load(path)}
    rows: list[dict] = []
    for p in sorted(d.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        dirn = parse_direction(extract_view(text)) or parse_direction(text)
        if not dirn or (date, p.stem) in seen:
            continue
        rows.append({"date": date, "industry": p.stem, "direction": dirn,
                     "source": "brief", "realized_pct": None, "horizon": None})
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def mature_call(frame0, frame1, industry: str) -> float | None:
    """两日成分帧 → 该行业同代码交集的中位 close→close 收益 %(缺列/空交集 → None)。"""
    import pandas as pd

    def _closes(df):
        if df is None or not {"code", "industry", "close"} <= set(getattr(df, "columns", [])):
            return None
        g = df[df["industry"].astype(str) == str(industry)].copy()
        if not len(g):
            return None
        g["code"] = g["code"].astype(str).str.zfill(6)
        g["close"] = pd.to_numeric(g["close"], errors="coerce")
        g = g.dropna(subset=["close"]).drop_duplicates("code")
        return g.set_index("code")["close"]

    s0, s1 = _closes(frame0), _closes(frame1)
    if s0 is None or s1 is None:
        return None
    common = s0.index.intersection(s1.index)
    if not len(common):
        return None
    ret = (s1.loc[common] / s0.loc[common] - 1.0) * 100.0
    return round(float(ret.median()), 2)


def backfill(call: dict, frame0, frame1, horizon: str = "fwd_2") -> dict:
    """给一条 call 回填 realized(retro 侧成熟后调;返回新 dict 不改原)。"""
    out = dict(call)
    v = mature_call(frame0, frame1, call.get("industry", ""))
    if v is not None:
        out["realized_pct"], out["horizon"] = v, horizon
    return out


def render_report(calls: list[dict]) -> str:
    matured = [c for c in calls if c.get("realized_pct") is not None]
    lines = [f"# sector_ledger — 行业嘴 MTM(calls {len(calls)} · 已成熟 {len(matured)})", ""]
    if len(matured) < 10:
        lines.append(f"> ⚠ 已成熟样本 {len(matured)} < 10 —— 只记账不下结论(薄样本先验比没有更坏)。\n")
    by_dir: dict[str, list[float]] = {}
    for c in matured:
        by_dir.setdefault(str(c.get("direction", "—")), []).append(float(c["realized_pct"]))
    if by_dir:
        lines += ["| 方向 | n | 中位已实现% | 命中率 |", "|---|---|---|---|"]
        for d, vals in sorted(by_dir.items()):
            med = statistics.median(vals)
            hit = (sum(1 for v in vals if v > 0) / len(vals) if d == "看多"
                   else sum(1 for v in vals if v < 0) / len(vals) if d == "看空" else None)
            lines.append(f"| {d} | {len(vals)} | {med:+.2f} | "
                         f"{'—' if hit is None else format(hit, '.0%')} |")
    pending = [c for c in calls if c.get("realized_pct") is None]
    if pending:
        lines.append(f"\n_待成熟 {len(pending)} 条(retro 侧数据成熟后 `backfill` 回填;horizon=fwd_2 主尺)。_")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="行业嘴 MTM 报告(确定性,零 LLM)")
    ap.add_argument("--path", default=str(LEDGER_PATH))
    args = ap.parse_args(argv)
    rpt = render_report(_load(args.path))
    out = Path("reports/learning/sector_ledger.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rpt, encoding="utf-8")
    print(rpt)
    print(f"\n[sector_ledger] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
