"""Assemble macro-research per-agent markdown into one macro_compass.md.

Two-tier like assemble_report.py, plus a 中观 tier:
  ▸ 决策主线   decision / variant / crossfire / calendar / premortem (+debate)
  ▸ 中观落地   sector_map / flows / sentiment / themes
  ▸ 证据附录   regional(us/china/global) · crossasset(rates/fx/equities/commodities/crypto[/credit])
               · sino-us(divergence/desync/geopolitics/relative) · meso_evidence(industry_cycle)

The decision (cross-asset) and sector_map (A股行业) tables each carry one keyed
`- <KEY>: **Rating**: <band>` line per row; parse_allocation runs the project's
parse_rating on each so all five-band tilts stay machine-checked.

Usage:
    python scripts/assemble_macro.py reports/macro/<YYYY-MM-DD>
"""
import re
import sys
from datetime import datetime
from pathlib import Path

from tradingagents.agents.utils.rating import parse_rating

DECISION_REL = "1_spine/decision.md"
SECTOR_MAP_REL = "2_meso/sector_map.md"

SPINE = [
    ("S2 · 投资逻辑 & 预期差", [("Variant View", "1_spine/variant.md", False)]),
    ("S3 · 中美对撞 & 情景矩阵", [("Crossfire & Scenarios", "1_spine/crossfire.md", False)]),
    ("S4 · 催化剂日历 & 触发位", [("Catalyst Calendar", "1_spine/calendar.md", False)]),
    ("S5 · 风险 · 认错 · 监控", [
        ("Pre-Mortem & Monitoring", "1_spine/premortem.md", False),
        ("Risk Debate", "1_spine/debate.md", True),
    ]),
]
MESO = [
    ("M1 · A股行业配置图", [("Sector Allocation Map", "2_meso/sector_map.md", False)]),
    ("M2 · 资金 & 游资", [("Flows & Hot Money", "2_meso/flows.md", False)]),
    ("M3 · 情绪周期 & 涨停结构", [("Sentiment Cycle", "2_meso/sentiment.md", False)]),
    ("M4 · 题材 & 风格轮动", [("Themes & Style", "2_meso/themes.md", False)]),
]
APPENDIX = [
    ("A · 区域宏观", [
        ("United States", "3_regional/us.md", False),
        ("China", "3_regional/china.md", False),
        ("Global (EU/Japan/EM)", "3_regional/global.md", False),
    ]),
    ("B · 跨资产 & 传导", [
        ("Rates & Central Banks", "4_crossasset/rates.md", False),
        ("FX (USD/CNY/JPY)", "4_crossasset/fx.md", False),
        ("Equities (US vs A/H)", "4_crossasset/equities.md", False),
        ("Commodities & Gold", "4_crossasset/commodities.md", False),
        ("Crypto", "4_crossasset/crypto.md", False),
        ("Credit & Liquidity", "4_crossasset/credit.md", True),
    ]),
    ("C · 中美专题", [
        ("Monetary Divergence", "5_sinous/divergence.md", False),
        ("Growth/Inflation Desync", "5_sinous/desync.md", False),
        ("Trade / Tariff / Geopolitics", "5_sinous/geopolitics.md", False),
        ("Relative Assets & Flows", "5_sinous/relative.md", False),
    ]),
    ("D · 中观明细", [
        ("Industry Cycle Bridge", "6_meso_evidence/industry_cycle.md", True),
    ]),
]

SPINE_BANNER = "**═══════════ 决策主线 · Decision Spine(读它就能配置)═══════════**"
MESO_BANNER = "**═══════════ 中观落地 · A股行业/资金/情绪═══════════**"
APPENDIX_BANNER = "**═══════════ 证据附录 · Evidence Appendix═══════════**"


def parse_allocation(text: str) -> dict:
    """Extract every keyed allocation rating. Each row: `- <KEY>: **Rating**: <band>`.
    parse_rating runs on the single line, so each row's band is machine-checked."""
    out = {}
    for line in text.splitlines():
        if "**Rating**" not in line and "Rating:" not in line:
            continue
        m = re.match(r"\s*[-*]\s*(.+?)\s*[::]", line)
        if not m:
            continue
        out[m.group(1).strip()] = parse_rating(line)
    return out


def _slug(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.strip().lower())
    return re.sub(r"\s+", "-", s)


def _anchored(tag: str, title: str, body: str = "") -> str:
    block = f'\n<a id="{_slug(title)}"></a>\n\n{tag} {title}\n'
    return f"{block}\n{body}\n" if body else block


def _present(root: Path, items):
    return [(name, rel) for name, rel, _ in items if (root / rel).exists()]


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8").strip()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    root = Path(sys.argv[1])

    required = [DECISION_REL] + [
        rel for _, items in (SPINE + MESO + APPENDIX) for _, rel, opt in items if not opt
    ]
    missing = [rel for rel in required if not (root / rel).exists()]
    if missing:
        print("[MISSING] 必需分段文件不存在,请先写齐核心 agent 文件再组装:")
        for rel in missing:
            print(f"  - {root / rel}")
        return 1

    skipped = [rel for _, items in (SPINE + MESO + APPENDIX)
               for _, rel, opt in items if opt and not (root / rel).exists()]

    out = [f"# Macro Research Report: {root.name}\n",
           f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
           "_Engine: Claude (in-session), zero paid LLM API. "
           "Data: FRED + akshare + yfinance._\n"]

    out.append("\n---\n\n" + SPINE_BANNER + "\n")
    out.append(_anchored("##", "S1 · 执行摘要 · 配置决策", _read(root, DECISION_REL)))
    for title, present in [(t, p) for t, items in SPINE if (p := _present(root, items))]:
        if len(present) == 1:
            out.append(_anchored("##", title, _read(root, present[0][1])))
        else:
            out.append(_anchored("##", title))
            for name, rel in present:
                out.append(_anchored("###", name, _read(root, rel)))

    out.append("\n---\n\n" + MESO_BANNER + "\n")
    for title, present in [(t, p) for t, items in MESO if (p := _present(root, items))]:
        out.append(_anchored("##", title, _read(root, present[0][1])))

    out.append("\n---\n\n" + APPENDIX_BANNER + "\n")
    for title, present in [(t, p) for t, items in APPENDIX if (p := _present(root, items))]:
        out.append(_anchored("##", title))
        for name, rel in present:
            out.append(_anchored("###", name, _read(root, rel)))

    (root / "macro_compass.md").write_text("\n".join(out), encoding="utf-8")
    print(f"[assembled] {root / 'macro_compass.md'}")

    alloc = parse_allocation(_read(root, DECISION_REL))
    print(f"[parse_rating → cross-asset ({len(alloc)})] {alloc}")
    if (root / SECTOR_MAP_REL).exists():
        sectors = parse_allocation(_read(root, SECTOR_MAP_REL))
        print(f"[parse_rating → A股 sectors ({len(sectors)})] {sectors}")
    if skipped:
        print("[note] 跳过未提供的可选分段: " + ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
