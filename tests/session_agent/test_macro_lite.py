from __future__ import annotations

import json

import pytest

from autoresearch.session_agent import artifacts
from autoresearch.session_agent.domain_ops import macro_lite_prepare, macro_lite_validate
from autoresearch.session_agent.workflows.macro import build_macro_plan

from .test_macro import _context, _request
from .test_service import _handle


def test_macro_lite_only_builds_frame_write_publish_chain(tmp_path):
    plan = build_macro_plan(_request("LITE"), _context(tmp_path))
    assert [task["task_id"] for task in plan["tasks"]] == [
        "macro.frame",
        "macro.brief",
        "macro.lite.validate",
        "macro.publish",
    ]
    assert not any("regional" in task["task_id"] for task in plan["tasks"])


def test_macro_lite_frame_uses_freshness_gate_and_projection(tmp_path):
    handle = _handle(tmp_path)
    request = _request("LITE")
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(request))
    source = handle.staging / "session_inputs/market_pack.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"regime": {"label": "range"}, "sector_healthy_top3": ["电子"]}))
    artifacts.register_artifact(handle, "macro.market_pack", source, "READ")
    artifacts.register_artifact(
        handle,
        "macro.strategist_pack",
        handle.staging / "session_outputs/strategist_pack.json",
        "WRITE",
    )
    result = macro_lite_prepare(handle, macro_state_path=tmp_path / "missing.json")
    assert "sector_healthy_top3" not in result["pack"]
    assert result["macro_state"] is None
    assert "无 macro_state" in result["macro_state_note"]


def test_macro_lite_validator_requires_all_six_sections(tmp_path):
    handle = _handle(tmp_path)
    path = handle.staging / "session_outputs/market_view.md"
    path.parent.mkdir(parents=True)
    path.write_text("1. **一句话定调**: range\n2. **市场结构**: 平稳\n")
    artifacts.register_artifact(handle, "macro.market_view", path, "WRITE")
    artifacts.bind_artifact_hash(handle, "macro.market_view")
    artifacts.register_artifact(
        handle,
        "macro.lite.validation",
        handle.staging / "session_outputs/macro.lite.validation.json",
        "WRITE",
    )
    with pytest.raises(RuntimeError, match="six sections"):
        macro_lite_validate(handle)


_TEMPLATE_VIEW = (
    "# 市场研判 2026-09-29\n\n"
    "1. **一句话定调**:震荡(range)缩杠杆轮动。\n\n"
    "2. **市场结构**:站上 MA60 43.76%。\n\n"
    "3. **板块红黑榜**:强——林业Ⅱ;弱——白酒Ⅱ。\n\n"
    "4. **操作基调**:姿态只由 regime 与资金面定。\n\n"
    "5. **关注**:国常会增量政策。\n\n"
    "6. 仅供研究,非投资建议。\n"
)


def _lite_handle(tmp_path, text):
    handle = _handle(tmp_path)
    path = handle.staging / "session_outputs/market_view.md"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    artifacts.register_artifact(handle, "macro.market_view", path, "WRITE")
    artifacts.bind_artifact_hash(handle, "macro.market_view")
    artifacts.register_artifact(
        handle, "macro.lite.validation",
        handle.staging / "session_outputs/macro.lite.validation.json", "WRITE")
    return handle


def test_macro_lite_validator_accepts_the_agent_template_shape(tmp_path):
    """macro-brief 模板的第 6 节是不加粗的免责行;09-26 回放里 13 份真实市场研判有 12 份是这个形状。
    扫描侧校验早已按模板判(`validation.market_view_complete`),这里是同一条规则的第二个调用点。"""
    value = macro_lite_validate(_lite_handle(tmp_path, _TEMPLATE_VIEW))
    assert value["sections"] == ["1", "2", "3", "4", "5", "6"]


@pytest.mark.parametrize("damage", ["drop_section_3", "drop_disclaimer", "unbold_section_2"])
def test_macro_lite_validator_still_rejects_an_incomplete_view(tmp_path, damage):
    text = {
        "drop_section_3": _TEMPLATE_VIEW.replace("3. **板块红黑榜**:强——林业Ⅱ;弱——白酒Ⅱ。\n\n", ""),
        "drop_disclaimer": _TEMPLATE_VIEW.replace("6. 仅供研究,非投资建议。\n", ""),
        "unbold_section_2": _TEMPLATE_VIEW.replace("2. **市场结构**", "2. 市场结构"),
    }[damage]
    with pytest.raises(RuntimeError, match="six sections"):
        macro_lite_validate(_lite_handle(tmp_path, text))


@pytest.mark.parametrize("stand_in", ["   6. 子项\n", "6.5% 的回撤仍在可承受区间。\n", "6.\n\n后记:以上为描述。\n"])
def test_macro_lite_validator_needs_a_real_section_six(tmp_path, stand_in):
    """An indented sub-item, a sentence starting with ``6.5%`` or a bare ``6.`` is not the disclaimer."""
    text = _TEMPLATE_VIEW.replace("6. 仅供研究,非投资建议。\n", stand_in)
    with pytest.raises(RuntimeError, match="six sections"):
        macro_lite_validate(_lite_handle(tmp_path, text))
