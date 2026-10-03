import json

from autoresearch.session_agent import evaluation
from autoresearch.session_agent.__main__ import main
from tests.forensics.test_acceptance_claims import _matrix


def test_empty_status_has_all_fixed_cells_and_does_not_enable_default():
    assert hasattr(evaluation, 'acceptance_status')
    result = evaluation.acceptance_status([])
    assert result['required_count'] == 28
    assert len(result['missing_real_sessions']) == 28
    assert len(result['workflows']) == 5
    assert result['software_status'] == 'UNKNOWN'
    assert result['real_session_status'] == 'INCOMPLETE'
    assert result['default_status'] == 'PILOT'


def test_complete_synthetic_status_remains_pilot():
    assert hasattr(evaluation, 'acceptance_status')
    records = sum((_matrix(w, evidence_kind='SYNTHETIC') for w in evaluation._ACCEPTANCE_SCENARIOS), [])
    value = evaluation.acceptance_status(records)
    assert value['accepted_count'] == 0
    assert value['default_status'] == 'PILOT'
    assert len(value['missing_real_sessions']) == 28


def test_cli_empty_status_is_read_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('AUTORESEARCH_ENGINE', 'codex')
    monkeypatch.setattr(evaluation.ws, 'reports_root', lambda: tmp_path)
    before = set(tmp_path.rglob('*'))
    assert main(['acceptance-status']) == 0
    assert json.loads(capsys.readouterr().out)['required_count'] == 28
    assert set(tmp_path.rglob('*')) == before


def test_cli_explicit_imported_records_remain_pilot_and_reject_path_escape(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('AUTORESEARCH_ENGINE', 'codex')
    monkeypatch.setattr(evaluation.ws, 'reports_root', lambda: tmp_path)
    audit = tmp_path / '_acceptance'
    audit.mkdir()
    records = audit / 'records.json'
    records.write_text(json.dumps(_matrix('macro-research', evidence_kind='REAL_SESSION')))
    assert main(['acceptance-status', '--records-file', str(records), '--evidence-root', str(audit / 'proofs')]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['default_status'] == 'PILOT'
    assert any('PROOF_MISSING' in e for e in value['invalid_records'])
    outside = tmp_path / 'outside.json'
    outside.write_text('[]')
    link = audit / 'escape.json'
    link.symlink_to(outside)
    assert main(['acceptance-status', '--records-file', str(link)]) != 0
    assert 'audit root' in capsys.readouterr().out


def test_status_keeps_duplicate_drill_and_invalid_proof_failures(tmp_path):
    assert hasattr(evaluation, 'acceptance_status')
    records = _matrix('macro-research', evidence_kind='REAL_SESSION_DRILL')
    records.append(records[0])
    value = evaluation.acceptance_status(records, evidence_root=tmp_path)
    assert any('DRILL_NOT_ALLOWED' in e for e in value['invalid_records'])
    assert any('DUPLICATE_RECORD' in e for e in value['invalid_records'])
    assert len(value['missing_real_sessions']) == 28


def test_redirected_audit_root_is_rejected(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setattr(evaluation.ws, 'reports_root', lambda: tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / '_acceptance').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='redirect'):
        evaluation.acceptance_status_from_paths()
