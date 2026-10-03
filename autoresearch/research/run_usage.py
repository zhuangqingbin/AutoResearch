"""Derived whole-run native usage readers, independent of the session orchestrator."""
from __future__ import annotations

from datetime import datetime

from autoresearch.trace.transcripts.snapshot import snapshot_from_archive_bytes


def _model(spec, source):
    return {'model': spec.get('model'), 'effort': spec.get('effort') or spec.get('reasoning_effort'), 'source': source}


# Derived run scope. This deliberately does not change the strict metering-v1 schema.
RUN_USAGE_VERSION = 'run-usage-join-v1'
RUN_TOKEN_FIELDS = ('input_tokens','output_tokens','cached_input_tokens',
                    'cache_creation_tokens','reasoning_output_tokens')


def join_usage_records(records, *, expected_scopes, intervals=()):
    """Join IO-validated native records, counting input inclusive of its cache subsets.

    Identity is native session/event or verified session/ordinal, never archive hash
    or content equality. Unknown overlaps, resets and attribution stay unmeasured.
    """
    records = list(records)
    scopes = {s['scope_id']:s for s in expected_scopes}
    unique, issues, unattributed = {}, [], []
    for record in records:
        scope = record['scope_id']
        if scope not in scopes:
            unattributed.append(record)
            continue
        basis = record.get('identity_basis')
        identifier = record.get('native_record_id') if basis == 'NATIVE_EVENT_ID' else record.get('ordinal')
        session = record.get('session_ref')
        valid = (basis in {'NATIVE_EVENT_ID','VERIFIED_SESSION_ORDINAL'} and session and identifier is not None
                 and record.get('usage_semantics') == 'DELTA_SESSION_LOCAL')
        if not valid:
            issues.append({'scope_id':scope,'reason':record.get('reason') or 'UNVERIFIED_USAGE_IDENTITY_OR_OVERLAP'})
            continue
        key = (session,basis,str(identifier))
        if key not in unique:
            unique[key] = {**record,'scope_ids':[scope],'source_refs':list(record.get('source_refs',[]))}
        else:
            old = unique[key]
            if old['metrics'] != record['metrics']:
                old['metrics'] = dict.fromkeys(RUN_TOKEN_FIELDS)
                issues.append({'scope_id':scope,'reason':'CONFLICTING_NATIVE_RECORD_REPLAY'})
            old['scope_ids'] = sorted(set(old['scope_ids']) | {scope})
            for ref in record.get('source_refs',[]):
                if ref not in old['source_refs']:
                    old['source_refs'].append(ref)
    rows = list(unique.values())
    ambiguous = [r for r in rows if len(set(r['scope_ids']) - {'root'}) > 1]
    unattributed.extend(ambiguous)
    seen = {scope for row in rows for scope in row['scope_ids']}
    missing = sorted(set(scopes)-seen)
    incomplete = {name for name,scope in scopes.items() if scope.get('complete') is not True}
    incomplete |= {row['scope_id'] for row in issues}
    metrics = {}
    for metric in RUN_TOKEN_FIELDS:
        known = [r['metrics'].get(metric) for r in rows]
        available = [v for v in known if type(v) is int and v >= 0]
        complete = bool(scopes and rows and not missing and not incomplete and len(available) == len(rows))
        metrics[metric] = {'total':sum(available) if complete else None,
            'observed_total':sum(available) if available else None,
            'status':'COMPLETE' if complete else 'PARTIAL' if available else 'UNKNOWN',
            'observed_records':len(available),'expected_scopes':len(scopes)}
    valid_intervals, interval_scopes = [], set()
    for interval in intervals:
        if interval.get('scope_id') not in scopes:
            continue
        try:
            start,end = [datetime.fromisoformat(interval[k].replace('Z','+00:00')) for k in ('start','end')]
            if start.tzinfo is None or end.tzinfo is None or end < start:
                continue
        except (TypeError,ValueError,KeyError):
            continue
        valid_intervals.append((start,end))
        interval_scopes.add(interval['scope_id'])
    from autoresearch.trace.usage_panorama import _union_seconds
    wall = _union_seconds(valid_intervals) if valid_intervals else None
    complete_wall = bool(scopes and interval_scopes == set(scopes) and not incomplete)
    metrics['wall_seconds'] = {'total':wall if complete_wall else None,'observed_total':wall,
        'status':'COMPLETE' if complete_wall else 'PARTIAL' if wall is not None else 'UNKNOWN',
        'basis':'OBSERVED_INTERVAL_UNION','missing_scopes':sorted(set(scopes)-interval_scopes)}
    def totals(selected):
        return {field:sum(r['metrics'][field] for r in selected if r['metrics'].get(field) is not None)
                if selected and all(r['metrics'].get(field) is not None for r in selected) else None
                for field in RUN_TOKEN_FIELDS}
    references = []
    for row in rows:
        for ref in row['source_refs']:
            if ref not in references:
                references.append(ref)
    return {'schema_version':RUN_USAGE_VERSION,'scope':'BOUND_RUN_ROOT_AND_ALL_KNOWN_ATTEMPTS',
        'input_basis':'INCLUDES_CACHED_INPUT','reasoning_basis':'SUBSET_OF_OUTPUT',
        'adapter_versions':sorted({r['adapter_version'] for r in records if r.get('adapter_version')}),
        'source_refs':references,'unique_usage_records':rows,'overlap_resolution':issues,
        'root_only':totals([r for r in rows if r['scope_ids'] == ['root']]),
        'task_attempts':{scope:(dict.fromkeys(RUN_TOKEN_FIELDS) if any(scope in r['scope_ids'] for r in ambiguous)
            else totals([r for r in rows if scope in r['scope_ids']])) for scope in scopes if scope != 'root'},
        'unattributed':unattributed,'metrics':metrics,
        'models':{scope:{'requested':scopes[scope].get('requested'),'resolved':scopes[scope].get('resolved'),
            'observed':sorted({r['observed_model'] for r in rows if scope in r['scope_ids'] and r.get('observed_model')}) or None,
            'observed_effort':sorted({r['observed_effort'] for r in rows if scope in r['scope_ids'] and r.get('observed_effort')}) or None}
            for scope in scopes},
        'measurement_coverage':{'status':'COMPLETE' if all(metrics[k]['status']=='COMPLETE' for k in ('input_tokens','output_tokens')) else 'UNKNOWN',
            'missing_scopes':missing,'incomplete_scopes':sorted(incomplete),'known_scopes':len(scopes),
            'actual_model_calls':None},'estimated_price':None}


