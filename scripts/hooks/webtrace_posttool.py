#!/usr/bin/env python3
"""Claude Code PostToolUse hook —— WebFetch/WebSearch 现场留痕兜底(L1,D7.2)。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md`;
task: `.superpowers/sdd/2026-08-31-stock-research-p0-p1/task-16-brief.md`。

Claude Code 在每次 WebFetch/WebSearch 工具调用**之后**把这段 PostToolUse JSON 喂到
本脚本的 stdin(见 `.claude/settings.json` 的 `matcher: "WebFetch|WebSearch"`)。这是
**兜底**证据:capsule 主账 `lineage/external_tools.jsonl` 由 `finalize()` 事后读
subagent transcript 物化(`trace/capsule.py::_archive_bound_transcripts`)——主会话
自己发起的 WebFetch/WebSearch 没有 subagent transcript 可读,主账天生看不见它们。
本脚本从 harness 视角**实时**补一份 L1 记录:不存 response 正文(hook 拿到的本来
也只是摘要视图),只记 url/query 与字符数。

零成本约定:`AUTORESEARCH_RUN_ID` 不在场(绝大多数会话)→ 第一步判完就 return,
不 import autoresearch、不读 stdin、不碰磁盘——本 hook 每次 WebFetch/WebSearch 后
都跑,不能拖慢日常问答。

落点(避免与主账双写破幂等):
- run 目录存在且还没 finalize(`capsule/capsule.json` 尚未写终稿)→ 追加到运行内
  `<run_root>/capsule/lineage/external_tools_hook.jsonl`(**独立文件名**,主账叫
  `external_tools.jsonl`)。
- 其余情况(run 已 finalize,或压根没找到 run 目录)→ 追加到 run 外的
  `$RPT/analyze/_ledger/webtrace/<run_id>.jsonl`(append-only)。

行 schema:`{ts, session_id, tool, url_or_query, response_chars, capture_level}`,
`capture_level` 恒为 `"HOOK_L1"`——区别于主账物化行的 `"HARNESS_RESPONSE"`,两个
真身不可互换。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _char_count(value: object) -> int:
    """不存 response 正文,只记它有多长:字符串按字符数,其它按 JSON 串长度。"""
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    return len(json.dumps(value, ensure_ascii=False))


def _resolve_capsule_dir(ws, run_id: str):
    """探 run 目录:两种 kind 都试,只认**已经在磁盘上**的那个。

    `ws.run_root(kind, run_id)` 是通用函数,今天可能还没落地(`getattr` 探测);没有
    就退到现成的 `scan_run_root`(scan-market 专属,今天唯一真正落盘过的 kind)。
    两者都探不到就是「没有这个 run」,交给调用方走账本兜底。
    """
    generic = getattr(ws, "run_root", None)
    for kind in ("stock-research", "scan-market"):
        root: Path | None = None
        if callable(generic):
            try:
                root = Path(generic(kind, run_id))
            except Exception:
                root = None
        elif kind == "scan-market":
            try:
                root = Path(ws.scan_run_root(run_id))
            except Exception:
                root = None
        if root is not None and (root / "capsule").is_dir():
            return root / "capsule"
    return None


def _target_path(ws, run_id: str) -> Path:
    capsule = _resolve_capsule_dir(ws, run_id)
    if capsule is not None and not (capsule / "capsule.json").exists():
        return capsule / "lineage" / "external_tools_hook.jsonl"
    return ws.reports_root() / "analyze" / "_ledger" / "webtrace" / f"{run_id}.jsonl"


def _append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main(raw: str | None = None) -> int:
    run_id_env = os.environ.get("AUTORESEARCH_RUN_ID", "").strip()
    if not run_id_env:
        return 0  # 零成本兜底:没有活跃 run,连 import 都不做

    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from autoresearch.common import workspace as ws

    try:
        run_id = ws.validate_run_id(run_id_env)
    except ValueError:
        return 0

    text = sys.stdin.read() if raw is None else raw
    try:
        payload = json.loads(text) if text and text.strip() else {}
    except (TypeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name")
    if not tool_name:
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    row = {
        "ts": _utc_now(),
        "session_id": str(payload.get("session_id") or ""),
        "tool": tool_name,
        "url_or_query": tool_input.get("url") or tool_input.get("query") or "",
        "response_chars": _char_count(payload.get("tool_response")),
        "capture_level": "HOOK_L1",
    }

    try:
        _append_row(_target_path(ws, run_id), row)
    except Exception:
        pass  # L1 兜底证据,写失败不得让 WebFetch/WebSearch 调用本身报错
    return 0


if __name__ == "__main__":
    sys.exit(main())
