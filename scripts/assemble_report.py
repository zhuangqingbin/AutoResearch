"""Assemble the per-agent markdown files into one complete_report.md (v4).

v4 reorganises the body from "org chart" order into "decision argument" order,
split into two tiers so the note reads top-down like a real PM memo:

  ▸ 决策主线 (Decision Spine) — read this to decide:
      S1 执行摘要 · PM 决策 (决策仪表盘 + 评分卡, = decision.md, parse_rating'd)
      S2 投资逻辑 & 预期差 (variant.md)
      S3 多空对撞 (faceoff.md; full bull/bear prose demoted to appendix)
      S4 催化剂日历 & 触发位 (calendar.md)
      S5 风险 · 认错 · 持仓监控 (premortem.md [+monitoring] + debate.md)
  ▸ 证据附录 (Evidence Appendix) — read this to verify / drill down:
      A 分析师证据 (market/news/fundamentals/quality/valuation/positioning/peer/solvency)
      B 研究与验证 (reality_check / bull / bear / manager)

Only the spine + the core analyst lenses are required; every other lens is
optional and skipped if absent. The final decision is still validated with the
project's own ``parse_rating`` (the function behind ``SignalProcessor``).

Usage:
    python scripts/assemble_report.py reports/<YYYYMMDD>/<TICKER>
"""

import re
import sys
from datetime import datetime
from pathlib import Path

from tradingagents.agents.utils.rating import parse_rating

# The PM decision — required, rendered FIRST as the executive summary. v4: the PM
# prepends a 决策仪表盘 (one-row dashboard) + 维度评分卡 (scorecard) at its top.
DECISION_REL = "4_portfolio/decision.md"
DECISION_TITLE = "S1 · 执行摘要 · PM 决策（决策仪表盘 + 维度评分卡）"

# ▸ 决策主线 — the sections AFTER the exec summary. (title, [(name, rel, optional)])
# A single-item group renders its body directly under the H2; multi-item groups
# render an H2 header + one H3 per present file.
SPINE = [
    ("S2 · 投资逻辑 & 预期差 (Thesis & Variant View)", [
        ("Variant View", "2_research/variant.md", False),
    ]),
    ("S3 · 多空对撞 (Bull vs Bear Face-off)", [
        ("Face-off", "2_research/faceoff.md", False),
    ]),
    ("S4 · 催化剂日历 & 触发位 (Catalyst Calendar)", [
        ("Catalyst Calendar", "4_portfolio/calendar.md", False),
    ]),
    ("S5 · 风险 · 认错条件 · 持仓监控 (Risk / Invalidation / Monitoring)", [
        ("Pre-Mortem & Monitoring (Red Team)", "3_risk/premortem.md", False),
        ("Risk Debate (Aggressive / Conservative / Neutral)", "3_risk/debate.md", True),
    ]),
]

# ▸ 证据附录 — supporting detail, drilled into only to verify.
APPENDIX = [
    ("A · 分析师证据 (Analyst Evidence)", [
        ("Market & Technicals", "1_analysts/market.md", False),
        ("News & Narrative", "1_analysts/news.md", False),
        ("Fundamentals", "1_analysts/fundamentals.md", False),
        ("Earnings Quality", "1_analysts/quality.md", True),
        ("Valuation", "1_analysts/valuation.md", True),
        ("Positioning & Flow", "1_analysts/positioning.md", True),
        ("Peer-Relative", "1_analysts/peer.md", True),
        ("Solvency & Refinancing", "1_analysts/solvency.md", True),
    ]),
    ("B · 研究与验证 (Research & Verification)", [
        ("Reality Check (Claims Audit + Base Rates)", "2_research/reality_check.md", True),
        ("Bull Researcher (full)", "2_research/bull.md", False),
        ("Bear Researcher (full)", "2_research/bear.md", False),
        ("Research Manager", "2_research/manager.md", False),
    ]),
]

