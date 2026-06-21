"""扫描漏斗各 Stage —— L0 选集 / L1 召回 / L2 粗排(确定性,Stage 契约)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §A。
"""
from __future__ import annotations

from autoresearch.scan.stages.base import Stage
from autoresearch.scan.stages.l0_universe import L0Universe
from autoresearch.scan.stages.l1_recall import L1Recall
from autoresearch.scan.stages.l2_rank import L2Rank

__all__ = ["Stage", "L0Universe", "L1Recall", "L2Rank"]
