"""Claude Code's native Read result shape must count as a complete structured read.

The rows below copy the shape Claude Code 2.1.x writes: the model-visible
``tool_result`` text is line-numbered (and may carry an appended reminder), while
the host records the raw file text in ``toolUseResult.file``.
"""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from autoresearch.common.atomic import sha256_bytes
from autoresearch.trace.read_observation import observe_read
from autoresearch.trace.transcripts.base import TranscriptRef
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter

BODY = "## 深核\n现金流/净利 0.92\n\n"


def _rendered(body: str) -> str:
    lines = body.split("\n")
    text = "\n".join(f"{index}\t{line}" for index, line in enumerate(lines, 1))
    return text + "\n\n<system-reminder>\nhost appended reminder\n</system-reminder>\n"


def claude_read_rows(path: str, body: str = BODY, *, call_id: str = "toolu_deep",
                     host: dict | None = None, extra_block: bool = False) -> list[dict]:
    lines = len(body.split("\n"))
    file_meta = {"filePath": path, "content": body, "numLines": lines,
                 "startLine": 1, "totalLines": lines}
    if host is not None:
        file_meta.update(host)
    blocks = [{"type": "tool_result", "tool_use_id": call_id, "content": _rendered(body)}]
    if extra_block:
        blocks.append({"type": "tool_result", "tool_use_id": "toolu_other", "content": "x"})
    common = {"sessionId": "host-session", "agentId": "child", "version": "2.1.285"}
    return [
        {**common, "type": "user", "timestamp": "2026-10-01T07:00:00Z",
         "message": {"role": "user", "content": "read the declared deep input"}},
        {**common, "type": "assistant", "timestamp": "2026-10-01T07:00:01Z",
         "message": {"id": "msg-1", "role": "assistant", "content": [
             {"type": "tool_use", "id": call_id, "name": "Read", "input": {"file_path": path}}]}},
        {**common, "type": "user", "timestamp": "2026-10-01T07:00:02Z",
         "message": {"role": "user", "content": blocks},
         "toolUseResult": {"type": "text", "file": file_meta}},
    ]


def _stats(tmp_path, rows):
    source = tmp_path / "agent-child.jsonl"
    source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                      encoding="utf-8")
    adapter = ClaudeTranscriptAdapter(projects_root=tmp_path)
    return adapter.stats_from_rows(rows, TranscriptRef(engine="claude", path=source))


def _normalized(tmp_path, rows) -> dict:
    """The same JSON shape the capsule writes into agents/normalized/*.json."""
    from autoresearch.trace.capsule import _operation_row

    stats = _stats(tmp_path, rows)
    return {"engine": "claude",
            "items": [{"index": item.index, "kind": item.kind,
                       "payload": json.loads(json.dumps(dict(item.payload))),
                       "timestamp": item.timestamp} for item in stats.normalized.items],
            "operations": [_operation_row(op) for op in stats.operations]}


def _observe(normalized: dict, path: str, data: bytes):
    identity = {"engine": "claude", "run_id": "run-1", "task_id": "stock.card", "attempt": 1}
    binding = {**identity, "binding_id": "b" * 64, "session_ref": "host-session",
               "context_ref": "child"}
    return observe_read(normalized=normalized, binding=binding, identity=identity,
                        artifact_id="stock.deep", artifact_path=path, data=data)


def test_adapter_keeps_host_structured_read_digest(tmp_path):
    path = str(tmp_path / "600519.SS_2026-09-30_slim_deep.md")
    stats = _stats(tmp_path, claude_read_rows(path))
    results = [item for item in stats.normalized.items if item.kind == "tool_result"]
    assert len(results) == 1
    raw = BODY.encode("utf-8")
    assert dict(results[0].payload["host_read"]) == {
        "file_path": path, "content_sha256": sha256_bytes(raw), "byte_count": len(raw),
        "start_line": 1, "num_lines": len(BODY.split("\n")),
        "total_lines": len(BODY.split("\n")),
    }
    # The model-visible rendering is preserved untouched next to the host digest.
    assert results[0].payload["content"] == _rendered(BODY)


@pytest.mark.parametrize("mutation", ["two_results", "not_text", "bad_lines", "no_file"])
def test_adapter_never_guesses_an_ambiguous_host_read(tmp_path, mutation):
    path = str(tmp_path / "deep.md")
    rows = claude_read_rows(path, extra_block=mutation == "two_results")
    if mutation == "not_text":
        rows[-1]["toolUseResult"]["type"] = "image"
    elif mutation == "bad_lines":
        rows[-1]["toolUseResult"]["file"]["numLines"] = True
    elif mutation == "no_file":
        rows[-1]["toolUseResult"] = "Error: file missing"
    stats = _stats(tmp_path, rows)
    assert all("host_read" not in item.payload for item in stats.normalized.items)


def test_complete_claude_read_is_observed(tmp_path):
    target = tmp_path / "600519.SS_2026-09-30_slim_deep.md"
    target.write_text(BODY, encoding="utf-8")
    normalized = _normalized(tmp_path, claude_read_rows(str(target)))
    proof = _observe(normalized, str(target), target.read_bytes())
    assert proof is not None and proof["call_id"] == "toolu_deep"
    assert proof["artifact_sha256"] == sha256_bytes(target.read_bytes())


