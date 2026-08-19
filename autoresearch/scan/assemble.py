#!/usr/bin/env python3
"""Compatibility adapter for scan L5 assembly.

Implementations live in decision, parser, report, publisher, and post-run
modules.  This path keeps historical imports and the CLI stable.
"""
from __future__ import annotations

import argparse
from datetime import date

from autoresearch.scan.publisher import (
    run,
)
from autoresearch.scan.report_sections import (
    gate_histogram,
)
from autoresearch.agents.utils.rating import RATINGS_5_TIER  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.agents.utils.rating import parse_rating  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import TIER_RANK  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _PROPOSAL_BY_RATING  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _VERDICT_BADGE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _apply_ensemble_fold  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _apply_verify_downgrade  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _build_decision_records  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _dump_decision_records  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _dump_final_ratings  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _ensemble_dissent_lines  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _ensemble_flag  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _load_ensemble  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _load_verify  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.decision_finalize import _verify_badge  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _BEARBULLET_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _BULLBEAR_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _CONF_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _DEV_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _EARLYSTOP_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _GATES3  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _GATESEG_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _PROPOSAL_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _RUBRIC_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _STOPWHY_RE  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _STOP_REASONS  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _clip  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _decision_text  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _finalist_row  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _get  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _l4_brief  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _load_json  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _parse_dashboard  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _parse_gate_seg  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _read_csv  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _seg_has_mark  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import _strip  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import gate_status  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import parse_early_stop  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.l4.parsers import write_early_stop  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.publisher import _archive_reasoning  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.publisher import _funnel_md  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.publisher import _publish_pipeline  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.publisher import _safe_name  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.publisher import publish_details as _publish_details  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _buylist_table_lines  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _channels_zh  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _degraded_line  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _funnel_rows  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _knowledge_note  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _l1_cell  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _l2_cell  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _load_market_view  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _pinned_section  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _portfolio_note  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _position_overlay  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _proposals_nag  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _same_chain_block  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _self_review_banner  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _sortkey  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _stage_overview  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _stage_token_estimate  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import _verify_detail  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import build_summary  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)
from autoresearch.scan.report_sections import regime_and_drift  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)

_gate_histogram = gate_histogram


def main() -> int:
    ap = argparse.ArgumentParser(description="scan-market L5 整合(漏斗 + 三段 summary + trace/)")
    ap.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    args = ap.parse_args()
    run(args.date or date.today().isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
