"""Token growth guard M8 / red line R8: the weekly ledger splits production from development."""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.research import token_ledger

START, END = "2026-10-05T00:00:00Z", "2026-10-12T00:00:00Z"


def _claude(folder: Path, name: str, cwd: str, *, stamp="2026-10-08T10:00:00Z", dup=True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.jsonl"
    message = {"id": f"m-{name}", "model": "claude-opus-5-5", "usage": {
        "input_tokens": 10, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 9000, "output_tokens": 500}}
    line = json.dumps({"type": "assistant", "timestamp": stamp, "cwd": cwd, "message": message})
    old = json.dumps({"type": "assistant", "timestamp": "2026-09-01T00:00:00Z", "cwd": cwd,
                      "message": {**message, "id": "m-old"}})
    path.write_text("\n".join([line, line if dup else "", old]) + "\n", encoding="utf-8")   # streamed twice
    return path


def _codex(root: Path, name: str, cwd: str) -> Path:
    folder = root / "2026" / "10" / "08"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-{name}.jsonl"
    events = [{"type": "session_meta", "timestamp": "2026-10-08T10:00:00Z", "payload": {"cwd": cwd}},
              {"type": "event_msg", "timestamp": "2026-10-08T10:01:00Z", "payload": {
                  "type": "token_count", "info": {"last_token_usage": {"input_tokens": 20000, "cached_input_tokens": 15000,
                                                                       "output_tokens": 300, "reasoning_output_tokens": 100}},
                  "rate_limits": {"primary": {"used_percent": 40.0}, "secondary": {"used_percent": 60.0}}}}]
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def test_ledger_dedupes_messages_and_splits_production_from_development(tmp_path):
    repo = tmp_path / "repo"
    projects = tmp_path / "claude"
    bound = _claude(projects / "p", "headless-card", "/x/TradingAgents")
    _claude(projects / "p", "dev-session", "/x/TradingAgents")
    _claude(projects / "q", "novel", "/x/NovelProject")
    codex_root = tmp_path / "codex"
    rollout = _codex(codex_root, "thread-a", "/x/TradingAgents")
    _codex(codex_root, "thread-b", "/x/TradingAgents")
    usage = repo / "reports_claude" / "scan" / "_failed" / "R" / "capsule" / "usage" / "_token_usage.json"
    usage.parent.mkdir(parents=True)
    usage.write_text(json.dumps({"rows": [{"path": str(bound)}]}), encoding="utf-8")
    record = repo / "context_codex" / "scan_runs" / "R" / "staging" / "D" / "_dispatch" / "headless" / "t.a1.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"transcript_path": str(rollout)}), encoding="utf-8")

    value = token_ledger.ledger(START, END, repo=repo, claude_root=projects, codex_root=codex_root)
    rows = {(r["engine"], r["project"], r["kind"]): r for r in value["rows"]}
    prod = rows[("claude", "TradingAgents", "production")]
    assert prod["transcripts"] == 1 and prod["messages"] == 1                 # duplicate stream line + old line dropped
    assert prod["cache_read"] == 9000 and prod["usd"] > 0
    assert rows[("claude", "TradingAgents", "development")]["transcripts"] == 1
    assert rows[("claude", "NovelProject", "development")]["transcripts"] == 1
    assert rows[("codex", "TradingAgents", "production")]["input_tokens"] == 20000
    assert rows[("codex", "TradingAgents", "development")]["transcripts"] == 1
    assert value["codex_limits"]["primary_max"] == 40.0 and value["codex_limits"]["secondary_max"] == 60.0
    assert "production" in token_ledger.render(value)
