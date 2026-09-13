"""scene-reconstruction Task 9: per-file salvage + attribution constraint.

Plan: `docs/superpowers/plans/2026-09-12-scene-reconstruction-transcript-binding.md` §12
Brief: `.superpowers/sdd/2026-09-12-scene-reconstruction-transcript-binding/task-9-brief.md`
Spec (binding authority): `docs/superpowers/specs/2026-09-12-scene-reconstruction-
transcript-binding-design.md` §6.3

Every fixture below is synthetic and lives entirely under ``tmp_path`` --
nothing here ever reads or writes the real ``~/.claude/projects`` or
``~/.codex/sessions`` (``sessions_root``/``projects_root`` are always
overridden explicitly).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.contracts import artifacts as ca
from autoresearch.scan import chain_view, salvage
from autoresearch.scan.relative_buy import DECISION_FILENAME
from autoresearch.scan.retention import verify_manifest, write_manifest

MARKET_VIEW = ca.by_name("market_view").path
ANALYSIS_DATE = "2026-08-17"


# --------------------------------------------------------------- redirection


def _redirect(monkeypatch, tmp_path: Path, engine: str = "claude") -> None:
    monkeypatch.setattr(ws, "ENGINE", engine)
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / f"context_{engine}")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / f"reports_{engine}")
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)


def _scan_reports_root(tmp_path: Path, engine: str = "claude") -> Path:
    return tmp_path / f"reports_{engine}" / "scan"


def _shared_dir(tmp_path: Path, engine: str, analysis_date: str) -> Path:
    d = tmp_path / f"context_{engine}" / "scan" / analysis_date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_run(
    tmp_path: Path,
    report_run_id: str,
    *,
    engine: str = "claude",
    analysis_date: str = ANALYSIS_DATE,
    created_at: str = "2026-08-17T09:00:00.000000Z",
    generated_at: str | None = "2026-08-17T20:30:00",
    contract_run_id: str | None = None,
    session_ref: str | None = None,
) -> Path:
    run = _scan_reports_root(tmp_path, engine) / report_run_id
    (run / "trace").mkdir(parents=True)
    contract_run_id = contract_run_id or f"{report_run_id}-contract"
    contract = {
        "schema_version": 3, "analysis_date": analysis_date, "run_id": contract_run_id,
        "created_at": created_at, "engine": engine, "session_ref": session_ref,
    }
    (run / "trace" / "run_contract.json").write_text(json.dumps(contract), encoding="utf-8")
    manifest = {"analysis_date": analysis_date, "run_id": contract_run_id}
    if generated_at is not None:
        manifest["generated_at"] = generated_at
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run


def _set_mtime(path: Path, when: datetime) -> None:
    ts = when.timestamp()
    import os

    os.utime(path, (ts, ts))


def _touch_shared(
    tmp_path: Path, engine: str, analysis_date: str, filename: str, content: str, *,
    mtime: datetime | None = None,
) -> Path:
    p = _shared_dir(tmp_path, engine, analysis_date) / filename
    p.write_text(content, encoding="utf-8")
    if mtime is not None:
        _set_mtime(p, mtime)
    return p


# --------------------------------------------------------------- pure helpers


def test_to_utc_converts_known_zones_and_epoch():
    aware = salvage._parse_instant("2026-08-17T14:00:00+00:00")
    assert aware == datetime(2026, 8, 17, 14, 0, 0, tzinfo=timezone.utc)
    assert salvage._parse_instant("2026-08-17T14:00:00Z") == aware
    assert salvage._parse_instant(1755439200) == datetime.fromtimestamp(
        1755439200, tz=timezone.utc
    )


def test_same_instant_utc_and_local_normalize_identically():
    """Required fixture 4: the same instant expressed once as aware UTC and
    once as a naive local string + a known zone name must normalize to the
    identical UTC instant -- and therefore drive the identical in-window
    verdict. Asia/Shanghai is UTC+8, so 22:00 local == 14:00 UTC."""
    utc_form = salvage._parse_instant("2026-08-17T14:00:00Z")
    local_form = salvage._parse_instant("2026-08-17T22:00:00", assume_tz="Asia/Shanghai")
    assert utc_form == local_form == datetime(2026, 8, 17, 14, 0, 0, tzinfo=timezone.utc)

    window = {"start": "2026-08-17T13:00:00Z", "end": "2026-08-17T15:00:00Z"}
    assert salvage._in_window(utc_form, window)[0] is True
    assert salvage._in_window(local_form, window)[0] is True


def test_naive_value_with_unknown_zone_is_unknown_not_guessed():
    """controller ruling 3: an unknown source zone must yield UNKNOWN, never a
    guess (never silently treated as UTC or as the local machine's zone)."""
    assert salvage._parse_instant("2026-08-17T22:00:00") is None
    assert salvage._parse_instant("2026-08-17T22:00:00", assume_tz="Not/AZone") is None


def test_in_window_both_edges_unknown_is_neither_true_nor_false():
    verdict, reason = salvage._in_window(datetime.now(timezone.utc), {"start": None, "end": None})
    assert verdict is None
    assert "未知" in reason


# --------------------------------------------------------------- window


def test_run_forensic_window_reads_contract_start_and_leaves_naive_end_unknown(
    tmp_path, monkeypatch
):
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000", generated_at="2026-08-17T20:30:00")
    window = salvage.run_forensic_window(run)
    assert window["start"] == "2026-08-17T09:00:00.000000+00:00"
    assert window["start_source"] == "run_contract.created_at"
    # naive manifest.generated_at, no assume_local_tz -> unknown, never guessed
    assert window["end"] is None
    assert window["end_source"] is None


def test_run_forensic_window_honors_explicit_local_tz_when_asked(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000", generated_at="2026-08-17T20:30:00")
    window = salvage.run_forensic_window(run, assume_local_tz="Asia/Shanghai")
    assert window["end"] == "2026-08-17T12:30:00.000000+00:00"
    assert window["end_source"] == "manifest.generated_at"


# --------------------------------------------------------------- required fixture 2:
# a file whose only link to the run is a close mtime


def test_close_mtime_only_is_time_window_only_never_a_fact(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    _touch_shared(
        tmp_path, "claude", ANALYSIS_DATE, DECISION_FILENAME, '{"mode": "active"}',
        mtime=datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc),
    )
    result = salvage.salvage_run(run, sessions_root=tmp_path / "no-sessions")
    row = next(r for r in result["files"] if r["file_kind"] == "e6_decision")
    assert row["attribution"] == "TIME_WINDOW_ONLY"
    assert salvage.is_fact(row["attribution"]) is False
    assert row["blob"] is not None  # still archived as reference material


# --------------------------------------------------------------- required fixture 1:
# a same-date re-run that overwrote an earlier file


def test_same_date_rerun_overwrites_earlier_file_is_detected(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    early = _write_run(
        tmp_path, "20260817_2000",
        created_at="2026-08-17T09:00:00.000000Z", generated_at="2026-08-17T20:00:00",
    )
    later = _write_run(
        tmp_path, "20260817_2215",
        created_at="2026-08-17T21:00:00.000000Z", generated_at="2026-08-17T22:15:00",
    )
    # the later run is "modern": it already mirrored its own copy into trace/staging/
    later_staging = later / "trace" / "staging"
    later_staging.mkdir(parents=True)
    later_content = '{"mode": "active", "buys": [{"code": "600000"}]}'
    (later_staging / DECISION_FILENAME).write_text(later_content, encoding="utf-8")
    # shared staging currently holds exactly the later run's bytes (it overwrote the
    # earlier run's original content, which is now unrecoverable from this location)
    _touch_shared(tmp_path, "claude", ANALYSIS_DATE, DECISION_FILENAME, later_content)

    result = salvage.salvage_run(early, sessions_root=tmp_path / "no-sessions")
    row = next(r for r in result["files"] if r["file_kind"] == "e6_decision")
    assert row["attribution"] == "OVERWRITTEN_BY_LATER_RUN"
    assert row["overwritten_by"] == "20260817_2215"
    assert salvage.is_fact(row["attribution"]) is False
    assert result["same_date_multi_run"] is True
    assert result["sibling_report_run_ids"] == ["20260817_2215"]


# --------------------------------------------------------------- required fixture 3:
# a run that already has its E6 decision but is missing its market view


def test_partial_run_missing_market_view_is_still_salvaged(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    # E6 decision already lives inside the run's own frozen evidence (no salvage needed)
    (run / "trace").mkdir(exist_ok=True)
    (run / "trace" / DECISION_FILENAME).write_text('{"mode": "active"}', encoding="utf-8")
    # market_view.md is missing from both run and shared -- must still be attempted
    result = salvage.salvage_run(run, sessions_root=tmp_path / "no-sessions")
    kinds = {r["file_kind"]: r for r in result["files"]}
    assert kinds["e6_decision"]["attribution"] == "VERIFIED_RUN"
    assert "无需抢救" in kinds["e6_decision"]["reason"]
    assert kinds["market_view"]["attribution"] == "ABSENT"
    assert "market_view" in kinds  # never skipped just because e6_decision was present


def test_already_present_in_mirror_is_verified_without_touching_shared(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    mirror = run / "trace" / "staging"
    mirror.mkdir(parents=True)
    (mirror / MARKET_VIEW).write_text("# 市场研判\n", encoding="utf-8")
    result = salvage.salvage_run(run, sessions_root=tmp_path / "no-sessions")
    row = next(r for r in result["files"] if r["file_kind"] == "market_view")
    assert row["attribution"] == "VERIFIED_RUN"
    assert row["source"] == str(Path("trace") / "staging" / MARKET_VIEW)


def test_absent_when_neither_run_nor_shared_has_it(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    result = salvage.salvage_run(run, sessions_root=tmp_path / "no-sessions")
    for row in result["files"]:
        if row["file_kind"] in ("e6_decision", "market_view"):
            assert row["attribution"] == "ABSENT"
            assert row["blob"] is None


# --------------------------------------------------------------- VERIFIED_RUN via
# transcript identity / write-hash cross-check


def _claude_transcript_write_row(tool_id, target: Path, *, ts: str, msg_id: str, content: str) -> dict:
    return {
        "type": "assistant", "timestamp": ts,
        "message": {
            "id": msg_id, "model": "claude-opus-5",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "Write",
                 "input": {"file_path": str(target), "content": content}}
            ],
        },
    }


def _claude_transcript_result_row(tool_id, *, ts: str) -> dict:
    return {
        "type": "user", "timestamp": ts,
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": "ok", "is_error": False}
        ]},
    }


def _write_claude_session(root: Path, slug: str, session_ref: str, rows: list[dict]) -> Path:
    p = root / slug / f"{session_ref}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_verified_run_via_located_transcript_and_matching_write_hash(tmp_path, monkeypatch):
    """A Claude transcript located by exact session_ref match, whose own
    recorded WRITE_SUCCEEDED hash equals the shared file's current bytes,
    promotes the flat file all the way to VERIFIED_RUN (run identity +
    original content hash -- the strong evidence spec §6.3 requires)."""
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000", session_ref="sess-abc")
    content = '{"mode": "active", "buys": []}'
    shared_path = _touch_shared(tmp_path, "claude", ANALYSIS_DATE, DECISION_FILENAME, content)
    target = Path("/scan-target") / DECISION_FILENAME
    sessions_root = tmp_path / "fake-claude-projects"
    _write_claude_session(
        sessions_root, "proj", "sess-abc",
        [
            _claude_transcript_write_row("tool-1", target, ts="2026-08-17T20:00:00Z",
                                          msg_id="m1", content=content),
            _claude_transcript_result_row("tool-1", ts="2026-08-17T20:00:01Z"),
        ],
    )
    result = salvage.salvage_run(run, sessions_root=sessions_root)
    row = next(r for r in result["files"] if r["file_kind"] == "e6_decision")
    assert row["attribution"] == "VERIFIED_RUN"
    assert "写入哈希一致" in row["reason"]
    assert salvage.is_fact(row["attribution"]) is True
    assert shared_path.read_text(encoding="utf-8") == content

    transcript_row = next(r for r in result["files"] if r["file_kind"] == "transcript")
    assert transcript_row["attribution"] == "VERIFIED_RUN"
    assert transcript_row["blob"] is not None


def test_missing_session_ref_caps_claude_transcript_at_unknown(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000", session_ref=None)
    result = salvage.salvage_run(run, sessions_root=tmp_path / "fake-claude-projects")
    row = next(r for r in result["files"] if r["file_kind"] == "transcript")
    assert row["attribution"] == "UNKNOWN"
    assert "session_ref" in row["reason"]


# --------------------------------------------------------------- required fixture 5:
# the Codex --all path


def _codex_session_meta(session_ref: str, cwd: str, *, ts: str) -> dict:
    return {"timestamp": ts, "ordinal": 0, "type": "session_meta",
            "payload": {"session_id": session_ref, "cwd": cwd}}


def _codex_write_row(call_id: str, target: Path, *, ts: str, ordinal: int, content: str) -> dict:
    return {"timestamp": ts, "ordinal": ordinal, "type": "response_item",
            "payload": {"type": "custom_tool_call", "call_id": call_id, "name": "write_file",
                        "input": {"file_path": str(target), "content": content}}}


def _codex_result_row(call_id: str, *, ts: str, ordinal: int) -> dict:
    return {"timestamp": ts, "ordinal": ordinal, "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "call_id": call_id,
                        "output": "ok", "is_error": False}}


def _write_codex_rollout(root: Path, day: datetime, session_ref: str, cwd: str,
                          rows: list[dict]) -> Path:
    p = root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}" / f"rollout-{session_ref}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    all_rows = [_codex_session_meta(session_ref, cwd, ts=day.isoformat())] + rows
    p.write_text("\n".join(json.dumps(r) for r in all_rows) + "\n", encoding="utf-8")
    return p


def test_all_codex_engine_uses_each_runs_own_window_and_reports_counts(tmp_path, monkeypatch):
    """Required fixture 5 + H03: --all under the Codex engine must derive each
    run's window from that run's own recorded start/end (never a fixed/shared
    clock), archive a real transcript per run via session_ref identity, and
    report per-file success/reference/absent/unknown counts without skipping
    a run just because one of its files is missing."""
    _redirect(monkeypatch, tmp_path, engine="codex")
    _write_run(
        tmp_path, "20260810_1900", engine="codex", analysis_date="2026-08-10",
        created_at="2026-08-10T09:00:00.000000Z", generated_at="2026-08-10T19:00:00",
        session_ref="codex-a",
    )
    _write_run(
        tmp_path, "20260812_1930", engine="codex", analysis_date="2026-08-12",
        created_at="2026-08-12T09:30:00.000000Z", generated_at="2026-08-12T19:30:00",
        session_ref="codex-b",
    )
    # run_a: E6 decision present in shared, market_view genuinely absent everywhere.
    # mtime is pinned close to run_a's own window -- using the real "now" would land
    # months after even the conservative open-ended-window upper bound and correctly
    # fail to qualify as TIME_WINDOW_ONLY, which is not what this fixture is testing.
    _touch_shared(
        tmp_path, "codex", "2026-08-10", DECISION_FILENAME, '{"mode": "active"}',
        mtime=datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    # run_b: neither flat file exists anywhere -- must not be skipped as a whole run.
    sessions_root = tmp_path / "fake-codex-sessions"
    _write_codex_rollout(
        sessions_root, datetime(2026, 8, 10, 9, 5, tzinfo=timezone.utc), "codex-a",
        str(tmp_path),
        [_codex_write_row("c1", Path("/x/whatever"), ts="2026-08-10T09:10:00Z", ordinal=1,
                           content="hi"),
         _codex_result_row("c1", ts="2026-08-10T09:10:01Z", ordinal=2)],
    )
    _write_codex_rollout(
        sessions_root, datetime(2026, 8, 12, 9, 40, tzinfo=timezone.utc), "codex-b",
        str(tmp_path),
        [_codex_write_row("c2", Path("/y/whatever"), ts="2026-08-12T09:50:00Z", ordinal=1,
                           content="hi"),
         _codex_result_row("c2", ts="2026-08-12T09:50:01Z", ordinal=2)],
    )

    result = salvage.salvage_all(sessions_root=sessions_root)
    assert not result["run_errors"]
    assert set(result["runs"]) == {"20260810_1900", "20260812_1930"}

    win_a = result["runs"]["20260810_1900"]["run_window"]
    win_b = result["runs"]["20260812_1930"]["run_window"]
    assert win_a["start"] != win_b["start"]
    assert win_a["start"].startswith("2026-08-10")
    assert win_b["start"].startswith("2026-08-12")
    # captured_at is real "now", not each run's own window -- and is not used as the
    # window itself (no fixed/fabricated clock standing in for either run's window).
    now = datetime.now(timezone.utc)
    for run_id in ("20260810_1900", "20260812_1930"):
        captured = datetime.fromisoformat(result["runs"][run_id]["captured_at"])
        assert abs((now - captured).total_seconds()) < 300

    run_a_files = {r["file_kind"]: r for r in result["runs"]["20260810_1900"]["files"]}
    assert run_a_files["e6_decision"]["attribution"] == "TIME_WINDOW_ONLY"
    assert run_a_files["market_view"]["attribution"] == "ABSENT"
    assert run_a_files["transcript"]["attribution"] == "VERIFIED_RUN"

    run_b_files = {r["file_kind"]: r for r in result["runs"]["20260812_1930"]["files"]}
    assert run_b_files["e6_decision"]["attribution"] == "ABSENT"
    assert run_b_files["market_view"]["attribution"] == "ABSENT"
    # run_b was NOT skipped wholesale even though two of its three files are absent
    assert "transcript" in run_b_files
    assert run_b_files["transcript"]["attribution"] == "VERIFIED_RUN"

    counts_a = result["runs"]["20260810_1900"]["counts"]
    assert counts_a["absent"] == 1 and counts_a["reference_only"] == 1 and counts_a["verified"] == 1


def test_all_never_aborts_on_one_malformed_run_directory(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path, engine="codex")
    _write_run(tmp_path, "20260810_1900", engine="codex", session_ref="codex-a")
    broken = _scan_reports_root(tmp_path, "codex") / "20260812_1930"
    broken.mkdir(parents=True)
    (broken / "manifest.json").write_text("not json{{{", encoding="utf-8")
    result = salvage.salvage_all(sessions_root=tmp_path / "no-sessions")
    assert "20260810_1900" in result["runs"]
    # fix-round-1 finding 3: assert the outcome actually expected, not an `or` between
    # two mutually exclusive branches that cannot discriminate which one happened. A
    # manifest that fails to parse degrades to an unknown analysis_date (every
    # downstream lookup gated on it returns UNKNOWN/ABSENT) rather than raising --
    # salvage_run itself must not crash on this shape, so the run completes cleanly.
    assert "20260812_1930" in result["runs"]
    assert not result["run_errors"]
    broken_files = {r["file_kind"]: r["attribution"] for r in result["runs"]["20260812_1930"]["files"]}
    assert broken_files["e6_decision"] == "UNKNOWN"
    assert broken_files["market_view"] == "UNKNOWN"
    assert not result["runs"]["20260812_1930"]["errors"]


# --------------------------------------------------------------- H02: attribution
# strength gate -- TIME_WINDOW_ONLY / OVERWRITTEN_BY_LATER_RUN never count as fact


def test_h02_is_fact_only_true_for_verified_run():
    assert salvage.is_fact("VERIFIED_RUN") is True
    for other in ("TIME_WINDOW_ONLY", "OVERWRITTEN_BY_LATER_RUN", "UNKNOWN", "ABSENT"):
        assert salvage.is_fact(other) is False


def test_h02_chain_view_source_order_promotes_only_verified_run(tmp_path, monkeypatch):
    """Ruling 5: an attributed (VERIFIED_RUN) salvage snapshot comes before the
    mutable shared directory in chain_view's own source order; a
    TIME_WINDOW_ONLY entry must never be promoted -- chain_view must still
    fall through to (and label) the shared directory as before."""
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    shared_content = '{"mode": "active", "buys": [], "candidates": [], "excluded": []}'
    # mtime pinned close to the run's own window so this genuinely lands on
    # TIME_WINDOW_ONLY (not UNKNOWN) -- the case this test must actually exercise.
    _touch_shared(
        tmp_path, "claude", ANALYSIS_DATE, DECISION_FILENAME, shared_content,
        mtime=datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc),
    )

    src = chain_view.Sources(run)
    # No provenance recorded yet -> falls through to shared, as before.
    found = src.find(DECISION_FILENAME)
    assert found is not None and src.used_shared is True

    # Record a TIME_WINDOW_ONLY provenance entry -- must NOT be promoted.
    result = salvage.salvage_run(run, sessions_root=tmp_path / "no-sessions")
    row = next(r for r in result["files"] if r["file_kind"] == "e6_decision")
    assert row["attribution"] == "TIME_WINDOW_ONLY"  # exercising the case this test is about
    src2 = chain_view.Sources(run)
    found2 = src2.find(DECISION_FILENAME)
    assert found2 is not None
    assert found2.read_text(encoding="utf-8") == shared_content
    # identity, not just coincidental byte-equality with a blob: this must resolve
    # to the *shared* path itself, never a salvage blob path.
    assert found2 == src2.shared / DECISION_FILENAME
    assert "blobs" not in found2.parts
    assert src2.used_shared is True  # still only reference material, still labelled


def test_h02_chain_view_promotes_verified_run_before_shared(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    (run / "manifest.json").write_text(
        json.dumps({"analysis_date": ANALYSIS_DATE}), encoding="utf-8"
    )
    mirror = run / "trace" / "staging"
    mirror.mkdir(parents=True)
    verified_content = '{"mode": "active"}'
    (mirror / DECISION_FILENAME).write_text(verified_content, encoding="utf-8")
    # Salvage picks this up as VERIFIED_RUN (already in the run's own evidence) and
    # records a blob for it -- but since it's already found via the mirror tier,
    # chain_view never even needs the salvage tier for this specific case. Prove the
    # *general* promotion mechanism instead, directly through resolve_attributed_source:
    salvage.salvage_run(run, sessions_root=tmp_path / "no-sessions")
    resolved = salvage.resolve_attributed_source(run, DECISION_FILENAME)
    # already-present rows are VERIFIED_RUN but carry no blob (chain_view's mirror
    # tier already serves them) -- resolve_attributed_source must not fabricate one.
    assert resolved is None

    # Now simulate the case that actually exercises the new tier: a VERIFIED_RUN row
    # sourced from *outside* the run (transcript write-hash cross-check), which does
    # carry a persisted blob distinct from the mutable shared file. Goes through the
    # same trace.blobs-backed _store_blob production code uses -- not a hand-rolled
    # path -- so the blob's recorded location matches trace.blobs' own fan-out layout.
    prov_path = salvage._provenance_path(run)
    doc = json.loads(prov_path.read_text(encoding="utf-8"))
    blob_bytes = b'{"mode": "active", "buys": ["600000"]}'
    blob_record = salvage._store_blob(run, blob_bytes)
    for row in doc["files"]:
        if row["file_kind"] == "market_view":
            row["attribution"] = "VERIFIED_RUN"
            row["blob"] = blob_record
    from autoresearch.common.atomic import atomic_write_json

    atomic_write_json(prov_path, doc)

    _touch_shared(tmp_path, "claude", ANALYSIS_DATE, MARKET_VIEW, "# 共享的市场研判(不该被读到)\n")
    src = chain_view.Sources(run)
    found = src.find(MARKET_VIEW)
    assert found is not None
    assert found.read_bytes() == blob_bytes
    assert src.used_shared is False  # promoted source, not the mutable shared fallback


# --------------------------------------------------------------- idempotence


def test_idempotent_rerun_does_not_duplicate_or_downgrade(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000", session_ref="sess-idem")
    content = '{"mode": "active"}'
    shared_path = _touch_shared(tmp_path, "claude", ANALYSIS_DATE, DECISION_FILENAME, content)
    target = Path("/scan-target") / DECISION_FILENAME
    sessions_root = tmp_path / "fake-claude-projects"
    _write_claude_session(
        sessions_root, "proj", "sess-idem",
        [_claude_transcript_write_row("t1", target, ts="2026-08-17T20:00:00Z", msg_id="m1",
                                       content=content),
         _claude_transcript_result_row("t1", ts="2026-08-17T20:00:01Z")],
    )
    first = salvage.salvage_run(run, sessions_root=sessions_root)
    assert first["wrote"] is True
    # trace.blobs' own fan-out layout (blobs/sha256/<xx>/<digest>) -- count actual
    # published files recursively, not a shallow glob of the "blobs" directory itself.
    blob_dir = salvage._salvage_dir(run) / "blobs"
    n_blobs_first = sum(1 for p in blob_dir.rglob("*") if p.is_file())

    second = salvage.salvage_run(run, sessions_root=sessions_root)
    assert second["wrote"] is False
    n_blobs_second = sum(1 for p in blob_dir.rglob("*") if p.is_file())
    assert n_blobs_second == n_blobs_first

    row1 = next(r for r in first["files"] if r["file_kind"] == "e6_decision")
    row2 = next(r for r in second["files"] if r["file_kind"] == "e6_decision")
    assert row1["attribution"] == row2["attribution"] == "VERIFIED_RUN"

    # Now the shared file changes (simulating a later overwrite by something else) --
    # the already-VERIFIED_RUN record must stick, never regress.
    shared_path.write_text('{"mode": "different"}', encoding="utf-8")
    third = salvage.salvage_run(run, sessions_root=sessions_root)
    row3 = next(r for r in third["files"] if r["file_kind"] == "e6_decision")
    assert row3["attribution"] == "VERIFIED_RUN"
    assert row3["source_sha256"] == row1["source_sha256"]  # unchanged, sticky


# --------------------------------------------------------------- exit code


def test_cli_exit_code_zero_when_everything_is_legitimately_absent(tmp_path, monkeypatch, capsys):
    _redirect(monkeypatch, tmp_path)
    _write_run(tmp_path, "20260817_2000")
    rc = salvage.main(["20260817_2000"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert all(row["attribution"] == "ABSENT" for row in out["files"]
               if row["file_kind"] in ("e6_decision", "market_view"))


def test_cli_exit_code_nonzero_on_unhandled_error(tmp_path, monkeypatch, capsys):
    _redirect(monkeypatch, tmp_path)
    other_engine_run = tmp_path / "reports_codex" / "scan" / "20260817_2000"
    other_engine_run.mkdir(parents=True)
    (other_engine_run / "manifest.json").write_text(
        json.dumps({"analysis_date": ANALYSIS_DATE}), encoding="utf-8"
    )
    rc = salvage.main([str(other_engine_run)])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_cli_single_run_exit_code_nonzero_on_a_genuine_per_file_error(tmp_path, monkeypatch, capsys):
    """FINDING 2: the per-file `errors` path (as opposed to the upfront wrong-engine
    raise, or --all's whole-run run_errors) must itself drive a non-zero single-run
    exit -- and a *different*, genuinely absent file in the same run must still read
    as a clean ABSENT, not get swept into "something went wrong" by association. An
    unreadable (chmod 000) shared file is a real, not simulated, per-file failure:
    `_flat_file_row`'s `shared_path.read_bytes()` raises `PermissionError`, caught by
    `salvage_run`'s own per-file try/except and recorded into `errors`."""
    import os

    _redirect(monkeypatch, tmp_path)
    _write_run(tmp_path, "20260817_2000")
    shared_path = _touch_shared(
        tmp_path, "claude", ANALYSIS_DATE, DECISION_FILENAME, '{"mode": "active"}'
    )
    os.chmod(shared_path, 0o000)
    try:
        rc = salvage.main(["20260817_2000"])
        payload = json.loads(capsys.readouterr().out)
    finally:
        os.chmod(shared_path, 0o644)  # restore before tmp_path teardown tries to clean up

    assert rc == 1
    assert payload["errors"], "expected a recorded per-file error, found none"
    assert {e["file_kind"] for e in payload["errors"]} == {"e6_decision"}
    # market_view was never touched -- must still read as a clean, non-error ABSENT,
    # never conflated with the unrelated e6_decision failure.
    market_row = next(r for r in payload["files"] if r["file_kind"] == "market_view")
    assert market_row["attribution"] == "ABSENT"


def test_all_exit_code_reflects_run_errors_not_absent_files(tmp_path, monkeypatch, capsys):
    _redirect(monkeypatch, tmp_path)
    _write_run(tmp_path, "20260817_2000")  # everything legitimately absent
    rc = salvage.main(["--all"])
    assert rc == 0


# --------------------------------------------------------------- frozen-run
# invariance: salvage must never touch the run's own file set / hashes / MANIFEST


def test_salvage_never_mutates_the_frozen_run(tmp_path, monkeypatch):
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000", session_ref="sess-frozen")
    _touch_shared(tmp_path, "claude", ANALYSIS_DATE, DECISION_FILENAME, '{"mode": "active"}')
    _touch_shared(tmp_path, "claude", ANALYSIS_DATE, MARKET_VIEW, "# 市场研判\n")
    write_manifest(run)
    before = verify_manifest(run)
    assert before["ok"] is True

    salvage.salvage_run(run, sessions_root=tmp_path / "no-sessions")
    salvage.salvage_run(run, sessions_root=tmp_path / "no-sessions")  # twice, for good measure

    after = verify_manifest(run)
    assert after == before
    assert after["ok"] is True
    # nothing was written under the run directory itself -- only under ledger/salvage/
    assert not (run / "salvage").exists()


# --------------------------------------------------------------- end-to-end proof:
# delete the synthetic source session, then re-derive the same observations from
# the salvaged archive alone


def test_same_observations_survive_deleting_the_original_session(tmp_path, monkeypatch):
    import gzip

    from autoresearch.trace.transcripts.base import TranscriptRef
    from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter

    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000", session_ref="sess-e2e")
    target = Path("/scan-target") / DECISION_FILENAME
    content = '{"mode": "active", "buys": []}'
    sessions_root = tmp_path / "fake-claude-projects"
    session_path = _write_claude_session(
        sessions_root, "proj", "sess-e2e",
        [
            _claude_transcript_write_row("t1", target, ts="2026-08-17T20:00:00Z", msg_id="m1",
                                          content=content),
            _claude_transcript_result_row("t1", ts="2026-08-17T20:00:01Z"),
        ],
    )

    before = salvage.salvage_run(run, sessions_root=sessions_root)
    transcript_row = next(r for r in before["files"] if r["file_kind"] == "transcript")
    assert transcript_row["attribution"] == "VERIFIED_RUN"
    blob_path = salvage._blob_path(run, transcript_row["blob"]["sha256"])
    assert blob_path.is_file()

    # Independently derive "what happened" from the *original* session, before deletion.
    adapter = ClaudeTranscriptAdapter(projects_root=sessions_root)
    ref = TranscriptRef(engine="claude", path=session_path, role="main", session_ref="sess-e2e")
    original_stats = adapter.stats_from_rows(
        [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()], ref
    )
    original_kinds = sorted(op.kind for op in original_stats.operations)
    assert "WRITE_SUCCEEDED" in original_kinds

    # Delete the synthetic source session entirely.
    session_path.unlink()
    assert not session_path.exists()

    # Re-derive the *same* observations purely from the salvaged, redacted archive --
    # never touching the (now-deleted) original path again.
    archive_rows = [
        json.loads(line)
        for line in gzip.decompress(blob_path.read_bytes()).decode("utf-8").splitlines()
        if line.strip()
    ]
    replay_ref = TranscriptRef(engine="claude", path=session_path, role="main",
                                session_ref="sess-e2e")
    replayed_stats = adapter.stats_from_rows(archive_rows, replay_ref)
    replayed_kinds = sorted(op.kind for op in replayed_stats.operations)
    assert replayed_kinds == original_kinds

    # A second salvage_run call after deletion must not crash and must not lose the
    # already-VERIFIED_RUN record (sticky merge -- the harness-managed source can
    # rotate away, but the archived evidence is what's supposed to survive it). A
    # fresh "not found this time" row may legitimately appear alongside it under a
    # different logical_name (an honest, distinct fact) -- look up the *original*
    # transcript's own logical_name specifically, not just any "transcript" row.
    after = salvage.salvage_run(run, sessions_root=sessions_root)
    after_transcript_rows = {
        r["logical_name"]: r for r in after["files"] if r["file_kind"] == "transcript"
    }
    transcript_row_after = after_transcript_rows[transcript_row["logical_name"]]
    assert transcript_row_after["attribution"] == "VERIFIED_RUN"
    assert transcript_row_after["blob"]["sha256"] == transcript_row["blob"]["sha256"]


# --------------------------------------------------------------- registration


def test_salvage_blob_artifact_is_registered():
    art = ca.by_name("salvage_blob")
    assert art.root == "ledger"
    assert art.kind == "dir"
    assert art.presence == "conditional"
    # fix-round-1 finding 1: the registered glob must match trace.blobs' own two-level
    # fan-out (blobs/sha256/<xx>/<digest>), not a flat blobs/<digest> layout -- the
    # registration must track the real store this module reuses, never invent its own.
    assert art.path == "salvage/*/blobs/sha256/*/*"


def test_store_blob_reuses_trace_blobs_layout_and_verifies_collisions(tmp_path, monkeypatch):
    """fix-round-1 finding 1 (Critical): `_store_blob`/`_blob_path` must delegate to the
    existing `trace.blobs` store, not a hand-rolled parallel one -- proven by checking
    the actual on-disk layout `trace.blobs.blob_path` itself would produce, and by
    confirming a corrupted pre-existing file at a digest's path is caught rather than
    silently trusted (the exact safety property the review found missing)."""
    from autoresearch.trace import blobs as trace_blobs

    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    payload = b'{"mode": "active"}'
    record = salvage._store_blob(run, payload)
    root = salvage._salvage_dir(run)
    expected_path = trace_blobs.blob_path(root, record["sha256"])
    assert (root / record["path"]) == expected_path
    assert expected_path.is_file()
    assert expected_path.read_bytes() == payload
    assert record["path"] == f"blobs/sha256/{record['sha256'][:2]}/{record['sha256']}"

    # Corrupt the already-published blob in place, then try to "store" the same
    # original bytes again -- trace.blobs' own _verify_existing must detect the
    # digest/content mismatch rather than silently treating the corrupt file as valid.
    expected_path.write_bytes(b"corrupted")
    with pytest.raises(RuntimeError):
        salvage._store_blob(run, payload)


# ------------------------------------------------------------- fix round 2:
# read compatibility for blobs recorded under the old flat layout
#
# Fix round 1 switched _store_blob's layout from a flat blobs/<digest> to
# trace.blobs' own blobs/sha256/<xx>/<digest>, but _merge_rows' sticky rule means a
# VERIFIED_RUN row written *before* that switch keeps its old blob["path"] forever --
# it is never recomputed. Recomputing a blob's location from its digest (rather than
# reading the recorded blob["path"]) therefore silently orphans every already-verified
# row written under the old layout: the file is still genuinely on disk, just not
# where the recompute rule looks. A repo-wide scan (the round-2 review) found 723 such
# rows across 45 real runs.


def _write_provenance_row(run, *, filename, file_kind, blob_path_str, payload,
                           attribution="VERIFIED_RUN"):
    """Build one provenance.json with a single row whose `blob["path"]` is exactly
    *blob_path_str* (old flat form or new nested form, caller's choice), and place
    *payload* bytes at that literal location under the salvage dir -- never through
    `_store_blob` (which only ever writes the *current* layout) -- so this genuinely
    exercises "a row written under either layout", not a fixture the current code
    would itself produce."""
    import hashlib

    digest = hashlib.sha256(payload).hexdigest()
    salvage_dir = salvage._salvage_dir(run)
    blob_file = salvage_dir / blob_path_str
    blob_file.parent.mkdir(parents=True, exist_ok=True)
    blob_file.write_bytes(payload)
    doc = {
        "schema_version": salvage.PROVENANCE_SCHEMA_VERSION,
        "report_run_id": run.name, "contract_run_id": "x", "engine": "claude",
        "analysis_date": ANALYSIS_DATE, "run_window": {"start": None, "end": None},
        "same_date_multi_run": False, "sibling_report_run_ids": [],
        "captured_at": "2026-08-17T20:00:00+00:00",
        "files": [{
            "file_kind": file_kind, "logical_name": filename, "attribution": attribution,
            "reason": "test fixture", "source": None, "source_sha256": digest,
            "source_bytes": len(payload), "source_mtime": None, "captured_at": "x",
            "same_date_multi_run": False, "sibling_report_run_ids": [], "overwritten_by": None,
            "blob": {"sha256": digest, "bytes": len(payload), "path": blob_path_str},
        }],
    }
    from autoresearch.common.atomic import atomic_write_json

    atomic_write_json(salvage._provenance_path(run), doc)
    return digest, blob_file


def test_resolve_blob_path_reads_old_flat_layout_by_its_recorded_path(tmp_path, monkeypatch):
    """The failing case the round-2 review found: a row whose blob["path"] is the old
    flat form (`blobs/<digest>`, pre-fix-round-1) must still resolve -- by *reading*
    that recorded path, never by recomputing a location from the digest alone (which
    would land on the *new* nested layout, where nothing exists for an old row)."""
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    payload = b"old-layout-payload"
    old_rel = "blobs/deadbeef-not-a-real-digest-but-a-realistic-old-style-relpath"
    salvage_dir = salvage._salvage_dir(run)
    blob_file = salvage_dir / old_rel
    blob_file.parent.mkdir(parents=True, exist_ok=True)
    blob_file.write_bytes(payload)
    import hashlib

    blob = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload), "path": old_rel}

    resolved = salvage.resolve_blob_path(run, blob)
    assert resolved == blob_file
    assert resolved.read_bytes() == payload


def test_resolve_blob_path_reads_new_nested_layout_by_its_recorded_path(tmp_path, monkeypatch):
    """Symmetric case: a row written under the *current* layout (via the real
    _store_blob) must remain resolvable through the exact same code path."""
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    payload = b"new-layout-payload"
    blob = salvage._store_blob(run, payload)

    resolved = salvage.resolve_blob_path(run, blob)
    assert resolved == salvage._salvage_dir(run) / blob["path"]
    assert resolved.read_bytes() == payload
    assert "sha256/" in blob["path"]  # genuinely the new fan-out, not a coincidence


def test_resolve_attributed_source_finds_a_verified_row_under_the_old_layout(tmp_path, monkeypatch):
    """Consumer-adjacent integration test: `resolve_attributed_source` (chain_view's
    own read point) must still find an old-layout VERIFIED_RUN row's blob -- proving
    the fix at the level the review's real-run check exercised, not just the pure
    resolver."""
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    payload = b'{"mode": "active", "old": "layout"}'
    digest, blob_file = _write_provenance_row(
        run, filename=MARKET_VIEW, file_kind="market_view",
        blob_path_str=f"blobs/{'a' * 64}", payload=payload,
    )
    resolved = salvage.resolve_attributed_source(run, MARKET_VIEW)
    assert resolved == blob_file
    assert resolved.read_bytes() == payload


def test_h02_chain_view_promotes_an_old_layout_verified_blob_too(tmp_path, monkeypatch):
    """Consumer-level proof (the review explicitly asked for at least one): chain_view's
    own promoted tier (Sources.find, ruling 5) must still promote a VERIFIED_RUN row
    whose blob sits at the pre-fix-round-1 flat-layout path -- this is the exact
    scenario the repo-wide scan found silently losing evidence in real runs."""
    _redirect(monkeypatch, tmp_path)
    run = _write_run(tmp_path, "20260817_2000")
    old_layout_content = '{"mode": "active", "buys": ["600000"], "old_layout": true}'
    _write_provenance_row(
        run, filename=DECISION_FILENAME, file_kind="e6_decision",
        blob_path_str=f"blobs/{'b' * 64}", payload=old_layout_content.encode("utf-8"),
    )
    _touch_shared(
        tmp_path, "claude", ANALYSIS_DATE, DECISION_FILENAME,
        "# 共享的、不该被读到的当前内容\n",
    )
    src = chain_view.Sources(run)
    found = src.find(DECISION_FILENAME)
    assert found is not None
    assert found.read_text(encoding="utf-8") == old_layout_content
    assert src.used_shared is False  # promoted from the old-layout blob, not shared
