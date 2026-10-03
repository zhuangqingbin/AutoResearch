import json
from pathlib import Path

import pytest

from autoresearch.common.atomic import atomic_write_json
from tests.forensics.test_host_evidence import _rollout, _running_case


def test_abandoned_retry_and_undispatched_tasks_remain_visible(tmp_path):
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    from autoresearch.session_agent.evidence import freeze_abandonment
    handle, _, claim = _running_case(tmp_path)
    task = {'task_id':'inference.one', 'kind':'INFERENCE'}
    freeze_abandonment(handle, task, 1, 'host interrupted with no response')
    record = handle.capsule / 'evidence/attempt_records/inference.one/a2'
    atomic_write_json(record / 'claim.json', {'task_id':'inference.one','attempt':2})
    atomic_write_json(record / 'accepted_receipt.json', {'task_id':'inference.one','attempt':2})
    result = build_run_diagnostics(handle.capsule)
    assert result['schema_version'] == 'research-run-diagnostics-v1'
    assert [r['state'] for r in result['attempts']] == ['ABANDONED','SUCCEEDED']
    assert result['attempts'][0]['activity_visibility'] == 'UNKNOWN'
    assert result['attempts'][0]['exemption_reason'] == 'host interrupted with no response'
    assert result['coverage']['known_attempt_count'] == 2
    assert result['coverage']['actual_model_calls'] is None
    assert result['cost']['metrics']['input_tokens']['total'] is None


def test_diagnostic_never_changes_sealed_capsule_and_rejects_cross_engine(tmp_path):
    from autoresearch.research.run_diagnostics import build_run_diagnostics, render_markdown
    handle, _, _ = _running_case(tmp_path)
    atomic_write_json(handle.capsule / 'verification/ROOT.json', {'sealed':True})
    before = {str(p):p.read_bytes() for p in handle.capsule.rglob('*') if p.is_file()}
    value = build_run_diagnostics(handle.capsule)
    assert {str(p):p.read_bytes() for p in handle.capsule.rglob('*') if p.is_file()} == before
    assert 'UNKNOWN' in render_markdown(value)
    plan = handle.capsule / 'identity/session/plan.json'
    data = json.loads(plan.read_text())
    data['engine'] = 'claude'
    plan.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='engine|plan'):
        build_run_diagnostics(handle.capsule)


def test_incremental_capture_retains_truncated_tail_and_seal_guard(tmp_path):
    from autoresearch.session_agent.host_evidence import capture_activity
    handle, _, _ = _running_case(tmp_path)
    source = _rollout(tmp_path)
    with source.open('ab') as stream:
        stream.write(b'\n{"unfinished":')
    registration = handle.capsule / 'identity/session/host_evidence.json'
    current = json.loads(registration.read_text())
    current['main_transcript'] = {'status':'REGISTERED','source_path':str(source),'start_ordinal':0}
    registration.write_text(json.dumps(current))
    result = capture_activity(handle, task_id='inference.one', attempt=1, event='poll')
    assert result['activity_visibility'] == 'PARTIAL'
    assert result['trailing_partial_bytes'] > 0
    assert result['end_ordinal'] is not None
    atomic_write_json(handle.capsule / 'verification/ROOT.json', {'sealed':True})
    with pytest.raises(ValueError, match='sealed'):
        capture_activity(handle, task_id='inference.one', attempt=1, event='late')


def test_unbound_outcome_or_wrong_run_ref_is_rejected(tmp_path):
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    handle, _, _ = _running_case(tmp_path)
    path = tmp_path / 'outcome.json'
    path.write_text(json.dumps({'engine':'codex','run_id':'other'}))
    with pytest.raises(ValueError, match='outcome.*identity'):
        build_run_diagnostics(handle.capsule, outcome_refs=[path])