def native_usage_records(engine, rows, *, scope_id, session_ref, start_ordinal, end_ordinal, source_ref):
    """Normalize one hash-verified native interval; retain counter reset uncertainty.

    Codex counters are session-local cumulative inclusive input. Claude message
    usage is message-local uncached input plus cache-read and cache-creation input.
    Neither record count denotes a model/API call count.
    """
    rows = [dict(row) for row in rows]
    if type(start_ordinal) is not int or type(end_ordinal) is not int or end_ordinal < start_ordinal:
        return [],None,False
    ordinals = [row.get('ordinal') for row in rows]
    if any(type(n) is not int for n in ordinals) or ordinals != sorted(set(ordinals)):
        return [],None,False
    if engine == 'codex':
        native_sessions = {r.get('payload',{}).get('id') for r in rows if r.get('type')=='session_meta'}
    elif engine == 'claude':
        native_sessions = {r.get('sessionId') for r in rows if r.get('sessionId')}
    else:
        return [],None,False
    if native_sessions != {session_ref}:
        return [],None,False
    segment = [r for r in rows if start_ordinal <= r['ordinal'] <= end_ordinal]
    if not segment:
        return [],None,False
    contiguous = [r['ordinal'] for r in segment] == list(range(start_ordinal,end_ordinal+1))
    terminal = segment[-1]
    closed = (terminal.get('payload',{}).get('type') == 'task_complete' if engine=='codex' else
              terminal.get('type')=='assistant' and (terminal.get('message') or {}).get('stop_reason')=='end_turn')
    interval = {'scope_id':scope_id,'start':segment[0].get('timestamp'),'end':segment[-1].get('timestamp')}
    previous = None
    initial_zero = start_ordinal == 0 and rows[0].get('type') == 'session_meta'
    model, effort = None,None
    records = []
    latest_messages = {r.get('message',{}).get('id'):r['ordinal'] for r in rows
        if engine=='claude' and r['ordinal']<=end_ordinal and r.get('type')=='assistant' and r.get('message',{}).get('id')}
    def number(value): return value if type(value) is int and value >= 0 else None
    for row in rows:
        ordinal = row['ordinal']
        if ordinal > end_ordinal:
            break
        payload = row.get('payload') or {}
        if engine == 'codex' and row.get('type') == 'turn_context':
            model = payload.get('model')
            effort = payload.get('effort') or (payload.get('collaboration_mode') or {}).get('settings',{}).get('reasoning_effort')
        if engine == 'codex':
            if row.get('type') != 'event_msg' or payload.get('type') != 'token_count':
                continue
            raw = (payload.get('info') or {}).get('total_token_usage')
            missing_usage = not isinstance(raw,dict)
            raw = raw if isinstance(raw,dict) else {}
            current = {name:number(raw.get('cache_write_input_tokens' if name=='cache_creation_tokens' else name)) for name in RUN_TOKEN_FIELDS}
            baseline = previous
            previous = current
            if ordinal < start_ordinal:
                continue
            if baseline is None and initial_zero:
                baseline = dict.fromkeys(RUN_TOKEN_FIELDS, 0)
            reset = baseline is not None and any(current[k] is not None and baseline[k] is not None and current[k]<baseline[k] for k in RUN_TOKEN_FIELDS)
            metrics = {name:(current[name]-baseline[name] if baseline is not None and
                       current[name] is not None and baseline[name] is not None and not reset else None)
                       for name in RUN_TOKEN_FIELDS}
            native_id = row.get('id') or payload.get('event_id')
            reason = ('MISSING_OR_MALFORMED_NATIVE_USAGE' if missing_usage else 'COUNTER_RESET' if reset
                      else 'COUNTER_BASELINE_UNKNOWN' if baseline is None else None)
        else:
            if ordinal < start_ordinal or row.get('type') != 'assistant':
                continue
            message = row.get('message') or {}
            if message.get('id') and latest_messages[message['id']] != ordinal:
                continue
            raw = message.get('usage')
            missing_usage = not isinstance(raw,dict)
            raw = raw if isinstance(raw,dict) else {}
            uncached = number(raw.get('input_tokens'))
            cached,creation = number(raw.get('cache_read_input_tokens')),number(raw.get('cache_creation_input_tokens'))
            metrics = {'input_tokens':sum((uncached,cached,creation)) if None not in (uncached,cached,creation) else None,
                'output_tokens':number(raw.get('output_tokens')),'cached_input_tokens':cached,
                'cache_creation_tokens':creation,'reasoning_output_tokens':number(raw.get('reasoning_output_tokens'))}
            native_id = message.get('id')
            model,effort = message.get('model'),message.get('effort')
            reason = 'MISSING_OR_MALFORMED_NATIVE_USAGE' if missing_usage else None
        if payload.get('includes_children') is True or (payload.get('info') or {}).get('includes_children') is True:
            reason = 'PARENT_INCLUDES_CHILDREN'
            metrics = dict.fromkeys(RUN_TOKEN_FIELDS)
        for child,parent in (('cached_input_tokens','input_tokens'),('reasoning_output_tokens','output_tokens')):
            if metrics[child] is not None and metrics[parent] is not None and metrics[child]>metrics[parent]:
                metrics[child] = metrics[parent] = None
                reason = 'INCONSISTENT_TOKEN_SUBSETS'
        records.append({'scope_id':scope_id,'session_ref':session_ref,'native_record_id':native_id,
            'ordinal':ordinal,'identity_basis':'NATIVE_EVENT_ID' if native_id else 'VERIFIED_SESSION_ORDINAL',
            'adapter_version':engine+'-run-usage-v1','usage_semantics':'DELTA_SESSION_LOCAL',
            'source_refs':[source_ref],'metrics':metrics,'observed_model':model,'observed_effort':effort,
            'reason':reason})
    return records,interval,bool(contiguous and closed)


