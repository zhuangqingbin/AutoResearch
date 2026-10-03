import pytest


def record(scope, event, tokens, *, cached=0, session=None):
    return {'scope_id':scope,'session_ref':session or scope,'native_record_id':event,
        'identity_basis':'NATIVE_EVENT_ID','adapter_version':'fixture-native-v1',
        'usage_semantics':'DELTA_SESSION_LOCAL','source_refs':[{'path':'fixture/'+event,'sha256':'a'*64}],
        'metrics':{'input_tokens':tokens,'output_tokens':10,'cached_input_tokens':cached,
                   'cache_creation_tokens':0,'reasoning_output_tokens':5},
        'observed_model':'actual','observed_effort':None}


def scopes(*names):
    return [{'scope_id':name,'complete':True,'state':'FAILED' if name=='A' else 'SUCCEEDED',
        'requested':{'model':'requested'},'resolved':{'model':'resolved'}} for name in names]


def test_root_children_duplicate_archive_and_subsets_count_once():
    from autoresearch.session_agent.metering import join_usage_records
    root,a,b = record('root','r',100),record('A','a',200,cached=150),record('B','b',300)
    duplicate = {**a,'source_refs':[{'path':'second-archive','sha256':'b'*64}]}
    result = join_usage_records([root,a,duplicate,b],expected_scopes=scopes('root','A','B'))
    assert result['metrics']['input_tokens']['total'] == 600
    assert result['metrics']['cached_input_tokens']['total'] == 150
    assert result['metrics']['output_tokens']['total'] == 30
    assert result['metrics']['reasoning_output_tokens']['total'] == 15
    assert len(result['unique_usage_records']) == 3
    assert result['task_attempts']['A']['input_tokens'] == 200
    assert result['root_only']['input_tokens'] == 100


def test_missing_failed_attempt_preserves_observed_lower_bound():
    from autoresearch.session_agent.metering import join_usage_records
    result = join_usage_records([record('root','r',100),record('A','a',200)],
        expected_scopes=scopes('root','A','B'))
    assert result['metrics']['input_tokens']['total'] is None
    assert result['metrics']['input_tokens']['observed_total'] == 300
    assert result['measurement_coverage']['missing_scopes'] == ['B']


def test_equal_content_with_distinct_native_ids_is_not_deduplicated():
    from autoresearch.session_agent.metering import join_usage_records
    result = join_usage_records([record('root','r1',100),record('root','r2',100)],expected_scopes=scopes('root'))
    assert result['metrics']['input_tokens']['total'] == 200


@pytest.mark.parametrize('change', [
    {'identity_basis':'UNVERIFIED'}, {'usage_semantics':'CUMULATIVE_UNKNOWN_RESET'},
    {'usage_semantics':'INCLUDES_CHILDREN'},
])
def test_unverified_identity_and_parent_overlap_never_get_total(change):
    from autoresearch.session_agent.metering import join_usage_records
    result = join_usage_records([{**record('root','r',100),**change}],expected_scopes=scopes('root'))
    assert result['metrics']['input_tokens']['total'] is None


def test_wall_time_is_interval_union_and_unknown_segment_stays_unknown():
    from autoresearch.session_agent.metering import join_usage_records
    intervals = [{'scope_id':'root','start':'2026-09-01T00:00:00Z','end':'2026-09-01T00:00:10Z'},
                 {'scope_id':'A','start':'2026-09-01T00:00:05Z','end':'2026-09-01T00:00:15Z'}]
    result = join_usage_records([record('root','r',100),record('A','a',200)],
        expected_scopes=scopes('root','A'),intervals=intervals)
    assert result['metrics']['wall_seconds']['total'] == 15
    result = join_usage_records([record('root','r',100),record('A','a',200)],
        expected_scopes=scopes('root','A'),intervals=intervals[:1])
    assert result['metrics']['wall_seconds']['total'] is None
    assert result['metrics']['wall_seconds']['observed_total'] == 10


