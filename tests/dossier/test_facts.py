import copy

import pytest

from autoresearch.common.atomic import sha256_bytes
from autoresearch.dossier import facts, schema

DAY = '2026-09-30'
CUTOFF = '2026-09-30T12:00:00+08:00'


def fact(kind='STABLE'):
    return {'fact_id': 'business', 'kind': kind, 'section': '1', 'statement': '已核事实',
            'source_claim_ids': ['claim-1'], 'effective_date': '2026-09-29',
            'available_at': '2026-09-29T12:00:00+08:00',
            'snapshot_at': '2026-09-30T11:00:00+08:00' if kind == 'DYNAMIC' else None,
            'proposed_at': '2026-09-29T12:00:00+08:00' if kind == 'HYPOTHESIS' else None,
            'expires_at': '2026-10-01T12:00:00+08:00' if kind == 'HYPOTHESIS' else None,
            'falsifier': '订单取消则失效' if kind == 'HYPOTHESIS' else None}


def proof(row, verdict='PASS'):
    from datetime import datetime, timedelta
    end = (datetime.fromisoformat(row['snapshot_at']) + timedelta(seconds=1)).isoformat() if row['snapshot_at'] else '2026-10-01T00:00:00+08:00'
    return {'claim-1': {'verdict': verdict, 'statement_sha256': sha256_bytes(row['statement'].encode()),
        'source_assertion_kind': 'conditional' if row['kind'] == 'HYPOTHESIS' else 'actual',
        'source_effective_at': {'start': row['effective_date'] + 'T00:00:00+08:00',
                                'end': end, 'precision': 'range'}}}


def check(row, **kwargs):
    return facts.evaluate_fact(row, analysis_date=DAY, knowledge_cutoff=CUTOFF,
                               evidence=kwargs.pop('evidence', proof(row)), **kwargs)


@pytest.mark.parametrize('kind', ['STABLE', 'DYNAMIC', 'HYPOTHESIS'])
def test_three_kinds_require_current_verified_evidence(kind):
    row = fact(kind)
    assert check(row)['reusable']
    assert not check(row, evidence={})['reusable']


@pytest.mark.parametrize(('field', 'value', 'reason'), [
    ('effective_date', '2026-10-01', 'NOT_AVAILABLE_BY_CUTOFF'),
    ('available_at', '2026-09-30T13:00:00+08:00', 'NOT_AVAILABLE_BY_CUTOFF'),
    ('snapshot_at', '2026-09-29T11:00:00+08:00', 'DYNAMIC_REFRESH_REQUIRED'),
    ('snapshot_at', '2026-09-30T13:00:00+08:00', 'DYNAMIC_REFRESH_REQUIRED'),
    ('expires_at', CUTOFF, 'EXPIRED'),
])
def test_expired_future_and_old_snapshot_never_reused(field, value, reason):
    row = dict(fact('DYNAMIC'), **{field: value})
    assert reason in check(row)['reasons']


def test_correction_rechecks_stable_fact_and_changed_statement_cannot_borrow_proof():
    row = fact()
    assert 'CORRECTION_RECHECK_REQUIRED' in check(row, corrected_claim_ids=['claim-1'])['reasons']
    changed = dict(row, statement='改写事实')
    assert not check(changed, evidence=proof(row))['reusable']


@pytest.mark.parametrize('changes', [
    {'falsifier': None}, {'expires_at': None}, {'proposed_at': None},
    {'expires_at': '2026-09-28T12:00:00+08:00'},
])
def test_hypothesis_requires_bounded_testable_claim(changes):
    with pytest.raises(ValueError):
        facts.validate_fact(dict(fact('HYPOTHESIS'), **changes))


def test_delta_preserves_conflicts_original_values_sources_and_all_history():
    old = facts.empty_ledger('600519')
    original = fact()
    old['facts'] = [original]
    proposed = dict(fact('HYPOTHESIS'), statement='new guess')
    updated = facts.apply_updates(old, [proposed], changed_at=CUTOFF, analysis_date=DAY,
                                  knowledge_cutoff=CUTOFF, evidence=proof(proposed))
    assert updated['facts'] == [original]
    assert updated['history'][0]['change'] == 'CONFLICT'
    assert updated['history'][0]['before'] == original
    assert updated['history'][0]['after'] == proposed
    assert old['history'] == []
    corrected = dict(original, statement='更正后的事实', source_claim_ids=['claim-1'])
    final = facts.apply_updates(updated, [corrected], changed_at=CUTOFF, analysis_date=DAY,
                                knowledge_cutoff=CUTOFF, evidence=proof(corrected))
    assert final['facts'] == [corrected]
    assert final['history'][:1] == updated['history']
    assert final['history'][-1]['before'] == original


def test_missing_source_and_expiry_remain_observable_for_unchanged_fact():
    row = fact('HYPOTHESIS')
    row['expires_at'] = CUTOFF
    old = facts.empty_ledger('600519')
    old['facts'] = [row]
    new = facts.apply_updates(old, [row], changed_at=CUTOFF, analysis_date=DAY,
                              knowledge_cutoff=CUTOFF, evidence={})
    assert new['history'][0]['change'] == 'EXPIRED'
    assert 'MISSING_SOURCE' in new['history'][0]['reasons']
    assert new['facts'] == old['facts']


