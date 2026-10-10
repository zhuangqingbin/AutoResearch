"""场后真计量红线(token 防膨胀 M3)+ 身份 / 前导指纹(M7)+ 断路器文件(M4 开场那一半)。零 LLM。

一场扫描结束(发布或封存 FAILED)后读本 run capsule 的去重用量账本,产出
``$RPT/_ops/redline/<run_id>.json``,与三样东西比:

- ``budgets.declared``:每场预算线(claude = API 等价美元;codex = 5h 窗口点数);
- ``session.max_turns``:每角色轮数信封(claude 硬封顶,codex 没有轮数开关,只在这里看);
- 上一份 redline:每角色前导(第一次调用的上下文)与输出的漂移,以及宿主 / 模型身份变化。

判 FAIL 时另写 ``$RPT/_ops/redline_breaker.json``:下一场 ``scan_run`` 开场拒开,直到
``--ack-redline <run_id>`` 或 ``budgets`` 配置改动。**本场的发布与退出码一概不受影响**——研究已经
付了钱,挡发布只会把钱白扔;要挡的是同一个漂移再烧下一场。

读不到账本 = ``UNMEASURED``(不是 PASS,也不开断路器:没量到不等于没超)。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_json

SCHEMA_VERSION = 1
HOST = "host"
#: 一场全扫的研究角色(session_v1 role id)。
SCAN_ROLES = ("macro.brief", "sector.brief", "scan.l3", "scan.l3.repair",
              "scan.l4.intel", "scan.l4.card", "scan.l4.review")
_LEVELS = {"FAIL": 3, "WARN": 2, "INFO": 1}


# ── 路径 ─────────────────────────────────────────────────────────────────────────

def ops_dir() -> Path:
    return ws.reports_root() / "_ops"


def readout_dir() -> Path:
    return ops_dir() / "redline"


def breaker_path() -> Path:
    return ops_dir() / "redline_breaker.json"


def locate_capsule(run_id: str) -> Path | None:
    """发布根 → 封存 FAILED 根 → 工作区,取第一个带用量账本的 capsule。"""
    candidates = [
        ws.canonical_publication_root("scan-market", run_id) / "capsule",
        ws.run_reports_root("scan-market") / "_failed" / run_id / "capsule",
        ws.scan_run_root(run_id) / "capsule",
    ]
    for capsule in candidates:
        if (capsule / "usage" / "_token_usage.json").is_file():
            return capsule
    return None


# ── 角色归一 ─────────────────────────────────────────────────────────────────────

def _agent_roles() -> dict[str, str]:
    """agent 名(claude agent type / codex 角色名)→ scan role;只收 scan 角色,先到先得。"""
    from autoresearch.session_agent.executors.base import CODEX_AGENT_NAMES, ROLE_DISPATCH

    table: dict[str, str] = {}
    for role in SCAN_ROLES:
        agent_type, config_role = ROLE_DISPATCH.get(role, (None, None))
        if agent_type:
            table.setdefault(str(agent_type), role)
        codex_name = CODEX_AGENT_NAMES.get(config_role) if config_role else None
        if codex_name:
            table.setdefault(str(codex_name), role)
    for role in SCAN_ROLES:          # 旧账本的 agent 列直接写 role id
        table.setdefault(role, role)
    return table


def role_of(row: dict, agents: dict[str, str] | None = None) -> str:
    role = str(row.get("role") or "")
    agent = str(row.get("agent") or "")
    if role in {"main", HOST} or agent.startswith("(主会话"):
        return HOST
    if role in SCAN_ROLES:
        return role
    bound = {str(item.get("role")) for item in row.get("bound_invocations") or []
             if isinstance(item, dict) and item.get("role") in SCAN_ROLES}
    if len(bound) == 1:
        return bound.pop()
    table = agents if agents is not None else _agent_roles()
    return table.get(agent) or f"other:{agent or role or '?'}"


def dedupe_rows(rows: list[dict], agents: dict[str, str] | None = None) -> list[dict]:
    """一份 transcript 只算一次(10-08 的账本把 31 个线程各列了两行:host 行 + headless 行)。

    同一路径的行里优先留能归到 scan 角色的那一行;没有路径的行原样保留。
    """
    table = agents if agents is not None else _agent_roles()
    by_path: dict[str, dict] = {}
    loose: list[dict] = []
    for row in rows:
        path = str(row.get("path") or "")
        if not path or path == "—":
            loose.append(row)
            continue
        kept = by_path.get(path)
        if kept is None or (role_of(kept, table).startswith("other:")
                            and not role_of(row, table).startswith("other:")):
            by_path[path] = row
    return [*by_path.values(), *loose]


# ── transcript 读数 ───────────────────────────────────────────────────────────────

def first_call_context(path: str | Path) -> int | None:
    """第一次模型调用的上下文 token(= 角色前导 + 首条任务 prompt)。

    Claude transcript:第一条 assistant 消息的 input + cache 写 + cache 读;
    Codex rollout:第一条带 ``last_token_usage`` 的 ``token_count`` 的 ``input_tokens``(含 cache)。
    """
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "event_msg":
                payload = obj.get("payload") or {}
                last = ((payload.get("info") or {}).get("last_token_usage") or {}) \
                    if payload.get("type") == "token_count" else {}
                if last.get("input_tokens") is not None:
                    return int(last["input_tokens"])
                continue
            message = obj.get("message")
            if obj.get("type") == "assistant" and isinstance(message, dict) \
                    and isinstance(message.get("usage"), dict):
                usage = message["usage"]
                return sum(int(usage.get(key) or 0) for key in (
                    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return None


def _rate_events(path: str | Path) -> list[tuple]:
    """``(ts, 5h 已用 %, 5h 窗口 id, 周已用 %, 周窗口 id)``:Codex rollout 的额度读数。"""
    events = []
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return events
    with handle:
        for line in handle:
            if '"rate_limits"' not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            payload = obj.get("payload") or {}
            limits = payload.get("rate_limits") if payload.get("type") == "token_count" else None
            if not isinstance(limits, dict):
                continue
            primary = limits.get("primary") or {}
            secondary = limits.get("secondary") or {}
            events.append((str(obj.get("timestamp") or ""),
                           primary.get("used_percent"), primary.get("resets_at"),
                           secondary.get("used_percent"), secondary.get("resets_at")))
    return events


def window_points(paths: list[str], *, sessions_root: Path | None = None) -> dict:
    """本场线程观察到的 5h / 周额度增量(点 = 百分点)。

    额度是**账号级**的:同时段别的 Codex 会话也在花同一个窗口 —— 查到同窗口里有本场以外的
    rollout 在动,就标 ``concurrent``,判定只告警不断路。跨过窗口重置 = ``RESET_CROSSED``,
    各窗口增量之和只是下界。
    """
    events = sorted(event for path in paths for event in _rate_events(path))
    if not events:
        return {"status": "UNMEASURED"}

    def span(used_at: int, window_at: int) -> tuple[float | None, int]:
        # 同一个窗口的 resets_at 会抖 1 秒(10-08 实测周窗口 …635 / …636 各半):重置相隔 5h / 7 天,
        # 一小时内的 id 归同一窗口,否则会把同一窗口拆成两段重复计点。
        ids = sorted({event[window_at] for event in events
                      if event[used_at] is not None and isinstance(event[window_at], (int, float))})
        anchor: dict = {}
        start = None
        for value in ids:
            if start is None or value - start >= 3600:
                start = value
            anchor[value] = start
        groups: dict = {}
        for event in events:
            if event[used_at] is not None:
                groups.setdefault(anchor.get(event[window_at], event[window_at]), []).append(float(event[used_at]))
        if not groups:
            return None, 0
        return float(sum(max(v) - min(v) for v in groups.values())), len(groups)

    primary, windows = span(1, 2)
    secondary, _ = span(3, 4)
    status = "UNMEASURED" if primary is None else ("RESET_CROSSED" if windows > 1 else "MEASURED")
    start, end = events[0][0], events[-1][0]
    others = _concurrent_rollouts(set(map(str, paths)), start, end, sessions_root)
    return {"status": status, "primary_points": primary, "secondary_points": secondary,
            "windows": windows, "started_at": start, "ended_at": end,
            "concurrent": len(others), "concurrent_sample": sorted(others)[:3]}


def _concurrent_rollouts(own: set[str], start: str, end: str, root: Path | None) -> list[str]:
    root = root or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "sessions"
    days = {start[:10], end[:10]}
    found = []
    for day in days:
        if len(day) != 10:
            continue
        pattern = str(root / day[:4] / day[5:7] / day[8:10] / "rollout-*.jsonl")
        for path in glob.glob(pattern):
            if path in own:
                continue
            if any(start <= event[0] <= end for event in _rate_events(path)):
                found.append(Path(path).name)
    return found


# ── 配置 ─────────────────────────────────────────────────────────────────────────

def _cfg(cfg: dict | None) -> dict:
    if cfg is not None:
        return cfg
    from autoresearch.scan.user_config import load_user_config

    return load_user_config()


def budgets_policy(cfg: dict | None = None) -> dict:
    from autoresearch.scan.budget import normalize_budgets

    return normalize_budgets(_cfg(cfg).get("budgets"))


def budgets_sha256(cfg: dict | None = None) -> str:
    payload = json.dumps(budgets_policy(cfg), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def config_snapshot(cfg: dict | None = None) -> dict:
    """决定线程数与前导的配置项(token_bom 用它把历史读数折到当前配置)。"""
    from autoresearch.session_agent.config import session_cfg

    raw = _cfg(cfg)
    sector = raw.get("sector") or {}
    session = session_cfg(raw)
    return {
        "max_cards": int((raw.get("l4") or {}).get("max_cards", 13)),
        "pinned_cap": int((raw.get("pinned") or {}).get("cap", 5)),
        "sector_briefs": int(sector.get("max_briefs", 6)) + (3 if sector.get("healthy_top3_extra", True) else 0),
        "intel_enabled": bool((raw.get("l4_intel") or {}).get("enabled", False)),
        "context": dict(session.get("context") or {}),
        "max_turns": dict(session.get("max_turns") or {}),
    }


def agent_chars(folder: Path | None = None) -> dict[str, int]:
    """agent 文件字符数(两个引擎的研究角色读的都是 ``.claude/agents/*.md``:claude 当系统提示,codex 经 broker)。"""
    folder = folder or Path(__file__).resolve().parents[2] / ".claude" / "agents"
    return {path.stem: len(path.read_text(encoding="utf-8")) for path in sorted(Path(folder).glob("*.md"))}


# ── 读数 ─────────────────────────────────────────────────────────────────────────

def _median(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return float(statistics.median(clean)) if clean else None


def build(run_id: str, *, capsule: Path | None = None, cfg: dict | None = None,
          sessions_root: Path | None = None) -> dict:
    """读 capsule 用量账本 → 每角色线程 / 调用 / 前导 / 输出 / 成本 + 宿主行 + 窗口点数。"""
    capsule = capsule or locate_capsule(run_id)
    meta = {}
    if capsule is not None and (capsule / "capsule.json").is_file():
        meta = json.loads((capsule / "capsule.json").read_text(encoding="utf-8"))
    readout = {
        "schema_version": SCHEMA_VERSION, "engine": meta.get("engine") or ws.ENGINE, "run_id": run_id,
        "analysis_date": meta.get("analysis_date"), "business_status": meta.get("business_status"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capsule": str(capsule) if capsule is not None else None,
        "config": config_snapshot(cfg),
        # 本场冻结的 agent 文件(capsule 身份快照)优先;没有快照才读现场文件。
        "agent_chars": agent_chars(capsule / "identity" / "prompts" / "agents")
        if capsule is not None and (capsule / "identity" / "prompts" / "agents").is_dir() else agent_chars(),
    }
    usage_path = capsule / "usage" / "_token_usage.json" if capsule is not None else None
    if usage_path is None or not usage_path.is_file():
        readout.update(usage_status="UNMEASURED", roles={}, host={}, totals={}, window={"status": "UNMEASURED"},
                       identity={})
        return readout
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    agents = _agent_roles()
    rows = [row for row in dedupe_rows(list(usage.get("rows") or []), agents)
            if row.get("status") != "UNMEASURED"]
    roles: dict[str, dict] = {}
    host = {"rows": 0, "weighted_input": 0.0, "usd": 0.0}
    hosts: set[str] = set()
    models: dict[str, set] = {}
    efforts: dict[str, set] = {}
    paths: list[str] = []
    for row in rows:
        role = role_of(row, agents)
        input_total = int(row.get("billed_in") or 0) or sum(
            int(row.get(key) or 0) for key in ("input", "cache_create", "cache_read"))
        weighted = float(row.get("weighted_in") or 0.0)
        usd = row.get("estimated_usd")
        if row.get("host_version"):
            hosts.add(str(row["host_version"]))
        if role == HOST:
            host["rows"] += 1
            host["weighted_input"] += weighted or float(input_total)
            host["usd"] += float(usd or 0.0)
            continue
        slot = roles.setdefault(role, {"threads": 0, "calls": [], "outputs": [], "prefixes": [],
                                       "input": 0, "output": 0, "usd": 0.0, "unpriced": 0})
        slot["threads"] += 1
        slot["calls"].append(int(row.get("messages") or 0))
        slot["outputs"].append(int(row.get("output") or 0))
        slot["input"] += input_total
        slot["output"] += int(row.get("output") or 0)
        if usd is None:
            slot["unpriced"] += 1
        else:
            slot["usd"] += float(usd)
        path = str(row.get("path") or "")
        if path and path != "—":
            paths.append(path)
            prefix = first_call_context(path)
            if prefix is not None:
                slot["prefixes"].append(prefix)
        if row.get("model"):
            models.setdefault(role, set()).add(str(row["model"]))
        if row.get("effort"):
            efforts.setdefault(role, set()).add(str(row["effort"]))
    for slot in roles.values():
        slot.update(calls_max=max(slot["calls"], default=0), calls_median=_median(slot["calls"]),
                    output_median=_median(slot["outputs"]), prefix_median=_median(slot["prefixes"]))
    totals = usage.get("totals") or {}
    readout.update(
        usage_status="MEASURED", roles=roles, host=host,
        totals={"usd": round(sum(s["usd"] for s in roles.values()) + host["usd"], 4),
                "unpriced_threads": sum(s["unpriced"] for s in roles.values()),
                "input": sum(s["input"] for s in roles.values()),
                "output": sum(s["output"] for s in roles.values()),
                "weighted_input": totals.get("weighted_in")},
        identity={"hosts": sorted(hosts), "models": {k: sorted(v) for k, v in models.items()},
                  "efforts": {k: sorted(v) for k, v in efforts.items()}},
        window=(window_points(paths, sessions_root=sessions_root)
                if readout["engine"] == "codex" else {"status": "NOT_APPLICABLE"}),
    )
    return readout


# ── 判定 ─────────────────────────────────────────────────────────────────────────

def previous_readout(run_id: str, engine: str | None = None) -> dict | None:
    """本引擎最近一份**量到了的** redline(不含本 run)。"""
    folder = readout_dir()
    if not folder.is_dir():
        return None
    best = None
    for path in folder.glob("*.json"):
        if path.stem == run_id:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if value.get("usage_status") != "MEASURED" or (engine and value.get("engine") != engine):
            continue
        if best is None or str(value.get("run_id")) > str(best.get("run_id")):
            best = value
    return best


def _finding(level: str, code: str, message: str, **extra) -> dict:
    return {"level": level, "code": code, "message": message, **extra}


def evaluate(readout: dict, *, cfg: dict | None = None, previous: dict | None = None) -> dict:
    """在 readout 上填 ``findings`` / ``verdict`` / ``line`` / ``ratchet_suggestion``(返回同一个 dict)。"""
    policy = budgets_policy(cfg)
    declared, envelope = policy["declared"], policy["envelope"]
    findings: list[dict] = []
    if readout.get("usage_status") != "MEASURED":
        readout.update(findings=[_finding("WARN", "UNMEASURED", "本场用量账本缺失:没量到 ≠ 没超,不判也不断路")],
                       verdict="UNMEASURED", line=None, ratchet_suggestion=None)
        return readout
    engine = readout.get("engine")
    roles = readout.get("roles") or {}

    # 场线(R5)
    line = None
    if engine == "claude":
        measured = float((readout.get("totals") or {}).get("usd") or 0.0)
        line = {"unit": "usd", "value": declared["claude_run_usd"], "measured": round(measured, 2)}
        if measured > declared["claude_run_usd"]:
            findings.append(_finding("FAIL", "RUN_OVER_LINE",
                                     f"本场研究 ${measured:.2f} > 线 ${declared['claude_run_usd']:.2f}"))
        if (readout.get("totals") or {}).get("unpriced_threads"):
            findings.append(_finding("WARN", "UNPRICED_THREADS", "有线程的模型不在价表:场线判定偏低"))
    elif engine == "codex":
        window = readout.get("window") or {}
        points = window.get("primary_points")
        line = {"unit": "window_points", "value": declared["codex_run_window_points"], "measured": points}
        if window.get("status") == "UNMEASURED" or points is None:
            findings.append(_finding("WARN", "WINDOW_UNMEASURED", "rollout 没有额度读数:窗口点数未知"))
        elif points > declared["codex_run_window_points"]:
            level = "WARN" if window.get("concurrent") else "FAIL"
            findings.append(_finding(level, "RUN_OVER_LINE",
                                     f"本场 5h 窗口 {points:.0f} 点 > 线 {declared['codex_run_window_points']:.0f}"
                                     + (f"(同窗口另有 {window['concurrent']} 个非本场线程,只告警)"
                                        if window.get("concurrent") else "")))
        if window.get("status") == "RESET_CROSSED":
            findings.append(_finding("INFO", "WINDOW_RESET_CROSSED", "本场跨过 5h 窗口重置:点数是下界"))

    # 宿主零研究(R2)
    host = readout.get("host") or {}
    if float(host.get("weighted_input") or 0.0) > envelope["host_weighted_max"]:
        findings.append(_finding("FAIL", "HOST_IN_LOOP",
                                 f"宿主行加权输入 {host['weighted_input']:.0f} > {envelope['host_weighted_max']:.0f}"))

    # 轮数信封(M5)
    caps = (readout.get("config") or {}).get("max_turns") or {}
    for role, slot in sorted(roles.items()):
        cap = caps.get(role)
        if cap and slot.get("calls_max", 0) >= cap:
            findings.append(_finding("WARN", "TURN_CAP_HIT" if engine == "claude" else "CALLS_OVER_ENVELOPE",
                                     f"{role} 最多 {slot['calls_max']} 次调用 ≥ 封顶 {cap}", role=role))

    # 漂移(M7):前导 / 输出 / 身份
    if previous is None:
        findings.append(_finding("INFO", "NO_BASELINE", "没有上一份量到的 redline:不判漂移"))
    else:
        prev_roles = previous.get("roles") or {}
        prev_identity = previous.get("identity") or {}
        identity = readout.get("identity") or {}
        host_changed = sorted(identity.get("hosts") or []) != sorted(prev_identity.get("hosts") or [])
        if host_changed:
            findings.append(_finding("INFO", "HOST_VERSION_CHANGED",
                                     f"宿主版本 {prev_identity.get('hosts')} → {identity.get('hosts')}"))
        for role, slot in sorted(roles.items()):
            prev = prev_roles.get(role) or {}
            model_changed = (identity.get("models") or {}).get(role) != (prev_identity.get("models") or {}).get(role)
            if model_changed and (prev_identity.get("models") or {}).get(role):
                findings.append(_finding("WARN", "MODEL_CHANGED",
                                         f"{role} 模型 {prev_identity['models'][role]} → {identity['models'].get(role)}",
                                         role=role))
            ratio = _ratio(slot.get("prefix_median"), prev.get("prefix_median"))
            if ratio is not None and ratio >= envelope["prefix_drift_fail"]:
                findings.append(_finding("FAIL", "PREFIX_DRIFT", f"{role} 前导 ×{ratio:.2f}", role=role))
            elif ratio is not None and ratio >= envelope["prefix_drift_warn"]:
                findings.append(_finding("WARN", "PREFIX_DRIFT", f"{role} 前导 ×{ratio:.2f}", role=role))
            ratio = _ratio(slot.get("output_median"), prev.get("output_median"))
            if ratio is not None and ratio >= envelope["output_drift_warn"]:
                changed = host_changed or model_changed
                findings.append(_finding("FAIL" if changed else "WARN", "OUTPUT_DRIFT",
                                         f"{role} 输出中位 ×{ratio:.2f}"
                                         + ("(同时宿主 / 模型身份变了)" if changed else ""), role=role))

    # 实际模型对档位锁(R12 的场后一半):锁外的模型 = 档位被静默换了;配置里的 fallback 只告警
    findings.extend(_lock_findings(readout, cfg))

    # 同日重跑(R6,只记)
    same_date = _same_date_runs(readout)
    if same_date:
        findings.append(_finding("INFO", "SAME_DATE_RERUN", f"同分析日已有 {len(same_date)} 场:{same_date[:3]}"))

    worst = max((_LEVELS[f["level"]] for f in findings), default=0)
    verdict = "FAIL" if worst == 3 else "WARN" if worst == 2 else "PASS"
    suggestion = None
    if verdict == "PASS" and line and isinstance(line.get("measured"), (int, float)):
        target = float(line["measured"]) * envelope["ratchet_margin"]
        if target < float(line["value"]):
            suggestion = math.ceil(target * 10) / 10
    readout.update(findings=findings, verdict=verdict, line=line, ratchet_suggestion=suggestion)
    return readout


def _lock_findings(readout: dict, cfg: dict | None) -> list[dict]:
    from autoresearch.scan import token_rules
    from autoresearch.session_agent.executors.base import ROLE_DISPATCH
    from autoresearch.trace.pricing import canonical_model_id

    engine = readout.get("engine")
    lock = token_rules.read_lock().get("entries") or {}
    out = []
    for role, models in sorted(((readout.get("identity") or {}).get("models") or {}).items()):
        config_role = ROLE_DISPATCH.get(role, (None, None))[1]
        history = lock.get(f"{engine}:{config_role}") or []
        expected = history[-1].get("model") if history else None
        if not expected:
            continue
        seen = {canonical_model_id(model) for model in models}
        if seen <= {canonical_model_id(expected)}:
            continue
        fallback = None
        try:
            from autoresearch.scan.user_config import _dual_role_specs

            fallback = (_dual_role_specs(_cfg(cfg), engine, config_role)[1] or {}).get("model")
        except Exception:  # noqa: BLE001 - no resolvable fallback = treat every stranger as drift
            fallback = None
        allowed = {canonical_model_id(expected)} | ({canonical_model_id(fallback)} if fallback else set())
        level = "WARN" if seen <= allowed else "FAIL"
        out.append(_finding(level, "MODEL_FALLBACK" if level == "WARN" else "MODEL_NOT_LOCKED",
                            f"{role} 实际模型 {sorted(seen)} ≠ 档位锁 {expected}", role=role))
    return out


def _ratio(now, before) -> float | None:
    if not now or not before:
        return None
    return float(now) / float(before)


def _same_date_runs(readout: dict) -> list[str]:
    date = readout.get("analysis_date")
    folder = readout_dir()
    if not date or not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.json"):
        if path.stem == readout.get("run_id"):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if value.get("analysis_date") == date and value.get("engine") == readout.get("engine"):
            found.append(path.stem)
    return sorted(found)


# ── 落盘 / 断路器 ─────────────────────────────────────────────────────────────────

def write(readout: dict, *, cfg: dict | None = None) -> Path:
    path = readout_dir() / f"{readout['run_id']}.json"
    atomic_write_json(path, readout)
    if readout.get("verdict") == "FAIL":
        atomic_write_json(breaker_path(), {
            "schema_version": SCHEMA_VERSION, "run_id": readout["run_id"], "engine": readout.get("engine"),
            "created_at": readout.get("generated_at"), "readout": str(path),
            "budgets_sha256": budgets_sha256(cfg),
            "findings": [f for f in readout.get("findings") or [] if f["level"] == "FAIL"],
        })
    return path


def post_run(run_id: str, *, cfg: dict | None = None) -> dict:
    """scan_run 场后调用:build → evaluate → write。绝不抛异常(返回 ``verdict=ERROR``)。"""
    try:
        readout = build(run_id, cfg=cfg)
        evaluate(readout, cfg=cfg, previous=previous_readout(run_id, readout.get("engine")))
        path = write(readout, cfg=cfg)
        return {"verdict": readout["verdict"], "path": str(path), "line": readout.get("line"),
                "findings": [f"{f['level']}:{f['code']}" for f in readout["findings"] if f["level"] != "INFO"],
                "ratchet_suggestion": readout.get("ratchet_suggestion")}
    except Exception as exc:  # noqa: BLE001 - the readout must never change the run's outcome
        return {"verdict": "ERROR", "error": f"{type(exc).__name__}: {exc}"[:300]}


def active_breaker(cfg: dict | None = None) -> dict | None:
    """未确认的断路器;``budgets`` 配置改过 = 有人看过了,自动归档放行。"""
    path = breaker_path()
    if not path.is_file():
        return None
    try:
        breaker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"run_id": "?", "findings": [], "error": "breaker unreadable"}
    if breaker.get("budgets_sha256") and breaker["budgets_sha256"] != budgets_sha256(cfg):
        acknowledge(breaker.get("run_id") or "?", reason="budgets 配置已改")
        return None
    return breaker


def ack_path(run_id: str) -> Path:
    return readout_dir() / "acks" / f"{run_id}.json"


def acknowledge(run_id: str, *, reason: str) -> Path:
    """确认 ``run_id`` 的红线:断路器(若属于它)移到 ``redline/acks/<run_id>.json``;没有断路器时只写
    确认标记 —— 恢复一个被场中前导守卫停下的 run(``--resume-run-id X --ack-redline X``)就靠它。"""
    path = breaker_path()
    breaker: dict = {}
    if path.is_file():
        try:
            breaker = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            breaker = {}
        if breaker.get("run_id") not in {run_id, None, "?"}:
            raise ValueError(f"断路器属于 run {breaker.get('run_id')},不是 {run_id}")
    target = ack_path(run_id)
    atomic_write_json(target, {**breaker, "run_id": run_id, "ack_reason": reason,
                               "acked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if path.is_file():
        path.unlink()
    return target


class PrefixGuard:
    """场中前导守卫(M4 场中那一半):每个角色**第一个**完成的线程,前导 ≥ 上一场该角色中位 ×
    ``envelope.prefix_drift_fail`` → runner 停派新线程(排空在飞的,run 保持可恢复)。

    上游静默注入(CLI 升级加段、插件 hook、自动记忆)会让每个线程每次调用都多付一截;场后才发现
    要烧完一整场,这里只烧一个线程。没有基线 / 该 run 已确认 → 不建守卫。
    """

    def __init__(self, baseline: dict[str, float], factor: float):
        self.baseline = {role: float(value) for role, value in baseline.items() if value}
        self.factor = float(factor)
        self.seen: set[str] = set()

    @classmethod
    def for_run(cls, run_id: str, *, engine: str | None = None, cfg: dict | None = None) -> "PrefixGuard | None":
        if ack_path(run_id).is_file():
            return None
        previous = previous_readout(run_id, engine)
        if previous is None:
            return None
        baseline = {role: slot.get("prefix_median") for role, slot in (previous.get("roles") or {}).items()}
        guard = cls(baseline, budgets_policy(cfg)["envelope"]["prefix_drift_fail"])
        return guard if guard.baseline else None

    def observe(self, role: str, transcript_path: str | None) -> dict | None:
        if role in self.seen or role not in self.baseline or not transcript_path:
            return None
        self.seen.add(role)
        prefix = first_call_context(transcript_path)
        if prefix is None:
            return None
        ratio = prefix / self.baseline[role]
        if ratio < self.factor:
            return None
        return {"role": role, "prefix": prefix, "baseline": self.baseline[role], "ratio": round(ratio, 2),
                "transcript": str(transcript_path)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m autoresearch.scan.redline", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="读一场的用量账本,判定并落 redline(FAIL 写断路器)")
    b.add_argument("--run-id", required=True)
    b.add_argument("--capsule", help="显式 capsule 目录(缺省按发布根 / _failed / 工作区找)")
    b.add_argument("--no-write", action="store_true", help="只打印,不落盘、不写断路器")
    sub.add_parser("status", help="当前断路器")
    a = sub.add_parser("ack", help="确认断路器(下一场放行)")
    a.add_argument("--run-id", required=True)
    a.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    if args.command == "build":
        readout = build(args.run_id, capsule=Path(args.capsule) if args.capsule else None)
        evaluate(readout, previous=previous_readout(args.run_id, readout.get("engine")))
        if not args.no_write:
            readout["path"] = str(write(readout))
        print(json.dumps(readout, ensure_ascii=False, indent=1))
        return 0
    if args.command == "status":
        print(json.dumps(active_breaker(), ensure_ascii=False, indent=1))
        return 0
    target = acknowledge(args.run_id, reason=args.reason)
    print(json.dumps({"acknowledged": str(target) if target else None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
