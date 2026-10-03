"""Dossier-only maintenance over a committed publication, backed by operation evidence.

The head contains references, not a second evidence or publication format. Every
read verifies the complete opening/candidate chain. A new publication starts a
new chain; historical operations remain available for offline replay.
"""
from __future__ import annotations

import json
import re

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_bytes, atomic_write_json, sha256_bytes
from autoresearch.common.published_state import target_locks
from autoresearch.dossier import schema
from autoresearch.trace.operation_evidence import load_operation_evidence


class MaintenanceConflict(RuntimeError):
    """The dossier or its publication base changed while a patch was computed."""


def _head_path(code):
    if not re.fullmatch(r'\d{6}', code):
        raise ValueError('invalid dossier maintenance subject')
    return schema.DOSSIER_DIR / '_maintenance' / f'{code}.json'


def _head(code, base_hash):
    path = _head_path(code)
    if not path.exists():
        return None
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('dossier maintenance head symlink')
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict)
            or set(value) != {'schema_version', 'engine', 'code', 'base_sha256', 'operations'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['engine'] != ws.ENGINE or value['code'] != code
            or not isinstance(value['base_sha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', value['base_sha256'])
            or not isinstance(value['operations'], list)
            or any(not isinstance(item, str) or not re.fullmatch(r'[0-9a-f]{64}', item)
                   for item in value['operations'])
            or len(value['operations']) != len(set(value['operations']))):
        raise ValueError('invalid dossier maintenance head')
    return value if value['base_sha256'] == base_hash else None


def _candidate(code, opening, operation_id):
    if not isinstance(operation_id, str) or not re.fullmatch(r'[0-9a-f]{64}', operation_id):
        raise ValueError('invalid dossier operation identity')
    base = schema.DOSSIER_DIR / '_operation_evidence'
    path = base / operation_id
    if base.is_symlink() or path.is_symlink():
        raise ValueError('dossier operation symlink')
    root, value = load_operation_evidence(path)
    if (value['operation_id'] != operation_id or value['engine'] != ws.ENGINE
            or value['operation'] not in {'dossier.reconcile', 'dossier.delta'}
            or value['status'] != 'SUCCEEDED' or value['error'] is not None
            or value['parameters'].get('code') != code):
        raise ValueError('dossier operation identity/status mismatch')
    def artifact(kind, name):
        matches = [ref for ref in value[kind] if ref['artifact_id'] == name]
        if len(matches) != 1:
            raise ValueError(f'dossier operation missing/duplicate artifact:{name}')
        return (root / matches[0]['captured_path']).read_bytes()
    if artifact('input_refs', 'dossier.opening') != opening:
        raise ValueError('dossier operation opening chain mismatch')
    candidate = artifact('output_refs', 'dossier.candidate')
    result = json.loads(artifact('output_refs', 'dossier.result'))
    if not isinstance(result, dict) or result.get('code') != code:
        raise ValueError('dossier operation result subject mismatch')
    for payload in (opening, candidate):
        if schema.parse_frontmatter(payload.decode('utf-8')).get('code') != code:
            raise ValueError('dossier operation document subject mismatch')
    expected = [{'kind': 'DOSSIER_PATCH', 'code': code,
                 'before_sha256': sha256_bytes(opening), 'after_sha256': sha256_bytes(candidate)}]
    if value['effects'] != expected:
        raise ValueError('dossier operation effects mismatch')
    return candidate


def read_overlay(code: str, base: bytes) -> bytes:
    head = _head(code, sha256_bytes(base))
    current = base
    for operation_id in (head or {}).get('operations', []):
        current = _candidate(code, current, operation_id)
    return current


def commit(code: str, opening: bytes, base_hash: str | None, operation_id: str) -> bytes:
    """CAS against both the visible opening and its underlying publication base."""
    with target_locks(ws.context_root() / '_published_state', [f'dossier.stock.{code}']):
        current, current_base = schema.read_dossier_snapshot(code)
        if current != opening or current_base != base_hash:
            raise MaintenanceConflict('CONFLICT: dossier changed while maintenance was prepared')
        candidate = _candidate(code, opening, operation_id)
        if base_hash is not None and candidate != opening:
            head = _head(code, base_hash) or {
                'schema_version': 1, 'engine': ws.ENGINE, 'code': code,
                'base_sha256': base_hash, 'operations': [],
            }
            head['operations'].append(operation_id)
            atomic_write_json(_head_path(code), head)
        # The fixed copy remains compatible; for published dossiers it is never
        # the authority. A crash after the head commit still reads the candidate.
        atomic_write_bytes(schema.dossier_path(code), candidate)
        return candidate
