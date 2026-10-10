"""Conservative admission of accepted same-run intel for a card validation retry.

Reused raw bytes still pass the registered intel guard in the new attempt. A
missing acceptance, changed frozen input, missing read proof or UTC+8 midnight
crossing restores the normal fresh-intel subtree.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoresearch.session_agent import artifacts, host_evidence, store

_MARKET_TIMEZONE = timezone(timedelta(hours=8))


def accepted_retry_intel(handle, code: str, previous_attempt: int, error_class: str,
                         previous_tasks: list[dict], *, now: datetime | None = None) -> dict | None:
    """Return immutable admitted evidence; never repair or mutate the old attempt."""
    if error_class != 'DOMAIN_VALIDATION':
        return None
    prefix = f'scan.l4.{code}.a{previous_attempt}'
    raw_task_id = f'l4.{code}.a{previous_attempt}.intel'
    status_task_id = f'l4.{code}.a{previous_attempt}.intel_status'
    specs = {task['task_id']: task for task in previous_tasks}
    if raw_task_id not in specs or status_task_id not in specs:
        return None
    try:
        from autoresearch.session_agent.evidence_bundle import require_read_evidence
        from autoresearch.trace.events import verify_event_chain

        entries = store.read_entries(Path(handle.workspace) / 'session/tasks.json')
        card = entries[f'l4.{code}.a{previous_attempt}.card']
        if card['state'] not in {'FAILED', 'BLOCKED'} or (card.get('error') or {}).get('code') != 'DOMAIN_VALIDATION':
            return None
        selected = [entries[raw_task_id], entries[status_task_id]]
        if any(entry['state'] != 'SUCCEEDED' or not entry.get('submission_hash')
               for entry in selected):
            return None
        raw = selected[0]
        binding = host_evidence._existing_binding(handle, raw_task_id, raw['attempt'])
        if binding is None:
            return None
        binding = host_evidence._load_binding_ref(handle, 'host-binding:' + binding['binding_id'])
        captured = datetime.fromisoformat(binding['created_at'].replace('Z', '+00:00'))
        current = now or datetime.now(timezone.utc)
        if (captured.utcoffset() is None or current.utcoffset() is None
                or captured > current
                or captured.astimezone(_MARKET_TIMEZONE).date()
                != current.astimezone(_MARKET_TIMEZONE).date()):
            return None
        frame = json.loads(artifacts.read_bytes(handle, 'research.frame'))
        if frame.get('analysis_session') != handle.analysis_date:
            return None
        hashes = {}
        for entry in selected:
            frozen = entry['claim_receipt']['input_snapshots']
            for snapshot in [*frozen, *entry['outputs']]:
                aid, expected = snapshot['artifact_id'], snapshot['sha256']
                actual = artifacts.snapshot_artifact(handle, aid)['sha256']
                if actual != expected or aid in hashes and hashes[aid] != actual:
                    return None
                hashes[aid] = actual
        required = {f'{prefix}.intel', f'{prefix}.intel_status', f'{prefix}.intel_doc',
                    f'{prefix}.intel_bundle', f'{prefix}.prompt',
                    'scan.l4.source.bundle', 'research.frame'}
        if not required <= hashes.keys():
            return None
        status = json.loads(artifacts.read_bytes(handle, f'{prefix}.intel_status'))
        if (status.get('code') != code or status.get('guard') not in {'KEPT', 'TRIMMED'}
                or status.get('availability_for_card') != 'INTEL'
                or status.get('acquisition') not in {'FULL', 'RETRIED_FULL'}):
            return None
        if not verify_event_chain(Path(handle.capsule) / 'events/events.jsonl')['ok']:
            return None
        require_read_evidence(handle, specs[raw_task_id], {'envelope': {'attempt': raw['attempt']}},
                              f'{prefix}.prompt')
        return {'intel_task_id': raw_task_id, 'status_task_id': status_task_id,
                'intel_artifact_id': f'{prefix}.intel',
                'status_artifact_id': f'{prefix}.intel_status',
                'snapshots': [{'artifact_id': aid, 'sha256': digest}
                              for aid, digest in sorted(hashes.items())]}
    except (OSError, KeyError, TypeError, ValueError, RuntimeError):
        # Reuse is an optimization only. The fresh inference path owns recovery
        # whenever original acceptance or boundary evidence cannot be verified.
        return None
