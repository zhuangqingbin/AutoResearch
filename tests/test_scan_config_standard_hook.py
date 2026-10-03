"""scan_config 标准的 PostToolUse hook(`scripts/hooks/scan_config_standard.sh`)行为契约。

Claude Code 在 Edit / Write / MultiEdit 之后、Codex 在 Bash 之后把工具负载喂到本脚本 stdin:
负载里出现受管路径(`contracts/scan_config.GUARDED_PATHS`)才起 Python 跑
`python -m autoresearch.scan.config_standard --hook`;违规 → exit 2 + 报告(编辑者必须改到零违规);
无关路径 → 不起 Python、无输出、exit 0。任何意外异常都放行(hook 崩溃会打断工具调用)。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from autoresearch.contracts import scan_config as reg

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "hooks" / "scan_config_standard.sh"
PROD = ROOT / reg.CONFIG_PATH


def _run(payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(WRAPPER)], input=json.dumps(payload), text=True,
                          capture_output=True, cwd=ROOT, env={**_base_env(), **(env or {})}, timeout=180)


def _base_env() -> dict:
    import os
    return {k: v for k, v in os.environ.items() if not k.startswith("SCAN_CONFIG_LINT")}


def _claude_edit(path: str) -> dict:
    return {"session_id": "s", "hook_event_name": "PostToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}, "tool_response": {}}


def _codex_bash(command: str) -> dict:
    return {"session_id": "s", "hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, "tool_response": {}}


def test_unrelated_edit_passes_silently():
    r = _run(_claude_edit(str(ROOT / "autoresearch/scan/menu.py")))
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_edit_on_the_config_runs_the_lint_and_is_clean_today():
    r = _run(_claude_edit(str(PROD)))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 条违规" in r.stdout + r.stderr


def test_edit_on_the_registry_or_skill_doc_also_triggers():
    for rel in ("autoresearch/contracts/scan_config.py", ".claude/skills/scan-market/SKILL.md"):
        r = _run(_claude_edit(str(ROOT / rel)))
        assert "条违规" in r.stdout + r.stderr, rel


def test_codex_bash_touching_the_config_triggers():
    r = _run(_codex_bash("sed -i '' 's/x/y/' .claude/skills/scan-market/scan_config.jsonc"))
    assert "条违规" in r.stdout + r.stderr


def test_violations_come_back_as_a_blocking_error(tmp_path):
    bad = tmp_path / "scan_config.jsonc"
    text = PROD.read_text(encoding="utf-8").replace('"recall_n": 1000,                  // L1 召回收口数',
                                                     '"recall_n": 1000,')
    assert text != PROD.read_text(encoding="utf-8")
    bad.write_text(text, encoding="utf-8")
    r = _run(_claude_edit(str(PROD)), env={"SCAN_CONFIG_LINT_PATH": str(bad)})
    assert r.returncode == 2
    assert "[R5]" in r.stderr and "SKILL.md" in r.stderr


def test_settings_wire_the_hook_for_both_engines():
    claude = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    post = claude["hooks"]["PostToolUse"]
    entry = next(e for e in post if "scan_config_standard.sh" in json.dumps(e))
    assert set(entry["matcher"].split("|")) >= {"Edit", "Write", "MultiEdit"}
    codex = json.loads((ROOT / ".codex/hooks.json").read_text(encoding="utf-8"))
    codex_post = codex["hooks"]["PostToolUse"]
    assert any("scan_config_standard.sh" in json.dumps(e) and e["matcher"] == "Bash" for e in codex_post)


def test_shell_prefilter_covers_every_guarded_path():
    """sh 层的路径过滤是 POSIX 字面量,不能 import 注册表 —— 用测试锁两份清单一致。"""
    src = WRAPPER.read_text(encoding="utf-8")
    for guarded in reg.GUARDED_PATHS:
        assert f"'{guarded}'" in src, guarded
