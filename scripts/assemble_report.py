"""Assemble the per-agent markdown files into one complete_report.md (v2).

Mirrors the CLI's ``save_report_to_disk`` section layout (I–V) so the
Claude-as-engine output is shaped like a real framework run, and validates
the final decision with the project's own ``parse_rating`` (the function
behind ``SignalProcessor.process_signal``).

v2 adds 5 optional sections (valuation, catalyst, peer, verification,
premortem). They are marked optional so a v1 report (12 files) still
assembles; only the original core files are required.

Usage:
    python scripts/assemble_report.py reports/<TICKER>_<YYYYMMDD>
"""

import sys
from datetime import datetime
from pathlib import Path

from tradingagents.agents.utils.rating import parse_rating

# (display name, relative path, optional?) — optional=True ⇒ v2 section, skipped if absent.
SECTIONS = [
    ("I. Analyst Team Reports", [
        ("Market Analyst", "1_analysts/market.md", False),
        ("Sentiment Analyst", "1_analysts/sentiment.md", False),
        ("News Analyst", "1_analysts/news.md", False),
        ("Fundamentals Analyst", "1_analysts/fundamentals.md", False),
        ("Valuation Analyst", "1_analysts/valuation.md", True),
        ("Catalyst & Positioning Analyst", "1_analysts/catalyst.md", True),
        ("Peer-Relative Analyst", "1_analysts/peer.md", True),
    ]),
    ("II. Research Team Decision", [
        ("Claims Verification", "2_research/verification.md", True),
        ("Bull Researcher", "2_research/bull.md", False),
        ("Bear Researcher", "2_research/bear.md", False),
        ("Research Manager", "2_research/manager.md", False),
    ]),
    ("III. Trading Team Plan", [
        ("Trader", "3_trading/trader.md", False),
    ]),
    ("IV. Risk Management Team Decision", [
        ("Aggressive Analyst", "4_risk/aggressive.md", False),
        ("Conservative Analyst", "4_risk/conservative.md", False),
        ("Neutral Analyst", "4_risk/neutral.md", False),
        ("Pre-Mortem (Red Team)", "4_risk/premortem.md", True),
    ]),
    ("V. Portfolio Manager Decision", [
        ("Portfolio Manager", "5_portfolio/decision.md", False),
    ]),
]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    root = Path(sys.argv[1])
    ticker = root.name.split("_")[0]

    required_missing = [
        rel for _, items in SECTIONS for _, rel, opt in items
        if not opt and not (root / rel).exists()
    ]
    if required_missing:
        print("[MISSING] 必需分段文件不存在，请先写齐核心 agent 文件再组装：")
        for rel in required_missing:
            print(f"  - {root / rel}")
        return 1

    out = [f"# Trading Analysis Report: {ticker}\n",
           f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
           "_Engine: Claude (in-session), zero paid LLM API. Data: project tools (yfinance/FRED) + v2 enrichments._\n"]

    skipped = []
    for sec_title, items in SECTIONS:
        present = [(n, rel) for n, rel, _ in items if (root / rel).exists()]
        skipped += [rel for _, rel, opt in items if opt and not (root / rel).exists()]
        if not present:
            continue
        out.append(f"\n## {sec_title}\n")
        for name, rel in present:
            text = (root / rel).read_text(encoding="utf-8").strip()
            out.append(f"\n### {name}\n\n{text}\n")

    (root / "complete_report.md").write_text("\n".join(out), encoding="utf-8")

    decision = (root / "5_portfolio/decision.md").read_text(encoding="utf-8")
    rating = parse_rating(decision)
    print(f"[assembled] {root / 'complete_report.md'}")
    print(f"[parse_rating → 5-tier signal] {rating}")
    if skipped:
        print("[note] 跳过未提供的可选(v2)分段: " + ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
