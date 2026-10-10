"""开发会话也算钱:两引擎、按项目、生产 / 开发分列的 token 周账本(token 防膨胀 M8 · 红线 R8)。零 LLM。

开发和生产花的是同一个订阅窗口(10-09 Codex 周额度 93%:扫描与三个开发 worker 同周),但只有生产被
计量过。本账本把本机所有 transcript 按时间窗汇总:

- **Claude**:``~/.claude/projects/**/*.jsonl`` 里时间窗内的 assistant 消息,按 ``message.id`` 去重,
  五分量 token + 按 ``trace.pricing`` 的 API 等价美元(订阅不是按这个计费,只用来比大小)。
- **Codex**:``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`` 里时间窗内的 ``token_count`` 增量
  (``last_token_usage``),外加窗口内观察到的 5h / 周额度最高读数。
- **生产 / 开发**:被某个 run 绑定过的 transcript 算生产 —— 来源是 capsule 用量账本的 ``path`` 列与
  headless 派发记录的 ``transcript_path``;其余全算开发。

用法::

    uv run --no-sync python -m autoresearch.research.token_ledger            # 最近 7 天
    uv run --no-sync python -m autoresearch.research.token_ledger --since 2026-10-05 --until 2026-10-12
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_USAGE_PATTERNS = (
    "reports_{engine}/scan/*/capsule/usage/_token_usage.json",
    "reports_{engine}/scan/_failed/*/capsule/usage/_token_usage.json",
    "reports_{engine}/scan/runs/*/*/capsule/usage/_token_usage.json",
    "reports_{engine}/analyze/*/capsule/usage/_token_usage.json",
    "reports_{engine}/analyze/_failed/*/capsule/usage/_token_usage.json",
    "reports_{engine}/analyze/runs/*/*/capsule/usage/_token_usage.json",
)
_DISPATCH_PATTERN = "context_{engine}/*_runs/*/staging/*/_dispatch/headless/*.json"


def production_paths(repo: Path = REPO) -> set[str]:
    """被 run 绑定过的 transcript 绝对路径。"""
    found: set[str] = set()
    for engine in ("claude", "codex"):
        for pattern in _USAGE_PATTERNS:
            for path in glob.glob(str(repo / pattern.format(engine=engine))):
                try:
                    rows = json.loads(Path(path).read_text(encoding="utf-8")).get("rows") or []
                except (OSError, ValueError):
                    continue
                found.update(str(row["path"]) for row in rows if row.get("path") and row["path"] != "—")
        for path in glob.glob(str(repo / _DISPATCH_PATTERN.format(engine=engine))):
            try:
                record = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if record.get("transcript_path"):
                found.add(str(record["transcript_path"]))
    return found


def _in_window(stamp: str | None, start: str, end: str) -> bool:
    return bool(stamp) and start <= stamp < end


def claude_rows(start: str, end: str, *, root: Path | None = None) -> list[dict]:
    """每个 Claude transcript 一行:时间窗内去重后的用量与估价。"""
    from autoresearch.trace.pricing import price_for_model

    root = root or Path.home() / ".claude" / "projects"
    cutoff = datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
    rows = []
    for path in glob.glob(str(root / "**" / "*.jsonl"), recursive=True):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            handle = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen: set[str] = set()
        totals = defaultdict(float)
        cwd = None
        with handle:
            for line in handle:
                if '"usage"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                message = obj.get("message") if isinstance(obj, dict) else None
                if obj.get("type") != "assistant" or not isinstance(message, dict):
                    continue
                if not _in_window(obj.get("timestamp"), start, end):
                    continue
                key = str(message.get("id") or obj.get("uuid"))
                if key in seen:
                    continue
                seen.add(key)
                cwd = cwd or obj.get("cwd")
                usage = message.get("usage") or {}
                split = usage.get("cache_creation") or {}
                write_1h = float(split.get("ephemeral_1h_input_tokens") or 0)
                write_all = float(usage.get("cache_creation_input_tokens") or 0)
                totals["input"] += float(usage.get("input_tokens") or 0)
                totals["cache_write_5m"] += max(0.0, write_all - write_1h)
                totals["cache_write_1h"] += write_1h
                totals["cache_read"] += float(usage.get("cache_read_input_tokens") or 0)
                totals["output"] += float(usage.get("output_tokens") or 0)
                profile = price_for_model(message.get("model"))
                if profile is None:
                    totals["unpriced_messages"] += 1
                    continue
                totals["usd"] += (float(usage.get("input_tokens") or 0) * profile.input_per_mtok
                                  + max(0.0, write_all - write_1h) * profile.cache_write_5m_per_mtok
                                  + write_1h * profile.cache_write_1h_per_mtok
                                  + float(usage.get("cache_read_input_tokens") or 0) * profile.cache_read_per_mtok
                                  + float(usage.get("output_tokens") or 0) * profile.output_per_mtok) / 1e6
        if seen:
            rows.append({"engine": "claude", "path": str(Path(path).resolve()), "messages": len(seen),
                         "project": Path(cwd).name if cwd else Path(path).parent.name, **totals})
    return rows


def codex_rows(start: str, end: str, *, root: Path | None = None) -> tuple[list[dict], dict]:
    """每个 Codex rollout 一行(时间窗内的 ``last_token_usage`` 增量),外加额度最高读数。"""
    root = root or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    day = datetime.fromisoformat(start[:10])
    last = datetime.fromisoformat(end[:10])
    rows, peaks = [], {"primary_max": None, "secondary_max": None, "secondary_last": None}
    while day <= last:
        for path in glob.glob(str(root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}" / "rollout-*.jsonl")):
            totals = defaultdict(float)
            cwd, calls = None, 0
            try:
                handle = open(path, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with handle:
                for line in handle:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    payload = obj.get("payload") or {}
                    if obj.get("type") == "session_meta":
                        cwd = payload.get("cwd")
                    if obj.get("type") != "event_msg" or payload.get("type") != "token_count":
                        continue
                    if not _in_window(obj.get("timestamp"), start, end):
                        continue
                    usage = (payload.get("info") or {}).get("last_token_usage") or {}
                    if usage:
                        calls += 1
                        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
                            totals[key] += float(usage.get(key) or 0)
                    limits = payload.get("rate_limits") or {}
                    for name, field in (("primary", "primary_max"), ("secondary", "secondary_max")):
                        used = (limits.get(name) or {}).get("used_percent")
                        if used is not None:
                            peaks[field] = max(float(used), peaks[field] or 0.0)
                    used = (limits.get("secondary") or {}).get("used_percent")
                    if used is not None:
                        peaks["secondary_last"] = (obj.get("timestamp"), float(used))
            if calls:
                rows.append({"engine": "codex", "path": str(Path(path).resolve()), "messages": calls,
                             "project": Path(cwd).name if cwd else "?", **totals})
        day += timedelta(days=1)
    return rows, peaks


def ledger(start: str, end: str, *, repo: Path = REPO, claude_root: Path | None = None,
           codex_root: Path | None = None) -> dict:
    production = {str(Path(path).resolve()) for path in production_paths(repo)}
    rows = claude_rows(start, end, root=claude_root)
    codex, peaks = codex_rows(start, end, root=codex_root)
    rows.extend(codex)
    groups: dict[tuple, dict] = {}
    for row in rows:
        kind = "production" if row["path"] in production else "development"
        key = (row["engine"], row["project"], kind)
        slot = groups.setdefault(key, defaultdict(float))
        slot["transcripts"] += 1
        for name, value in row.items():
            if isinstance(value, (int, float)):
                slot[name] += value
    table = [{"engine": e, "project": p, "kind": k, **{name: round(v, 4) for name, v in slot.items()}}
             for (e, p, k), slot in sorted(groups.items())]
    return {"schema_version": 1, "since": start, "until": end, "rows": table, "codex_limits": peaks}


def render(value: dict) -> str:
    lines = [f"# token 周账本 {value['since'][:10]} → {value['until'][:10]}", "",
             "| 引擎 | 项目 | 类别 | transcript | 调用 | 输入(含 cache) | 输出 | API 等价 $ |",
             "|---|---|---|---:|---:|---:|---:|---:|"]
    for row in value["rows"]:
        if row["engine"] == "claude":
            billed = row.get("input", 0) + row.get("cache_write_5m", 0) + row.get("cache_write_1h", 0) + row.get("cache_read", 0)
            usd = f"{row.get('usd', 0):.2f}"
        else:
            billed = row.get("input_tokens", 0)
            usd = "—"
        lines.append(f"| {row['engine']} | {row['project']} | {row['kind']} | {row['transcripts']:.0f} | "
                     f"{row['messages']:.0f} | {billed / 1e6:.2f}M | "
                     f"{(row.get('output', 0) or row.get('output_tokens', 0)) / 1e3:.0f}k | {usd} |")
    peaks = value.get("codex_limits") or {}
    lines += ["", f"Codex 额度:窗口内 5h 最高 {peaks.get('primary_max')}% · 周最高 {peaks.get('secondary_max')}%"
                  f" · 周末读数 {peaks.get('secondary_last')}",
              "", "生产 = 被 run 绑定过的 transcript(capsule 用量账本 + headless 派发记录);其余算开发。"
                  "Claude 美元是 API 牌价等价,不是订阅账单。"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from autoresearch.common import workspace as ws
    from autoresearch.common.atomic import atomic_write_json

    ap = argparse.ArgumentParser(prog="python -m autoresearch.research.token_ledger", description=__doc__.splitlines()[0])
    ap.add_argument("--since", help="起始日(含),缺省 7 天前")
    ap.add_argument("--until", help="截止日(不含),缺省明天")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    today = datetime.now(timezone.utc).date()
    since = args.since or (today - timedelta(days=7)).isoformat()
    until = args.until or (today + timedelta(days=1)).isoformat()
    value = ledger(f"{since}T00:00:00Z", f"{until}T00:00:00Z")
    text = render(value)
    print(text)
    if not args.no_write:
        out = ws.reports_root() / "_ops" / f"token_ledger_{since}_{until}"
        atomic_write_json(out.with_suffix(".json"), value)
        out.with_suffix(".md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
