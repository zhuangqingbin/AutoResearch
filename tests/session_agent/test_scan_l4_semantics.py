"""Current run semantics retain pinned failures and accept valid Hold cards."""
import pytest

from autoresearch.common.atomic import atomic_write_json
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.validation import (
    DomainValidationError,
    validate_registered_contract,
)
from autoresearch.trace.completeness import freeze_card_rules
from tests.scan.test_research_card_migration import FULL_CARD

from .test_service import _handle


@pytest.mark.parametrize("pinned,text,error", [
    (False, FULL_CARD + "\n进入P4倾向: Overweight\n", "PASS"),
    (True, "600000\n**Rating**: Hold\n**早停**:停于 P3 ｜ 停因:资金流出\nFINAL TRANSACTION PROPOSAL: **HOLD**", "PINNED"),
    (False, "600000\n**Rating**: Hold\n**早停**:停于 P3 ｜ 停因:资金流出\nFINAL TRANSACTION PROPOSAL: **HOLD**", None),
])
def test_scan_submission_uses_frozen_rules_and_pinned_identity(tmp_path, pinned, text, error):
    handle = _handle(tmp_path)
    atomic_write_json(handle.capsule / "verification/profile.json", {"card_rules_version": "skills-gap-v2"})
    freeze_card_rules(handle.capsule, kind="scan-market")
    card = handle.staging / "card.md"
    card.write_text(text)
    finalists = handle.staging / "finalists.csv"
    finalists.write_text("code,lane\n600000," + ("pinned" if pinned else "main") + "\n")
    for artifact_id, path in (("scan.card", card), ("scan.finalists", finalists)):
        artifacts.register_artifact(handle, artifact_id, path, "WRITE")
        artifacts.bind_artifact_hash(handle, artifact_id)
    task = {"role": "scan.l4.card", "subject": "600000", "output_artifact_ids": ["scan.card"]}
    submission = {"outputs": [{"artifact_id": "scan.card"}]}
    if error:
        with pytest.raises(DomainValidationError, match=error):
            validate_registered_contract(handle, submission, task)
    else:
        validate_registered_contract(handle, submission, task)
    assert card.read_text() == text


def test_pinned_hold_still_requires_observed_deep_read(tmp_path):
    handle = _handle(tmp_path)
    atomic_write_json(handle.capsule / "verification/profile.json", {"card_rules_version": "skills-gap-v2"})
    freeze_card_rules(handle.capsule, kind='scan-market')
    card = handle.staging / 'card.md'
    card.write_text(FULL_CARD.replace('**Rating**: Overweight', '**Rating**: Hold')
                    .replace('PROPOSAL: **BUY**', 'PROPOSAL: **HOLD**'))
    finalists = handle.staging / 'finalists.csv'
    finalists.write_text('code,lane\n600000,pinned\n')
    deep = handle.staging / 'deep.md'
    deep.write_text('真实深度财务证据')
    for artifact_id, path in [('scan.card', card), ('scan.finalists', finalists), ('scan.l4.600000.a1.deep', deep)]:
        artifacts.register_artifact(handle, artifact_id, path, 'READ')
    task = {'task_id': 'l4.600000.a1.card', 'role': 'scan.l4.card', 'subject': '600000',
            'output_artifact_ids': ['scan.card'], 'input_artifact_ids': ['scan.l4.600000.a1.deep']}
    with pytest.raises(DomainValidationError, match='未核'):
        validate_registered_contract(handle, {'outputs': [{'artifact_id': 'scan.card'}], 'envelope': {'attempt': 1}}, task)
