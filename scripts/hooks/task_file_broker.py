#!/usr/bin/env python3
"""Only entry point admitted by the C4 canonical shell adapter (stdlib, -I -S)."""
import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
policy = runpy.run_path(str(ROOT / 'autoresearch/session_agent/task_access.py'))
try:
    if len(sys.argv) != 2:
        raise ValueError('one canonical argument required')
    data = json.loads(sys.argv[1])
    if policy['encode_broker_argument'](data) != sys.argv[1]:
        raise ValueError('noncanonical broker data argument')
    # Engine is checked against the trusted hook binding before this command is allowed.
    if data.get('engine') not in {'codex', 'claude'}:
        raise ValueError('invalid engine')
    result = policy['execute_broker'](data, data['engine'])
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
    sys.exit(2)