def codex_rows(session='native', *, reset=False):
    rows = [{'ordinal':0,'type':'session_meta','timestamp':'2026-09-01T00:00:00Z','payload':{'id':session}},
            {'ordinal':1,'type':'turn_context','timestamp':'2026-09-01T00:00:01Z','payload':{'model':'actual','effort':'high'}}]
    for ordinal,tokens in [(2,100),(3,50 if reset else 300)]:
        rows.append({'ordinal':ordinal,'type':'event_msg','timestamp':f'2026-09-01T00:00:0{ordinal}Z',
            'payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':tokens,
             'output_tokens':tokens//10,'cached_input_tokens':tokens//2,'cache_write_input_tokens':0,
             'reasoning_output_tokens':tokens//20}}}})
    rows.append({'ordinal':4,'type':'event_msg','timestamp':'2026-09-01T00:00:04Z','payload':{'type':'task_complete'}})
    return rows


def test_cumulative_native_counter_delta_and_reset_are_explicit():
    from autoresearch.session_agent.metering import join_usage_records, native_usage_records
    kw = {'scope_id': 'root', 'session_ref': 'native','start_ordinal': 0,'end_ordinal': 4,
              'source_ref': {'path':'fixture','sha256':'a'*64}}
    records, interval, complete = native_usage_records('codex',codex_rows(),**kw)
    assert complete
    result = join_usage_records(records,expected_scopes=scopes('root'),intervals=[interval])
    assert result['metrics']['input_tokens']['total'] == 300
    assert result['models']['root']['observed'] == ['actual']
    reset, _, complete = native_usage_records('codex',codex_rows(reset=True),**kw)
    result = join_usage_records(reset,expected_scopes=scopes('root'))
    assert result['metrics']['input_tokens']['total'] is None
    assert result['metrics']['input_tokens']['observed_total'] == 100


def test_same_session_other_run_prefix_is_only_a_counter_baseline():
    from autoresearch.session_agent.metering import join_usage_records, native_usage_records
    records, _, _ = native_usage_records('codex',codex_rows(),scope_id='root',session_ref='native',
        start_ordinal=3,end_ordinal=4,source_ref={'path':'fixture','sha256':'a'*64})
    result = join_usage_records(records,expected_scopes=scopes('root'))
    assert result['metrics']['input_tokens']['total'] == 200
    wrong, _, _ = native_usage_records('codex',codex_rows(),scope_id='root',session_ref='different-session',
        start_ordinal=0,end_ordinal=4,source_ref={'path':'fixture','sha256':'a'*64})
    assert not wrong


def test_diagnostics_cost_joins_bound_root_and_child_native_archives(tmp_path):
    import json

    from autoresearch.common.atomic import atomic_write_json, sha256_file
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    from autoresearch.session_agent.host_evidence import bind_task_transcript
    from autoresearch.trace.transcripts.snapshot import capture_snapshot
    from tests.forensics.test_host_evidence import _running_case
    handle,_,_ = _running_case(tmp_path)
    child = tmp_path / 'child.jsonl'
    child.write_text('\n'.join(json.dumps(r) for r in codex_rows('child'))+'\n')
    bind_task_transcript(handle.run_id,'inference.one',1,child,context_ref='child',parent_context_ref='root',
        session_ref='child',start_ordinal=0,end_ordinal=4,context_source='SUBAGENT',handle_loader=lambda _:handle)
    root_source = tmp_path / 'root.jsonl'
    root_source.write_text('\n'.join(json.dumps(r) for r in codex_rows('root'))+'\n')
    snapshot = capture_snapshot(root_source,engine='codex')
    archive = handle.capsule / 'root.jsonl.gz'
    archive.write_bytes(snapshot.archive_bytes)
    registration = handle.capsule / 'identity/session/host_evidence.json'
    value = json.loads(registration.read_text())
    value['session_ref']='root'
    value['main_transcript'] = {'status':'REGISTERED','source_path':str(root_source),'start_ordinal':0}
    atomic_write_json(registration,value)
    from autoresearch.session_agent.host_evidence import capture_activity
    capture_activity(handle,event='root_bound')
    atomic_write_json(handle.capsule / 'evidence/main_host.json', {'engine':'codex','run_id':handle.run_id,
        'status':'PRESENT','start_ordinal':0,'end_ordinal':4,'raw_path':'root.jsonl.gz','archive_sha256':sha256_file(archive)})
    result = build_run_diagnostics(handle.capsule)
    assert result['cost']['metrics']['input_tokens']['total'] == 600, result['cost']
    assert result['cost']['metrics']['wall_seconds']['total'] == 4
    assert result['cost']['models']['inference.one:a1']['observed'] == ['actual']


def test_native_archive_retains_only_numeric_usage_and_redacts_secrets(tmp_path):
    import json

    from autoresearch.trace.transcripts.snapshot import (
        capture_snapshot,
        snapshot_from_archive_bytes,
    )
    rows = codex_rows()
    usage = rows[2]['payload']['info']['total_token_usage']
    usage['api_token'] = 'secret-sensitive-value'
    usage['output_tokens'] = 'not-a-number-secret'
    usage['cached_input_tokens'] = -1
    usage['cache_write_input_tokens'] = True
    usage['other'] = {'input_tokens':456}
    path = tmp_path / 'native.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    snap = capture_snapshot(path,engine='codex')
    archived = snapshot_from_archive_bytes(snap.archive_bytes,engine='codex',path=path)
    raw = archived.rows[2]['payload']['info']['total_token_usage']
    assert raw['input_tokens'] == 100
    assert set(raw) == {'input_tokens','reasoning_output_tokens'}
    assert 'secret-sensitive-value' not in str(archived.rows)
    assert 'not-a-number-secret' not in str(archived.rows)


def test_overlapping_task_attribution_does_not_duplicate_stage_totals():
    from autoresearch.session_agent.metering import join_usage_records
    a = record('A','same',200,session='native')
    b = {**a,'scope_id':'B'}
    result = join_usage_records([a,b],expected_scopes=scopes('A','B'))
    assert result['metrics']['input_tokens']['total'] == 200
    assert result['task_attempts']['A']['input_tokens'] is None
    assert result['task_attempts']['B']['input_tokens'] is None
    assert result['unattributed']


def test_native_parent_inclusive_and_missing_optional_counters_are_unknown():
    from autoresearch.session_agent.metering import join_usage_records, native_usage_records
    rows = codex_rows()
    for row in rows[2:4]:
        row['payload']['info']['includes_children'] = True
    records,_,_ = native_usage_records('codex',rows,scope_id='root',session_ref='native',
        start_ordinal=0,end_ordinal=4,source_ref={'path':'fixture','sha256':'a'*64})
    result = join_usage_records(records,expected_scopes=scopes('root'))
    assert result['metrics']['input_tokens']['total'] is None
    assert all(r['reason']=='PARENT_INCLUDES_CHILDREN' for r in result['unique_usage_records'])


def test_run_usage_efficiency_reader_uses_complete_run_scope_only():
    from autoresearch.research.efficiency_baseline import run_usage_row
    from autoresearch.session_agent.metering import join_usage_records
    cost = join_usage_records([record('root','r',100)],expected_scopes=scopes('root'))
    diagnostic = {'schema_version':'research-run-diagnostics-v1','identity':{'engine':'codex','run_id':'run'},'cost':cost}
    kwargs = {'workflow': 'scan','mode': 'FULL','real_run': True,'source': 'native','quality_passed': True,'run_complete': True}
    row = run_usage_row(diagnostic,**kwargs)
    assert row['input_tokens'] == 100
    assert row['input_basis'] == 'INCLUDES_CACHED_INPUT'
    assert row['net_runtime_seconds'] is None
    cost['metrics']['input_tokens'].update(total=None,status='PARTIAL')
    cost['measurement_coverage']['status']='UNKNOWN'
    row = run_usage_row(diagnostic,**kwargs)
    assert row['coverage'] == 'INCOMPLETE'
    assert row['input_tokens'] is None
    assert row['metric_coverage']['input_tokens']['observed_total'] == 100


def test_root_capture_bad_tail_never_has_complete_run_cost(tmp_path):
    import json

    from autoresearch.common.atomic import atomic_write_json
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    from autoresearch.session_agent.host_evidence import capture_main_context
    from tests.forensics.test_host_evidence import _running_case
    handle,_,_ = _running_case(tmp_path)
    source = tmp_path / 'root.jsonl'
    source.write_text('\n'.join(json.dumps(r) for r in codex_rows('root'))+'\nBROKEN\n')
    path = handle.capsule / 'identity/session/host_evidence.json'
    value = json.loads(path.read_text())
    value.update(session_ref='root',main_transcript={
        'status':'REGISTERED','source_path':str(source),'start_ordinal':0})
    atomic_write_json(path,value)
    capture_main_context(handle)
    cost = build_run_diagnostics(handle.capsule)['cost']
    assert 'root' in cost['measurement_coverage']['incomplete_scopes']
    assert cost['metrics']['input_tokens']['observed_total'] == 300


def test_live_root_prefix_has_observed_cost_without_claiming_run_total(tmp_path):
    import json

    from autoresearch.common.atomic import atomic_write_json
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    from autoresearch.session_agent.host_evidence import capture_activity
    from tests.forensics.test_host_evidence import _running_case
    handle,_,_ = _running_case(tmp_path)
    source = tmp_path / 'root.jsonl'
    source.write_text('\n'.join(json.dumps(r) for r in codex_rows('root')[:-1])+'\n')
    path = handle.capsule / 'identity/session/host_evidence.json'
    value = json.loads(path.read_text())
    value.update(session_ref='root',main_transcript={
        'status':'REGISTERED','source_path':str(source),'start_ordinal':0})
    atomic_write_json(path,value)
    capture_activity(handle,event='poll')
    cost = build_run_diagnostics(handle.capsule)['cost']
    assert cost['metrics']['input_tokens']['observed_total'] == 300
    assert cost['metrics']['input_tokens']['total'] is None


def test_conflicting_native_replay_cannot_be_complete():
    from autoresearch.session_agent.metering import join_usage_records
    first = record('root','same',100)
    second = record('root','same',200)
    result = join_usage_records([first,second],expected_scopes=scopes('root'))
    assert result['metrics']['input_tokens']['total'] is None
    assert result['overlap_resolution'][0]['reason'] == 'CONFLICTING_NATIVE_RECORD_REPLAY'


def test_claude_native_cached_input_is_inclusive_and_actual_model_unknown():
    from autoresearch.session_agent.metering import join_usage_records, native_usage_records
    rows = [{'ordinal':0,'type':'assistant','sessionId':'native','timestamp':'2026-09-01T00:00:00Z',
        'message':{'id':'message-one','stop_reason':'end_turn','usage':{
            'input_tokens':100,'cache_read_input_tokens':150,'cache_creation_input_tokens':50,
            'output_tokens':20}}}]
    records, interval, complete = native_usage_records('claude',rows,scope_id='root',session_ref='native',
        start_ordinal=0,end_ordinal=0,source_ref={'path':'fixture','sha256':'a'*64})
    assert complete
    result = join_usage_records(records,expected_scopes=scopes('root'),intervals=[interval])
    assert result['metrics']['input_tokens']['total'] == 300
    assert result['metrics']['reasoning_output_tokens']['total'] is None
    assert result['models']['root']['observed'] is None
    assert result['models']['root']['requested'] == {'model':'requested'}
    assert result['models']['root']['resolved'] == {'model':'resolved'}


def test_missing_native_counter_field_never_becomes_zero():
    from autoresearch.session_agent.metering import join_usage_records, native_usage_records
    rows = codex_rows()
    for row in rows[2:4]:
        del row['payload']['info']['total_token_usage']['cache_write_input_tokens']
    records,_,_ = native_usage_records('codex',rows,scope_id='root',session_ref='native',
        start_ordinal=0,end_ordinal=4,source_ref={'path':'fixture','sha256':'a'*64})
    result = join_usage_records(records,expected_scopes=scopes('root'))
    assert result['metrics']['input_tokens']['total'] == 300
    assert result['metrics']['cache_creation_tokens']['total'] is None
    assert result['metrics']['cache_creation_tokens']['observed_total'] is None


@pytest.mark.parametrize('engine',['codex','claude'])
def test_missing_whole_native_usage_record_keeps_total_unknown(engine):
    from autoresearch.research.efficiency_baseline import run_usage_row
    from autoresearch.session_agent.metering import join_usage_records, native_usage_records
    if engine == 'codex':
        rows = codex_rows()
        rows[3]['payload']['info']['total_token_usage']='[REDACTED]'
    else:
        rows = [
            {'ordinal':0,'type':'assistant','sessionId':'native','timestamp':'2026-09-01T00:00:00Z',
             'message':{'id':'first','stop_reason':'tool_use','usage':{'input_tokens':100,
                 'cache_read_input_tokens':0,'cache_creation_input_tokens':0,'output_tokens':10}}},
            {'ordinal':1,'type':'assistant','sessionId':'native','timestamp':'2026-09-01T00:00:01Z',
             'message':{'id':'second','stop_reason':'end_turn'}}]
    records,interval,complete = native_usage_records(engine,rows,scope_id='root',session_ref='native',
        start_ordinal=0,end_ordinal=len(rows)-1,source_ref={'path':'fixture','sha256':'a'*64})
    cost = join_usage_records(records,expected_scopes=[{**scopes('root')[0],'complete':complete}],intervals=[interval])
    assert cost['metrics']['input_tokens']['total'] is None
    assert cost['metrics']['input_tokens']['observed_total'] == 100
    diagnostic = {'schema_version':'research-run-diagnostics-v1','identity':{'engine':engine,'run_id':'r'},'cost':cost}
    row = run_usage_row(diagnostic,workflow='scan',mode='FULL',real_run=True,source='native',
        quality_passed=True,run_complete=True)
    assert row['coverage'] == 'INCOMPLETE'


def test_claude_stream_same_message_uses_latest_record_once():
    from autoresearch.session_agent.metering import join_usage_records, native_usage_records
    rows = [{'ordinal':0,'type':'assistant','sessionId':'native','timestamp':'2026-09-01T00:00:00Z',
        'message':{'id':'same','usage':{'input_tokens':100,'output_tokens':1,
            'cache_read_input_tokens':0,'cache_creation_input_tokens':0}}},
        {'ordinal':1,'type':'assistant','sessionId':'native','timestamp':'2026-09-01T00:00:01Z',
        'message':{'id':'same','stop_reason':'end_turn','usage':{'input_tokens':100,'output_tokens':10,
            'cache_read_input_tokens':0,'cache_creation_input_tokens':0}}}]
    records,_,_ = native_usage_records('claude',rows,scope_id='root',session_ref='native',
        start_ordinal=0,end_ordinal=1,source_ref={'path':'fixture','sha256':'a'*64})
    result = join_usage_records(records,expected_scopes=scopes('root'))
    assert result['metrics']['input_tokens']['total'] == 100
    assert result['metrics']['output_tokens']['total'] == 10
    assert len(records) == 1
