from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autoresearch.sector.brief import extract_terrain
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.domain_ops import (
    sector_full_validate,
    sector_lite_validate,
    sector_prepare_publication,
)
from autoresearch.session_agent.validation import (
    DomainValidationError,
    validate_registered_domain_contract,
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


# ───── 2026-10-02 首场 session_v1 真扫:行业 brief 交稿 8 份,8 份全被判「方向性字样」 ─────
#
# 不是 agent 违约:`.claude/agents/sector-brief.md` 的地形段模板**要求**写资金流事实标签
# 「主动买卖单净流入合计」,而两处方向词正则(scan 的 `sector.terrain.v1` 与单行业 LITE 的
# `sector_lite_validate`)都含裸「买卖」→ 照模板写的 brief 必拒,`scan.l3.rank` 永远起不来。
# 用模板原文(占位符填数)过两处校验;真正的方向措辞仍必须被拒。

_AGENT_TEMPLATE = Path(__file__).resolve().parents[2] / ".claude/agents/sector-brief.md"


def _template_brief() -> str:
    """agent 定义里「## 模板」那一段围栏块,占位符一律填 1。"""
    block = _AGENT_TEMPLATE.read_text(encoding="utf-8").split("## 模板", 1)[1].split("```", 2)[1]
    return re.sub(r"<[^<>\n]+>", "1", block)


def _terrain_submission(handle, text: str):
    artifact_id = "scan.sector.abc.brief"
    _register(handle, artifact_id, "scan.sector.abc.brief.md", text)
    task = {"task_id": artifact_id, "role": "sector.brief",
            "expected_output_contract": "sector.terrain.v1", "output_artifact_ids": [artifact_id]}
    return {"outputs": [{"artifact_id": artifact_id}]}, task


def _lite_handle(tmp_path, text: str):
    handle = _handle(tmp_path)
    _register(handle, "sector.report", "sector.md", text)
    _register(handle, "sector.reuse", "sector.reuse.json", json.dumps({"reused": False}))
    artifacts.register_artifact(
        handle, "sector.validation", handle.staging / "session_outputs/sector.validation.json", "WRITE")
    return handle


def test_agent_template_terrain_passes_scan_direction_check(tmp_path):
    brief = _template_brief()
    assert "主动买卖单净流入合计" in extract_terrain(brief), "模板已改,本用例要测的标签不在了"
    handle = _handle(tmp_path)
    submission, task = _terrain_submission(handle, brief)
    assert validate_registered_domain_contract(handle, submission, task) == []


def test_agent_template_terrain_passes_sector_lite_direction_check(tmp_path):
    handle = _lite_handle(tmp_path, _template_brief())
    assert sector_lite_validate(handle)["contract"] == "sector.terrain.v1"


@pytest.mark.parametrize("phrase", ["给出买卖建议", "买卖点已现", "建议回避", "看空本行业"])
def test_directional_phrases_in_template_terrain_are_still_rejected(tmp_path, phrase):
    brief = _template_brief().replace("- **链定位一句**:1", f"- **链定位一句**:{phrase}")
    assert phrase in extract_terrain(brief), "锚点行没替换上,后面的断言是空操作"
    handle = _handle(tmp_path / "scan")
    submission, task = _terrain_submission(handle, brief)
    with pytest.raises(DomainValidationError, match="directional"):
        validate_registered_domain_contract(handle, submission, task)
    with pytest.raises(RuntimeError, match="directional"):
        sector_lite_validate(_lite_handle(tmp_path / "lite", brief))
