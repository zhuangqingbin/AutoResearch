"""Headless runs are metered from the executor's call records (batch 4 Task 2, spec R3).

One ``claude -p`` session per inference attempt → one row with ``dispatcher=headless``.
Cost = the CLI's own ``total_cost_usd``; a record whose transcript is gone is an
UNMEASURED row, never a $0 row.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from autoresearch.trace import usage_harvest as U
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter

COSTS = {"scan.l4.card.600000": 0.81, "scan.l4.intel.600000": 0.27, "scan.l3": 1.5}
AGENTS = {"scan.l4.card.600000": "l4-card", "scan.l4.intel.600000": "l4-intel",
          "scan.l3": "l3-rank"}


def _transcript(path: Path, mid: str, *, output: int = 40, cache_read: int = 30_000) -> None:
    rows = [
        {"type": "user", "message": {"role": "user", "content": "prompt"}},
        {"type": "assistant", "message": {
            "id": mid, "model": "claude-opus-5-5", "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": output,
                      "cache_read_input_tokens": cache_read,
                      "cache_creation_input_tokens": 1_000}}},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def _staging(tmp_path: Path, *, drop_transcript: str | None = None) -> Path:
    staging = tmp_path / "staging"
    folder = staging / "_dispatch" / "headless"
    folder.mkdir(parents=True)
    for index, (task_id, cost) in enumerate(COSTS.items()):
        sid = f"0000000{index}-aaaa-bbbb-cccc-dddddddddddd"
        transcript = tmp_path / "projects" / "slug" / f"{sid}.jsonl"
        if task_id != drop_transcript:
            _transcript(transcript, f"msg-{index}")
        (folder / f"{task_id}.a1.json").write_text(json.dumps({
            "schema_version": 1, "run_id": "R", "task_id": task_id, "attempt": 1,
            "role": task_id.rsplit(".", 1)[0] if task_id != "scan.l3" else "scan.l3",
            "agent_type": AGENTS[task_id], "state": "EXITED", "exit_code": 0,
            "session_id": sid, "total_cost_usd": cost, "usage": {"output_tokens": 40},
            "transcript_path": str(transcript) if task_id != drop_transcript else None,
        }), encoding="utf-8")
    (folder / "scan.l3.a1.stdout").write_text("{}", encoding="utf-8")   # streams are not records
    return staging


def test_three_records_become_three_headless_rows_costed_from_result_json(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "PROJECTS_ROOT", tmp_path / "projects")
    rows = U.collect_headless(_staging(tmp_path))
    assert len(rows) == 3
    assert {row["dispatcher"] for row in rows} == {"headless"}
    assert sorted(row["agent"] for row in rows) == ["l3-rank", "l4-card", "l4-intel"]
    assert all(row["status"] != "UNMEASURED" and row["cost_source"] == "result_json" for row in rows)
    ledger = U.build_ledger(rows, source="headless")
    assert abs(ledger["totals"]["estimated_usd"] - sum(COSTS.values())) < 1e-9
    assert ledger["totals"]["headless_transcripts"] == 3
    # tokens still come from the transcript (message.id-deduplicated), not from the record
    assert all(row["cache_read"] == 30_000 for row in rows)


def test_missing_transcript_is_an_unmeasured_row_not_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "PROJECTS_ROOT", tmp_path / "projects")
    rows = U.collect_headless(_staging(tmp_path, drop_transcript="scan.l3"))
    lost = next(row for row in rows if row["task_id"] == "scan.l3")
    assert lost["status"] == "UNMEASURED" and lost["estimated_usd"] is None
    assert lost["dispatcher"] == "headless" and lost["agent"] == "l3-rank"
    assert lost["reported_cost_usd"] == 1.5          # kept as a fact, not summed
    ledger = U.build_ledger(rows, source="headless")
    assert ledger["totals"]["unmeasured_transcripts"] == 1
    assert abs(ledger["totals"]["estimated_usd"] - (0.81 + 0.27)) < 1e-9
    md = U.render(rows, sub_dir="headless")
    lost_line = next(line for line in md.splitlines() if "| l3-rank |" in line)
    assert "— (UNMEASURED)" in lost_line and "$0.0000" not in lost_line


def test_spawn_failed_records_are_not_sessions_so_not_unmeasured_rows(tmp_path, monkeypatch):
    """A `claude` that never started ran no session: no row at all (review M11a)."""
    monkeypatch.setattr(U, "PROJECTS_ROOT", tmp_path / "projects")
    staging = _staging(tmp_path)
    (staging / "_dispatch" / "headless" / "scan.l4.review.600000.a1.json").write_text(
        json.dumps({"schema_version": 1, "task_id": "scan.l4.review.600000", "attempt": 1,
                    "agent_type": "l4-card", "state": "SPAWN_FAILED",
                    "requested_session_id": "ffffffff-0000-0000-0000-000000000000"}),
        encoding="utf-8")
    rows = U.collect_headless(staging)
    assert len(rows) == 3 and all(row["status"] != "UNMEASURED" for row in rows)


def test_transcript_found_by_session_id_when_the_record_has_no_path(tmp_path, monkeypatch):
    staging = _staging(tmp_path)
    record = staging / "_dispatch" / "headless" / "scan.l3.a1.json"
    doc = json.loads(record.read_text(encoding="utf-8"))
    record.write_text(json.dumps({**doc, "transcript_path": None}), encoding="utf-8")
    monkeypatch.setattr(U, "PROJECTS_ROOT", tmp_path / "projects")
    rows = U.collect_headless(staging)
    assert next(r for r in rows if r["task_id"] == "scan.l3")["status"] != "UNMEASURED"


def test_render_shows_a_dispatcher_column_only_when_headless_rows_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(U, "PROJECTS_ROOT", tmp_path / "projects")
    md = U.render(U.collect_headless(_staging(tmp_path)), sub_dir="headless")
    assert "| dispatcher |" in md and "| headless |" in md
    assert "3 headless" in md
    legacy = tmp_path / "legacy" / "agent-a.jsonl"
    _transcript(legacy, "legacy-1")
    assert "dispatcher" not in U.render(U.collect(legacy.parent), sub_dir="legacy")


def test_cli_transcripts_from_writes_the_headless_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(U, "PROJECTS_ROOT", tmp_path / "projects")
    staging = _staging(tmp_path)
    out = tmp_path / "token_usage.md"
    ledger_path = tmp_path / "ledger.json"
    assert U.main(["--transcripts-from", str(staging), "--out", str(out),
                   "--json-out", str(ledger_path)]) == 0
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert len(ledger["rows"]) == 3
    assert {row["dispatcher"] for row in ledger["rows"]} == {"headless"}
    assert abs(ledger["totals"]["estimated_usd"] - sum(COSTS.values())) < 1e-9
    assert "| headless |" in out.read_text(encoding="utf-8")


def test_collect_run_appends_the_headless_rows_of_the_run_staging(tmp_path, monkeypatch):
    from autoresearch.trace import capsule

    staging = _staging(tmp_path)
    monkeypatch.setattr(U, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(U.ws, "find_run_root", lambda run_id: tmp_path)
    monkeypatch.setattr(capsule, "load_run", lambda run_id: SimpleNamespace(
        staging=staging, contract=SimpleNamespace(session_ref="headless-host")))
    monkeypatch.setattr(U, "adapter_for",
                        lambda engine: ClaudeTranscriptAdapter(projects_root=tmp_path / "projects"))
    rows = U.collect_run("R", engine="claude")
    assert len(rows) == 3 and {row["dispatcher"] for row in rows} == {"headless"}


def test_collect_run_without_headless_records_is_unchanged(tmp_path, monkeypatch):
    from autoresearch.trace import capsule

    staging = tmp_path / "staging"
    staging.mkdir()
    projects = tmp_path / "projects"
    _transcript(projects / "slug" / "host-session" / "subagents" / "agent-x.jsonl", "sub-1")
    monkeypatch.setattr(U.ws, "find_run_root", lambda run_id: tmp_path)
    monkeypatch.setattr(capsule, "load_run", lambda run_id: SimpleNamespace(
        staging=staging, contract=SimpleNamespace(session_ref="host-session")))
    monkeypatch.setattr(U, "adapter_for",
                        lambda engine: ClaudeTranscriptAdapter(projects_root=projects))
    rows = U.collect_run("R", engine="claude")
    assert len(rows) == 1 and "dispatcher" not in rows[0]
