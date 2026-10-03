"""Regenerate the legacy adapter's shared prompt block; no research is executed."""
import argparse
from pathlib import Path

from autoresearch.common.research_prompts import END, START, legacy_template_block


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    path = Path(__file__).resolve().parents[1] / '.claude/workflows/l4-stock.js'
    text = path.read_text()
    if START not in text or END not in text:
        raise ValueError('generated research template markers missing')
    start, end = text.index(START), text.index(END) + len(END)
    replacement = text[:start] + legacy_template_block() + text[end:]
    if args.check:
        return 0 if replacement == text else 1
    path.write_text(replacement)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
