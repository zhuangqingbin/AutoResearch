"""The frozen task contract selects L3 validation, including resumed v1 runs."""
import json
from types import SimpleNamespace

import pytest

from autoresearch.session_agent import dispatch, validation
from autoresearch.session_agent.workflows.scan import build_scan_plan, l3_expansion
from tests.scan.test_l3_eligibility import ranked

from .test_scan_prelude import context, request


def test_new_l3_task_declares_v2(tmp_path):
    expansion = l3_expansion(build_scan_plan(request(), context(tmp_path)),
                             {"mode": "FULL", "pinned_codes": []}, [], [])
    task = next(t for t in expansion["tasks"] if t["role"] == "scan.l3")
    assert task["expected_output_contract"] == "scan.l3.v2"


@pytest.mark.parametrize("version", [1, 2])
def test_l3_submission_cannot_downgrade_or_upgrade_frozen_contract(monkeypatch, version):
    task = {"expected_output_contract": f"scan.l3.v{version}"}
    good = [ranked("000001")] if version == 2 else [{"finalist": True}]
    bad = [{"finalist": True}] if version == 2 else [ranked("000001")]
    monkeypatch.setattr(validation, "_open_outputs", lambda *args: {"out": json.dumps(good)})
    validation.validate_registered_contract(None, {}, task)
    monkeypatch.setattr(validation, "_open_outputs", lambda *args: {"out": json.dumps(bad)})
    with pytest.raises(validation.DomainValidationError, match="version"):
        validation.validate_registered_contract(None, {}, task)


def test_old_l3_dispatch_explicitly_selects_historical_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch, "l3_bounds", lambda handle: (3, 8))
    monkeypatch.setattr(dispatch, "_pass1_target", lambda handle: 20)
    handle = SimpleNamespace(staging=tmp_path, analysis_date="2026-09-30")
    task = {"role": "scan.l3", "expected_output_contract": "scan.l3.v1"}
    prompt = dispatch.render_prompt(handle, task, 1, inputs={}, outputs={"out": "rank.json"})
    assert "scan.l3.v1" in prompt and "schema_version" in prompt
    assert "历史" in prompt


def test_v2_submit_rejects_duplicate_code_before_merge(monkeypatch):
    rows = [ranked("000001"), ranked("000001")]
    monkeypatch.setattr(validation, "_open_outputs", lambda *args: {"out": json.dumps(rows)})
    with pytest.raises(validation.DomainValidationError, match="duplicate"):
        validation.validate_registered_contract(None, {}, {"expected_output_contract": "scan.l3.v2"})
