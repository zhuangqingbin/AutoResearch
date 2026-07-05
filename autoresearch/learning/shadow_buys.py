#!/usr/bin/env python3
"""影子买单 —— 每日 conviction top-k Hold 的确定性记账(零 LLM,不改评级不进报告)。

spec: 2026-07-05 wave §WS-A2。语义:"如果门不拦,系统最想买的 k 只"。与机会成本红队正交
(红队=0买日 2 只 LLM 深核进观察单;本模块=每日纯记账广度)。消费端:paper_nav 影子线、
评级基率样本池。`真实线 − 影子线` = 门的价值的日频读数。

  uv run --no-sync python -m autoresearch.learning.shadow_buys   # 回填全部历史 scan 日
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_COLS = ["date", "code", "name", "conviction", "binding", "close"]
_PATH = Path("context/learning/shadow_buys.csv")


def _load(path: Path) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame(columns=_COLS)
    df = pd.read_csv(path, dtype={"code": str}).fillna("")
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def _binding(scan_dir: Path, code: str) -> str:
    """卡片 OW 三门里 ✗ 的门名(压评级的那道);解析失败/无 → ""。

    注意 `assemble.gate_status` 语义:返回 {门: **是否✗失守**}(True=失守),非"是否通过"。
    """
    p = Path(scan_dir) / "details" / f"{code}.md"
    if not p.exists():
        return ""
    try:
        from autoresearch.scan.assemble import gate_status
        gates = gate_status(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    if not gates:
        return ""
    return "|".join(k for k, failed in gates.items() if failed)


def record(scan_dir: Path | str, path: Path | str = _PATH, k: int = 3) -> int:
    """该 scan 日 top-k Hold(L3 conviction 序)入账;(date,code) 幂等。返回新增行数。"""
    from autoresearch.scan.agents.l4_card import pick_opportunity_candidates
    from autoresearch.scan.health import final_ratings
    scan_dir, path = Path(scan_dir), Path(path)
    ratings = final_ratings(scan_dir)
    picks = pick_opportunity_candidates(ratings, scan_dir, k=k)
    if not picks:
        return 0
    date = scan_dir.name
    fin, closes = {}, {}
    fp, lp = scan_dir / "finalists.csv", scan_dir / "L1_scored_full.csv"
    if fp.exists():
        f = pd.read_csv(fp, dtype={"code": str})
        f["code"] = f["code"].astype(str).str.zfill(6)
        fin = {r["code"]: r for r in f.to_dict("records")}
    if lp.exists():
        l1 = pd.read_csv(lp, dtype={"code": str})
        if {"code", "close"} <= set(l1.columns):
            closes = dict(zip(l1["code"].astype(str).str.zfill(6),
                              pd.to_numeric(l1["close"], errors="coerce"), strict=False))
    old = _load(path)
    seen = set(zip(old["date"], old["code"], strict=False)) if len(old) else set()
    rows = []
    for code in picks:
        if (date, code) in seen:
            continue
        fr = fin.get(code, {})
        cl = closes.get(code)
        rows.append({"date": date, "code": code, "name": fr.get("name", ""),
                     "conviction": fr.get("conviction", ""), "binding": _binding(scan_dir, code),
                     "close": None if cl is None or pd.isna(cl) else float(cl)})
    if not rows:
        return 0
    out = pd.concat([old, pd.DataFrame(rows)], ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return len(rows)


def backfill(scan_root: Path | str = "context/scan", path: Path | str = _PATH) -> int:
    """对全部历史 scan 日 record(幂等)——上线即让影子线有 13 日底仓数据。

    单日故障隔离:坏历史日(e.g. 损坏 finalists.csv)跳过,不中断整个回填。
    """
    scan_root = Path(scan_root)
    if not scan_root.exists():
        return 0
    total = 0
    for d in sorted(p for p in scan_root.iterdir()
                    if p.is_dir() and p.name[:2] == "20"):
        try:
            total += record(d, path=path)
        except Exception as e:  # noqa: BLE001
            print(f"[shadow_buys] 跳过 {d.name}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
    return total


def main() -> int:
    n = backfill()
    print(f"[shadow_buys] 回填 {n} 行 → {_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