def test_ledger_roundtrip_preserves_machine_sections_and_coverage():
    from .test_schema import _ok_doc
    original = _ok_doc()
    ledger = facts.empty_ledger('300857')
    ledger['facts'] = [fact()]
    text = facts.replace_ledger(original, ledger)
    assert facts.parse_ledger(text) == ledger
    assert schema.lint_dossier(text) == []
    for section in schema.SECTIONS:
        assert schema._section_block(text, section) == schema._section_block(original, section)
    assert facts.replace_ledger(text, ledger) == text
    view = facts.reusable_view(ledger, analysis_date=DAY, knowledge_cutoff=CUTOFF, evidence={})
    assert view['coverage'] == {'declared': 1, 'reusable': 0}
    assert view['semantic_coverage'] == 'UNKNOWN'
    assert '已核事实' not in facts.render_reusable(view)


def test_duplicate_ledger_or_subject_mismatch_fails_lint():
    from .test_schema import _ok_doc
    ledger = facts.empty_ledger('600519')
    text = facts.replace_ledger(_ok_doc(), ledger)
    assert '档案事实主体不一致' in schema.lint_dossier(text)
    assert any('档案事实契约' in issue for issue in schema.lint_dossier(text + facts.render_ledger(ledger)))


def test_current_injection_never_falls_back_to_untyped_summary(monkeypatch):
    from .test_schema import _ok_doc
    text = _ok_doc().replace('initiated: null', 'initiated: 2026-09-29')
    view = facts.reusable_view(facts.empty_ledger('300857'), analysis_date=DAY,
                               knowledge_cutoff=CUTOFF, evidence={})
    monkeypatch.setattr(schema, 'read_dossier_text', lambda code: text)
    monkeypatch.setattr(schema, 'current_fact_view', lambda code, text: copy.deepcopy(view))
    assert '0/0' in schema.injectable_summary('300857')
    assert '(内容)' not in schema.dossier_sections('300857', ('§1', '§2'))


def test_old_dynamic_cannot_refresh_by_relabeling_date_or_kind():
    original = dict(fact('DYNAMIC'), snapshot_at='2026-09-29T11:00:00+08:00')
    ledger = facts.empty_ledger('600519')
    ledger['facts'] = [original]
    for proposed in (dict(original, snapshot_at='2026-09-30T11:00:00+08:00'),
                     dict(original, kind='STABLE', snapshot_at=None)):
        result = facts.apply_updates(ledger, [proposed], changed_at=CUTOFF,
            analysis_date=DAY, knowledge_cutoff=CUTOFF, evidence=proof(original))
        assert result['facts'] == [original]
        assert result['history'][-1]['change'] in {'CONFLICT', 'EXPIRED', 'MISSING_SOURCE'}


def test_expiry_cannot_be_removed_with_same_source():
    old = dict(fact(), expires_at=CUTOFF)
    ledger = facts.empty_ledger('600519')
    ledger['facts'] = [old]
    new = facts.apply_updates(ledger, [dict(old, expires_at=None)], changed_at=CUTOFF,
        analysis_date=DAY, knowledge_cutoff=CUTOFF, evidence=proof(old))
    assert new['facts'] == [old]
    assert new['history'][-1]['change'] == 'CONFLICT'


def test_failed_attempt_source_is_not_fact_evidence(tmp_path):
    import json

    from tests.news.test_frozen_claim_sources import frame, setup_claim
    handle, _, _, _ = setup_claim(tmp_path)
    handle.workspace = tmp_path
    owner = tmp_path / 'session/tasks.json'
    owner.parent.mkdir()
    owner.write_text(json.dumps({'schema_version': 1, 'engine': handle.engine, 'run_id': handle.run_id,
        'tasks': {'intel.600000': {'state': 'FAILED', 'attempt': 1,
        'spec': {'task_id': 'intel.600000', 'subject': '600000', 'dependencies': []}}}}))
    assert facts.evidence_from_capsule(handle, frame(), subject='600000') == {}


@pytest.mark.parametrize('state,attempt,consumer', [('FAILED', 1, None), ('SUCCEEDED', 2, None), ('PENDING', 1, 'intel.600000')])
def test_fact_source_requires_accepted_or_running_current_attempt(tmp_path, state, attempt, consumer):
    import json

    from tests.news.test_frozen_claim_sources import frame, setup_claim
    handle, _, _, _ = setup_claim(tmp_path)
    handle.workspace = tmp_path
    owner = tmp_path / 'session/tasks.json'
    owner.parent.mkdir()
    owner.write_text(json.dumps({'schema_version': 1, 'engine': handle.engine, 'run_id': handle.run_id,
        'tasks': {'intel.600000': {'state': state, 'attempt': attempt,
        'spec': {'task_id': 'intel.600000', 'subject': '600000', 'dependencies': []}}}}))
    assert facts.evidence_from_capsule(handle, frame(), subject='600000', consumer_task_id=consumer) == {}


def test_dynamic_same_instant_uses_exchange_day_not_utc_date():
    row = dict(fact('DYNAMIC'), snapshot_at='2026-09-29T16:30:00+00:00')
    assert check(row)['reusable']
    assert not facts.evaluate_fact(row, analysis_date=DAY, knowledge_cutoff=CUTOFF,
        evidence=proof(row), timezone='UTC')['reusable']
