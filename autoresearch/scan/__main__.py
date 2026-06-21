#!/usr/bin/env python3
"""`python -m autoresearch.scan <run|capture|check> ...` → 统一 scan CLI。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A(CLI)。委派 `autoresearch.scan.cli`。
"""
from __future__ import annotations

from autoresearch.scan.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
