"""Sector event eligibility uses complete claims and immutable public source clocks."""

import pytest

from autoresearch.common.atomic import atomic_write_json
from autoresearch.news.material_claims import bind_material_claim, verify_material_claims
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.sector_terrain import source_binding
from autoresearch.trace.source_receipts import record_response
from tests.news.test_frozen_claim_sources import CUTOFF, TEXT, event, frame, record, setup_claim


def fixture(tmp_path, monkeypatch, *, published=CUTOFF, available=CUTOFF, precision='second'):
    # These tests isolate receipt clocks, claim-version merging and attempt scope.
    # Legacy provider labels no longer authorize semantics; end-to-end rejection
    # is covered separately in test_terrain_session and the news producer tests.
    monkeypatch.setattr('autoresearch.news.material_claims.evaluate_material_claim',
                        lambda *args, **kwargs: {'verdict': 'PASS'})
    handle, _, _, _ = setup_claim(tmp_path)
    # Use a fresh logical claim/receipt so timestamp variants do not rewrite evidence.
    timing = {'published_at': published, 'first_available_at': available,
              'received_at': '2026-09-02T07:00:01Z', 'timestamp_precision': {
                  'published_at': precision if published else None,
                  'first_available_at': precision if available else None, 'received_at': 'second'}}
    source = record_response(handle, {'engine': handle.engine, 'run_id': handle.run_id,
        'task_id': 'intel.600000', 'attempt': 1, 'provider': 'host_tool', 'endpoint': 'web.fetch',
        'normalized_params': {'url': 'https://issuer/sector'}, 'started_at': '2026-09-02T07:00:00Z',
        'ended_at': '2026-09-02T07:00:01Z', 'as_of': '2026-09-01', 'available_at': available,
        'consumer_refs': [], 'source_timing': timing, 'source_status': 'CURRENT', 'supersedes_receipt_ids': []}, TEXT)
    review = record(handle, {'schema_version': 1, 'source_receipt_id': source['receipt_id'],
        'source_hash': source['payload_hash'], 'event': event(), 'checked_fields': list(event()),
        'reviewer': None}, provider='deterministic', endpoint='claim_fields.v1')
    args = {'engine': handle.engine, 'run_id': handle.run_id, 'task_id': 'intel.600000', 'attempt': 1,
        'claim_id': 'sector-event', 'statement': TEXT, 'source_receipt_ids': [source['receipt_id']],
        'quote_refs': [{'blob_hash': source['payload_hash'], 'start': 0, 'end': len(TEXT), 'text': TEXT}],
        'calculation_ids': [], 'claim_event': event(), 'review_receipt_ids': [review['receipt_id']]}
    bind_material_claim(handle.capsule, **args)
    handle.workspace = tmp_path
    handle.staging = tmp_path / 'staging'
    handle.staging.mkdir()
    path = handle.staging / 'frame.json'
    atomic_write_json(path, frame())
    artifacts.register_artifact(handle, 'research.frame', path, 'READ')
    owner = tmp_path / 'session/tasks.json'
    atomic_write_json(owner, {'run_id': handle.run_id, 'engine': handle.engine})
    monkeypatch.setattr('autoresearch.session_agent.store.read_entries', lambda path: {
        'intel.600000': {'state': 'SUCCEEDED', 'attempt': 1, 'spec': {'subject': '半导体'}}})
    value = {'claim': TEXT, 'source_observation_id': source['receipt_id'],
        'source_text_sha256': source['payload_hash'], 'source_url': 'https://issuer/sector',
        'quote': TEXT, 'published_at': published or CUTOFF, 'available_at': available or CUTOFF}
    return handle, value, args


def test_sector_binding_merges_conflicting_versions_before_admission(tmp_path, monkeypatch):
    handle, value, args = fixture(tmp_path, monkeypatch)
    assert source_binding(handle)(value, {'knowledge_cutoff': CUTOFF, 'industry': '半导体'})['verdict'] == 'PASS'
    bind_material_claim(handle.capsule, **dict(args, claim_event=dict(event(), amount_value='100000000')))
    aggregate = verify_material_claims(handle.capsule, decision_frame=frame())
    assert next(row for row in aggregate['results'] if row['claim_id'] == 'sector-event')['verdict'] == 'UNKNOWN'
    assert source_binding(handle)(value, {'knowledge_cutoff': CUTOFF, 'industry': '半导体'})['verdict'] == 'UNKNOWN'


