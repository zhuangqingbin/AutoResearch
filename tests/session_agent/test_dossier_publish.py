from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.dossier import builder, schema
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.domain_ops import (
    dossier_build_skeleton,
    dossier_validate,
)
from autoresearch.session_agent.workflows.dossier import publish_dossier

from .test_dossier import _request
from .test_service import _handle


def _setup(handle):
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(_request()))
    output = handle.staging / "session_outputs"
    for artifact_id, name in {
        "dossier.prefetch": "dossier.prefetch.json",
        "dossier.skeleton": "dossier.skeleton.md",
        "dossier.permissions": "dossier.permissions.json",
        "dossier.candidate": "dossier.candidate.md",
        "dossier.validation": "dossier.validation.json",
        "dossier.publication.bundle": "dossier.publication.json",
    }.items():
        artifacts.register_artifact(handle, artifact_id, output / name, "WRITE")
    output.mkdir(parents=True)
    (output / "dossier.prefetch.json").write_text(
        json.dumps(
            {
                "code": "600519",
                "asof": "2026-09-13",
                "mainbz": [],
                "fwd_eps": None,
                "val_band": None,
                "notes": ["prefetch degraded"],
            }
        )
    )
    artifacts.bind_artifact_hash(handle, "dossier.prefetch")
    dossier_build_skeleton(handle, target_path=handle.workspace / "live/600519.md")
    artifacts.bind_artifact_hash(handle, "dossier.skeleton")
    artifacts.bind_artifact_hash(handle, "dossier.permissions")
    return output


def _completed_candidate(skeleton: str) -> str:
    text = skeleton.replace("initiated: null", "initiated: 2026-09-13")
    text = text.replace("- 业务: (待首覆)", "- 业务: 高端白酒品牌与渠道")
    text = text.replace("- 驱动: (待首覆)", "- 驱动: 量价与渠道库存")
    text = text.replace("- 风险: (待首覆)", "- 风险: 动销不及预期")
    text = text.replace("- 催化: (待首覆)", "- 催化: 旺季回款")
    return text.replace("<!-- LLM:待首覆 -->", "首覆研究内容", 3)


def _bind_candidate(handle, output: Path, text: str):
    (output / "dossier.candidate.md").write_text(text)
    artifacts.bind_artifact_hash(handle, "dossier.candidate")


def test_dossier_validation_rejects_changes_to_deterministic_sections(tmp_path):
    handle = _handle(tmp_path)
    output = _setup(handle)
    skeleton = (output / "dossier.skeleton.md").read_text()
    candidate = _completed_candidate(skeleton).replace(
        schema.SECTIONS[2], schema.SECTIONS[2] + "\n模型改写了估值表"
    )
    _bind_candidate(handle, output, candidate)
    with pytest.raises(RuntimeError, match="deterministic section"):
        dossier_validate(handle)


def test_dossier_validation_rejects_summary_over_cap(tmp_path):
    handle = _handle(tmp_path)
    output = _setup(handle)
    skeleton = (output / "dossier.skeleton.md").read_text()
    candidate = _completed_candidate(skeleton).replace(
        "- 业务: 高端白酒品牌与渠道", "- 业务: " + ("超长" * 5000)
    )
    _bind_candidate(handle, output, candidate)
    with pytest.raises(RuntimeError, match="summary>cap"):
        dossier_validate(handle)


def test_dossier_publish_detects_concurrent_manual_creation_and_is_idempotent(tmp_path):
    handle = _handle(tmp_path)
    target = handle.workspace / "live/600519.md"
    output = _setup(handle)
    candidate = _completed_candidate((output / "dossier.skeleton.md").read_text())
    _bind_candidate(handle, output, candidate)
    dossier_validate(handle)
    artifacts.bind_artifact_hash(handle, "dossier.validation")
    from autoresearch.session_agent.domain_ops import dossier_prepare_publication

    dossier_prepare_publication(handle)
    artifacts.bind_artifact_hash(handle, "dossier.publication.bundle")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("人工在研究期间新建")
    with pytest.raises(RuntimeError, match="CONFLICT"):
        publish_dossier(handle, target_path=target)
    target.unlink()
    assert publish_dossier(handle, target_path=target) == target
    assert publish_dossier(handle, target_path=target) == target
    assert schema.parse_frontmatter(target.read_text())["initiated"] == "2026-09-13"


def test_builder_explicit_candidate_path_does_not_touch_live_dossier(tmp_path, monkeypatch):
    live = tmp_path / "live"
    monkeypatch.setattr(schema, "DOSSIER_DIR", live)
    prefetch = tmp_path / "prefetch.json"
    prefetch.write_text(json.dumps({"mainbz": [], "fwd_eps": None, "val_band": None}))
    candidate = tmp_path / "run/candidate.md"
    result = builder.build_skeleton(
        "600519",
        "2026-09-13",
        output_path=candidate,
        prefetch_path=prefetch,
        scan_root=tmp_path / "scan",
    )
    assert result["path"] == candidate
    assert candidate.is_file()
    assert not schema.dossier_path("600519").exists()

