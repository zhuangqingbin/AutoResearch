"""Host evidence adapter for the deterministic sector terrain candidate."""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.common.atomic import canonical_json, sha256_bytes


def source_binding(handle, *, task_id: str | None = None, attempt: int | None = None):
    """Use existing B2 material-claim evidence; JSON supplied by a model grants nothing."""
    from autoresearch.contracts.execution import parse_aware
    from autoresearch.contracts.source_time import latest_possible, validate_source_times
    from autoresearch.news.material_claims import evaluate_material_claim
    from autoresearch.session_agent import artifacts, store
    from autoresearch.trace.blobs import blob_path
    from autoresearch.trace.source_receipts import materialize_tool_receipts, read_receipts

    with artifacts.open_artifact(handle, 'research.frame') as stream:
        frame = json.load(stream)
    materialize_tool_receipts(handle)
    receipts = {row['receipt_id']: row for row in read_receipts(handle.capsule)}
    owner = Path(handle.workspace) / 'session/tasks.json'
    entries = store.read_entries(owner) if owner.is_file() else {}
    accepted = {key: value['attempt'] for key, value in entries.items() if value['state'] == 'SUCCEEDED'}
    if task_id is not None:
        accepted[task_id] = attempt

    def check(event, request):
        if request['knowledge_cutoff'] != frame['knowledge_cutoff']:
            return {'verdict': 'UNKNOWN', 'reason': 'FRAME_MISMATCH'}
        receipt = receipts.get(event['source_observation_id'])
        if receipt is None or receipt['engine'] != handle.engine or receipt['run_id'] != handle.run_id:
            return {'verdict': 'UNKNOWN', 'reason': 'SOURCE_NOT_BOUND'}
        timing = receipt.get('source_timing')
        if timing is None:
            return {'verdict': 'UNKNOWN', 'reason': 'SOURCE_TIME_UNAVAILABLE'}
        validate_source_times(timing)
        for declared, field in (('published_at', 'published_at'), ('available_at', 'first_available_at')):
            if declared == 'published_at' and 'published_at' not in event:
                continue  # Stable-fact records declare available_at only.
            actual = timing[field]
            if (actual is None or parse_aware(event.get(declared)) != parse_aware(actual)
                    or latest_possible(actual, timing['timestamp_precision'][field]) > parse_aware(frame['knowledge_cutoff'])):
                return {'verdict': 'UNKNOWN', 'reason': 'SOURCE_TIME_UNBOUND'}
        if receipt['payload_hash'] != event['source_text_sha256']:
            return {'verdict': 'UNKNOWN', 'reason': 'SOURCE_HASH_MISMATCH'}
        path = blob_path(handle.capsule, receipt['payload_hash'])
        if not path.is_file() or sha256_bytes(path.read_bytes()) != receipt['payload_hash']:
            return {'verdict': 'UNKNOWN', 'reason': 'SOURCE_BYTES_UNAVAILABLE'}
        raw = path.read_text(encoding='utf-8')
        if event['source_url'] not in json.dumps(receipt.get('normalized_params', {}), ensure_ascii=False) and event['source_url'] not in raw:
            return {'verdict': 'UNKNOWN', 'reason': 'SOURCE_URL_UNBOUND'}
        if event.get('quote') and event['quote'] not in raw:
            return {'verdict': 'UNKNOWN', 'reason': 'QUOTE_NOT_BOUND'}
        groups = {}
        for path in sorted((Path(handle.capsule) / 'evidence/material_claims').glob('*.json')):
            value = json.loads(path.read_text())
            producer = entries.get(value.get('task_id'), {}).get('spec', {})
            if (value.get('engine') != handle.engine or value.get('run_id') != handle.run_id
                    or accepted.get(value.get('task_id')) != value.get('attempt')
                    or producer.get('subject') != request.get('industry')):
                continue
            if path.stem != value.get('sidecar_id'):
                return {'verdict': 'UNKNOWN', 'reason': 'SIDECAR_IDENTITY_MISMATCH'}
            groups.setdefault(value['claim_id'], []).append(value)
        matched = []
        for versions in groups.values():
            if not any(value['statement_sha256'] == sha256_bytes(event['claim'].encode())
                       and event['source_observation_id'] in value['source_receipt_ids'] for value in versions):
                continue
            # Apply the B2 logical-claim identity rule before accepting any version.
            identities = {canonical_json({'statement': value['statement_sha256'],
                                          'event': value.get('claim_event')}) for value in versions}
            if len(identities) != 1:
                return {'verdict': 'UNKNOWN', 'reason': 'CURRENT_VERSION_AMBIGUOUS'}
            matched.extend(evaluate_material_claim(handle.capsule, value, decision_frame=frame)
                           for value in versions)
        if matched and all(result.get('verdict') == 'PASS' for result in matched):
            return {**matched[0], 'source_timing': timing}
        return {'verdict': 'UNKNOWN', 'reason': 'SEMANTIC_SOURCE_BINDING_UNAVAILABLE'}
    return check


