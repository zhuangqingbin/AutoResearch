from __future__ import annotations

import json

import pytest

from autoresearch.analyze import assemble
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.domain_ops import stock_full_assemble, stock_full_validate
from autoresearch.session_agent.validation import (
    DomainValidationError,
    validate_registered_contract,
)
from autoresearch.session_agent.workflows.stock import (
    full_product_artifacts,
    prepare_stock_bundle,
    publish_stock,
)

from .test_service import _handle


def required_full_products() -> set[str]:
    result = {assemble.DECISION_REL}
    for _, items in assemble.SPINE + assemble.APPENDIX:
        result.update(rel for _, rel, optional in items if not optional)
    return result


def test_full_required_products_match_current_assembler():
    required = required_full_products()
    assert "4_portfolio/decision.md" in required
    assert "2_research/faceoff.md" in required
    assert "3_risk/premortem.md" in required
    assert "1_analysts/peer.md" not in required
    mapping = full_product_artifacts()
    assert required <= set(mapping)
    assert len(set(mapping.values())) == len(mapping)


def test_full_pm_rejects_a_missing_atomic_output(tmp_path):
    handle = _handle(tmp_path)
    output_ids = [
        "stock.full.4_portfolio.decision",
        "stock.full.4_portfolio.calendar",
        "stock.full.2_research.variant",
        "stock.full.2_research.faceoff",
    ]
    output = handle.staging / "full"
    for artifact_id in output_ids:
        path = output / f"{artifact_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")
    for artifact_id in output_ids[:-1]:
        path = output / f"{artifact_id}.md"
        path.write_text(
            "**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"
            if artifact_id.endswith("decision")
            else "content\n"
        )
        artifacts.bind_artifact_hash(handle, artifact_id)
    with pytest.raises(DomainValidationError, match="output"):
        validate_registered_contract(
            handle,
            {"outputs": [{"artifact_id": item} for item in output_ids[:-1]]},
            {
                "expected_output_contract": "stock.pm.v1",
                "output_artifact_ids": output_ids,
            },
        )


def test_full_validation_requires_current_assembler_core_files(tmp_path):
    handle = _handle(tmp_path)
    mapping = full_product_artifacts()
    root = handle.staging / "analyze/600519.SS_20260913"
    for relative, artifact_id in mapping.items():
        path = root / relative
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")
        if relative in required_full_products():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"
                if relative == assemble.DECISION_REL
                else "content\n"
            )
            artifacts.bind_artifact_hash(handle, artifact_id)
    validation_path = handle.staging / "session_outputs/full.validation.json"
    artifacts.register_artifact(handle, "stock.full.validation", validation_path, "WRITE")
    value = stock_full_validate(handle)
    assert set(value["required_products"]) == required_full_products()


def test_full_assembler_and_publisher_accept_missing_optional_lenses(tmp_path):
    handle = _handle(tmp_path)
    request = {
        "schema_version": 1,
        "kind": "stock-research",
        "requested_mode": "FULL",
        "analysis_date": "2026-09-13",
        "subject": "600519.SS",
        "peers": [],
        "asset_type": "stock",
        "name": "贵州茅台",
        "force_full": False,
        "host_profile": {},
        "predecessor_run_id": None,
    }
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(request))
    mapping = full_product_artifacts()
    root = handle.staging / "analyze/600519.SS_20260913"
    for relative, artifact_id in mapping.items():
        path = root / relative
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")
        if relative in required_full_products():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "**Rating**: Hold\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"
                if relative == assemble.DECISION_REL
                else f"# {relative}\ncontent\n"
            )
            artifacts.bind_artifact_hash(handle, artifact_id)
    outputs = handle.staging / "session_outputs"
    for artifact_id, name in {
        "stock.full.validation": "full.validation.json",
        "stock.full.report": "full_report.md",
        "stock.full.manifest": "full_manifest.json",
        "stock.publication.bundle": "publication.json",
    }.items():
        artifacts.register_artifact(handle, artifact_id, outputs / name, "WRITE")
    stock_full_validate(handle)
    artifacts.bind_artifact_hash(handle, "stock.full.validation")
    bundle = stock_full_assemble(handle)
    assert bundle["mode"] == "FULL"
    for artifact_id in (
        "stock.full.report",
        "stock.full.manifest",
        "stock.publication.bundle",
    ):
        artifacts.bind_artifact_hash(handle, artifact_id)
    prepared = prepare_stock_bundle(handle)
    assert {item["artifact_id"] for item in prepared["business_files"]} == {
        "stock.full.report",
        "stock.full.manifest",
    }
    assert prepared["state_mutations"] == []
    report_dir = publish_stock(handle, reports_root=tmp_path / "published")
    assert (report_dir / "贵州茅台.md").is_file()
    assert json.loads((report_dir / "manifest.json").read_text())["run_id"] == handle.run_id
