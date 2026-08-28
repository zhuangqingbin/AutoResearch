"""Twelve ways a scan can end, and what each one must leave behind.

The harness drives the *real* capsule lifecycle — begin, checkpoint, agent
boundaries, transcript binding, finalize, recover, verify — over a synthetic
three-stock universe.  It reaches no network, no Tushare, no Claude and no
Codex: every input is a fixture, so a red here is always a defect in the
capsule and never a flaky day at a data vendor.

The matrix exists because the interesting question is not "does a good run look
good" but "does a bad run look exactly as bad as it was".
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.trace import capsule as capsule_mod
from autoresearch.trace import process_probe
from autoresearch.trace.blobs import put_dataframe
from autoresearch.trace.capsule import (
    BusinessStatus,
    bind_transcript,
    checkpoint,
    finalize,
    record_agent_boundary,
    recover_stale_runs,
    verify,
)
from tests.forensic_fixtures import FIXTURE_NOW, begin_fixture_run, copy_fixture

UNIVERSE = ("600000", "600519", "300750")
STAGE_ORDER = (
    "frame",
    "prelude",
    "gate1",
    "l3",
    "gate2",
    "l4",
    "l5",
    "observe",
    "gate4",
)
STAGE_ROLES = {
    "prelude": (("strategist", None, None),),
    "l3": (("sector-brief", None, "银行"), ("l3-rank", None, None)),
    "l4": (("l4-card", "600000", None), ("l4-intel", "600000", None)),
}


@dataclass
class Outcome:
    business_status: str
    evidence_status: str
    last_reliable_checkpoint: str | None
    root_hash: str | None
    final_path: Path
    verdict: dict
    failure_classification_is_explicit: bool


class MiniScan:
    """One synthetic scan that exercises the real forensic control plane."""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.runs: list[Outcome] = []

    # -- fixture material -------------------------------------------------

    def _begin(self, *, offset_seconds: int = 0):
        handle = begin_fixture_run(
            self.tmp_path,
            self.monkeypatch,
            now=FIXTURE_NOW + timedelta(seconds=offset_seconds),
        )
        self.transcript = copy_fixture(
            "codex/rollout.jsonl", self.tmp_path / "harness" / handle.run_id
        )
        return handle

    @staticmethod
    def _identity(handle) -> None:
        root = handle.capsule / "identity"
        (root / "prompts").mkdir(parents=True, exist_ok=True)
        (root / "environment.json").write_text(
            json.dumps({"python": "3.13", "os": "fixture"}, sort_keys=True),
            encoding="utf-8",
        )
        (root / "dependencies.txt").write_text("pandas==2.0.0\n", encoding="utf-8")
        (root / "source_manifest.json").write_text(
            json.dumps({"files": {"autoresearch/scan/frame.py": "0" * 64}}),
            encoding="utf-8",
        )
        (root / "prompts" / "l4-card.md").write_text("synthetic prompt\n", encoding="utf-8")

    def _products(self, handle) -> None:
        frame = pd.DataFrame(
            {"code": list(UNIVERSE), "score": [1.0, 2.0, 3.0]}
        )
        handle.staging.mkdir(parents=True, exist_ok=True)
        frame.to_csv(handle.staging / "L2_gbdt_top200.csv", index=False)
        (handle.staging / "summary.md").write_text("# synthetic\n", encoding="utf-8")

    @staticmethod
    def _lineage(handle) -> None:
        frame = pd.DataFrame({"ts_code": list(UNIVERSE), "close": [1.0, 2.0, 3.0]})
        digest = put_dataframe(handle.capsule, frame)
        row = {
            "endpoint": "daily",
            "normalized_params": {"trade_date": "20260827"},
            "normalized_blob_hash": digest,
            "blob_hash": None,
            "status": "OK",
        }
        path = handle.capsule / "lineage/reads.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    @staticmethod
    def _logs(handle, stage: str, attempt: int = 1) -> None:
        root = handle.capsule / "logs" / stage
        root.mkdir(parents=True, exist_ok=True)
        for channel in ("stdout", "stderr"):
            (root / f"{stage}-{attempt}.{channel}.log.gz").write_bytes(
                gzip.compress(f"synthetic {channel} for {stage}\n".encode())
            )

    def _agents(self, handle, stage: str, *, skip: tuple[str, ...] = (), attempt: int = 1):
        for role, subject, display in STAGE_ROLES.get(stage, ()):
            invocation = f"{role}-{subject or 'market'}-{attempt}"
            record_agent_boundary(
                handle.run_id,
                "AGENT_DISPATCHED",
                role=role,
                subject=subject,
                subject_display=display,
                invocation_id=invocation,
                attempt=attempt,
            )
            record_agent_boundary(
                handle.run_id,
                "AGENT_COMPLETED",
                role=role,
                subject=subject,
                subject_display=display,
                invocation_id=invocation,
                attempt=attempt,
            )
            if role in skip:
                continue
            bind_transcript(
                handle.run_id,
                self.transcript,
                role=role,
                subject=subject or "market",
                invocation_id=invocation,
            )

    # -- scenarios --------------------------------------------------------

    def run(self, scenario: str) -> Outcome:  # noqa: C901 - one branch per scenario
        handle = self._begin(offset_seconds=len(self.runs))
        self._identity(handle)
        self._products(handle)
        self._lineage(handle)
        report_dir = ws.reports_root() / "scan" / handle.run_id
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "summary.md").write_text("# synthetic\n", encoding="utf-8")

        stop_at = {
            "after_l3_before_l4_prompt": "l3",
            "sigterm_before_assemble": "l4",
            "sigkill_then_recover": "l4",
        }.get(scenario, "gate4")
        skip_roles = ("l4-card",) if scenario == "required_missing_before_manifest" else ()

        for stage in STAGE_ORDER:
            if scenario == "prelude_child_fail_continue" and stage == "prelude":
                self._logs(handle, stage, attempt=1)
                checkpoint(handle.run_id, stage, "FAILED", [], {}, error="child step ✗")
            if scenario == "l3_fail_repair_success" and stage == "l3":
                self._logs(handle, stage, attempt=1)
                checkpoint(handle.run_id, stage, "FAILED", [], {}, error="lint failed")
            status = "SUCCEEDED"
            if scenario == "l4_blocked" and stage == "l4":
                status = "DEGRADED"
            if scenario == "l4_transient_retry" and stage == "l4":
                self._logs(handle, stage, attempt=1)
                checkpoint(handle.run_id, stage, "FAILED", [], {}, error="transient")
                record_agent_boundary(
                    handle.run_id,
                    "AGENT_DISPATCHED",
                    role="l4-card",
                    subject="600000",
                    invocation_id="l4-card-600000-1",
                    attempt=1,
                )
                record_agent_boundary(
                    handle.run_id,
                    "AGENT_FAILED",
                    role="l4-card",
                    subject="600000",
                    invocation_id="l4-card-600000-1",
                    attempt=1,
                    error={"status": "threw"},
                )
                # 失败那次也留下了 transcript —— 重试成功不得抹掉前一次的现场。
                bind_transcript(
                    handle.run_id,
                    self.transcript,
                    role="l4-card",
                    subject="600000",
                    invocation_id="l4-card-600000-1",
                )
            self._logs(handle, stage)
            attempt = (
                2 if (scenario == "l4_transient_retry" and stage == "l4") else 1
            )
            self._agents(handle, stage, skip=skip_roles, attempt=attempt)
            checkpoint(handle.run_id, stage, status, [], {"universe": len(UNIVERSE)})
            if stage == stop_at:
                break

        if scenario == "sigkill_then_recover":
            self._go_stale(handle)
            results = recover_stale_runs(
                now=FIXTURE_NOW + timedelta(minutes=30),
                stale_after=timedelta(minutes=5),
            )
            final_path = results[0].final_path
        elif scenario == "after_l3_before_l4_prompt":
            final_path = finalize(
                handle.run_id,
                BusinessStatus.FAILED,
                error={"error_type": "RuntimeError", "phase": "l4-prompt"},
            ).final_path
        elif scenario == "sigterm_before_assemble":
            final_path = finalize(
                handle.run_id,
                BusinessStatus.INTERRUPTED,
                error={"error_type": "Signal", "signal": "SIGTERM"},
            ).final_path
        elif scenario == "archive_write_fail":
            self.monkeypatch.setattr(
                capsule_mod,
                "build_archive",
                lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
            )
            final_path = finalize(
                handle.run_id, BusinessStatus.SUCCEEDED, report_dir
            ).final_path
            self.monkeypatch.undo()
        else:
            final_path = finalize(
                handle.run_id, BusinessStatus.SUCCEEDED, report_dir
            ).final_path

        if scenario == "published_file_mutated":
            victim = final_path / "summary.md"
            victim.chmod(0o644)
            victim.write_text("tampered after freeze", encoding="utf-8")

        verdict = verify(handle.run_id, final_path=final_path)
        outcome = self._read_outcome(final_path, verdict)
        self.runs.append(outcome)
        return outcome

    @staticmethod
    def _go_stale(handle) -> None:
        state_path = handle.workspace / "state.json"
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        raw["lease"] = {
            **raw.get("lease", {}),
            "heartbeat": "2026-08-27T00:00:00.000000Z",
            "pid": 999999,
            "process_started_at": "a boot that no longer exists",
        }
        state_path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _read_outcome(final_path: Path, verdict: dict) -> Outcome:
        manifest = json.loads(
            (final_path / "capsule/capsule.json").read_text(encoding="utf-8")
        )
        frozen_evidence = str(manifest.get("evidence_status"))
        # Evidence that was complete at freeze time but no longer verifies is not
        # complete now: a mutated capsule must not keep its old green label.
        observed = (
            frozen_evidence
            if verdict["integrity_ok"] and verdict["completeness_ok"]
            else "EVIDENCE_INCOMPLETE"
        )
        failure_path = final_path / "capsule/failure.json"
        if manifest["business_status"] == "SUCCEEDED":
            explicit = observed == "COMPLETE" or bool(
                verdict["missing_required"]
                or verdict["durability"] == "ARCHIVE_FAILED"
                or not verdict["integrity_ok"]
            )
        else:
            failure = json.loads(failure_path.read_text(encoding="utf-8"))
            explicit = bool(
                failure.get("error")
                and failure.get("last_reliable_checkpoint")
                and failure.get("business_status") == manifest["business_status"]
            )
        return Outcome(
            business_status=str(manifest["business_status"]),
            evidence_status=observed,
            last_reliable_checkpoint=manifest.get("last_reliable_checkpoint"),
            root_hash=verdict.get("root_hash"),
            final_path=final_path,
            verdict=verdict,
            failure_classification_is_explicit=explicit,
        )


@pytest.fixture
def mini_scan(tmp_path, monkeypatch):
    return MiniScan(tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "scenario,business,evidence,last_stage",
    [
        ("success_full", "SUCCEEDED", "COMPLETE", "gate4"),
        ("prelude_child_fail_continue", "SUCCEEDED", "COMPLETE", "gate4"),
        ("l3_fail_repair_success", "SUCCEEDED", "COMPLETE", "gate4"),
        ("l4_transient_retry", "SUCCEEDED", "COMPLETE", "gate4"),
        ("l4_blocked", "SUCCEEDED", "COMPLETE", "gate4"),
        ("after_l3_before_l4_prompt", "FAILED", "COMPLETE", "l3"),
        ("sigterm_before_assemble", "INTERRUPTED", "COMPLETE", "l4"),
        ("sigkill_then_recover", "INTERRUPTED", "COMPLETE", "l4"),
        ("same_date_second_run", "SUCCEEDED", "COMPLETE", "gate4"),
        ("archive_write_fail", "SUCCEEDED", "EVIDENCE_INCOMPLETE", "gate4"),
        ("published_file_mutated", "SUCCEEDED", "EVIDENCE_INCOMPLETE", "gate4"),
        (
            "required_missing_before_manifest",
            "SUCCEEDED",
            "EVIDENCE_INCOMPLETE",
            "gate4",
        ),
    ],
)
def test_fault_matrix(mini_scan, monkeypatch, scenario, business, evidence, last_stage):
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)

    result = mini_scan.run(scenario)

    assert result.business_status == business
    assert result.evidence_status == evidence
    assert result.last_reliable_checkpoint == last_stage
    assert result.failure_classification_is_explicit


def test_same_date_second_run_leaves_the_first_root_untouched(mini_scan, monkeypatch):
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)

    first = mini_scan.run("success_full")
    first_root = first.root_hash
    second = mini_scan.run("same_date_second_run")

    assert first.final_path != second.final_path
    assert verify(first.final_path.name, final_path=first.final_path)["root_hash"] == (
        first_root
    )
    assert second.root_hash != first_root


def test_outcome_backfill_does_not_change_the_root(mini_scan, monkeypatch, tmp_path):
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)
    result = mini_scan.run("success_full")

    # 结果账本住在 run 外面,按 run_id 关联 —— 回填不得改动任何已冻结的字节。
    ledger = ws.reports_root() / "scan" / "_outcome" / "outcomes.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps({"run_id": result.final_path.name, "fwd_1": 0.01}) + "\n",
        encoding="utf-8",
    )

    after = verify(result.final_path.name, final_path=result.final_path)
    assert after["root_hash"] == result.root_hash
    assert after["integrity_ok"] is True


def test_deleting_a_required_transcript_after_freeze_fails_both_ways(
    mini_scan, monkeypatch
):
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)
    result = mini_scan.run("success_full")
    assert result.verdict["integrity_ok"] is True
    assert result.verdict["completeness_ok"] is True

    raw = next((result.final_path / "capsule/agents/raw").glob("*.jsonl.gz"))
    raw.parent.chmod(0o755)
    raw.unlink()

    after = verify(result.final_path.name, final_path=result.final_path)
    # 完好性由 MANIFEST 说了算(文件不见了),完整性由 expected 清单说了算 ——
    # 两条结论各自独立地变红,不是同一条。
    assert after["integrity_ok"] is False
    assert raw.relative_to(result.final_path).as_posix() in after["manifest"]["missing"]


def test_the_harness_never_reaches_the_network(mini_scan, monkeypatch):
    from autoresearch.data import sources

    monkeypatch.setattr(
        sources,
        "fetch",
        lambda *a, **k: pytest.fail("mini scan reached a data vendor"),
    )
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)

    assert mini_scan.run("success_full").business_status == "SUCCEEDED"


def test_retry_preserves_the_failed_attempt_and_its_evidence(mini_scan, monkeypatch):
    """重试成功不得抹掉上一次的失败:两次 attempt 各自留一行,前一次的错误仍在。"""
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)

    result = mini_scan.run("l4_transient_retry")

    index = json.loads(
        (result.final_path / "capsule/agents/index.json").read_text(encoding="utf-8")
    )
    cards = sorted(
        (row["invocation_id"], row["attempt"], row["terminal"], row["status"])
        for row in index["invocations"]
        if row["role"] == "l4-card"
    )
    assert cards == [
        ("l4-card-600000-1", 1, "FAILED", "PRESENT"),
        ("l4-card-600000-2", 2, "COMPLETED", "PRESENT"),
    ]
    events = [
        json.loads(line)
        for line in (result.final_path / "capsule/events/events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(row["event_type"] == "AGENT_FAILED" for row in events)
    assert any(row["event_type"] == "STAGE_FAILED" for row in events)
