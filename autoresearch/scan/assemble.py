#!/usr/bin/env python3
"""Compatibility adapter for scan L5 assembly.

Implementations live in decision, parser, report, publisher, and post-run
modules.  This path keeps historical imports and the CLI stable.
"""
from __future__ import annotations

from datetime import date

from autoresearch.agents.utils.rating import RATINGS_5_TIER, parse_rating
from autoresearch.scan.decision_finalize import (
    TIER_RANK,
    _PROPOSAL_BY_RATING,
    _VERDICT_BADGE,
    _apply_ensemble_fold,
    _apply_verify_downgrade,
    _build_decision_records,
    _dump_decision_records,
    _dump_final_ratings,
    _ensemble_dissent_lines,
    _ensemble_flag,
    _load_ensemble,
    _load_verify,
    _verify_badge,
)
from autoresearch.scan.l4.parsers import (
    _BEARBULLET_RE,
    _BULLBEAR_RE,
    _CONF_RE,
    _DEV_RE,
    _EARLYSTOP_RE,
    _GATES3,
    _GATESEG_RE,
    _PROPOSAL_RE,
    _RUBRIC_RE,
    _STOPWHY_RE,
    _STOP_REASONS,
    _clip,
    _decision_text,
    _finalist_row,
    _get,
    _l4_brief,
    _load_json,
    _parse_dashboard,
    _parse_gate_seg,
    _read_csv,
    _seg_has_mark,
    _strip,
    gate_status,
    parse_early_stop,
    write_early_stop,
)
from autoresearch.scan.publisher import (
    _archive_reasoning,
    _funnel_md,
    _publish_pipeline,
    _safe_name,
    publish_details as _publish_details,
    run,
)
from autoresearch.scan.report_sections import (
    _buylist_table_lines,
    _channels_zh,
    _degraded_line,
    _funnel_rows,
    _knowledge_note,
    _l1_cell,
    _l2_cell,
    _load_market_view,
    _pinned_section,
    _portfolio_note,
    _position_overlay,
    _proposals_nag,
    _same_chain_block,
    _sector_view_section,
    _self_review_banner,
    _sortkey,
    _stage_overview,
    _stage_token_estimate,
    _verify_detail,
    build_summary,
    gate_histogram,
    regime_and_drift,
)

_gate_histogram = gate_histogram


def main() -> int:
    ap = argparse.ArgumentParser(description="scan-market L5 整合(漏斗 + 三段 summary + trace/)")
    ap.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    args = ap.parse_args()
    run(args.date or date.today().isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
