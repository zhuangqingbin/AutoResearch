from __future__ import annotations

import json

import pytest

from autoresearch.session_agent import artifacts
from autoresearch.session_agent.domain_ops import (
    sector_full_validate,
    sector_lite_validate,
    sector_prepare_publication,
)
from autoresearch.session_agent.workflows.sector import prepare_sector_bundle, publish_sector

from .test_sector import _request
from .test_service import _handle


def _register(handle, artifact_id, name, text):
    path = handle.staging / "session_outputs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    artifacts.register_artifact(handle, artifact_id, path, "WRITE")
    artifacts.bind_artifact_hash(handle, artifact_id)


def test_sector_lite_rejects_directional_language(tmp_path):
    handle = _handle(tmp_path)
    _register(
        handle,
        "sector.report",
        "sector.md",
        "# 行业 brief\n\n## 地形段(描述性)\n- 看多本行业\n",
    )
    artifacts.register_artifact(
        handle,
        "sector.validation",
        handle.staging / "session_outputs/sector.validation.json",
        "WRITE",
    )
    with pytest.raises(RuntimeError, match="directional"):
        sector_lite_validate(handle)


def test_sector_full_requires_six_sections_and_discloses_missing_history(tmp_path):
    handle = _handle(tmp_path)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(_request("FULL")))
    _register(handle, "sector.pack", "sector.pack.json", json.dumps({"industry": "电子"}))
    _register(
        handle,
        "sector.report",
        "sector.md",
        "\n".join(
            [
                "## 1. 链结构",
                "## 2. 景气位置",
                "## 3. 竞争格局",
                "## 4. 估值",
                "历史估值分位：缺失",
                "## 5. 龙头映射",
                "## 6. 研判结论",
            ]
        ),
    )
    artifacts.register_artifact(
        handle,
        "sector.validation",
        handle.staging / "session_outputs/sector.validation.json",
        "WRITE",
    )
    result = sector_full_validate(handle)
    assert result["sections"] == [1, 2, 3, 4, 5, 6]


def test_sector_full_rejects_etf_described_as_company(tmp_path):
    handle = _handle(tmp_path)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(_request("FULL")))
    _register(
        handle,
        "sector.pack",
        "sector.pack.json",
        json.dumps({"industry": "电子", "readthrough": [{"symbol": "SOXX", "kind": "etf"}]}),
    )
    body = "\n".join(f"## {index}. section" for index in range(1, 7))
    body += "\n历史估值分位：缺失\nSOXX 公司财报显示增长\n"
    _register(handle, "sector.report", "sector.md", body)
    artifacts.register_artifact(
        handle,
        "sector.validation",
        handle.staging / "session_outputs/sector.validation.json",
        "WRITE",
    )
    with pytest.raises(RuntimeError, match="non-company"):
        sector_full_validate(handle)


def test_sector_publisher_is_idempotent_and_conflict_safe(tmp_path):
    handle = _handle(tmp_path)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(_request("LITE")))
    _register(
        handle,
        "sector.report",
        "sector.md",
        "# 行业 brief\n\n## 地形段(描述性)\n- 成分 10 只\n",
    )
    report_hash = json.loads(
        (handle.workspace / "session/artifacts.json").read_text()
    )["artifacts"]["sector.report"]["sha256"]
    _register(
        handle,
        "sector.validation",
        "sector.validation.json",
        json.dumps({"report_sha256": report_hash}),
    )
    artifacts.register_artifact(
        handle,
        "sector.publication.bundle",
        handle.staging / "session_outputs/sector.publication.json",
        "WRITE",
    )
    sector_prepare_publication(handle)
    artifacts.bind_artifact_hash(handle, "sector.publication.bundle")
    prepared = prepare_sector_bundle(handle)
    assert prepared["business_files"][0]["artifact_id"] == "sector.report"
    assert prepared["state_mutations"] == []
    target = publish_sector(handle, reports_root=tmp_path / "reports")
    assert target == tmp_path / "reports/2026-09-13/电子.md"
    assert publish_sector(handle, reports_root=tmp_path / "reports") == target
    target.write_text("manual edit")
    with pytest.raises(RuntimeError, match="conflict"):
        publish_sector(handle, reports_root=tmp_path / "reports")
