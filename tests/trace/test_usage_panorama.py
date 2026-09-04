from __future__ import annotations

import json
import stat
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.common.run_identity import RunContract, sha256_json, write_run_contract
from autoresearch.trace.usage_panorama import (
    Selection,
    SelectionError,
    build_panorama,
    main,
    render_panorama,
    validate_selection,
    write_panorama,
)


def _write_transcript(
    projects_root: Path,
    session: str,
    *,
    timestamp: str | None,
    end_timestamp: str | None = None,
    model: str = "claude-sonnet-4-5",
    secret: str = "prompt-secret",
) -> Path:
    end_timestamp = timestamp if end_timestamp is None else end_timestamp
    path = projects_root / "project-slug" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "type": "user",
            "timestamp": end_timestamp,
            "message": {"content": secret},
        },
        {
            "type": "assistant",
            "timestamp": timestamp,
            "attributionAgent": "l3-rank",
            "message": {
                "id": f"msg-{session}",
                "model": model,
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 3,
                    "cache_read_input_tokens": 20,
                    "cache_creation_input_tokens": 4,
                    "cache_creation": {"ephemeral_5m_input_tokens": 4},
                },
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Bash",
                        "input": {"command": f"echo {secret}"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "timestamp": end_timestamp,
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": f"result-{secret}",
                    }
                ]
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _selection(**overrides) -> Selection:
    values = {"engine": "claude", "cohort": "candidate", "sessions": ("s-one",)}
    values.update(overrides)
    return Selection(**values)


