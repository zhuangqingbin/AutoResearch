"""Strict metering sidecar; existing task and receipt contracts are unchanged."""
import math

from autoresearch.contracts.session_task import require_exact_fields

METRICS = ('input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_creation_tokens',
           'reasoning_output_tokens', 'host_usage_records', 'host_model_messages',
           'model_calls', 'duration_seconds')


def _number(value):
    if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
        raise ValueError('metering requires finite nonnegative numbers or null')


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError('metering count/ordinal must be a nonnegative integer')


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('invalid metering digest')


def validate_metering(value):
    require_exact_fields(value, {'schema_version', 'engine', 'run_id', 'evidence_plan_hash', 'attempts',
                                 'dispatch_count', 'dispatch_count_basis', 'not_dispatched_count', 'metrics', 'estimated_price'})
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['engine'] not in {'codex', 'claude'}:
        raise ValueError('invalid metering identity')
    if value['dispatch_count_basis'] != 'FROZEN_HANDOFF_NOT_MODEL_EXECUTION':
        raise ValueError('invalid dispatch count basis')
    if not isinstance(value['run_id'], str) or not value['run_id']:
        raise ValueError('metering run identity required')
    digest = value['evidence_plan_hash']
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('invalid metering evidence plan hash')
    if not isinstance(value['attempts'], list):
        raise ValueError('metering attempts must be a list')
    _count(value['dispatch_count'])
    _count(value['not_dispatched_count'])
    keys = set()
    for row in value['attempts']:
        require_exact_fields(row, {'task_id', 'attempt', 'role', 'state', 'dispatch_count', 'evidence_status',
                                   'evidence_source', 'requested', 'resolved', 'observed', 'metrics',
                                   'estimated_price', 'proxy_input_chars', 'errors'})
        key = row['task_id'], row['attempt']
        if key in keys or type(row['attempt']) is not int or row['attempt'] < 1:
            raise ValueError('invalid/duplicate metering attempt')
        keys.add(key)
        if type(row['dispatch_count']) is not int or row['dispatch_count'] not in {0, 1}:
            raise ValueError('invalid handoff count')
        if row['evidence_status'] not in {'MISSING', 'INVALID', 'PRESENT', 'NOT_DISPATCHED'}:
            raise ValueError('invalid metering evidence status')
        for name in ('requested', 'resolved', 'observed'):
            require_exact_fields(row[name], {'model', 'effort', 'source'} | ({'status', 'observations'} if name == 'observed' else set()))
            for field in ('model', 'effort'):
                if row[name][field] is not None and not isinstance(row[name][field], str):
                    raise ValueError('model/effort must be string or null')
            if not isinstance(row[name]['source'], str) or not row[name]['source']:
                raise ValueError('model/effort source required')
        if row['observed']['status'] not in {'UNKNOWN', 'OBSERVED', 'MIXED'}:
            raise ValueError('invalid observed model status')
        for observation in row['observed']['observations']:
            require_exact_fields(observation, {'ordinal', 'model', 'effort'})
            _count(observation['ordinal'])
            for field in ('model', 'effort'):
                if observation[field] is not None and not isinstance(observation[field], str):
                    raise ValueError('invalid model observation')
        if row['evidence_source'] is not None:
            source = row['evidence_source']
            require_exact_fields(source, {'binding_id', 'archive_sha256', 'raw_path', 'start_ordinal', 'end_ordinal'})
            for field in ('binding_id', 'archive_sha256'):
                _digest(source[field])
            for field in ('start_ordinal', 'end_ordinal'):
                _count(source[field])
            if source['end_ordinal'] < source['start_ordinal'] or not isinstance(source['raw_path'], str) or not source['raw_path']:
                raise ValueError('invalid metering archive interval')
        require_exact_fields(row['metrics'], set(METRICS))
        for number in row['metrics'].values():
            _number(number)
        _number(row['proxy_input_chars'])
        _number(row['estimated_price'])
        if row['evidence_status'] != 'PRESENT' and (any(v is not None for v in row['metrics'].values()) or row['observed']['status'] != 'UNKNOWN'):
            raise ValueError('unbound metering must remain unknown')
    count = sum(row['dispatch_count'] for row in value['attempts'])
    if value['dispatch_count'] != count or value['not_dispatched_count'] != len(keys) - count:
        raise ValueError('metering handoff totals mismatch')
    require_exact_fields(value['metrics'], set(METRICS))
    for field, summary in value['metrics'].items():
        require_exact_fields(summary, {'value', 'observed_total', 'observed_count', 'expected_count', 'coverage', 'status'})
        measured = [r['metrics'][field] for r in value['attempts'] if r['dispatch_count'] and r['metrics'][field] is not None]
        for field_name in ('observed_count', 'expected_count'):
            _count(summary[field_name])
        for field_name in ('value', 'observed_total', 'coverage'):
            _number(summary[field_name])
        observed = len(measured)
        expected = {'value': sum(measured) if observed == count and count else None,
                    'observed_total': sum(measured) if measured else None,
                    'observed_count': observed, 'expected_count': count,
                    'coverage': observed / count if count else None,
                    'status': 'COMPLETE' if count and observed == count else 'PARTIAL' if observed else 'MISSING'}
        if summary != expected:
            raise ValueError('metering coverage mismatch')
    _number(value['estimated_price'])
    return value