def test_lifecycle_freeze_records_native_capability_gaps(tmp_path):
    from autoresearch.session_agent.evidence import freeze_failure
    handle, _, _ = _running_case(tmp_path)
    freeze_failure(handle, {'task_id':'inference.one'}, 1, {'message':'failed'})
    rows = [json.loads(p.read_text()) for p in (handle.capsule / 'evidence/activity').glob('*.json')]
    assert any(r['event'] == 'claim' and r['activity_visibility'] == 'UNKNOWN' for r in rows)
    assert any(r['event'] == 'failure' for r in rows)


def test_missing_tool_response_cannot_be_fully_observed(tmp_path):
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    from autoresearch.session_agent.host_evidence import bind_task_transcript
    handle, _, _ = _running_case(tmp_path)
    source = tmp_path / 'native.jsonl'
    source.write_text('\n'.join(json.dumps(r) for r in [
        {'ordinal':0,'type':'turn_context','payload':{'model':'actual'}},
        {'ordinal':1,'type':'response_item','payload':{'type':'function_call','name':'web_search','call_id':'missing','arguments':'{}'}},
        {'ordinal':2,'type':'event_msg','payload':{'type':'task_complete'}},
    ])+'\n')
    bind_task_transcript(handle.run_id,'inference.one',1,source,context_ref='root',parent_context_ref=None,
        session_ref='session',start_ordinal=0,end_ordinal=2,context_source='MAIN',handle_loader=lambda _:handle)
    row = build_run_diagnostics(handle.capsule)['attempts'][0]
    assert row['activity_visibility'] == 'PARTIAL'
    assert row['transcripts'][0]['missing_tool_results'] == ['missing']


def test_frozen_expansions_count_undispatched_tasks_and_write_derived_view(tmp_path):
    from autoresearch.contracts.session_plan import expansion_hash, plan_hash
    from autoresearch.research.run_diagnostics import build_run_diagnostics, write_diagnostics
    handle, _, _ = _running_case(tmp_path)
    path = handle.capsule / 'identity/session/plan.json'
    plan = json.loads(path.read_text())
    plan['task_templates'] = [{'template_id':'scan.l4','expander':'scan.l4',
        'depends_on':['inference.one'],'allowed_roles':['stock.card']}]
    plan['plan_hash'] = plan_hash(plan)
    atomic_write_json(path,plan)
    expansion = {'schema_version':1,'expansion_id':'','plan_hash':plan['plan_hash'],
        'template_id':'scan.l4','input_artifacts':[{'artifact_id':'scan.finalists','sha256':'e'*64}],
        'tasks':[{**plan['tasks'][0],'task_id':'inference.two','dependencies':['inference.one']}], 'expansion_hash':''}
    expansion['expansion_hash'] = expansion_hash(expansion)
    expansion['expansion_id'] = 'scan.l4-'+expansion['expansion_hash'][:16]
    atomic_write_json(handle.capsule / 'identity/session/expansions' / (expansion['expansion_id']+'.json'),expansion)
    value = build_run_diagnostics(handle.capsule)
    assert value['coverage']['planned_task_count'] == 2
    assert value['coverage']['not_dispatched_count'] == 1
    target = write_diagnostics(value, tmp_path / 'context_codex/diagnostics/view1')
    assert (target / 'diagnostics.json').is_file()
    assert (target / 'diagnostics.md').is_file()
    with pytest.raises(ValueError, match='source run'):
        write_diagnostics(value, handle.capsule / 'late')


def test_evidence_plan_not_reached_is_not_attempted(tmp_path):
    from autoresearch.contracts.session_plan import plan_hash
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    from autoresearch.session_agent.evidence import build_evidence_plan
    handle, _, _ = _running_case(tmp_path)
    path = handle.capsule / 'identity/session/plan.json'
    plan = json.loads(path.read_text())
    plan['tasks'].append({**plan['tasks'][0], 'task_id':'inference.two',
        'dependencies':['inference.one'], 'output_artifact_ids':['inference.two.output']})
    plan['plan_hash'] = plan_hash(plan)
    atomic_write_json(path, plan)
    atomic_write_json(handle.workspace / 'session/plan.json', plan)
    atomic_write_json(handle.capsule / 'evidence/evidence_plan.json', build_evidence_plan(handle))
    result = build_run_diagnostics(handle.capsule)
    row = next(r for r in result['attempts'] if r['task_id']=='inference.two')
    assert row['state'] == 'NOT_DISPATCHED'
    assert row['gaps'] == []
    assert result['coverage']['known_attempt_count'] == 1
    assert result['coverage']['not_dispatched_count'] == 1


