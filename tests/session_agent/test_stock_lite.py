from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from autoresearch.session_agent import artifacts, service
from autoresearch.session_agent.domain_ops import stock_prepare_publication, stock_validate
from autoresearch.session_agent.operations import build_argv
from autoresearch.session_agent.workflows.stock import (
    build_stock_plan,
    publish_stock,
    register_stock_artifacts,
    validate_stock_operation_params,
)

from .test_service import _request


def _context(tmp_path):
    return SimpleNamespace(
        run_id="20260913T010203000000Z",
        engine="codex",
        analysis_date="2026-09-13",
        workspace=tmp_path,
        staging=tmp_path / "staging/2026-09-13",
        contract=SimpleNamespace(
            contract_hash="a" * 64,
            config_hash="b" * 64,
        ),
    )


def test_lite_plan_keeps_progressive_card_as_one_inference_task(tmp_path):
    plan = build_stock_plan(_request(), _context(tmp_path))
    assert [task["task_id"] for task in plan["tasks"]] == [
        "stock.harvest",
        "stock.card",
        "stock.validate",
        "stock.publish",
    ]
    card = plan["tasks"][1]
    assert card["input_artifact_ids"] == ["stock.slim", "stock.deep"]
    assert card["expected_output_contract"] == "stock.lite.v1"


def test_lite_begin_registers_the_complete_artifact_boundary(tmp_path):
    from .test_service import _handle

    handle = _handle(tmp_path)
    result = service.begin(_request(), begin_capsule=lambda request: handle)
    assert result["state"] == "READY"
    registry = json.loads((handle.workspace / "session/artifacts.json").read_text())
    assert set(registry["artifacts"]) == {
        "research.frame",
        "stock.slim",
        "stock.deep",
        "stock.card.output",
        "stock.card.validation",
        "stock.publication.bundle",
    }


def test_lite_begin_with_default_relative_workspace(tmp_path, monkeypatch):
    from .test_service import _handle

    repo = Path.cwd()
    for directory in (".claude", ".codex"):
        (tmp_path / directory).symlink_to(repo / directory, target_is_directory=True)
    handle = _handle(tmp_path)
    monkeypatch.chdir(tmp_path)
    for name in ("workspace", "staging", "capsule"):
        setattr(handle, name, getattr(handle, name).relative_to(tmp_path))

    result = service.begin(_request(), begin_capsule=lambda request: handle)

    assert result["state"] == "READY"
    with artifacts.open_artifact(handle, "research.frame") as stream:
        assert json.load(stream)["analysis_session"] == "2026-09-13"
    registry = json.loads((handle.workspace / "session/artifacts.json").read_text())
    assert registry["artifacts"]["stock.slim"]["relative_path"] == (
        "staging/2026-09-13/600519.SS_2026-09-13_slim.md"
    )


def test_stock_plan_identity_changes_with_host_profile(tmp_path):
    first = build_stock_plan(_request(), _context(tmp_path))
    request = _request()
    request["host_profile"] = {
        **request["host_profile"],
        "observed_model": "different-observation",
    }
    second = build_stock_plan(request, _context(tmp_path))
    assert first["host_profile_hash"] != second["host_profile_hash"]
    assert first["plan_hash"] != second["plan_hash"]


def test_lite_deterministic_operations_have_no_path_parameters():
    validate = build_argv("stock.validate", {})
    publish = build_argv("stock.publish", {})
    assert validate[-1] == "stock-validate"
    assert publish[-1] == "stock-publish"


def test_harvest_params_must_equal_the_frozen_request(tmp_path):
    plan = build_stock_plan(_request(), _context(tmp_path))
    harvest = plan["tasks"][0]
    params = {
        "ticker": "000001.SZ",
        "analysis_date": "2026-09-13",
        "asset_type": "stock",
        "peers": [],
        "slim": True,
    }
    import pytest

    with pytest.raises(ValueError, match="frozen request"):
        validate_stock_operation_params(_request(), harvest, params)


def test_lite_validate_and_prepare_publication_use_registered_artifacts(tmp_path):
    handle = _context(tmp_path)
    handle.staging.mkdir(parents=True)
    plan = build_stock_plan(_request(), handle)
    register_stock_artifacts(_request(), handle, plan)
    (handle.workspace / "session").mkdir(exist_ok=True)
    (handle.workspace / "session/request.json").write_text(json.dumps(_request()))
    card_path = handle.staging / "session_outputs/card.md"
    deep_path = handle.staging / "600519.SS_2026-09-13_slim_deep.md"
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(
        "**Rating**: Hold\n**早停**: 停于 P3 ｜ 停因:其他\n"
        "FINAL TRANSACTION PROPOSAL: **HOLD**\n"
    )
    deep_path.write_text("unused")
    artifacts.bind_artifact_hash(handle, "stock.card.output")
    artifacts.bind_artifact_hash(handle, "stock.deep")
    validation = stock_validate(handle)
    assert validation["rating"] == "Hold"
    artifacts.bind_artifact_hash(handle, "stock.card.validation")
    bundle = stock_prepare_publication(handle)
    assert bundle["output_name"] == "600519_lite.md"
    assert bundle["card_sha256"]