def _write_contract(
    repo_root: Path,
    monkeypatch,
    run_id: str,
    *,
    session_ref: str | None,
    user_config: dict | None = None,
    run_kind: str = "scan-market",
) -> tuple[Path, RunContract]:
    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(ws, "ENGINE", "claude")
    spool = "scan_runs" if run_kind == "scan-market" else "analyze_runs"
    workspace = repo_root / "context_claude" / spool / run_id
    contract = RunContract.build(
        analysis_date="2026-09-04",
        user_config=user_config or {},
        pinned={},
        data_policy={},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="abc1234",
        git_dirty=False,
        dirty_paths=[],
        run_kind=run_kind,
        engine="claude",
        workspace_path=workspace,
        session_ref=session_ref,
        run_id=run_id,
        now=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    path = workspace / "capsule" / "identity" / "run_contract.json"
    path.parent.mkdir(parents=True)
    write_run_contract(path, contract)
    return path, contract


@pytest.mark.parametrize(
    "selection, match",
    [
        (
            Selection(engine="claude", cohort="candidate"),
            "explicit session/run or complete time range",
        ),
        (Selection(engine="codex", cohort="candidate", sessions=("s",)), "only claude"),
        (Selection(engine="claude", cohort="other", sessions=("s",)), "cohort"),
        (
            Selection(
                engine="claude",
                cohort="candidate",
                from_ts=datetime(2026, 9, 1, tzinfo=timezone.utc),
            ),
            "complete time range",
        ),
    ],
)
def test_validate_selection_rejects_implicit_or_invalid_scope(selection, match):
    with pytest.raises(SelectionError, match=match):
        validate_selection(selection)


def test_explicit_sessions_are_intersected_with_event_time_and_never_mtime(tmp_path):
    projects = tmp_path / "projects"
    inside = _write_transcript(projects, "s-one", timestamp="2026-09-04T01:00:00Z")
    outside = _write_transcript(projects, "s-two", timestamp="2026-08-01T01:00:00Z")
    no_time = _write_transcript(projects, "s-three", timestamp=None)
    # All mtimes fall inside; selection must still use transcript event timestamps.
    inside.touch()
    outside.touch()
    no_time.touch()
    selection = _selection(
        sessions=("s-one", "s-two", "s-three"),
        from_ts=datetime(2026, 9, 1, tzinfo=timezone.utc),
        to_ts=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    payload = build_panorama(selection, projects_root=projects, repo_root=tmp_path)

    assert [row["session_ref"] for row in payload["sessions"]] == ["s-one", "s-three"]
    assert payload["sessions"][0]["status"] == "MEASURED"
    assert payload["sessions"][1]["status"] == "UNMEASURED_TIME"
    assert "s-two" in payload["provenance"]["exclusions"][0]["session_ref"]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "prompt-secret" not in serialized
    assert "echo" not in serialized
    assert "result-prompt-secret" not in serialized
    assert payload["agents"][0]["tool_requests"] == {"Bash": 1}
    assert payload["agents"][0]["tool_result_chars"] == {"Bash": 20}
    assert payload["totals"]["weighted_input_proxy"] == 17
    assert payload["totals"]["messages"] == 1
    assert payload["totals"]["cache_create_5m"] == 4
    assert payload["totals"]["cache_read"] == 20
    assert payload["totals"]["estimated_usd"] is not None
    assert payload["model_mix"][0]["model_family"] == "sonnet"
    assert set(payload["efficiency_metrics"]) == {
        "E1_run_weighted_input_proxy",
        "E2_cohort",
        "E3_scan_shell",
        "E4_gp_shell_first_context_p50",
        "E5_main_context_p90",
        "E6_max_subagent",
        "E7_suspected_tail",
        "E8_selection_weighted_input_proxy",
    }
    assert payload["quality_guards"]["panorama_changes_run_status"] is False
    assert payload["quality_guards"]["truncated"] is False
    assert payload["quality_guards"]["gate1"] == {
        "status": "UNMEASURED",
        "value": None,
    }


def test_run_ids_resolve_only_verified_contract_bindings_and_unbound_is_unclaimed(
    tmp_path, monkeypatch
):
    projects = tmp_path / "projects"
    _write_transcript(projects, "bound-session", timestamp="2026-09-04T01:00:00Z")
    _, contract = _write_contract(
        tmp_path,
        monkeypatch,
        "20260904T010000000000Z",
        session_ref="bound-session",
        user_config={"profile": "candidate"},
    )

    payload = build_panorama(
        _selection(
            sessions=(),
            run_ids=("20260904T010000000000Z", "20260904T020000000000Z"),
        ),
        projects_root=projects,
        repo_root=tmp_path,
    )

    assert payload["sessions"][0]["status"] == "MEASURED"
    assert payload["sessions"][0]["run_ids"] == ["20260904T010000000000Z"]
    assert payload["sessions"][1]["status"] == "UNCLAIMED"
    assert payload["sessions"][1]["run_ids"] == ["20260904T020000000000Z"]
    assert payload["provenance"]["run_config_hashes"] == {
        "20260904T010000000000Z": contract.config_hash,
        "20260904T020000000000Z": None,
    }
    assert payload["provenance"]["run_config_list_hash"]
    assert payload["efficiency_metrics"]["E1_run_weighted_input_proxy"] == {
        "20260904T010000000000Z": {"status": "MEASURED", "value": 17},
        "20260904T020000000000Z": {"status": "UNCLAIMED", "value": None},
    }
    assert payload["wall_time"]["by_run"]["20260904T010000000000Z"] == {
        "status": "MEASURED",
        "value": 0.0,
    }
    assert payload["wall_time"]["by_run"]["20260904T020000000000Z"] == {
        "status": "UNMEASURED",
        "value": None,
    }


def test_verified_bound_run_with_missing_transcript_is_gone_not_unclaimed(tmp_path, monkeypatch):
    run_id = "20260904T025000000000Z"
    _write_contract(tmp_path, monkeypatch, run_id, session_ref="gone-session")

    payload = build_panorama(
        _selection(sessions=(), run_ids=(run_id,)),
        projects_root=tmp_path / "projects",
        repo_root=tmp_path,
    )

    assert payload["sessions"][0]["status"] == "GONE"
    assert payload["efficiency_metrics"]["E1_run_weighted_input_proxy"][run_id] == {
        "status": "GONE",
        "value": None,
    }
    assert payload["coverage_status"] == "UNMEASURED"


def test_pruned_live_run_can_resolve_from_verified_published_capsule(tmp_path, monkeypatch):
    run_id = "20260904T025500000000Z"
    live_path, contract = _write_contract(
        tmp_path, monkeypatch, run_id, session_ref="published-session"
    )
    published = (
        tmp_path
        / "reports_claude"
        / "scan"
        / "20260904-0904_1234"
        / "capsule"
        / "identity"
        / "run_contract.json"
    )
    published.parent.mkdir(parents=True)
    write_run_contract(published, contract)
    live_path.unlink()
    _write_transcript(
        tmp_path / "projects",
        "published-session",
        timestamp="2026-09-04T01:00:00Z",
    )

    payload = build_panorama(
        _selection(sessions=(), run_ids=(run_id,)),
        projects_root=tmp_path / "projects",
        repo_root=tmp_path,
    )

    assert payload["sessions"][0]["status"] == "MEASURED"
    assert payload["efficiency_metrics"]["E1_run_weighted_input_proxy"][run_id]["value"] == 17


def test_partial_population_does_not_publish_cohort_percentiles(tmp_path):
    projects = tmp_path / "projects"
    _write_transcript(projects, "measured", timestamp="2026-09-04T01:00:00Z")
    payload = build_panorama(
        _selection(sessions=("measured", "missing")),
        projects_root=projects,
        repo_root=tmp_path,
    )

    assert payload["coverage_status"] == "PARTIAL"
    assert payload["efficiency_metrics"]["E2_cohort"] == {
        "status": "PARTIAL",
        "selected_count": 2,
        "eligible_count": 0,
        "p50": None,
        "p90": None,
    }
    assert payload["efficiency_metrics"]["E8_selection_weighted_input_proxy"] == {
        "status": "UNMEASURED",
        "value": None,
        "reason": "development-line classification is unavailable",
    }


@pytest.mark.parametrize("corruption", ["hash", "schema", "engine", "nested"])
def test_invalid_or_unrelated_contract_data_never_claims_a_session(
    tmp_path, monkeypatch, corruption
):
    run_id = "20260904T040000000000Z"
    path, contract = _write_contract(
        tmp_path,
        monkeypatch,
        run_id,
        session_ref=None,
        user_config={"nested": {"session_ref": "must-not-claim"}},
    )
    raw = contract.to_dict()
    if corruption == "hash":
        raw["contract_hash"] = "0" * 64
    elif corruption == "schema":
        raw["schema_version"] = 999
    elif corruption == "engine":
        wrong = replace(contract, engine="codex", contract_hash="")
        raw = replace(wrong, contract_hash=sha256_json(wrong._hash_payload())).to_dict()
    path.write_text(json.dumps(raw), encoding="utf-8")

    payload = build_panorama(
        _selection(sessions=(), run_ids=(run_id,)),
        projects_root=tmp_path / "projects",
        repo_root=tmp_path,
    )

    assert payload["sessions"] == [
        {
            "session_ref": None,
            "run_ids": [run_id],
            "status": "UNCLAIMED",
            "agents": 0,
        }
    ]
    assert payload["provenance"]["run_config_hashes"][run_id] == (
        contract.config_hash if corruption == "nested" else None
    )
    assert payload["totals"]["weighted_input_proxy"] is None
    assert payload["totals"]["estimated_usd"] is None


def test_two_runs_sharing_one_session_are_not_double_claimed(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    _write_transcript(projects, "shared-session", timestamp="2026-09-04T01:00:00Z")
    run_ids = ("20260904T050000000000Z", "20260904T060000000000Z")
    for run_id in run_ids:
        _write_contract(tmp_path, monkeypatch, run_id, session_ref="shared-session")

    payload = build_panorama(
        _selection(sessions=(), run_ids=run_ids),
        projects_root=projects,
        repo_root=tmp_path,
    )

    assert payload["totals"]["weighted_input_proxy"] == 17
    assert all(
        row["status"] == "AMBIGUOUS_SHARED_SESSION"
        and row["value"] is None
        and "not apportioned" in row["ambiguity_note"]
        for row in payload["efficiency_metrics"]["E1_run_weighted_input_proxy"].values()
    )
    assert all(
        row == {"status": "AMBIGUOUS_SHARED_SESSION", "value": None}
        for row in payload["wall_time"]["by_run"].values()
    )


def test_unselected_sibling_contract_still_makes_selected_run_ambiguous(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    _write_transcript(projects, "shared-session", timestamp="2026-09-04T01:00:00Z")
    selected = "20260904T061000000000Z"
    _write_contract(tmp_path, monkeypatch, selected, session_ref="shared-session")
    _write_contract(
        tmp_path,
        monkeypatch,
        "20260904T062000000000Z",
        session_ref="shared-session",
    )

    payload = build_panorama(
        _selection(sessions=(), run_ids=(selected,)),
        projects_root=projects,
        repo_root=tmp_path,
    )

    assert (
        payload["efficiency_metrics"]["E1_run_weighted_input_proxy"][selected]["status"]
        == "AMBIGUOUS_SHARED_SESSION"
    )
    assert payload["efficiency_metrics"]["E1_run_weighted_input_proxy"][selected]["value"] is None


@pytest.mark.parametrize("case", ["comparable", "incomparable", "wrong_kind"])
def test_e2_requires_ten_distinct_comparable_scan_run_contracts(tmp_path, monkeypatch, case):
    projects = tmp_path / "projects"
    run_ids = []
    for index in range(10):
        run_id = f"20260904T07{index:02d}00000000Z"
        session = f"cohort-{index}"
        run_ids.append(run_id)
        _write_transcript(projects, session, timestamp="2026-09-04T01:00:00Z")
        _write_contract(
            tmp_path,
            monkeypatch,
            run_id,
            session_ref=session,
            user_config={"profile": "other" if case == "incomparable" and index == 9 else "same"},
            run_kind="stock-research" if case == "wrong_kind" and index == 9 else "scan-market",
        )

    payload = build_panorama(
        _selection(sessions=(), run_ids=tuple(run_ids)),
        projects_root=projects,
        repo_root=tmp_path,
    )

    e2 = payload["efficiency_metrics"]["E2_cohort"]
    assert e2["selected_count"] == 10
    assert e2["eligible_count"] == (9 if case == "wrong_kind" else 10)
    expected = {
        "comparable": "ELIGIBLE",
        "incomparable": "INCOMPARABLE",
        "wrong_kind": "INELIGIBLE",
    }[case]
    assert e2["status"] == expected
    assert e2["p50"] == (17 if case == "comparable" else None)
    assert e2["p90"] == (17 if case == "comparable" else None)


def test_bounded_mixed_session_excludes_timeless_ref_from_measured_totals(tmp_path):
    projects = tmp_path / "projects"
    _write_transcript(projects, "mixed", timestamp="2026-09-04T01:00:00Z")
    subagent = projects / "project-slug" / "mixed" / "subagents" / "agent-timeless.jsonl"
    subagent.parent.mkdir(parents=True)
    source = _write_transcript(tmp_path / "source", "timeless", timestamp=None)
    subagent.write_bytes(source.read_bytes())
    selection = _selection(
        sessions=("mixed",),
        from_ts=datetime(2026, 9, 1, tzinfo=timezone.utc),
        to_ts=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    payload = build_panorama(selection, projects_root=projects, repo_root=tmp_path)

    assert payload["sessions"][0]["status"] == "PARTIAL_UNMEASURED_TIME"
    assert payload["sessions"][0]["weighted_input_proxy"] == 17
    assert payload["totals"]["calls"] == 1
    assert payload["totals"]["weighted_input_proxy"] == 17
    assert any(
        row["reason"] == "missing reliable transcript event time"
        for row in payload["provenance"]["exclusions"]
    )
    assert any(row["reason"] == "missing agent meta" for row in payload["provenance"]["exclusions"])


def test_model_mix_coalesces_versions_by_family_and_keeps_components(tmp_path):
    projects = tmp_path / "projects"
    main_path = _write_transcript(
        projects,
        "models",
        timestamp="2026-09-04T01:00:00Z",
        model="claude-sonnet-4-5-20250929",
    )
    main_rows = main_path.read_text(encoding="utf-8").splitlines()
    extra = json.loads(main_rows[1])
    extra["message"]["id"] = "msg-models-second"
    main_path.write_text("\n".join([*main_rows, json.dumps(extra)]) + "\n", encoding="utf-8")
    subagent = projects / "project-slug" / "models" / "subagents" / "agent-model.jsonl"
    subagent.parent.mkdir(parents=True)
    source = _write_transcript(
        tmp_path / "source-model",
        "newer",
        timestamp="2026-09-04T01:01:00Z",
        model="claude-sonnet-4-6-20260217",
    )
    subagent.write_bytes(source.read_bytes())

    payload = build_panorama(
        _selection(sessions=("models",)), projects_root=projects, repo_root=tmp_path
    )

    assert payload["model_mix"] == [
        {
            "model_family": "sonnet",
            "calls": 3,
            "input": 30,
            "cache_read": 60,
            "cache_create_5m": 12,
            "cache_create_1h": 0,
            "output": 9,
            "weighted_input_proxy": 51,
            "estimated_usd": pytest.approx(0.000288),
            "unpriced_calls": 0,
        }
    ]
    assert payload["totals"]["calls"] == sum(row["calls"] for row in payload["model_mix"])
    assert payload["totals"]["calls"] == sum(row["messages"] for row in payload["agents"])
    assert payload["totals"]["transcript_count"] == len(payload["agents"])
    assert payload["coverage_status"] == "COMPLETE"
    assert payload["efficiency_metrics"]["E2_cohort"]["status"] == "INELIGIBLE"
    assert payload["efficiency_metrics"]["E2_cohort"]["p50"] is None
    assert payload["efficiency_metrics"]["E8_selection_weighted_input_proxy"] == {
        "status": "UNMEASURED",
        "value": None,
        "reason": "development-line classification is unavailable",
    }


def test_wall_time_uses_union_of_session_envelopes_not_overlapping_agent_sum(tmp_path):
    projects = tmp_path / "projects"
    _write_transcript(
        projects,
        "overlap",
        timestamp="2026-09-04T01:00:00Z",
        end_timestamp="2026-09-04T01:10:00Z",
    )
    subagent = projects / "project-slug" / "overlap" / "subagents" / "agent-overlap.jsonl"
    subagent.parent.mkdir(parents=True)
    source = _write_transcript(
        tmp_path / "source-overlap",
        "sub",
        timestamp="2026-09-04T01:02:00Z",
        end_timestamp="2026-09-04T01:05:00Z",
    )
    subagent.write_bytes(source.read_bytes())

    payload = build_panorama(
        _selection(sessions=("overlap",)), projects_root=projects, repo_root=tmp_path
    )

    assert payload["totals"]["wall_time_seconds"] == 600
    assert payload["wall_time"]["cumulative_agent_time_s"] == 780
    assert payload["wall_time"]["by_session"]["overlap"] == {
        "status": "MEASURED",
        "value": 600,
    }
    assert payload["wall_time"]["stage_wall_time_seconds"] is None
    assert payload["wall_time"]["stage_status"] == "UNMEASURED"


def test_time_only_selection_discovers_sessions_from_transcript_events(tmp_path):
    projects = tmp_path / "projects"
    _write_transcript(projects, "included", timestamp="2026-09-04T01:00:00Z")
    _write_transcript(projects, "excluded", timestamp="2026-08-01T01:00:00Z")
    selection = Selection(
        engine="claude",
        cohort="baseline",
        from_ts=datetime(2026, 9, 1, tzinfo=timezone.utc),
        to_ts=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    payload = build_panorama(selection, projects_root=projects, repo_root=tmp_path)

    assert [row["session_ref"] for row in payload["sessions"]] == ["included"]


def test_canonical_json_is_markdown_source_and_writes_are_private_non_overwriting(tmp_path):
    projects = tmp_path / "projects"
    _write_transcript(projects, "s-one", timestamp="2026-09-04T01:00:00Z")
    payload = build_panorama(_selection(), projects_root=projects, repo_root=tmp_path)
    markdown = render_panorama(payload)
    now = datetime(2026, 9, 4, 2, 3, 4, tzinfo=timezone.utc)

    json_path, md_path = write_panorama(
        payload, markdown, reports_root=tmp_path / "reports", now=now
    )

    assert json_path.name == "panorama_20260904T020304Z.json"
    assert md_path.name == "panorama_20260904T020304Z.md"
    assert json.loads(json_path.read_text(encoding="utf-8")) == payload
    assert md_path.read_text(encoding="utf-8") == render_panorama(
        json.loads(json_path.read_text(encoding="utf-8"))
    )
    assert stat.S_IMODE(json_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(md_path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_panorama(payload, markdown, reports_root=tmp_path / "reports", now=now)


def test_cli_write_is_forbidden_from_codex_even_with_claude_argument(monkeypatch, capsys):
    monkeypatch.setattr(ws, "ENGINE", "codex")

    rc = main(["--engine", "claude", "--cohort", "candidate", "--session", "s", "--write"])

    assert rc == 2
    assert "Codex" in capsys.readouterr().err


def test_cli_read_is_forbidden_from_codex_before_build(monkeypatch, capsys):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(
        "autoresearch.trace.usage_panorama.build_panorama",
        lambda *args, **kwargs: pytest.fail("Codex CLI must reject before access"),
    )

    rc = main(["--engine", "claude", "--cohort", "candidate", "--session", "s"])

    assert rc == 2
    assert "Codex" in capsys.readouterr().err


def test_cli_successfully_writes_explicit_selection_to_private_metering_root(
    tmp_path, monkeypatch, capsys
):
    projects = tmp_path / ".claude" / "projects"
    _write_transcript(projects, "cli-session", timestamp="2026-09-04T01:00:00Z")
    reports = tmp_path / "reports_claude"
    monkeypatch.setattr(ws, "ENGINE", "claude")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(ws, "reports_root", lambda: reports)

    rc = main(
        [
            "--engine",
            "claude",
            "--cohort",
            "candidate",
            "--session",
            "cli-session",
            "--write",
        ]
    )

    assert rc == 0
    files = sorted((reports / "_metering").iterdir())
    assert [path.suffix for path in files] == [".json", ".md"]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)
    assert "JSON" in capsys.readouterr().out