def test_bad_native_rows_are_not_cleaned_into_observed(tmp_path):
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    from autoresearch.session_agent.host_evidence import bind_task_transcript, capture_activity
    handle, _, _ = _running_case(tmp_path)
    source = tmp_path / 'broken.jsonl'
    source.write_text(json.dumps({'ordinal':0,'type':'turn_context','payload':{}})+'\nBROKEN\n'+
        json.dumps({'ordinal':2,'type':'event_msg','payload':{'type':'task_complete'}})+'\n')
    bind_task_transcript(handle.run_id,'inference.one',1,source,context_ref='root',parent_context_ref=None,
        session_ref='session',start_ordinal=0,end_ordinal=2,context_source='MAIN',handle_loader=lambda _:handle)
    capture_activity(handle,task_id='inference.one',attempt=1,event='poll')
    row = build_run_diagnostics(handle.capsule)['attempts'][0]
    assert row['activity_visibility'] == 'PARTIAL'
    assert 'NATIVE_CAPTURE_INCOMPLETE' in row['gaps']


def _bind_outputs(handle, paths, inputs=()):
    from autoresearch.common.atomic import sha256_bytes
    def ref(p): return {'captured_path':str(p.relative_to(handle.capsule)), 'sha256':sha256_bytes(p.read_bytes())}
    atomic_write_json(handle.capsule / 'evidence/tasks/inference.one/a1/evidence.json', {
        'engine':handle.engine,'run_id':handle.run_id,'task_id':'inference.one','attempt':1,
        'output_refs':[ref(p) for p in paths], 'input_refs':[ref(p) for p in inputs]})


def test_actual_decision_book_producer_is_summarized(tmp_path):
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
    handle, _, _ = _running_case(tmp_path)
    decision = DecisionRecord.build(analysis_date=handle.analysis_date,contract_hash=None,code='600519',
        source_rating='Overweight',rubric_rating='Overweight',post_verify_rating='Hold',gate_states={},
        early_stop=None,ensemble_ratings=['Hold','Hold'],final_rating='Hold',proposal='HOLD',
        reason='post_verify:missing proof',evidence_refs=['details/600519.md'],first_rejection_stage='POST_VERIFY')
    book = write_decision_records(handle.capsule / 'derived' / handle.analysis_date,[decision])
    _bind_outputs(handle,[book])
    change = build_run_diagnostics(handle.capsule)['decision_changes'][0]
    assert change['initial_rating'] == 'Overweight' and change['final_rating'] == 'Hold'
    assert change['post_verify_rating'] == 'Hold'
    assert change['reason'] == decision.reason
    assert change['record_hash'] == decision.record_hash


def test_two_stage_change_contract_keeps_actual_reason_and_initial_hash(tmp_path):
    from autoresearch.common.atomic import sha256_bytes
    from autoresearch.contracts.agent_output import OW_GATES, RUBRIC_DIMENSIONS
    from autoresearch.contracts.research_card import (
        validate_decision_changes,
        validate_initial_assessment,
    )
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    handle, _, _ = _running_case(tmp_path)
    initial = handle.capsule / 'initial.json'
    atomic_write_json(initial,validate_initial_assessment({'schema_version':1,'subject':'600519.SS',
        'frame_hash':'a'*64,'fact_manifest_hash':'b'*64,'initial_dimensions':dict.fromkeys(RUBRIC_DIMENSIONS,'中'),
        'initial_gates':dict.fromkeys(OW_GATES,'PASS'),'initial_rating':'Overweight',
        'key_risks':[],'missing_evidence':[],'evidence_refs':[]}))
    change = handle.capsule / 'changes.json'
    atomic_write_json(change,validate_decision_changes({'schema_version':1,'subject':'600519.SS',
        'initial_hash':sha256_bytes(initial.read_bytes()),'changed_fields':['rating'],
        'change_reason':'new filing changed coverage','new_evidence_refs':['stock.intel']}))
    _bind_outputs(handle,[change],inputs=[initial])
    result = build_run_diagnostics(handle.capsule)['decision_changes'][0]
    assert result['initial_rating'] == 'Overweight'
    assert result['final_rating'] is None
    assert result['reason'] == 'new filing changed coverage'
    assert result['changed_fields'] == ['rating']