@pytest.mark.parametrize('field', ['published_at', 'available_at'])
def test_sector_binding_rejects_model_invented_early_clock(tmp_path, monkeypatch, field):
    handle, value, _ = fixture(tmp_path, monkeypatch)
    value[field] = '2000-01-01T00:00:00+08:00'
    assert source_binding(handle)(value, {'knowledge_cutoff': CUTOFF, 'industry': '半导体'})['verdict'] == 'UNKNOWN'


@pytest.mark.parametrize('missing', ['published', 'available'])
def test_unknown_public_clock_cannot_borrow_received_timestamp(tmp_path, monkeypatch, missing):
    handle, value, _ = fixture(tmp_path, monkeypatch, **{missing: None})
    assert source_binding(handle)(value, {'knowledge_cutoff': CUTOFF, 'industry': '半导体'})['verdict'] == 'UNKNOWN'


def test_sector_binding_retains_source_precision_and_latest_possible_cutoff(tmp_path, monkeypatch):
    stamp = '2026-09-02T14:44:00+08:00'
    handle, value, _ = fixture(tmp_path, monkeypatch, published=stamp, available=stamp, precision='minute')
    result = source_binding(handle)(value, {'knowledge_cutoff': CUTOFF, 'industry': '半导体'})
    assert result['verdict'] == 'PASS'
    assert result['source_timing']['timestamp_precision']['published_at'] == 'minute'
    assert result['source_timing']['published_at'] == stamp


def test_minute_precision_at_cutoff_is_not_known_by_cutoff(tmp_path, monkeypatch):
    handle, value, _ = fixture(tmp_path, monkeypatch, precision='minute')
    assert source_binding(handle)(value, {'knowledge_cutoff': CUTOFF, 'industry': '半导体'})['verdict'] == 'UNKNOWN'


def test_source_precision_survives_deterministic_render_and_replay(tmp_path, monkeypatch):
    from autoresearch.sector.terrain import digest, event_request, render_terrain
    from autoresearch.session_agent.sector_terrain import replay_terrain
    from tests.sector.test_deterministic_terrain import marked_candidate

    stamp = '2026-09-02T14:44:00+08:00'
    handle, value, _ = fixture(tmp_path, monkeypatch, published=stamp, available=stamp, precision='minute')
    pack = marked_candidate(tmp_path / 'pack')
    request = event_request(pack, max_queries=1, knowledge_cutoff=CUTOFF)
    value['reason_id'] = 'policy-1'
    supplement = {'schema_version': 1, 'pack_sha256': digest(pack), 'events': [value],
                  'unresolved_reason_ids': []}
    binding = source_binding(handle)
    text = render_terrain(pack, request=request, supplement=supplement, bind_claim=binding)
    assert f'公开: {stamp}（精度 minute）' in text
    assert f'可得: {stamp}（精度 minute）' in text
    snapshot = {'schema_version': 2, 'pack': pack, 'request': request, 'supplement': supplement,
                'stable_facts': [], 'bound_event_hashes': [digest(value)],
                'bound_event_timings': {digest(value): binding(value, request)['source_timing']}}
    assert replay_terrain(snapshot)[0] == text
    snapshot['bound_event_timings'][digest(value)]['published_at'] = '2000-01-01T00:00:00+08:00'
    with pytest.raises(ValueError, match='timing differs'):
        replay_terrain(snapshot)


def test_failed_late_attempt_does_not_conflict_with_accepted_event(tmp_path, monkeypatch):
    handle, value, args = fixture(tmp_path, monkeypatch)
    bind_material_claim(handle.capsule, **dict(args, attempt=2, claim_event=dict(event(), amount_value='1')))
    assert source_binding(handle)(value, {'knowledge_cutoff': CUTOFF, 'industry': '半导体'})['verdict'] == 'PASS'