def build_run_usage(capsule, *, attempts):
    """Read only registered run archives. No host-directory discovery or API calls."""
    from autoresearch.common.atomic import sha256_file
    from autoresearch.contracts.forensic import validate_host_evidence_binding
    from autoresearch.research.run_diagnostics import _path, _read, _ref, _resolve
    capsule,plan = _resolve(capsule)
    records,intervals,errors = [],[],[]
    scopes = [{'scope_id':'root','complete':False,'state':'UNKNOWN','requested':None,'resolved':None}]
    def capture(scope, value, session):
        if (value.get('engine'),value.get('run_id')) != (plan['engine'],plan['run_id']):
            raise ValueError('run usage archive identity mismatch')
        path = _path(capsule,value['raw_path'])
        reference = _ref(path)
        if reference['sha256'] != value['archive_sha256']:
            raise ValueError('run usage archive hash mismatch')
        snapshot = snapshot_from_archive_bytes(path.read_bytes(),engine=plan['engine'],path=path)
        found,interval,complete = native_usage_records(plan['engine'],snapshot.rows,
            scope_id=scope['scope_id'],session_ref=session,start_ordinal=value['start_ordinal'],
            end_ordinal=value['end_ordinal'],source_ref=reference)
        records.extend(found)
        if interval:
            intervals.append(interval)
        scope['complete'] = bool(complete and not snapshot.bad_lines)
    registration_path = capsule / 'identity/session/host_evidence.json'
    main_path = capsule / 'evidence/main_host.json'
    if registration_path.exists() and main_path.exists():
        registration,main = _read(registration_path),_read(main_path)
        if main.get('status') == 'PRESENT':
            try:
                if (registration.get('engine'),registration.get('run_id')) != (plan['engine'],plan['run_id']):
                    raise ValueError('root usage registration identity mismatch')
                capture(scopes[0],main,registration['session_ref'])
                checkpoints = []
                for path in (capsule / 'evidence/activity').glob('*.json'):
                    checkpoint = _read(_path(capsule,path.relative_to(capsule)))
                    if (checkpoint.get('engine'),checkpoint.get('run_id')) != (plan['engine'],plan['run_id']):
                        raise ValueError('root usage checkpoint identity mismatch')
                    if checkpoint.get('task_id') is None and checkpoint.get('archive_sha256') == main['archive_sha256']:
                        checkpoints.append(checkpoint)
                if not checkpoints or any(c.get('bad_lines') or c.get('trailing_partial_bytes') or c.get('source_changed') for c in checkpoints):
                    scopes[0]['complete'] = False
            except (OSError,ValueError,KeyError) as exc:
                errors.append({'scope_id':'root','reason':str(exc)})
    bindings = {}
    for path in sorted((capsule / 'agents/session/task_bindings').glob('*.json')):
        value = validate_host_evidence_binding(_read(_path(capsule,path.relative_to(capsule))))
        bindings.setdefault((value['task_id'],value['attempt']),[]).append(value)
    model_metadata = {}
    sidecar_path = capsule / 'agents/session/metering.json'
    if sidecar_path.exists():
        from autoresearch.contracts.session_metering import validate_metering
        sidecar = validate_metering(_read(sidecar_path))
        if (sidecar['engine'],sidecar['run_id']) != (plan['engine'],plan['run_id']):
            raise ValueError('run usage metering identity mismatch')
        model_metadata = {(a['task_id'],a['attempt']):a for a in sidecar['attempts']}
    for attempt in attempts:
        if attempt['kind'] != 'INFERENCE' or attempt['state'] in {'NOT_DISPATCHED','NOT_REACHED'}:
            continue
        task_id,number = attempt['task_id'],attempt['attempt']
        scope = {'scope_id':f'{task_id}:a{number}','complete':False,'state':attempt['state'],
                 'requested':None,'resolved':None}
        scopes.append(scope)
        if (task_id,number) in model_metadata:
            metadata = model_metadata[(task_id,number)]
            scope.update(requested=metadata['requested'],resolved=metadata['resolved'])
        request_path = capsule / 'agents/session/requests' / f'session-{task_id}-a{number}.json'
        if request_path.exists():
            request = _read(request_path).get('dispatch_request') or {}
            if request:
                expected = {'engine':plan['engine'],'run_id':plan['run_id'],'task_id':task_id,'attempt':number}
                if any(request.get(k)!=v for k,v in expected.items()):
                    raise ValueError('run usage request identity mismatch')
                if scope['resolved'] is None:
                    scope['resolved'] = _model(request,'FROZEN_DISPATCH_REQUEST')
        for binding in bindings.get((task_id,number),[]):
            try:
                normalized = _path(capsule,binding['normalized_path'])
                if sha256_file(normalized) != binding['normalized_sha256']:
                    raise ValueError('run usage normalized archive hash mismatch')
                capture(scope,binding,binding['session_ref'])
                if attempt.get('activity_visibility') != 'OBSERVED':
                    scope['complete'] = False
            except (OSError,ValueError,KeyError) as exc:
                scope['complete'] = False
                errors.append({'scope_id':scope['scope_id'],'reason':str(exc)})
    # Prefix checkpoints add observed lower bounds for scopes lacking a final
    # bound archive. Native identity deduplicates overlapping prefixes.
    for path in sorted((capsule / 'evidence/activity').glob('*.json')):
        checkpoint = _read(_path(capsule,path.relative_to(capsule)))
        if (checkpoint.get('engine'),checkpoint.get('run_id')) != (plan['engine'],plan['run_id']):
            raise ValueError('usage checkpoint identity mismatch')
        if not checkpoint.get('raw_path'):
            continue
        name = (f"{checkpoint['task_id']}:a{checkpoint['attempt']}"
                if checkpoint.get('attribution') == 'TASK_ATTEMPT' else 'root')
        scope = next((s for s in scopes if s['scope_id']==name), None)
        if scope is None or scope['complete']:
            continue
        try:
            capture(scope,checkpoint,checkpoint['session_ref'])
        except (OSError,ValueError,KeyError) as exc:
            errors.append({'scope_id':name,'reason':str(exc)})
        scope['complete'] = False
    result = join_usage_records(records,expected_scopes=scopes,intervals=intervals)
    from autoresearch.trace.transcripts.snapshot import NATIVE_USAGE_ARCHIVE_VERSION
    result['archive_usage_version'] = NATIVE_USAGE_ARCHIVE_VERSION
    result['source_errors'] = errors
    return result
