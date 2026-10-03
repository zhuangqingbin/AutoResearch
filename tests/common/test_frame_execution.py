from datetime import datetime

from autoresearch.common.execution_math import build_decision_frame
from autoresearch.scan.exec_anchor import build_execution_block


def test_unknown_frozen_frame_cannot_be_made_actionable_by_new_calendar():
    frame = build_decision_frame(
        analysis_session="2026-09-11",
        knowledge_cutoff="2026-09-11T20:00:00+08:00",
        venue="XSHG",
        research_depth="LITE",
        usage="scan",
        sessions=[],
        calendar_quality="UNKNOWN",
    )
    block = build_execution_block(
        "2026-09-11",
        approved_at=datetime.fromisoformat("2026-09-11T21:00:00+08:00"),
        sessions=["2026-09-11", "2026-09-14", "2026-09-15"],
        calendar_quality="trade_cal",
        decision_frame=frame,
    )
    assert block["actionability_status"] == "UNKNOWN"
    assert block["first_available_session"] is None