def test_two_stage_final_card_is_bound_to_change_summary(tmp_path):
    test_two_stage_change_contract_keeps_actual_reason_and_initial_hash(tmp_path)
    from autoresearch.common.atomic import sha256_bytes
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    from tests.common.test_card_decision_v3 import decision_text
    capsule = next(tmp_path.glob('**/capsule/identity/session/plan.json')).parents[2]
    card = capsule / 'final.md'
    card.write_text(decision_text(subject='600519.SS',venue='XSHG',rating='Hold'))
    evidence = capsule / 'evidence/tasks/inference.one/a1/evidence.json'
    value = json.loads(evidence.read_text())
    value['output_refs'].append({'captured_path':'final.md','sha256':sha256_bytes(card.read_bytes())})
    atomic_write_json(evidence,value)
    result = build_run_diagnostics(capsule)['decision_changes'][0]
    assert result['final_rating'] == 'Hold'
    assert result['final_ref']['sha256'] == sha256_bytes(card.read_bytes())


def test_diagnostics_cli_writes_new_view_and_reports_legacy_missing_plan(tmp_path, capsys):
    from autoresearch.research.run_diagnostics import main
    handle, _, _ = _running_case(tmp_path)
    target = tmp_path / 'context_codex/diagnostics/cli-view'
    assert main(['--run-ref',str(handle.capsule),'--output',str(target)]) == 0
    assert (target / 'diagnostics.md').exists()
    legacy = tmp_path / 'context_codex/legacy'
    legacy.mkdir()
    assert main(['--run-ref',str(legacy),'--output',str(target)+'2']) == 2
    assert 'session plan' in capsys.readouterr().err


def _late_supplement(tmp_path):
    from datetime import datetime, timezone

    from autoresearch.common.atomic import sha256_file
    from autoresearch.session_agent.host_evidence import bind_task_transcript
    from autoresearch.trace.transcripts.snapshot import capture_snapshot
    from tests.research.test_run_usage_join import codex_rows
    handle,_,_ = _running_case(tmp_path)
    source = tmp_path / 'source.jsonl'
    source.write_text('\n'.join(json.dumps(r) for r in codex_rows('bound-native')[:3])+'\n')
    binding = bind_task_transcript(handle.run_id,'inference.one',1,source,context_ref='child',
        parent_context_ref='root',session_ref='bound-native',start_ordinal=0,end_ordinal=4,
        context_source='SUBAGENT',handle_loader=lambda _:handle)
    binding_path = handle.capsule / 'agents/session/task_bindings' / (binding['binding_id']+'.json')
    atomic_write_json(handle.capsule / 'verification/ROOT.json', {'sealed':True})
    folder = tmp_path / 'context_codex/supplements'
    folder.mkdir(parents=True)
    native = folder / 'native.jsonl'
    native.write_text('\n'.join(json.dumps(r) for r in codex_rows('bound-native'))+'\n')
    snapshot = capture_snapshot(native,engine='codex')
    archive = folder / 'native.jsonl.gz'
    archive.write_bytes(snapshot.archive_bytes)
    def ref(path): return {'path':str(path),'sha256':sha256_file(path)}
    value = {'schema_version':'run-diagnostic-supplement-v1','engine':'codex','run_id':handle.run_id,
        'task_id':'inference.one','attempt':1,'session_ref':'bound-native','start_ordinal':0,'end_ordinal':4,
        'binding_ref':ref(binding_path),'archive_ref':ref(archive),'source_ref':ref(native),
        'captured_at':datetime.now(timezone.utc).isoformat(),
        'capture':{'bad_lines':0,'trailing_partial_bytes':0,'source_changed':False,
            'source_prefix_sha256':snapshot.source_prefix.sha256,'cutoff_bytes':snapshot.cutoff_bytes}}
    path = folder / 'supplement.json'
    atomic_write_json(path,value)
    return handle,path,value,ref


