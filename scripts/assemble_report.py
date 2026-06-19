"""Assemble the per-agent markdown files into one complete_report.md.

Mirrors the CLI's ``save_report_to_disk`` section layout (I–V) so the
Claude-as-engine output is shaped exactly like a real framework run, and
validates the final decision with the project's own ``parse_rating`` (the
function behind ``SignalProcessor.process_signal``).

Usage:
    python scripts/assemble_report.py reports/<TICKER>_<YYYYMMDD>
"""

import sys
from datetime import datetime
from pathlib import Path

from tradingagents.agents.utils.rating import parse_rating

SECTIONS = [
    ("I. Analyst Team Reports", [
        ("Market Analyst", "1_analysts/market.md"),
        ("Sentiment Analyst", "1_analysts/sentiment.md"),
        ("News Analyst", "1_analysts/news.md"),
        ("Fundamentals Analyst", "1_analysts/fundamentals.md"),
    ]),
    ("II. Research Team Decision", [
        ("Bull Researcher", "2_research/bull.md"),
        ("Bear Researcher", "2_research/bear.md"),
        ("Research Manager", "2_research/manager.md"),
    ]),
    ("III. Trading Team Plan", [
        ("Trader", "3_trading/trader.md"),
    ]),
    ("IV. Risk Management Team Decision", [
        ("Aggressive Analyst", "4_risk/aggressive.md"),
        ("Conservative Analyst", "4_risk/conservative.md"),
        ("Neutral Analyst", "4_risk/neutral.md"),
    ]),
    ("V. Portfolio Manager Decision", [
        ("Portfolio Manager", "5_portfolio/decision.md"),
    ]),
]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    root = Path(sys.argv[1])
    ticker = root.name.split("_")[0]

    out = [f"# Trading Analysis Report: {ticker}\n",
           f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
           "_Engine: Claude (in-session), zero paid LLM API. Data: project tools (yfinance/FRED)._\n"]

    missing = [rel for _, items in SECTIONS for _, rel in items if not (root / rel).exists()]
    if missing:
        print("[MISSING] 以下分段文件不存在，请先写齐全部 12 个 agent 文件再组装：")
        for rel in missing:
            print(f"  - {root / rel}")
        return 1

    for sec_title, items in SECTIONS:
        out.append(f"\n## {sec_title}\n")
        for name, rel in items:
            text = (root / rel).read_text(encoding="utf-8").strip()
            out.append(f"\n### {name}\n\n{text}\n")

    (root / "complete_report.md").write_text("\n".join(out), encoding="utf-8")

    decision = (root / "5_portfolio/decision.md").read_text(encoding="utf-8")
    rating = parse_rating(decision)
    print(f"[assembled] {root / 'complete_report.md'}")
    print(f"[parse_rating → 5-tier signal] {rating}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
