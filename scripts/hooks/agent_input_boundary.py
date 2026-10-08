#!/usr/bin/env python3
"""Fail-closed C4 PreToolUse policy; configuration is not observed enforcement."""
from __future__ import annotations

import json
import re
import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Load the small stdlib policy without importing session_agent's package initializer.
ACCESS = runpy.run_path(str(REPO_ROOT / 'autoresearch/session_agent/task_access.py'))

GUARDED_AGENT_TYPES = frozenset({'company_intel', 'dossier_init', 'global_intel', 'l3_rank',
    'l4_card', 'l4_intel', 'macro_brief', 'sector_brief', 'sector_intel', 'us_intel',
    'ensemble_review', 'l3_repair', 'scan_strategist', 'stock_full', 'macro_full', 'sector_full'})
EXEMPT_AGENT_TYPES = frozenset({'default', 'general_purpose', 'explore', 'explorer', 'worker', 'plan', 'claude',
                              'deterministic_command_relay', 'deterministic_json_relay'})
MARKER = 'AGENT_INPUT_BOUNDARY'


def normalize_agent_type(value):
    return re.sub(r'[\s-]+', '_', str(value).strip().lower())


def _deny(reason):
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
            'permissionDecisionReason': f'{MARKER}: {reason}. Use registered structured file tools or the canonical task file broker; ask the root to bind the host identity or authorize the declared deep stage.'}}


def decide(payload: dict, engine: str = 'claude') -> dict | None:
    try:
        if not isinstance(payload, dict):
            return _deny('invalid hook payload')
        role = normalize_agent_type(payload.get('agent_type', ''))
        bound_identity = ACCESS['has_binding'](payload, engine, repo_root=REPO_ROOT)
        if not bound_identity:
            if role in EXEMPT_AGENT_TYPES:
                return None
            if not role and not payload.get('agent_id'):
                return None  # Explicit unbound main session, never a bound headless child.
        bound = ACCESS['load_bound_access'](payload, engine, repo_root=REPO_ROOT)
        tool, args = payload.get('tool_name'), payload.get('tool_input')
        if not isinstance(args, dict):
            return _deny('malformed structured tool input')
        if tool in {'Bash', 'exec_command', 'functions.exec_command'}:
            command = ACCESS['broker_tool_command'](tool, args)
            ACCESS['validate_broker_call'](bound, command, payload)
            return None
        if tool in {'Read', 'read_file', 'Write', 'write_file', 'Edit', 'edit_file', 'MultiEdit'}:
            mode = 'read' if tool in {'Read', 'read_file'} else 'write'
            fields = [args[key] for key in ('file_path', 'path') if key in args]
            if len(fields) != 1 or not ACCESS['path_allowed'](bound, fields[0], mode, cwd=payload.get('cwd') or str(REPO_ROOT)):
                return _deny('path is outside frozen task authorization')
            return None
        if tool == 'Grep' and ACCESS['path_allowed'](
                bound, args.get('path'), 'read', cwd=payload.get('cwd') or str(REPO_ROOT)):
            return None
        if ((tool in {'WebSearch', 'WebFetch', 'web_search', 'web_fetch', 'web.run'}
             or (engine == 'codex' and tool == 'webrun'))
                and 'WEB' in bound['manifest']['tool_policy'].split('_')):
            return None
        return _deny('tool is not registered for this task')
    except Exception as exc:
        return _deny(f'missing, invalid or stale task access binding ({type(exc).__name__}: {exc})')


def main(stdin=None, stdout=None, argv=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    argv = sys.argv[1:] if argv is None else argv
    try:
        payload = json.loads(stdin.read())
        result = decide(payload, argv[0] if argv else 'claude')
        ACCESS['record_boundary_event'](payload, argv[0] if argv else 'claude', result,
                                        repo_root=REPO_ROOT)
    except Exception:
        result = _deny('hook payload parse failure')
    if result:
        stdout.write(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