@pytest.mark.parametrize("host, reason", [
    ({"startLine": 2}, "paged"),
    ({"numLines": 1}, "truncated"),
    ({"content": "## 深核\n"}, "different bytes"),
    ({"filePath": "/elsewhere/deep.md"}, "other file"),
])
def test_partial_or_mismatched_claude_read_is_not_observed(tmp_path, host, reason):
    target = tmp_path / "deep.md"
    target.write_text(BODY, encoding="utf-8")
    normalized = _normalized(tmp_path, claude_read_rows(str(target), host=host))
    assert _observe(normalized, str(target), target.read_bytes()) is None, reason


def test_rendered_text_alone_never_counts_as_a_complete_read(tmp_path):
    target = tmp_path / "deep.md"
    target.write_text(BODY, encoding="utf-8")
    rows = claude_read_rows(str(target))
    rows[-1].pop("toolUseResult")
    normalized = _normalized(tmp_path, rows)
    assert _observe(normalized, str(target), target.read_bytes()) is None


def test_errored_claude_read_is_not_observed(tmp_path):
    target = tmp_path / "deep.md"
    target.write_text(BODY, encoding="utf-8")
    rows = claude_read_rows(str(target))
    rows[-1]["message"]["content"][0]["is_error"] = True
    normalized = _normalized(tmp_path, rows)
    assert _observe(normalized, str(target), target.read_bytes()) is None


def test_codex_raw_read_file_output_still_observed(tmp_path):
    """The pre-existing exact-response branch is unchanged for raw outputs."""
    from autoresearch.trace.transcripts.base import hash_tool_response

    target = tmp_path / "deep.md"
    target.write_text(BODY, encoding="utf-8")
    response = asdict(hash_tool_response(BODY))
    normalized = {
        "items": [
            {"index": 0, "kind": "tool_request",
             "payload": {"tool_call_id": "c1", "tool_name": "read_file",
                         "input": {"path": str(target)}}},
            {"index": 1, "kind": "tool_result",
             "payload": {"tool_call_id": "c1", "content": BODY, "is_error": False}},
        ],
        "operations": [{"kind": "READ_SUCCEEDED", "call_id": "c1", "tool_name": "read_file",
                        "path": str(target), "path_source": "tool_input",
                        "response": response}],
    }
    proof = _observe(normalized, str(target), target.read_bytes())
    assert proof is not None and proof["call_id"] == "c1"


def test_read_streamed_before_sibling_calls_of_the_same_message_is_observed(tmp_path):
    # Claude Code 2.1.288 streams one API response holding several tool_use blocks as one row
    # per block (same message id) and runs each call before the next block arrives. In run
    # 20261003T101330575299Z the holding review's deep Read was the first block of its message;
    # keeping only that message's last row dropped the Read and rejected the review (deep DD 未核).
    path = str(tmp_path / "688981.deep.md")
    rows = claude_read_rows(path)
    common = {"sessionId": "host-session", "agentId": "child", "version": "2.1.288"}
    rows[1]["message"]["id"] = "msg-stream"
    rows.insert(1, {**common, "type": "assistant", "timestamp": "2026-10-01T07:00:01Z",
                    "message": {"id": "msg-stream", "role": "assistant",
                                "content": [{"type": "thinking", "thinking": ""}]}})
    rows += [
        {**common, "type": "assistant", "timestamp": "2026-10-01T07:00:03Z",
         "message": {"id": "msg-stream", "role": "assistant", "stop_reason": "tool_use", "content": [
             {"type": "tool_use", "id": "toolu_grep", "name": "Grep",
              "input": {"pattern": "688981", "path": "/finalists.csv"}}]}},
        {**common, "type": "user", "timestamp": "2026-10-01T07:00:04Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_grep", "content": "12:688981"}]}},
    ]
    proof = _observe(_normalized(tmp_path, rows), path, BODY.encode())
    assert proof is not None and proof["call_id"] == "toolu_deep"


def test_every_tool_call_streamed_under_one_message_is_normalized_in_order(tmp_path):
    common = {"sessionId": "host-session", "agentId": "child", "version": "2.1.288"}

    def call(call_id, name, target):
        return {**common, "type": "assistant", "message": {"id": "msg-stream", "role": "assistant",
                "content": [{"type": "tool_use", "id": call_id, "name": name, "input": {"file_path": target}}]}}

    def result(call_id):
        return {**common, "type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": call_id, "content": "1\tx"}]}}

    rows = [call("c1", "Read", "/a.md"), result("c1"), call("c2", "Read", "/b.md"), result("c2"),
            call("c3", "Read", "/c.md"), result("c3")]
    rows[-2]["message"]["stop_reason"] = "tool_use"
    items = _stats(tmp_path, rows).normalized.items
    assert [(item.kind, item.payload["tool_use_id"]) for item in items] == [
        ("tool_request", "c1"), ("tool_result", "c1"), ("tool_request", "c2"),
        ("tool_result", "c2"), ("tool_request", "c3"), ("tool_result", "c3")]
