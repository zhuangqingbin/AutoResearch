"""E2:推理信封 —— 九字段全 required,少一项迟到的回报就能冒充当前的。"""
import pytest

from autoresearch.contracts.inference_task import FIELDS, validate_envelope


def envelope(**changes):
    row = {"schema_version": 1, "engine": "claude", "run_id": "20260901T000000000001Z",
           "task_id": "600000", "role": "l4-card", "input_artifact_ids": ["prompt", "slim"],
           "input_contract_hash": "a" * 64, "expected_output_contract": "L4_CARD.v1", "attempt": 1}
    return dict(row, **changes)


def test_valid_envelope_passes():
    assert validate_envelope(envelope()) == envelope()


@pytest.mark.parametrize("field", sorted(FIELDS))
def test_every_missing_field_rejected(field):
    row = envelope()
    del row[field]
    with pytest.raises(ValueError):
        validate_envelope(row)


@pytest.mark.parametrize("changes", [
    {"attempt": 0}, {"attempt": True}, {"attempt": "1"}, {"schema_version": 2},
    {"input_contract_hash": "bad"}, {"input_artifact_ids": []}, {"input_artifact_ids": [""]},
    {"input_artifact_ids": "prompt"}, {"engine": ""}, {"role": None}, {"session_model": "x"},
])
def test_invalid_envelope_rejected(changes):
    with pytest.raises(ValueError):
        validate_envelope(envelope(**changes))


def test_envelope_has_no_field_that_pretends_to_be_a_session_api():
    assert not any("api" in f.lower() or "session" in f.lower() for f in FIELDS)