def replay_terrain(snapshot: dict) -> tuple[str, dict]:
    """Render from the frozen owner source receipt; never reconstruct host evidence."""
    from autoresearch.sector.reuse import stable_fact_snapshot
    from autoresearch.sector.terrain import digest, render_terrain
    required = {'schema_version', 'pack', 'request', 'supplement', 'stable_facts', 'bound_event_hashes'}
    if snapshot.get('schema_version') == 2:
        required.add('bound_event_timings')
    if set(snapshot) != required or snapshot['schema_version'] not in {1, 2}:
        raise ValueError('invalid frozen terrain source snapshot')
    bound = set(snapshot['bound_event_hashes'])
    events = (snapshot['supplement'] or {}).get('events', [])
    if bound != {digest(event) for event in events}:
        raise ValueError('frozen terrain event binding coverage differs')
    timings = snapshot.get('bound_event_timings', {})
    if snapshot['schema_version'] == 2:
        from autoresearch.contracts.execution import parse_aware
        from autoresearch.contracts.source_time import latest_possible, validate_source_times
        if set(timings) != bound:
            raise ValueError('frozen terrain timing coverage differs')
        for event in events:
            timing = validate_source_times(timings[digest(event)])
            for declared, field in (('published_at', 'published_at'), ('available_at', 'first_available_at')):
                actual = timing[field]
                if (actual is None or parse_aware(event[declared]) != parse_aware(actual)
                        or latest_possible(actual, timing['timestamp_precision'][field]) > parse_aware(snapshot['request']['knowledge_cutoff'])):
                    raise ValueError('frozen terrain timing differs from source')
    def replay_binding(event, request):
        proof = {'verdict': 'PASS' if digest(event) in bound else 'UNKNOWN'}
        if digest(event) in timings:
            proof['source_timing'] = timings[digest(event)]
        return proof
    text = render_terrain(snapshot['pack'], request=snapshot['request'], supplement=snapshot['supplement'],
        bind_claim=replay_binding, stable_facts=snapshot['stable_facts'])
    return text, stable_fact_snapshot(snapshot['pack'], snapshot['stable_facts'])


def replay_operation(context) -> list[dict]:
    from autoresearch.common.atomic import atomic_write_json
    from autoresearch.session_agent.replay_adapters.common import source_snapshot
    snapshot = source_snapshot(context, 'sector.terrain.snapshot.v1')
    text, stable = replay_terrain(snapshot)
    for ref in context.unit['expected_outputs']:
        key = ref['artifact_id']
        if key.endswith('.stable.snapshot'):
            atomic_write_json(context.output_path(key), stable)
        elif key.endswith(('.report', '.brief')):
            context.output_path(key).write_text(text, encoding='utf-8')
        else:
            raise ValueError('unknown terrain replay output')
    return []