def test_late_supplement_preserves_sealed_bytes_and_original_missing(tmp_path):
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    handle,path,_,ref = _late_supplement(tmp_path)
    before = {str(p):p.read_bytes() for p in handle.capsule.rglob('*') if p.is_file()}
    original = build_run_diagnostics(handle.capsule)
    value = build_run_diagnostics(handle.capsule,supplemental_refs=[ref(path)])
    row = value['attempts'][0]
    assert row['activity_visibility'] == original['attempts'][0]['activity_visibility'] == 'PARTIAL'
    assert row['gaps'] == original['attempts'][0]['gaps']
    assert row['supplemental_activity_visibility'] == 'OBSERVED'
    assert value['supplemental_evidence'][0]['received_at']
    assert value['cost']['metrics']['input_tokens']['total'] is None
    assert {str(p):p.read_bytes() for p in handle.capsule.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('change',[
    {'run_id':'wrong-run'}, {'engine':'claude'}, {'attempt':2}, {'session_ref':'different'},
    {'end_ordinal':5}, {'capture':{'bad_lines':-1}},
])
def test_late_supplement_rejects_identity_boundary_and_capture_changes(tmp_path,change):
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    handle,path,value,ref = _late_supplement(tmp_path)
    atomic_write_json(path,{**value,**change})
    with pytest.raises(ValueError):
        build_run_diagnostics(handle.capsule,supplemental_refs=[ref(path)])


def test_late_supplement_requires_frozen_binding_and_verified_bytes(tmp_path):
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    handle,path,value,ref = _late_supplement(tmp_path)
    frozen_ref = ref(path)
    atomic_write_json(path,{**value,'captured_at':'changed'})
    with pytest.raises(ValueError,match='hash'):
        build_run_diagnostics(handle.capsule,supplemental_refs=[frozen_ref])
    value['binding_ref']['path'] = str(path.parent / 'unbound.json')
    atomic_write_json(path,value)
    with pytest.raises(ValueError,match='UNBOUND'):
        build_run_diagnostics(handle.capsule,supplemental_refs=[ref(path)])


def test_late_archive_cannot_change_already_captured_native_rows(tmp_path):
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    from autoresearch.trace.transcripts.snapshot import capture_snapshot
    handle,path,value,ref = _late_supplement(tmp_path)
    source = path.parent / 'native.jsonl'
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    rows[1]['payload']['model'] = 'altered'
    source.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    archive = Path(value['archive_ref']['path'])
    archive.write_bytes(capture_snapshot(source,engine='codex').archive_bytes)
    value['archive_ref'] = ref(archive)
    value['source_ref'] = ref(source)
    snapshot = capture_snapshot(source,engine='codex')
    value['capture'].update(source_prefix_sha256=snapshot.source_prefix.sha256,cutoff_bytes=snapshot.cutoff_bytes)
    atomic_write_json(path,value)
    with pytest.raises(ValueError,match='existing native'):
        build_run_diagnostics(handle.capsule,supplemental_refs=[ref(path)])


def test_supplement_capture_declaration_must_match_original_export_bytes(tmp_path):
    from autoresearch.research.run_diagnostics import build_run_diagnostics
    handle,path,value,ref = _late_supplement(tmp_path)
    value['capture']['cutoff_bytes'] = 1
    atomic_write_json(path,value)
    with pytest.raises(ValueError,match='capture declaration'):
        build_run_diagnostics(handle.capsule,supplemental_refs=[ref(path)])
