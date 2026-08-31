"""WebFetch/WebSearch PostToolUse 留痕兜底 hook 的行为契约(D7.2,task-16)。

`scripts/hooks/webtrace_posttool.py` 不在 `autoresearch` 包内(Claude Code 的
PostToolUse hook 按仓库相对路径调用独立脚本),这里用 `importlib.util` 按文件路径
动态装载,直接调它的 `main()` 喂一段 stdin JSON——不起子进程,断言精确到字段值。
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "hooks" / "webtrace_posttool.py"

RUN_ID = "20260831T235959123456Z"
_TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_ROW_FIELDS = {"ts", "session_id", "tool", "url_or_query", "response_chars", "capture_level"}

_WEBFETCH_PAYLOAD = {
    "session_id": "sess-abc123",
    "hook_event_name": "PostToolUse",
    "tool_name": "WebFetch",
    "tool_input": {"url": "https://example.com/nvda-8k", "prompt": "找业绩指引"},
    "tool_response": "NVDA raises Q3 guidance on datacenter demand.",
}

_WEBSEARCH_PAYLOAD = {
    "session_id": "sess-def456",
    "hook_event_name": "PostToolUse",
    "tool_name": "WebSearch",
    "tool_input": {"query": "英伟达 三季度 指引"},
    "tool_response": "3 results",
}


def _load_hook():
    spec = importlib.util.spec_from_file_location("webtrace_posttool", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def hook_module():
    return _load_hook()


def test_hook_noop_without_run_id(monkeypatch, tmp_path, hook_module):
    """无 AUTORESEARCH_RUN_ID → 立即 exit 0,零写盘(不碰 cwd 下任何路径)。

    `tmp_path` 里的 `_dossiers/` 是 `tests/conftest.py::_isolate_dossier_dir` 这个
    全局 autouse fixture 无条件建的,与本 hook 无关——真正要断言的是 hook 自己
    一个字节都没往 cwd 底下写(不新建 `context_*`/`reports_*` 根)。
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    before = set(tmp_path.iterdir())

    exit_code = hook_module.main(raw=json.dumps(_WEBFETCH_PAYLOAD))

    assert exit_code == 0
    assert set(tmp_path.iterdir()) == before
    assert not (tmp_path / "context_claude").exists()
    assert not (tmp_path / "reports_claude").exists()


def test_hook_appends_line(monkeypatch, tmp_path, hook_module):
    """活跃 run(capsule 目录已建、还没 finalize)→ 追加进 run 内独立文件,字段齐全。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ws, "ENGINE", "claude")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", RUN_ID)
    capsule_dir = ws.scan_run_root(RUN_ID) / "capsule"
    capsule_dir.mkdir(parents=True)

    exit_code = hook_module.main(raw=json.dumps(_WEBFETCH_PAYLOAD))

    assert exit_code == 0
    target = capsule_dir / "lineage" / "external_tools_hook.jsonl"
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert set(row) == _ROW_FIELDS
    assert row["session_id"] == "sess-abc123"
    assert row["tool"] == "WebFetch"
    assert row["url_or_query"] == "https://example.com/nvda-8k"
    assert row["response_chars"] == len("NVDA raises Q3 guidance on datacenter demand.")
    assert row["capture_level"] == "HOOK_L1"
    assert _TS_PATTERN.match(row["ts"])
    # 不写主账(external_tools.jsonl 是 finalize 才物化的),双写会破幂等
    assert not (capsule_dir / "lineage" / "external_tools.jsonl").exists()


def test_hook_falls_back_to_ledger_when_run_finalized(monkeypatch, tmp_path, hook_module):
    """capsule.json 已写终稿(=finalize 已跑过)→ 不再写 run 内,落 run 外账本。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ws, "ENGINE", "claude")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", RUN_ID)
    capsule_dir = ws.scan_run_root(RUN_ID) / "capsule"
    capsule_dir.mkdir(parents=True)
    (capsule_dir / "capsule.json").write_text("{}", encoding="utf-8")

    exit_code = hook_module.main(raw=json.dumps(_WEBSEARCH_PAYLOAD))

    assert exit_code == 0
    run_internal = capsule_dir / "lineage" / "external_tools_hook.jsonl"
    assert not run_internal.exists()
    ledger_path = ws.reports_root() / "analyze" / "_ledger" / "webtrace" / f"{RUN_ID}.jsonl"
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert set(row) == _ROW_FIELDS
    assert row["tool"] == "WebSearch"
    assert row["url_or_query"] == "英伟达 三季度 指引"
    assert row["response_chars"] == len("3 results")
    assert row["capture_level"] == "HOOK_L1"


def test_hook_falls_back_to_ledger_when_no_run_found(monkeypatch, tmp_path, hook_module):
    """RUN_ID 在场但磁盘上找不到对应 run 目录(两种 kind 都探不到)→ 落 run 外账本。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ws, "ENGINE", "claude")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", RUN_ID)

    exit_code = hook_module.main(raw=json.dumps(_WEBFETCH_PAYLOAD))

    assert exit_code == 0
    assert not (tmp_path / "context_claude").exists()
    ledger_path = ws.reports_root() / "analyze" / "_ledger" / "webtrace" / f"{RUN_ID}.jsonl"
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert set(row) == _ROW_FIELDS
    assert row["url_or_query"] == "https://example.com/nvda-8k"
    assert row["response_chars"] == len("NVDA raises Q3 guidance on datacenter demand.")
