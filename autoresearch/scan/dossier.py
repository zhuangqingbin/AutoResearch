#!/usr/bin/env python3
"""scan-market · 个股档案(前科卡)—— finalist 复现时的跨日记忆(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-dossier-design.md

紫光国微三天三张满卡撞死在同一道 CFO 门 = 系统对"研究过谁、被什么杀死"零记忆。
本模块按需聚合最近 N 个 scan 日的 finalists/details/verify → 前科卡注入 L4 简报,
让 L4 从"重新研究"变**增量研究**(重点核对"什么变了")。

**防锚定铁律**(同策略师):档案只给历史事实与已知证伪点,不给方向;本次评级仍由
本卡 rubric 三门独立定。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def _finalist_row(d: Path, code6: str) -> dict | None:
    p = d / "finalists.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, dtype={"code": str})
    except Exception:
        return None
    if "code" not in df.columns:
        return None
    sub = df[df["code"].astype(str).str.zfill(6) == code6]
    return sub.iloc[0].to_dict() if len(sub) else None


def _card_fields(d: Path, code6: str) -> tuple[str | None, str | None]:
    """(rating, l4_line〔binding 理由〕);缺卡 → (None, None)。lazy import 防环。"""
    base = d / "details"
    cands = [base / f"{code6}.md", *sorted(base.glob(f"{code6}*.md"))] if base.is_dir() else []
    for p in cands:
        if p.exists():
            text = p.read_text(encoding="utf-8")
            from autoresearch.agents.utils.rating import parse_rating
            from autoresearch.scan.assemble import _l4_brief
            rating = parse_rating(text)
            return rating, _l4_brief(text, rating)
    return None, None


def _verify_row(d: Path, code6: str) -> dict | None:
    p = d / "verify.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, dtype={"code": str})
    except Exception:
        return None
    sub = df[df["code"].astype(str).str.zfill(6) == code6]
    if not len(sub):
        return None
    r = sub.iloc[0]
    return {"verdict": str(r.get("verdict", "")), "trigger": str(r.get("trigger", ""))}


def stock_dossier(code: str, scan_root: Path | str = "context/scan",
                  max_days: int = 10, exclude: str | None = None) -> list[dict]:
    """最近 max_days 个 scan 日里该票的入围史(exclude 当日;档案=历史)。日期升序。"""
    code6 = str(code).split(".")[0].zfill(6)
    root = Path(scan_root)
    if not root.exists():
        return []
    dates = sorted((p.name for p in root.iterdir()
                    if p.is_dir() and p.name[:2] == "20" and p.name != exclude), reverse=True)
    out: list[dict] = []
    for date in dates[:max_days]:
        fr = _finalist_row(root / date, code6)
        if fr is None:
            continue
        rating, l4_line = _card_fields(root / date, code6)
        out.append({"date": date, "conviction": fr.get("conviction"), "lane": fr.get("lane"),
                    "l3_risk": str(fr.get("risk") or ""), "rating": rating,
                    "l4_line": l4_line or "", "verify": _verify_row(root / date, code6)})
    return sorted(out, key=lambda e: e["date"])


def render_dossier(code: str, scan_root: Path | str = "context/scan",
                   max_days: int = 10, exclude: str | None = None) -> str:
    """前科卡 markdown;无历史 → ""(presence-gated)。"""
    entries = stock_dossier(code, scan_root=scan_root, max_days=max_days, exclude=exclude)
    if not entries:
        return ""
    lines = [f"### 📁 个股档案(近 {len(entries)} 次入围,历史事实非预判)"]
    kills: list[str] = []
    for e in entries:
        v = e.get("verify")
        vtxt = f" ｜ skeptic:{v['verdict']}(触发:{v['trigger']})" if v else ""
        lines.append(f"- **{e['date']}**:{e['rating'] or '—'} —— {e['l4_line'] or e['l3_risk'] or '—'}{vtxt}")
        for k in (e.get("l4_line"), e.get("l3_risk")):
            if k and k not in kills:
                kills.append(k)
    if kills:
        lines.append("- **已知证伪点**:" + ";".join(kills[:4]))
    lines.append("- 用途:**只做增量研究**——在卡片『变化项(vs 档案)』节逐条核对上述证伪点"
                 "【已变/未变】,未变的引档案一句带过,变了的才展开。"
                 "**本次评级仍由本卡 rubric 三门独立定,档案不预判方向。**")
    return "\n".join(lines) + "\n"