SPINE_BANNER = "**═══════════ 决策主线 · Decision Spine（读它就能下单）═══════════**"
APPENDIX_BANNER = "**═══════════ 证据附录 · Evidence Appendix（按需下钻核实）═══════════**"


def _slug(title: str) -> str:
    """Stable anchor id (keeps CJK word chars, drops punctuation, spaces->'-').

    We emit an explicit ``<a id>`` per heading and link the TOC to the SAME slug,
    so resolution never depends on a renderer's heading-slug algorithm."""
    s = re.sub(r"[^\w\s-]", "", title.strip().lower())
    return re.sub(r"\s+", "-", s)


def _anchored(tag: str, title: str, body: str = "") -> str:
    """A heading carrying its own explicit anchor, optionally followed by body."""
    block = f'\n<a id="{_slug(title)}"></a>\n\n{tag} {title}\n'
    return f"{block}\n{body}\n" if body else block


def _present(root: Path, items):
    """Drop the optional flag; keep only items whose file exists."""
    return [(name, rel) for name, rel, _ in items if (root / rel).exists()]


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8").strip()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    root = Path(sys.argv[1])
    ticker = root.name.split("_")[0]

    required = [DECISION_REL] + [
        rel for _, items in (SPINE + APPENDIX) for _, rel, opt in items if not opt
    ]
    missing = [rel for rel in required if not (root / rel).exists()]
    if missing:
        print("[MISSING] 必需分段文件不存在，请先写齐核心 agent 文件再组装：")
        for rel in missing:
            print(f"  - {root / rel}")
        return 1

    spine_present = [(t, p) for t, items in SPINE if (p := _present(root, items))]
    appx_present = [(t, p) for t, items in APPENDIX if (p := _present(root, items))]
    skipped = [rel for _, items in (SPINE + APPENDIX)
               for _, rel, opt in items if opt and not (root / rel).exists()]

    out = [f"# Trading Analysis Report: {ticker}\n",
           f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
           "_Engine: Claude (in-session), zero paid LLM API. "
           "Data: project tools (yfinance/FRED) + v2/v3/v4 enrichments._\n"]

    # --- table of contents (two-tier: spine then appendix) ----------------
    toc = ["## 目录 (Contents)\n", "**▸ 决策主线（读它就能下单）**",
           f"- [{DECISION_TITLE}](#{_slug(DECISION_TITLE)})"]
    for title, present in spine_present:
        toc.append(f"- [{title}](#{_slug(title)})")
        if len(present) > 1:
            toc += [f"  - [{name}](#{_slug(name)})" for name, _ in present]
    toc.append("\n**▸ 证据附录（按需下钻核实）**")
    for title, present in appx_present:
        toc.append(f"- [{title}](#{_slug(title)})")
        toc += [f"  - [{name}](#{_slug(name)})" for name, _ in present]
    out.append("\n".join(toc) + "\n")

    # --- spine: exec summary first, then S2..S5 ---------------------------
    out.append("\n---\n\n" + SPINE_BANNER + "\n")
    out.append(_anchored("##", DECISION_TITLE, _read(root, DECISION_REL)))
    for title, present in spine_present:
        if len(present) == 1:
            out.append(_anchored("##", title, _read(root, present[0][1])))
        else:
            out.append(_anchored("##", title))
            for name, rel in present:
                out.append(_anchored("###", name, _read(root, rel)))

    # --- appendix: analyst evidence + research/verification ---------------
    out.append("\n---\n\n" + APPENDIX_BANNER + "\n")
    for title, present in appx_present:
        out.append(_anchored("##", title))
        for name, rel in present:
            out.append(_anchored("###", name, _read(root, rel)))

    (root / "complete_report.md").write_text("\n".join(out), encoding="utf-8")

    rating = parse_rating(_read(root, DECISION_REL))
    print(f"[assembled] {root / 'complete_report.md'}")
    print(f"[parse_rating → 5-tier signal] {rating}")
    if skipped:
        print("[note] 跳过未提供的可选 lens 分段: " + ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
