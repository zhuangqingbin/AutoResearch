#!/usr/bin/env python3
"""sector-research · brief TTL 复用(镜像 l4_reuse;确定性判定,零 LLM)。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.3(Phase 3)。

判据(全部成立才复用,保守):① 近 ttl 天内某 scan 日已有该行业 brief;② 两日 `meta.regime`
同(任一缺 → 不复用);③ 行业中位 60 日动量位移 |Δ| ≤ mom_shift_pp(两日 L1_scored_full 对比;
spec 原定行业指数 |Δ| 待 sw_daily 权限核实,先以中位动量代理)。`--apply` → 拷贝 + ♻️banner。

用法:uv run --no-sync python -m autoresearch.sector.reuse <date> [--industries a,b] [--apply]
"""
from __future__ import annotations

import argparse
import json
from datetime import date as _date
from pathlib import Path

from autoresearch.sector.brief import brief_path
from autoresearch.sector.pack import _num, _read_csv


def _regime(scan_dir: Path) -> str | None:
    p = Path(scan_dir) / "meta.json"
    if not p.exists():
        return None
    try:
        reg = json.loads(p.read_text(encoding="utf-8")).get("regime")
    except Exception:  # noqa: BLE001
        return None
    if isinstance(reg, dict):
        reg = reg.get("label")
    return reg if isinstance(reg, str) else None


def _ind_mom(scan_dir: Path, industry: str) -> float | None:
    l1 = _read_csv(Path(scan_dir) / "L1_scored_full.csv")
    if l1 is None or "industry" not in l1.columns:
        return None
    g = l1[l1["industry"].astype(str) == str(industry)]
    m = _num(g, "pct_60d").dropna()
    return float(m.median()) if len(m) else None


def find_reusable(date: str, industries, root: Path | str = Path("context/scan"),
                  ttl_days: int = 5, mom_shift_pp: float = 3.0) -> dict[str, dict]:
    """逐行业找最近可复用 brief → {行业: {src, prev, shift_pp}};判不中 → 不入结果。"""
    root = Path(root)
    today_dir = root / date
    reg_today = _regime(today_dir)
    prev_dirs = (sorted((p for p in root.iterdir() if p.is_dir() and p.name < date),
                        key=lambda p: p.name, reverse=True) if root.exists() else [])
    out: dict[str, dict] = {}
    for ind in industries:
        for pdir in prev_dirs:
            try:
                age = (_date.fromisoformat(date) - _date.fromisoformat(pdir.name)).days
            except ValueError:  # 非日期目录名,跳过
                continue
            if age > ttl_days:
                break                                   # 目录按日期降序 → 后面只会更老
            src = brief_path(pdir, ind)
            if not src.exists():
                continue
            reg_prev = _regime(pdir)
            if not reg_today or not reg_prev or reg_today != reg_prev:
                continue                                # regime 判据缺任一侧 → 保守不复用
            m0, m1 = _ind_mom(pdir, ind), _ind_mom(today_dir, ind)
            if m0 is None or m1 is None or abs(m1 - m0) > mom_shift_pp:
                continue
            out[ind] = {"src": str(src), "prev": pdir.name, "shift_pp": round(abs(m1 - m0), 2)}
            break
    return out


def apply_reuse(date: str, found: dict[str, dict], root: Path | str = Path("context/scan")) -> int:
    """把可复用 brief 拷到今日 sector_briefs/,顶部 ♻️banner(带失效条件)。返回份数。"""
    n = 0
    for ind, info in found.items():
        dst = brief_path(Path(root) / date, ind)
        dst.parent.mkdir(parents=True, exist_ok=True)
        body = Path(info["src"]).read_text(encoding="utf-8")
        banner = (f"> ♻️ 复用自 {info['prev']} 的行业 brief(regime 同 · 中位60日动量位移 "
                  f"{info['shift_pp']}pp ≤ 3)。失效条件:regime 翻转 / 行业动量位移 >3pp / "
                  f"重大行业级公告。\n\n")
        dst.write_text(banner + body, encoding="utf-8")
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="行业 brief TTL 复用(确定性,零 LLM)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--industries", default=None, help="逗号分隔;缺省 = 自动选(同 sector.pack)")
    ap.add_argument("--apply", action="store_true", help="真拷贝(缺省只打印判定)")
    ap.add_argument("--ttl", type=int, default=5, help="回看天数,默认 5")
    args = ap.parse_args(argv)
    root = Path("context/scan")
    if args.industries:
        inds = [s.strip() for s in args.industries.split(",") if s.strip()]
    else:
        from autoresearch.sector.pack import select_briefing_sectors
        inds, _ = select_briefing_sectors(root / args.date)
    found = find_reusable(args.date, inds, root=root, ttl_days=args.ttl)
    for ind in inds:
        if ind in found:
            print(f"[sector.reuse] ♻️ {ind} ← {found[ind]['prev']}(动量位移 {found[ind]['shift_pp']}pp)")
        else:
            print(f"[sector.reuse] ✗ {ind} 需新 brief")
    if args.apply and found:
        print(f"[sector.reuse] 拷贝 {apply_reuse(args.date, found, root=root)} 份 → sector_briefs/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
