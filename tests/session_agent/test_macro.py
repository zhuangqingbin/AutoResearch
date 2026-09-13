from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from autoresearch.macro import assemble
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.domain_ops import macro_full_assemble, macro_full_validate
from autoresearch.session_agent.workflows.macro import (
    build_macro_plan,
    macro_product_artifacts,
    publish_macro,
    required_macro_products,
)

from .test_service import _handle, _profile


def _request(mode="FULL"):
    return {
        "schema_version": 1,
        "kind": "macro-research",
        "requested_mode": mode,
        "analysis_date": "2026-09-13",
        "subject": None,
        "peers": [],
        "asset_type": None,
        "name": None,
        "host_profile": _profile(),
        "predecessor_run_id": None,
    }


def _context(tmp_path):
    return SimpleNamespace(
        run_id="20260913T010203000000Z",
        engine="codex",
        analysis_date="2026-09-13",
        workspace=tmp_path,
        staging=tmp_path / "staging/2026-09-13",
        contract=SimpleNamespace(contract_hash="a" * 64, config_hash="b" * 64),
    )


def test_macro_full_plan_matches_current_assembler_and_order(tmp_path):
    plan = build_macro_plan(_request(), _context(tmp_path))
    tasks = {task["task_id"]: task for task in plan["tasks"]}
    assert required_macro_products() == {
        assemble.DECISION_REL,
        *(
            rel
            for _, items in assemble.SPINE + assemble.MESO + assemble.APPENDIX
            for _, rel, optional in items
            if not optional
        ),
    }
    assert tasks["macro.regional.us"]["dependencies"] == ["macro.harvest"]
    assert tasks["macro.crossasset.rates"]["dependencies"] == ["macro.regional.global"]
    assert tasks["macro.sinous.divergence"]["dependencies"] == ["macro.crossasset.crypto"]
    assert tasks["macro.meso.sector_map"]["dependencies"] == ["macro.sinous.relative"]
    assert tasks["macro.meso.flows"]["dependencies"] == ["macro.meso.sector_map"]
    assert tasks["macro.spine.decision"]["dependencies"] == ["macro.spine.premortem"]
    assert tasks["macro.assemble"]["dependencies"] == ["macro.full.validate"]


def test_macro_full_requires_every_current_core_product(tmp_path):
    handle = _handle(tmp_path)
    mapping = macro_product_artifacts()
    root = handle.staging / "macro/2026-09-13"
    for relative, artifact_id in mapping.items():
        path = root / relative
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")
        if relative in required_macro_products():
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative in {assemble.DECISION_REL, assemble.SECTOR_MAP_REL}:
                path.write_text("- 资产: **Rating**: Hold\n置信度: 中\n")
            else:
                path.write_text("content\n置信度: 中\n")
            artifacts.bind_artifact_hash(handle, artifact_id)
    target = handle.staging / "session_outputs/macro.full.validation.json"
    artifacts.register_artifact(handle, "macro.full.validation", target, "WRITE")
    result = macro_full_validate(handle)
    assert set(result["required_products"]) == required_macro_products()


def test_macro_full_rejects_unparseable_allocation_row(tmp_path):
    handle = _handle(tmp_path)
    mapping = macro_product_artifacts()
    root = handle.staging / "macro/2026-09-13"
    for relative in required_macro_products():
        artifact_id = mapping[relative]
        path = root / relative
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "- 资产: **Rating**: Maybe\n置信度: 中\n"
            if relative == assemble.DECISION_REL
            else "content\n置信度: 中\n"
        )
        artifacts.bind_artifact_hash(handle, artifact_id)
    artifacts.register_artifact(
        handle,
        "macro.full.validation",
        handle.staging / "session_outputs/macro.full.validation.json",
        "WRITE",
    )
    with pytest.raises(RuntimeError, match="allocation"):
        macro_full_validate(handle)


def test_macro_assemble_publishes_run_state_without_overwriting_newer_state(tmp_path):
    handle = _handle(tmp_path)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(_request()))
    mapping = macro_product_artifacts()
    root = handle.staging / "macro/2026-09-13"
    for relative, artifact_id in mapping.items():
        artifacts.register_artifact(handle, artifact_id, root / relative, "WRITE")
        if relative in required_macro_products():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            text = (
                "- OVERALL 风险档: **Rating**: Hold\n置信度: 中\n"
                if relative == assemble.DECISION_REL
                else (
                    "- 电子: **Rating**: Overweight\n置信度: 中\n"
                    if relative == assemble.SECTOR_MAP_REL
                    else f"# {relative}\ncontent\n置信度: 中\n"
                )
            )
            path.write_text(text)
            artifacts.bind_artifact_hash(handle, artifact_id)
    outputs = handle.staging / "session_outputs"
    for artifact_id, name in {
        "macro.full.validation": "macro.full.validation.json",
        "macro.full.report": "macro.full.report.md",
        "macro.state.candidate": "macro_state.json",
        "macro.publication.bundle": "macro.publication.json",
    }.items():
        artifacts.register_artifact(handle, artifact_id, outputs / name, "WRITE")
    macro_full_validate(handle)
    artifacts.bind_artifact_hash(handle, "macro.full.validation")
    macro_full_assemble(handle)
    for artifact_id in (
        "macro.full.report",
        "macro.state.candidate",
        "macro.publication.bundle",
    ):
        artifacts.bind_artifact_hash(handle, artifact_id)
    reports = tmp_path / "reports"
    latest = tmp_path / "context/macro/macro_state.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(json.dumps({"as_of": "2026-09-14"}))
    published = publish_macro(handle, reports_root=reports, state_path=latest)
    assert published.is_file()
    assert json.loads(latest.read_text())["as_of"] == "2026-09-14"
