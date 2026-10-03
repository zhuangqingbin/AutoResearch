"""Read-only, reference-based diagnostics for one explicitly selected same-engine run.

Native archives remain the facts. This view never fabricates private reasoning,
turns evidence exemptions into observation, or scans host session directories.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import sha256_bytes
from autoresearch.scan.research_provenance import safe_path

VERSION = 'research-run-diagnostics-v1'


def _read(path):
    return json.loads(path.read_text())


def _path(root, relative):
    path = safe_path(root / relative)
    if not path.is_relative_to(root):
        raise ValueError('diagnostic reference escapes selected run')
    return path


def _ref(path):
    path = safe_path(path)
    content = path.read_bytes()
    return {'path': str(path), 'sha256': sha256_bytes(content), 'size': len(content)}


def _verified_ref(capsule, row):
    path = _path(capsule, row.get('captured_path') or row.get('raw_path'))
    result = _ref(path)
    expected = row.get('sha256') or row.get('archive_sha256')
    if not expected or result['sha256'] != expected:
        raise ValueError('diagnostic source hash mismatch')
    return result


def _resolve(run_ref):
    root = safe_path(run_ref)
    capsule = root if (root / 'identity/session/plan.json').is_file() else _path(root, 'capsule')
    if not (capsule / 'identity/session/plan.json').is_file():
        raise ValueError('unsupported legacy run: frozen session plan required')
    from autoresearch.contracts.session_plan import validate_plan
    plan = validate_plan(_read(_path(capsule, 'identity/session/plan.json')))
    if plan['engine'] != ws.ENGINE:
        raise ValueError('diagnostic engine mismatch')
    return capsule, plan


def _transcript(capsule, binding, engine, run_id):
    from autoresearch.contracts.forensic import validate_host_evidence_binding
    binding = validate_host_evidence_binding(binding)
    if (binding['engine'], binding['run_id']) != (engine, run_id):
        raise ValueError('diagnostic transcript identity mismatch')
    reference = _verified_ref(capsule, binding)
    normalized = _path(capsule, binding['normalized_path'])
    if _ref(normalized)['sha256'] != binding['normalized_sha256']:
        raise ValueError('normalized transcript hash mismatch')
    return _archive_activity(reference, binding, engine)


def _archive_activity(reference, binding, engine):
    from autoresearch.trace.transcripts import TranscriptRef, adapter_for, tool_call_id
    from autoresearch.trace.transcripts.snapshot import snapshot_from_archive_bytes
    snapshot = snapshot_from_archive_bytes(Path(reference['path']).read_bytes(), engine=engine, path=reference['path'])
    ref = TranscriptRef(engine=engine, path=Path(reference['path']),
        session_ref=binding['session_ref'], start_ordinal=binding['start_ordinal'],
        end_ordinal=binding['end_ordinal'], role=binding['role'])
    stats = adapter_for(engine).stats_from_rows(snapshot.rows, ref)
    requests, results = set(), set()
    for item in stats.normalized.items:
        call_id = tool_call_id(item.payload)
        if call_id and item.kind == 'tool_request':
            requests.add(call_id)
        if call_id and item.kind == 'tool_result':
            results.add(call_id)
    segment = [r for r in snapshot.rows if isinstance(r.get('ordinal'), int)
        and binding['start_ordinal'] <= r['ordinal'] <= binding['end_ordinal']]
    closed = any(r.get('payload', {}).get('type') == 'task_complete' or
                 (r.get('type') == 'assistant' and (r.get('message') or {}).get('stop_reason') == 'end_turn')
                 for r in segment)
    contiguous = [r['ordinal'] for r in segment] == list(range(binding['start_ordinal'], binding['end_ordinal']+1))
    complete = bool(segment and contiguous and closed and not requests-results and not snapshot.bad_lines)
    return {'reference': reference, 'activity_visibility': 'OBSERVED' if complete else 'PARTIAL',
            'missing_tool_results': sorted(requests-results), 'observed_items': len(stats.normalized.items),
            'boundary': {'session_ref': binding['session_ref'], 'start_ordinal': binding['start_ordinal'],
                         'end_ordinal': binding['end_ordinal']}}


def _decision_changes(row, plan):
    """Describe recorded decisions only; no inferred rationale or private reasoning."""
    from autoresearch.contracts.research_card import (
        validate_decision_changes,
        validate_initial_assessment,
    )
    from autoresearch.scan.decision_record import load_decision_records
    changes = []
    for reference in row['output_refs']:
        try:
            output = _read(Path(reference['path']))
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(output, dict):
            continue
        common = {'task_id':row['task_id'], 'attempt':row['attempt'], 'source_ref':reference}
        if 'records_hash' in output and 'records' in output:
            records = load_decision_records(reference['path'])
            for record in records.values():
                if record.analysis_date != plan['analysis_date']:
                    raise ValueError('decision record analysis date mismatch')
                changes.append({**common, 'code':record.code, 'initial_rating':record.source_rating,
                    'rubric_rating':record.rubric_rating, 'post_verify_rating':record.post_verify_rating,
                    'ensemble_ratings':record.ensemble_ratings, 'final_rating':record.final_rating,
                    'reason':record.reason or 'REASON_NOT_RECORDED', 'record_hash':record.record_hash,
                    'evidence_refs':record.evidence_refs, 'first_rejection_stage':record.first_rejection_stage})
        elif 'changed_fields' in output and 'initial_hash' in output:
            output = validate_decision_changes(output)
            initial_refs = [r for r in row['input_refs'] if r['sha256'] == output['initial_hash']]
            initial = validate_initial_assessment(_read(Path(initial_refs[0]['path']))) if initial_refs else None
            if initial and initial['subject'] != output['subject']:
                raise ValueError('decision change initial subject mismatch')
            final_cards = []
            for candidate in row['output_refs']:
                import re

                from autoresearch.common.card_decision import card_from_decision_text
                text = Path(candidate['path']).read_text()
                blocks = re.findall(r"```research-decision-v2\s*\n(.*?)\n```", text, re.S)
                if len(blocks) != 1:
                    continue
                identity = json.loads(blocks[0])
                if identity.get('subject') != output['subject']:
                    continue
                card = card_from_decision_text(text, subject=output['subject'], venue=identity['venue'],
                    analysis_date=plan['analysis_date'])
                final_cards.append((card['initial_rating'], candidate))
            if len(final_cards) > 1:
                raise ValueError('ambiguous final decision references')
            changes.append({**common, **output, 'initial_rating':initial['initial_rating'] if initial else None,
                'initial_ref':initial_refs[0] if initial_refs else None,
                'final_rating':final_cards[0][0] if final_cards else None,
                'final_ref':final_cards[0][1] if final_cards else None,
                'reason':output['change_reason'] or 'REASON_NOT_RECORDED'})
        elif 'initial_rating' in output and 'final_rating' in output:
            changes.append({**common, 'initial_rating':output['initial_rating'], 'final_rating':output['final_rating'],
                'reason':output.get('change_reason') or 'REASON_NOT_RECORDED'})
    return changes


def _external_ref(value):
    if not isinstance(value,dict) or set(value) != {'path','sha256'}:
        raise ValueError('supplement requires exact path/hash reference')
    reference = _ref(value['path'])
    if reference['sha256'] != value['sha256']:
        raise ValueError('supplement reference hash mismatch')
    return reference


def _supplements(capsule, plan, attempts, references):
    """Admit explicit late exports against existing frozen transcript bindings.

    This does not establish a new host identity, widen a bound interval, replace
    captured native rows, or change the sealed attempt's state and original gaps.
    """
    import re
    from datetime import datetime, timezone

    from autoresearch.contracts.forensic import validate_host_evidence_binding
    from autoresearch.trace.transcripts.snapshot import snapshot_from_archive_bytes
    admitted = []
    fields = {'schema_version','engine','run_id','task_id','attempt','session_ref','start_ordinal',
        'end_ordinal','binding_ref','archive_ref','source_ref','captured_at','capture'}
    capture_fields = {'bad_lines','trailing_partial_bytes','source_changed','source_prefix_sha256','cutoff_bytes'}
    for supplied in references:
        reference = _external_ref(supplied)
        value = _read(Path(reference['path']))
        if not isinstance(value,dict) or set(value) != fields or value['schema_version'] != 'run-diagnostic-supplement-v1':
            raise ValueError('invalid supplement schema')
        if (value['engine'],value['run_id']) != (plan['engine'],plan['run_id']):
            raise ValueError('supplement engine/run identity mismatch')
        row = next((r for r in attempts if (r['task_id'],r['attempt']) == (value['task_id'],value['attempt'])),None)
        if row is None or 'claim' not in row['record_refs']:
            raise ValueError('UNBOUND supplement: no frozen dispatched attempt')
        binding_path = safe_path(value['binding_ref']['path'])
        if binding_path.parent != capsule / 'agents/session/task_bindings' or not binding_path.is_file():
            raise ValueError('UNBOUND supplement: existing frozen host binding required')
        binding_ref = _external_ref(value['binding_ref'])
        binding = validate_host_evidence_binding(_read(binding_path))
        if any(binding[k] != value[k] for k in ('engine','run_id','task_id','attempt','session_ref','start_ordinal','end_ordinal')):
            raise ValueError('supplement differs from frozen host identity/boundary')
        captured = datetime.fromisoformat(value['captured_at'].replace('Z','+00:00'))
        if captured.tzinfo is None:
            raise ValueError('supplement capture timestamp requires timezone')
        capture = value['capture']
        if (not isinstance(capture,dict) or set(capture) != capture_fields
            or any(type(capture[k]) is not int or capture[k]<0 for k in ('bad_lines','trailing_partial_bytes','cutoff_bytes'))
            or type(capture['source_changed']) is not bool
            or not isinstance(capture['source_prefix_sha256'],str)
            or not re.fullmatch('[0-9a-f]{64}',capture['source_prefix_sha256'])):
            raise ValueError('invalid supplemental capture declaration')
        archive_ref = _external_ref(value['archive_ref'])
        source_ref = _external_ref(value['source_ref'])
        from autoresearch.trace.transcripts.snapshot import capture_snapshot
        source_snapshot = capture_snapshot(source_ref['path'], engine=plan['engine'])
        observed_capture = {'bad_lines':source_snapshot.bad_lines,
            'trailing_partial_bytes':source_snapshot.trailing_partial_bytes,
            'source_changed':source_snapshot.source_changed,
            'source_prefix_sha256':source_snapshot.source_prefix.sha256,
            'cutoff_bytes':source_snapshot.cutoff_bytes}
        if capture != observed_capture or source_snapshot.archive.sha256 != archive_ref['sha256']:
            raise ValueError('supplement capture declaration differs from source export')
        # Recheck the original bytes after capture to reject a live-changing export.
        if _ref(source_ref['path'])['sha256'] != source_ref['sha256']:
            raise ValueError('supplement source changed during capture')
        snapshot = snapshot_from_archive_bytes(Path(archive_ref['path']).read_bytes(),engine=plan['engine'],path=archive_ref['path'])
        original_ref = _verified_ref(capsule,binding)
        original = snapshot_from_archive_bytes(Path(original_ref['path']).read_bytes(),engine=plan['engine'],path=original_ref['path'])
        rows = list(snapshot.rows)
        ordinals = [r.get('ordinal') for r in rows]
        if any(type(n) is not int for n in ordinals) or ordinals != sorted(set(ordinals)):
            raise ValueError('supplement native ordinal mapping invalid')
        native = ({r.get('payload',{}).get('id') for r in rows if r.get('type')=='session_meta'}
            if plan['engine']=='codex' else {r.get('sessionId') for r in rows if r.get('sessionId')})
        if native != {binding['session_ref']}:
            raise ValueError('supplement native session identity mismatch')
        by_ordinal = {r['ordinal']:r for r in rows}
        if any(by_ordinal.get(r.get('ordinal')) != r for r in original.rows):
            raise ValueError('supplement changes existing native records')
        activity = _archive_activity(archive_ref,binding,plan['engine'])
        if capture['bad_lines'] or capture['trailing_partial_bytes'] or capture['source_changed']:
            activity['activity_visibility'] = 'PARTIAL'
        entry = {'reference':reference,'binding_ref':binding_ref,'archive_ref':archive_ref,
            'source_ref':source_ref,'task_id':row['task_id'],'attempt':row['attempt'],
            'captured_at':value['captured_at'],'capture_time_basis':'EXTERNAL_DECLARATION',
            'received_at':datetime.now(timezone.utc).isoformat(),'capture':capture,
            'activity':activity,'original_activity_visibility':row['activity_visibility'],
            'original_gaps':list(row['gaps']),'usage_status':'UNKNOWN_NOT_JOINED'}
        admitted.append(entry)
        row.setdefault('supplemental_transcripts',[]).append(activity)
        row['supplemental_activity_visibility'] = ('OBSERVED' if any(
            a['activity_visibility']=='OBSERVED' for a in row['supplemental_transcripts']) else 'PARTIAL')
    return admitted


def build_run_diagnostics(run_ref, *, outcome_refs=(), supplemental_refs=()):
    capsule, plan = _resolve(run_ref)
    identity = {k: plan[k] for k in ('engine','run_id','analysis_date','run_kind','requested_mode','plan_hash')}
    identity['capsule_path'] = str(capsule)
    identity['plan_ref'] = _ref(capsule / 'identity/session/plan.json')
    refs = [identity['plan_ref']]
    for name in ('request.json','host_evidence.json'):
        path = capsule / 'identity/session' / name
        if path.exists():
            reference = _ref(path)
            refs.append(reference)
            identity[name.removesuffix('.json')+'_ref'] = reference
    tasks = {t['task_id']:t for t in plan['tasks']}
    from autoresearch.contracts.session_expansion import apply_expansion
    from autoresearch.contracts.session_plan import validate_expansion
    pending = []
    for path in sorted((capsule / 'identity/session/expansions').glob('*.json')):
        pending.append(validate_expansion(_read(safe_path(path))))
        refs.append(_ref(path))
    while pending:
        ready = [e for e in pending if {d for t in e['tasks'] for d in t['dependencies']}
                 <= set(tasks) | {t['task_id'] for t in e['tasks']}]
        if not ready:
            raise ValueError('diagnostic expansion dependencies unresolved')
        for expansion in ready:
            tasks = {t['task_id']:t for t in apply_expansion(plan, expansion, existing_tasks=list(tasks.values()))}
            pending.remove(expansion)
    keys = {}
    evidence_plan_path = capsule / 'evidence/evidence_plan.json'
    if evidence_plan_path.exists():
        from autoresearch.contracts.forensic import validate_evidence_plan
        evidence_plan = validate_evidence_plan(_read(evidence_plan_path))
        if (evidence_plan['engine'], evidence_plan['run_id']) != (plan['engine'], plan['run_id']):
            raise ValueError('diagnostic evidence plan identity mismatch')
        keys = {(k['task_id'],k['attempt']):k for k in evidence_plan['task_keys']}
        refs.append(_ref(evidence_plan_path))
    records = {}
    for path in sorted((capsule / 'evidence/attempt_records').glob('*/*/*.json')):
        task_id, number = path.parent.parent.name, path.parent.name
        if task_id not in tasks or not number.startswith('a') or not number[1:].isdigit():
            raise ValueError('unregistered diagnostic attempt')
        key = (task_id,int(number[1:]))
        records.setdefault(key,{})[path.stem] = (_read(safe_path(path)), _ref(path))
    for task_id in tasks:
        if not any(k[0] == task_id for k in keys.keys() | records.keys()):
            keys[(task_id,1)] = {'state':'NOT_DISPATCHED','requirements':[]}
    bindings = {}
    for path in sorted((capsule / 'agents/session/task_bindings').glob('*.json')):
        value = _read(safe_path(path))
        key = (value['task_id'],value['attempt'])
        if key[0] not in tasks:
            raise ValueError('unregistered transcript attempt')
        bindings.setdefault(key,[]).append((value,_ref(path)))
    observations = []
    for path in sorted((capsule / 'evidence/activity').glob('*.json')):
        value = _read(safe_path(path))
        if (value.get('engine'),value.get('run_id')) != (plan['engine'],plan['run_id']):
            raise ValueError('activity observation run mismatch')
        observations.append({**value,'reference':_ref(path)})
    attempts, changes = [], []
    for key in sorted(keys.keys() | records.keys() | bindings.keys()):
        task_id, attempt = key
        record = records.get(key,{})
        state = keys.get(key,{}).get('state','UNKNOWN')
        if not record and state in {'NOT_REACHED', 'PENDING'}:
            state = 'NOT_DISPATCHED'
        for kind, terminal in (('claim','RUNNING'),('failure','FAILED'),('abandoned','ABANDONED'),('accepted_receipt','SUCCEEDED')):
            if kind in record:
                state = terminal
        row = {'task_id':task_id,'attempt':attempt,'role':tasks[task_id].get('role'),
            'kind':tasks[task_id]['kind'], 'state':state,'activity_visibility':'UNKNOWN',
            'requirements':keys.get(key,{}).get('requirements',[]),
            'record_refs':{name:value[1] for name,value in record.items()},
            'exemption_reason':record.get('abandoned',({},))[0].get('reason'),
            'transcripts':[], 'input_refs':[], 'output_refs':[], 'source_receipt_ids':[],
            'gaps':[], 'checkpoints':[v for v in observations if (v.get('task_id'),v.get('attempt')) == key]}
        for binding, reference in bindings.get(key,[]):
            refs.append(reference)
            try:
                row['transcripts'].append(_transcript(capsule,binding,plan['engine'],plan['run_id']))
            except (OSError,ValueError,KeyError) as exc:
                row['gaps'].append('TRANSCRIPT_INVALID:'+str(exc))
        for transcript in row['transcripts']:
            captures = [v for v in row['checkpoints'] if v.get('archive_sha256') == transcript['reference']['sha256']]
            if not captures or any(v.get('bad_lines') or v.get('trailing_partial_bytes') or v.get('source_changed') for v in captures):
                transcript['activity_visibility'] = 'PARTIAL'
                row['gaps'].append('NATIVE_CAPTURE_INCOMPLETE')
        vis = [t['activity_visibility'] for t in row['transcripts']]
        if vis:
            row['activity_visibility'] = 'OBSERVED' if all(v == 'OBSERVED' for v in vis) else 'PARTIAL'
        evidence_path = capsule / 'evidence/tasks' / task_id / f'a{attempt}' / 'evidence.json'
        if evidence_path.exists():
            evidence = _read(safe_path(evidence_path))
            refs.append(_ref(evidence_path))
            if (evidence.get('engine'),evidence.get('run_id'),evidence.get('task_id'),evidence.get('attempt')) != (plan['engine'],plan['run_id'],task_id,attempt):
                raise ValueError('task evidence identity mismatch')
            for name in ('input_refs','output_refs'):
                for ref in evidence.get(name,[]):
                    try:
                        row[name].append(_verified_ref(capsule,ref))
                    except (OSError,ValueError):
                        row['gaps'].append(name.upper()+'_INVALID')
            row['source_receipt_ids'] = evidence.get('source_receipt_ids',[])
            row['gaps'].extend(evidence.get('reasons',[]))
        if state != 'NOT_DISPATCHED' and tasks[task_id]['kind'] == 'INFERENCE':
            if not row['transcripts']:
                row['gaps'].append('TRANSCRIPT_NOT_OBSERVED')
            if any(t['missing_tool_results'] for t in row['transcripts']):
                row['gaps'].append('TOOL_RESULTS_NOT_OBSERVED')
            if 'source_receipts' in row['requirements'] and not row['source_receipt_ids']:
                row['gaps'].append('SOURCE_RECEIPTS_NOT_OBSERVED')
        row['gaps'] = sorted(set(row['gaps']))
        attempts.append(row)
        changes.extend(_decision_changes(row, plan))
    outcomes = []
    for path in outcome_refs:
        path = safe_path(path)
        value = _read(path)
        if value.get('engine') != plan['engine'] or (value.get('run_id') or value.get('contract_run_id')) != plan['run_id']:
            raise ValueError('outcome reference identity mismatch')
        outcomes.append({'reference':_ref(path),'status':value.get('outcome_status',value.get('status','UNKNOWN')),
                         'label_version':value.get('label_version','UNKNOWN')})
    from autoresearch.research.run_usage import build_run_usage
    cost = build_run_usage(capsule, attempts=attempts)
    supplements = _supplements(capsule, plan, attempts, supplemental_refs)
    if supplements:
        # Late evidence is visible separately; this version does not claim a
        # complete native cost join across externally supplied capture segments.
        cost['supplemental_usage_status'] = 'UNKNOWN_NOT_JOINED'
        cost['measurement_coverage']['status'] = 'UNKNOWN'
        for summary in cost['metrics'].values():
            summary['total'] = None
            summary['status'] = 'PARTIAL' if summary['observed_total'] is not None else 'UNKNOWN'

    counts = Counter(row['activity_visibility'] for row in attempts if row['state'] != 'NOT_DISPATCHED')
    return {'schema_version':VERSION,'identity':identity,'attempts':attempts,'evidence':refs,
        'decision_changes':changes,'coverage':{'planned_task_count':len(tasks),
            'known_attempt_count':sum(r['state'] != 'NOT_DISPATCHED' for r in attempts),
            'not_dispatched_count':sum(r['state'] == 'NOT_DISPATCHED' for r in attempts),
            'activity_visibility':dict(counts), 'actual_model_calls':None,
            'missing_transcripts':sum('TRANSCRIPT_NOT_OBSERVED' in r['gaps'] for r in attempts),
            'missing_tool_results':sum('TOOL_RESULTS_NOT_OBSERVED' in r['gaps'] for r in attempts),
            'missing_sources':sum('SOURCE_RECEIPTS_NOT_OBSERVED' in r['gaps'] for r in attempts)},
        'cost':cost,'outcomes':outcomes,'supplemental_evidence':supplements}


def render_markdown(value):
    lines = [f"# Run diagnostics: {value['identity']['run_id']}", '',
             'Derived observations; missing host activity remains UNKNOWN.', '',
             '| Task | Attempt | State | Activity visibility | Gaps |', '|---|---:|---|---|---|']
    lines += [f"| {r['task_id']} | {r['attempt']} | {r['state']} | {r['activity_visibility']} | {', '.join(r['gaps'])} |" for r in value['attempts']]
    lines += ['', '## Observed cost', '', '| Metric | Complete total | Observed total | Status |',
              '|---|---:|---:|---|']
    for metric, summary in value['cost']['metrics'].items():
        def display(v):
            return 'UNKNOWN' if v is None else str(v)
        lines.append(f"| {metric} | {display(summary['total'])} | {display(summary['observed_total'])} | {summary['status']} |")
    lines += ['', 'Input includes cached input; reasoning is a subset of output. Wall time uses interval union.',
              '', '## Recorded decision changes', '']
    for change in value['decision_changes']:
        lines.append(f"- {change.get('code',change.get('subject',change['task_id']))}: "
                     f"{change.get('initial_rating')} → {change.get('final_rating')}; {change['reason']} "
                     f"({change['source_ref']['path']}, {change['source_ref']['sha256']})")
    if not value['decision_changes']:
        lines.append('No bound decision-change records observed.')
    if value.get('supplemental_evidence'):
        lines += ['', '## Late evidence', '']
        for late in value['supplemental_evidence']:
            lines.append(f"- {late['task_id']} a{late['attempt']}: received {late['received_at']}; "
                         f"bounded activity {late['activity']['activity_visibility']}; original gaps retained; "
                         f"{late['reference']['path']} ({late['reference']['sha256']})")
    lines += ['', '## References', '']
    lines += [f"- {ref['path']} ({ref['sha256']})" for ref in value['evidence']]
    return '\n'.join(lines)+'\n'


def write_diagnostics(value, output_dir):
    """Publish a new derived version; all original run bytes remain immutable."""
    from autoresearch.common.atomic import canonical_json
    target = safe_path(output_dir)
    capsule = safe_path(value['identity']['capsule_path'])
    if target.is_relative_to(capsule.parent):
        raise ValueError('diagnostic output cannot modify source run')
    target.mkdir(parents=True, exist_ok=False)
    (target / 'diagnostics.json').write_text(canonical_json(value)+'\n')
    (target / 'diagnostics.md').write_text(render_markdown(value))
    return target


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description='Write a read-only, same-engine run diagnostic view.')
    parser.add_argument('--run-ref', required=True, help='Canonical run directory or capsule')
    parser.add_argument('--output', required=True, help='New derived output directory outside the source run')
    parser.add_argument('--supplemental-refs-json', help='JSON array of explicit late path/hash references')
    parser.add_argument('--outcome-refs-json', help='JSON array of same-engine, run-bound outcome paths')
    args = parser.parse_args(argv)
    try:
        outcomes = _read(safe_path(args.outcome_refs_json)) if args.outcome_refs_json else []
        if not isinstance(outcomes,list) or any(not isinstance(p,str) for p in outcomes):
            raise ValueError('outcome refs must be a JSON array of paths')
        supplements = _read(safe_path(args.supplemental_refs_json)) if args.supplemental_refs_json else []
        if not isinstance(supplements,list):
            raise ValueError('supplemental refs must be a JSON array')
        value = build_run_diagnostics(args.run_ref, outcome_refs=outcomes, supplemental_refs=supplements)
        target = write_diagnostics(value, args.output)
    except (OSError,ValueError,KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(str(target / 'diagnostics.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
