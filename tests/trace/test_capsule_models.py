"""Immutable lifecycle values keep business and evidence truth separate."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from autoresearch.trace.atomic import canonical_json
from autoresearch.trace.capsule_models import (
    BusinessStatus,
    Checkpoint,
    EvidenceStatus,
    FinalizationResult,
    Replayability,
    RunHandle,
    RunState,
)

RUN_ID = "20260827T010203456789Z"


def test_lifecycle_models_are_immutable_dataclasses():
    assert all(
        model.__dataclass_params__.frozen
        for model in (RunState, RunHandle, Checkpoint, FinalizationResult)
    )


def test_status_enums_expose_the_stable_wire_values():
    assert [status.value for status in BusinessStatus] == [
        "ACTIVE",
        "SUCCEEDED",
        "FAILED",
        "INTERRUPTED",
    ]
    assert [status.value for status in EvidenceStatus] == [
        "PENDING",
        "COMPLETE",
        "EVIDENCE_INCOMPLETE",
        "LEGACY_PARTIAL",
    ]
    assert [status.value for status in Replayability] == [
        "FULL",
        "PARTIAL",
        "NONE",
        "EVIDENCE_ONLY",
    ]


def test_business_and_evidence_states_are_orthogonal():
    state = RunState.build(
        run_id=RUN_ID,
        business_status=BusinessStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.EVIDENCE_INCOMPLETE,
    )

    assert state.business_status == "SUCCEEDED"
    assert state.evidence_status == "EVIDENCE_INCOMPLETE"


def test_run_state_normalizes_timestamps_to_utc():
    local = datetime(
        2026, 8, 27, 9, 2, 3, 456789, tzinfo=timezone(timedelta(hours=8))
    )
    state = RunState.build(run_id=RUN_ID, now=local)

    assert state.created_at == "2026-08-27T01:02:03.456789Z"
    assert state.updated_at == state.created_at


def test_active_business_state_can_transition_to_terminal_state():
    active = RunState.build(
        run_id=RUN_ID,
        now=datetime(2026, 8, 27, 1, tzinfo=timezone.utc),
    )
    succeeded = RunState.build(
        run_id=RUN_ID,
        business_status=BusinessStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.EVIDENCE_INCOMPLETE,
        previous=active,
        now=datetime(2026, 8, 27, 2, tzinfo=timezone.utc),
    )

    assert succeeded.business_status == "SUCCEEDED"
    assert succeeded.created_at == active.created_at
    assert succeeded.updated_at == "2026-08-27T02:00:00.000000Z"


def test_semantic_noop_returns_previous_without_refreshing_timestamp():
    previous = RunState.build(
        run_id=RUN_ID,
        now=datetime(2026, 8, 27, 1, tzinfo=timezone.utc),
    )

    repeated = RunState.build(
        run_id=RUN_ID,
        previous=previous,
        now=datetime(2026, 8, 27, 2, tzinfo=timezone.utc),
    )

    assert repeated is previous
    assert repeated.updated_at == "2026-08-27T01:00:00.000000Z"


def test_transition_rejects_time_before_previous_update():
    previous = RunState.build(
        run_id=RUN_ID,
        evidence_status=EvidenceStatus.EVIDENCE_INCOMPLETE,
        now=datetime(2026, 8, 27, 2, tzinfo=timezone.utc),
    )

    with pytest.raises(ValueError, match="before previous.updated_at"):
        RunState.build(
            run_id=RUN_ID,
            evidence_status=EvidenceStatus.COMPLETE,
            previous=previous,
            now=datetime(2026, 8, 27, 1, tzinfo=timezone.utc),
        )


def test_complete_evidence_cannot_regress_to_pending():
    complete = RunState.build(
        run_id=RUN_ID,
        business_status=BusinessStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.COMPLETE,
    )

    with pytest.raises(ValueError, match="illegal evidence transition"):
        RunState.build(
            run_id=RUN_ID,
            business_status=BusinessStatus.SUCCEEDED,
            evidence_status=EvidenceStatus.PENDING,
            previous=complete,
        )


def test_active_evidence_cannot_be_reclassified_as_legacy():
    pending = RunState.build(run_id=RUN_ID)

    with pytest.raises(ValueError, match="illegal evidence transition"):
        RunState.build(
            run_id=RUN_ID,
            evidence_status=EvidenceStatus.LEGACY_PARTIAL,
            previous=pending,
        )


def test_terminal_business_state_permits_incomplete_evidence_repair():
    incomplete = RunState.build(
        run_id=RUN_ID,
        business_status=BusinessStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.EVIDENCE_INCOMPLETE,
    )

    repaired = RunState.build(
        run_id=RUN_ID,
        business_status=BusinessStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.COMPLETE,
        previous=incomplete,
    )

    assert repaired.business_status == "SUCCEEDED"
    assert repaired.evidence_status == "COMPLETE"


def test_complete_evidence_can_be_marked_incomplete_after_tamper_detection():
    complete = RunState.build(
        run_id=RUN_ID,
        business_status=BusinessStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.COMPLETE,
    )

    invalidated = RunState.build(
        run_id=RUN_ID,
        business_status=BusinessStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.EVIDENCE_INCOMPLETE,
        previous=complete,
    )

    assert invalidated.evidence_status == "EVIDENCE_INCOMPLETE"


@pytest.mark.parametrize(
    ("current", "requested"),
    [
        (BusinessStatus.SUCCEEDED, BusinessStatus.ACTIVE),
        (BusinessStatus.SUCCEEDED, BusinessStatus.FAILED),
        (BusinessStatus.FAILED, BusinessStatus.INTERRUPTED),
        (BusinessStatus.INTERRUPTED, BusinessStatus.SUCCEEDED),
    ],
)
def test_terminal_business_state_rejects_illegal_transition(current, requested):
    previous = RunState.build(run_id=RUN_ID, business_status=current)

    with pytest.raises(ValueError, match="illegal business transition"):
        RunState.build(run_id=RUN_ID, business_status=requested, previous=previous)


def test_transition_rejects_a_different_run_identity():
    previous = RunState.build(run_id=RUN_ID)
    with pytest.raises(ValueError, match="run_id"):
        RunState.build(run_id="20260827T010203456790Z", previous=previous)


def test_run_state_instance_rejects_mutation():
    state = RunState.build(run_id=RUN_ID)
    with pytest.raises(FrozenInstanceError):
        state.run_id = "other"


def test_checkpoint_metrics_are_deeply_immutable_and_json_serializable(tmp_path):
    source = {"counts": {"rows": [1, 2]}}
    checkpoint = Checkpoint(
        run_id=RUN_ID,
        stage="l3",
        attempt=1,
        status="SUCCEEDED",
        path=tmp_path / "result.json",
        created_at="2026-08-27T01:02:03.000000Z",
        metrics=source,
    )
    source["counts"]["rows"].append(3)

    assert checkpoint.metrics["counts"]["rows"] == (1, 2)
    with pytest.raises(TypeError):
        checkpoint.metrics["new"] = 1
    with pytest.raises(TypeError):
        checkpoint.metrics["counts"]["rows"] += (3,)
    assert canonical_json(checkpoint.metrics) == '{"counts":{"rows":[1,2]}}'
    assert checkpoint.to_dict()["metrics"] == {"counts": {"rows": [1, 2]}}


@pytest.mark.parametrize("metrics", ([], (), [1, 2]))
def test_checkpoint_rejects_non_mapping_metrics_root(tmp_path, metrics):
    with pytest.raises(TypeError, match="metrics.*mapping"):
        Checkpoint(
            run_id=RUN_ID,
            stage="l3",
            attempt=1,
            status="SUCCEEDED",
            path=tmp_path / "result.json",
            created_at="2026-08-27T01:02:03.000000Z",
            metrics=metrics,
        )


def test_checkpoint_defensively_freezes_artifacts(tmp_path):
    artifacts = ["_l3_judged.json"]
    checkpoint = Checkpoint(
        run_id=RUN_ID,
        stage="l3",
        attempt=1,
        status="SUCCEEDED",
        path=tmp_path / "result.json",
        created_at="2026-08-27T01:02:03.000000Z",
        artifacts=artifacts,
    )
    artifacts.append("late.json")

    assert checkpoint.artifacts == ("_l3_judged.json",)
