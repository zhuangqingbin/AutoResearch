"""The frozen macro input, rather than generated prose, owns allocation keys."""
from __future__ import annotations

import json

import pytest

from autoresearch.macro import assemble, state
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.domain_ops import macro_full_assemble, macro_full_validate
from autoresearch.session_agent.validation import DomainValidationError, validate_registered_contract
from autoresearch.session_agent.workflows.macro import macro_product_artifacts, required_macro_products

from .test_macro import _request
from .test_service import _handle

ASSET_KEYS = (
    "OVERALL 风险档", "美债", "美股", "A股·港股", "USD", "CNY", "JPY", "黄金", "大宗", "加密(BTC)", "信用",
)
DATA = """# Deterministic macro data
**行业资金净流入(tushare moneyflow_ind_ths,20260913;top8 入 / bottom5 出)**:
| 行业 | 主力净流入(亿) | 领涨股 |
|---|---:|---|
| 电子 | +1.5 | 示例甲 |
| … | … | … |
| 煤炭 | -2.0 | 示例乙 |

**其它表**
| 行业 | 主力净流入(亿) | 领涨股 |
|---|---:|---|
| 模型臆造行业 | 2.0 | 示例丙 |
"""


def _body(keys):
    return "\n".join(f"- {key}: **Rating**: Hold" for key in keys) + "\n置信度: 中\n"


def _run(tmp_path, changes=None):
    handle = _handle(tmp_path)
    session = handle.workspace / "session"
    session.mkdir()
    (session / "request.json").write_text(json.dumps(_request()))
    root = handle.staging / "macro/2026-09-13"
    root.mkdir(parents=True)
    (root / "data.md").write_text(DATA)
    artifacts.register_artifact(handle, "macro.data", root / "data.md", "WRITE")
    artifacts.bind_artifact_hash(handle, "macro.data")
    for relative, artifact_id in macro_product_artifacts().items():
        path = root / relative
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")
        if relative not in required_macro_products():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (
            _body(ASSET_KEYS) if relative == assemble.DECISION_REL else
            _body(["电子", "煤炭"]) if relative == assemble.SECTOR_MAP_REL else "content\n置信度: 中\n"
        )
        path.write_text((changes or {}).get(relative, body))
        artifacts.bind_artifact_hash(handle, artifact_id)
    for artifact_id, name in {
        "macro.full.validation": "macro.full.validation.json",
        "macro.full.report": "macro.full.report.md",
        "macro.state.candidate": "macro_state.json",
        "macro.publication.bundle": "macro.publication.json",
    }.items():
        artifacts.register_artifact(handle, artifact_id, handle.staging / "session_outputs" / name, "WRITE")
    return handle, root


def test_scope_uses_only_deterministic_flow_table_not_whole_industry_universe():
    keys = assemble.allocation_scope(DATA)
    assert set(keys[assemble.DECISION_REL]) == set(ASSET_KEYS)
    assert keys[assemble.SECTOR_MAP_REL] == ("电子", "煤炭")
    with pytest.raises(ValueError, match="scope"):
        assemble.allocation_scope("no deterministic industry table")


@pytest.mark.parametrize("relative,body", [
    (assemble.DECISION_REL, _body(ASSET_KEYS[:-1])),
    (assemble.DECISION_REL, _body([*ASSET_KEYS, "神秘资产"])),
    (assemble.SECTOR_MAP_REL, _body(["电子"])),
    (assemble.SECTOR_MAP_REL, _body(["电子", "煤炭", "模型臆造行业"])),
])
def test_production_submit_validate_assemble_and_state_reject_scope_drift(tmp_path, relative, body):
    handle, root = _run(tmp_path, {relative: body})
    artifact_id = macro_product_artifacts()[relative]
    with pytest.raises(DomainValidationError, match="allocation"):
        validate_registered_contract(handle, {"outputs": [{"artifact_id": artifact_id}]}, {
            "expected_output_contract": "macro.allocation.v1",
            "input_artifact_ids": ["macro.data"], "output_artifact_ids": [artifact_id],
        })
    with pytest.raises(RuntimeError, match="allocation"):
        macro_full_validate(handle)
    assert not (handle.staging / "session_outputs/macro.full.validation.json").exists()
    previous = tmp_path / "state/macro_state.json"
    previous.parent.mkdir()
    previous.write_text('{"as_of":"2026-09-01"}')
    original = previous.read_bytes()
    with pytest.raises(ValueError, match="allocation"):
        state.write_macro_state(root, out_dir=previous.parent)
    assert previous.read_bytes() == original
    assert state.main([str(root), "--out-dir", str(previous.parent)]) == 1
    assert previous.read_bytes() == original
    report_dir = tmp_path / "report"
    assert assemble.main([
        str(root), "--output-dir", str(report_dir), "--state-out-dir", str(previous.parent),
    ]) == 1
    assert not report_dir.exists()
    assert previous.read_bytes() == original


def test_full_assembly_forwards_same_scope_to_report_and_state(tmp_path):
    handle, _ = _run(tmp_path)
    result = macro_full_validate(handle)
    assert result["allocation_keys"][assemble.SECTOR_MAP_REL] == ["电子", "煤炭"]
    artifacts.bind_artifact_hash(handle, "macro.full.validation")
    macro_full_assemble(handle)
    saved = json.loads((handle.staging / "session_outputs/macro_state.json").read_text())
    assert set(saved["cross_asset"]) == set(ASSET_KEYS)
    assert set(saved["ashare_sectors"]) == {"电子", "煤炭"}
    assert saved["session_run_id"] == handle.run_id
