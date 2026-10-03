import json

import pytest

from autoresearch.session_agent import artifacts, workflows

from .test_service import _handle, _request


@pytest.mark.parametrize("kind,mode,subject", [
    ("stock-research", "LITE", "600519.SS"), ("stock-research", "FULL", "NVDA"),
    ("macro-research", "LITE", None), ("macro-research", "FULL", None),
    ("sector-research", "LITE", "半导体"), ("sector-research", "FULL", "半导体"),
    ("scan-market", "AUTO", None),
])
def test_new_entrypoint_plans_freeze_one_clock_input(tmp_path, kind, mode, subject):
    handle = _handle(tmp_path)
    request = dict(_request(), kind=kind, requested_mode=mode, subject=subject)
    plan = workflows.build_plan(request, handle)
    assert all("research.frame" in task["input_artifact_ids"] for task in plan["tasks"])


def test_register_frame_is_read_only_frozen_and_never_guesses_holidays(tmp_path):
    from autoresearch.session_agent import decision_frame

    handle = _handle(tmp_path)
    request = _request()
    request["analysis_date"] = "2026-09-11"
    decision_frame.register_frame(request, handle, calendar_loader=lambda start, end: (
        ["2026-09-11", "2026-09-14", "2026-09-15"], "trade_cal",
    ))
    with artifacts.open_artifact(handle, "research.frame") as stream:
        value = json.load(stream)
    assert value["entry_session"] == "2026-09-14"
    assert value["usage"] == "standalone"
    old = artifacts.snapshot_artifact(handle, "research.frame")
    decision_frame.register_frame(request, handle, calendar_loader=lambda *_: pytest.fail("reloaded"))
    assert artifacts.snapshot_artifact(handle, "research.frame") == old


def test_expansion_inherits_frame_without_mutating_existing_plan(tmp_path):
    from autoresearch.contracts.session_plan import expansion_hash
    from autoresearch.session_agent import decision_frame

    task = {"input_artifact_ids": ["scan.l2"]}
    value = {"tasks": [task], "template_id": "scan.l3", "expansion_id": "",
             "expansion_hash": "", "plan_hash": "a" * 64, "schema_version": 1,
             "input_artifacts": []}
    snapshot = {"artifact_id": "research.frame", "sha256": "b" * 64,
                "relative_path": "session_outputs/decision_frame.json"}
    result = decision_frame.attach_expansion(value, snapshot)
    assert result["tasks"][0]["input_artifact_ids"] == ["scan.l2", "research.frame"]
    assert task["input_artifact_ids"] == ["scan.l2"]
    assert result["expansion_hash"] == expansion_hash(result)
    assert result["input_artifacts"] == [{"artifact_id": "research.frame", "sha256": "b" * 64}]


def test_frame_is_consumed_without_breaking_single_domain_input_prompt(tmp_path):
    from autoresearch.common.execution_math import build_decision_frame
    from autoresearch.session_agent.dispatch import render_prompt

    handle = _handle(tmp_path)
    frame = handle.staging / "frame.json"
    frame.write_text(json.dumps(build_decision_frame(
        analysis_session="2026-09-11", knowledge_cutoff="2026-09-11T21:00:00+08:00",
        venue="XSHG", research_depth="LITE", usage="macro",
        sessions=["2026-09-11", "2026-09-14", "2026-09-15"], calendar_quality="trade_cal",
    )))
    artifacts.register_artifact(handle, "research.frame", frame, "READ")
    prompt = render_prompt(handle, {"role": "macro.brief"}, 1,
        inputs={"macro.strategist_pack": "/tmp/pack.json", "research.frame": str(frame)},
        outputs={"macro.market_view": "/tmp/view.md"})
    assert "gap_c1_o2" in prompt
    assert "2026-09-14" in prompt and "2026-09-15" in prompt
    assert "ENTRY_PRICE" in prompt


@pytest.mark.parametrize("field,value", [
    ("knowledge_cutoff", "2030-01-01T12:00:00+08:00"),
    ("research_depth", "FULL"), ("usage", "sector"),
    ("venue", "XHKG"),
])
def test_unbound_preexisting_frame_must_match_frozen_request(tmp_path, field, value):
    from autoresearch.common.execution_math import build_decision_frame
    from autoresearch.session_agent import decision_frame

    handle = _handle(tmp_path)
    request = _request()
    frame = build_decision_frame(
        analysis_session=request["analysis_date"], knowledge_cutoff="2026-09-13T01:02:03+00:00",
        venue="XSHG", research_depth="LITE", usage="standalone", sessions=[], calendar_quality="UNKNOWN",
    )
    frame[field] = value
    if field == "venue":
        frame["timezone"] = "Asia/Hong_Kong"
    path = handle.staging / "session_outputs/decision_frame.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(frame))
    with pytest.raises(ValueError, match="frozen frame"):
        decision_frame.register_frame(request, handle, calendar_loader=lambda *_: pytest.fail("reloaded"))