def test_lite_publication_is_idempotent_and_contains_run_identity(tmp_path):
    handle = _context(tmp_path)
    handle.staging.mkdir(parents=True)
    request = _request()
    plan = build_stock_plan(request, handle)
    register_stock_artifacts(request, handle, plan)
    (handle.workspace / "session").mkdir(exist_ok=True)
    (handle.workspace / "session/request.json").write_text(json.dumps(request))
    card_path = handle.staging / "session_outputs/card.md"
    card_path.parent.mkdir(parents=True)
    card_path.write_text(
        "**Rating**: Hold\n**早停**: 停于 P3 ｜ 停因:其他\n"
        "FINAL TRANSACTION PROPOSAL: **HOLD**\n"
    )
    artifacts.bind_artifact_hash(handle, "stock.card.output")
    stock_validate(handle)
    artifacts.bind_artifact_hash(handle, "stock.card.validation")
    stock_prepare_publication(handle)
    artifacts.bind_artifact_hash(handle, "stock.publication.bundle")
    reports = tmp_path / "reports_codex/analyze"
    first = publish_stock(handle, reports_root=reports)
    second = publish_stock(handle, reports_root=reports)
    assert first == second
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["run_id"] == handle.run_id
    assert (first / "600519_lite.md").read_text() == card_path.read_text()


def test_v2_lite_submission_enforces_structured_semantics(tmp_path):
    import pytest

    from autoresearch.common.atomic import atomic_write_json
    from autoresearch.session_agent.validation import (
        DomainValidationError,
        validate_registered_contract,
    )
    from tests.scan.test_research_card_migration import FULL_CARD

    from .test_service import _handle

    handle = _handle(tmp_path)
    atomic_write_json(handle.capsule / "verification/profile.json", {"card_rules_version": "skills-gap-v2"})
    path = handle.staging / "card.md"
    text = FULL_CARD.replace("600000", "600519") + "\n进入P4倾向: Overweight\n"
    path.write_text(text)
    artifacts.register_artifact(handle, "stock.card.output", path, "WRITE")
    artifacts.bind_artifact_hash(handle, "stock.card.output")
    deep = handle.staging / "deep.md"
    deep.write_text("deep")
    artifacts.register_artifact(handle, "stock.deep", deep, "READ")
    artifacts.bind_artifact_hash(handle, "stock.deep")
    with pytest.raises(DomainValidationError, match="PASS"):
        validate_registered_contract(handle, {"outputs": []}, {
            "expected_output_contract": "stock.lite.v1", "subject": "600519.SS",
        })
    assert path.read_text() == text


def test_begin_freezes_card_rules_before_first_task(tmp_path):
    from autoresearch.trace.completeness import card_rules_from_capsule

    from .test_service import _handle

    handle = _handle(tmp_path)
    assert card_rules_from_capsule(handle.capsule) == "legacy-v1"
    service.begin(_request(), begin_capsule=lambda request: handle)
    assert card_rules_from_capsule(handle.capsule) == "skills-gap-v3"


def test_legacy_lite_uses_frozen_historical_contract(tmp_path):
    from autoresearch.session_agent.validation import validate_registered_contract
    from tests.scan.test_research_card_migration import FULL_CARD

    from .test_service import _handle

    handle = _handle(tmp_path)
    profile = handle.capsule / "verification/profile.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps({"kind": "stock-research", "card_rules_version": "legacy-v1"}))
    card = handle.staging / "card.md"
    card.write_text(FULL_CARD + "\n进入P4倾向: Overweight\n")
    artifacts.register_artifact(handle, "stock.card.output", card, "WRITE")
    artifacts.bind_artifact_hash(handle, "stock.card.output")
    deep = handle.staging / "deep.md"
    deep.write_text("deep")
    artifacts.register_artifact(handle, "stock.deep", deep, "READ")
    artifacts.bind_artifact_hash(handle, "stock.deep")
    validate_registered_contract(handle, {"outputs": []}, {
        "expected_output_contract": "stock.lite.v1", "subject": "600000.SS",
    })
