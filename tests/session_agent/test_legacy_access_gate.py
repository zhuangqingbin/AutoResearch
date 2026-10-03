"""Legacy research cannot launch unbound children; collectors remain independent."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize('name', ['scan-market', 'l4-stock', 'dossier-init'])
def test_legacy_workflows_refuse_before_first_effect(name):
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    source = Path(f'.claude/workflows/{name}.js')
    args = {'date': '2026-09-30', 'run_id': '20260930T010203000000Z', 'code': '600000',
            'attempt': 1, 'config': {'x': 1}, 'cfg': {'x': 1}, 'engine': 'codex'}
    script = """const fs=require('fs'); const F=Object.getPrototypeOf(async function(){}).constructor;
const source=fs.readFileSync(process.argv[1],'utf8').replace(/^export const meta/m,'const meta');
let calls=0; const effect=()=>{calls++;throw new Error('unexpected effect')};
new F('agent','parallel','pipeline','log','phase','args','budget','workflow',source)(effect,effect,effect,effect,effect,JSON.parse(process.argv[2]),{},effect)
.catch(e=>console.log(JSON.stringify({error:e.message,calls})));"""
    result = subprocess.run([node, '-e', script, str(source), json.dumps(args)], capture_output=True, text=True, check=True)
    value = json.loads(result.stdout)
    assert value['calls'] == 0
    assert 'HOST_CAPABILITY_REQUIRED' in value['error']


def test_preflight_cli_and_legacy_python_begin_refuse(tmp_path, monkeypatch, capsys):
    from autoresearch.analyze import runctl
    from autoresearch.session_agent import task_access as access
    from autoresearch.trace import capsule
    assert access.main(['preflight', '--orchestration', 'legacy']) == 2
    value = json.loads(capsys.readouterr().out)
    assert value['status'] == 'HOST_CAPABILITY_REQUIRED'
    monkeypatch.setattr(capsule, 'begin_run', lambda *a, **k: pytest.fail('no capsule mutation'))
    with pytest.raises(ValueError, match='HOST_CAPABILITY_REQUIRED'):
        runctl.begin('600519.SS', '2026-09-30', mode='LITE', legacy_reason='test')
    assert capsule.main(['begin', 'scan-market', '2026-09-30', '--engine', 'codex', '--legacy-reason', 'test']) != 0
    assert 'HOST_CAPABILITY_REQUIRED' in capsys.readouterr().err
