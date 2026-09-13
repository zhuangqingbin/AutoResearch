from __future__ import annotations

import pytest

from autoresearch.session_agent import artifacts
from autoresearch.session_agent.validation import (
    DomainValidationError,
    validate_registered_contract,
)

from .test_service import _handle

EARLY = """# 决策卡 — 600519 贵州茅台 @ 2026-09-13 · 〔早停·表面 DD〕
**独立初判**:资金 中 ｜ 技术 中 ｜ 估值 弱
**Rubric建议**: 表面 4 维净分 0/4 → **建议 Hold**
**Rating**: Hold
**早停**: 停于 P3 ｜ 停因:估值透支
FINAL TRANSACTION PROPOSAL: **HOLD**
"""

FULL = """# 决策卡 — 600519 贵州茅台 @ 2026-09-13
进入P4倾向: Overweight
**Rubric建议**: 6 维净分 +3/6 ｜ OW三门 主力真在 ✓·业绩真兑现 ✓·估值不透支 ✓ → **建议 Overweight**
**Rating**: Overweight
FINAL TRANSACTION PROPOSAL: **BUY**
"""


def _submission(digest):
    return {"outputs": [{"artifact_id": "stock.card.output", "sha256": digest}]}


def _task():
    return {
        "task_id": "stock.card",
        "expected_output_contract": "stock.lite.v1",
        "output_artifact_ids": ["stock.card.output"],
    }


def test_lite_early_stop_does_not_read_deep(tmp_path, monkeypatch):
    handle = _handle(tmp_path)
    card = handle.staging / "session_outputs/card.md"
    deep = handle.staging / "600519.SS_2026-09-13_slim_deep.md"
    card.parent.mkdir(parents=True)
    card.write_text(EARLY)
    deep.write_text("DEEP_TRAP_SENTINEL")
    artifacts.register_artifact(handle, "stock.card.output", card, "WRITE")
    artifacts.register_artifact(handle, "stock.deep", deep, "READ")
    digest = artifacts.bind_artifact_hash(handle, "stock.card.output")["sha256"]
    opened = []
    original = artifacts.open_artifact

    def observe(current, artifact_id):
        opened.append(artifact_id)
        return original(current, artifact_id)

    monkeypatch.setattr(artifacts, "open_artifact", observe)
    validate_registered_contract(handle, _submission(digest), _task())
    assert "stock.deep" not in opened


def test_lite_full_card_requires_deep_evidence(tmp_path):
    handle = _handle(tmp_path)
    card = handle.staging / "session_outputs/card.md"
    card.parent.mkdir(parents=True)
    card.write_text(FULL)
    artifacts.register_artifact(handle, "stock.card.output", card, "WRITE")
    digest = artifacts.bind_artifact_hash(handle, "stock.card.output")["sha256"]
    with pytest.raises(DomainValidationError, match="stock.deep"):
        validate_registered_contract(handle, _submission(digest), _task())


@pytest.mark.parametrize(
    "text",
    [
        "FINAL TRANSACTION PROPOSAL: **HOLD**\n",
        "**Rating**: Hold\n",
        "**Rating**: Buy\nFINAL TRANSACTION PROPOSAL: **HOLD**\n",
        "**Rating**: Buy\nFINAL TRANSACTION PROPOSAL: **BUY**\n**早停**: 停于 P3 ｜ 停因:其他\n",
    ],
)
def test_lite_contract_rejects_malformed_or_inconsistent_cards(tmp_path, text):
    handle = _handle(tmp_path)
    card = handle.staging / "session_outputs/card.md"
    card.parent.mkdir(parents=True)
    card.write_text(text)
    artifacts.register_artifact(handle, "stock.card.output", card, "WRITE")
    digest = artifacts.bind_artifact_hash(handle, "stock.card.output")["sha256"]
    with pytest.raises(DomainValidationError):
        validate_registered_contract(handle, _submission(digest), _task())

