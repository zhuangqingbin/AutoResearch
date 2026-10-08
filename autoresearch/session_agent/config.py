"""`scan_config.session`:session_v1 宿主(mailbox / headless)的运行时旋钮(2026-09-27 P3)。

缺键 = 各执行器模块常量(调用时现读);字典类键逐 role 深合并一层。
"""
from __future__ import annotations


def load_orchestration_config(*, engine: str) -> dict:
    """Resolve explicit operator configuration before allocating a standalone run."""
    from autoresearch.scan import user_config

    config = user_config.load_user_config(user_config.DEFAULT_PATH)
    capabilities = user_config.load_codex_capabilities() if engine == "codex" else None
    bundle = user_config.resolve_agent_bundle(config, engine=engine, capabilities=capabilities)
    return {**config, "engine": engine, "resolved_agents": bundle["roles"],
            "resolved_agent_bundle": bundle}


def orchestration_config(handle) -> dict:
    """Keep standalone business parameters separate from the frozen scheduler knobs."""
    config = getattr(getattr(handle, "contract", None), "user_config", None) or {}
    return config.get("orchestration_config", config)


def session_cfg(cfg: dict | None = None) -> dict:
    from autoresearch.scan.user_config import knob
    from autoresearch.session_agent import runner as _runner
    from autoresearch.session_agent.executors import base as _base, headless_claude as _hl, headless_codex as _hx, mailbox as _mb

    tm = knob("session", "timeouts", None, {}, cfg) or {}
    if not isinstance(tm, dict):
        tm = {}
    mb = knob("session", "mailbox", None, {}, cfg) or {}
    if not isinstance(mb, dict):
        mb = {}
    rn = knob("session", "runner", None, {}, cfg) or {}
    if not isinstance(rn, dict):
        rn = {}
    return {
        "timeouts": {"mailbox": {**dict(_base.DEFAULT_TIMEOUTS), **(tm.get("mailbox") or {})},
                     "headless": {**dict(_hl.HEADLESS_TIMEOUTS), **(tm.get("headless") or {})},
                     "fallback_s": float(tm.get("fallback_s", _base.FALLBACK_TIMEOUT)),
                     # codex headless(2026-10-08):开线程那一轮(拿 thread id)的墙钟,不占角色预算。
                     "codex_open_s": float(tm.get("codex_open_s", _hx.OPEN_TIMEOUT_S))},
        "max_turns": {**dict(_hl.MAX_TURNS), **(knob("session", "max_turns", None, {}, cfg) or {})},
        "tier_max_turns": {**dict(_hl.TIER_MAX_TURNS), **(knob("session", "tier_max_turns", None, {}, cfg) or {})},
        "default_max_turns": int(knob("session", "default_max_turns", None, _hl.DEFAULT_MAX_TURNS, cfg)),
        "mailbox": {"never_taken_factor": float(mb.get("never_taken_factor", _mb.NEVER_TAKEN_FACTOR)),
                    "wait_s": float(mb.get("wait_s", _mb.DEFAULT_WAIT_SECONDS)),
                    "dead_heartbeats": int(mb.get("dead_heartbeats", _mb.DEAD_HEARTBEATS)),
                    "by_reference": bool(mb.get("by_reference", False))},
        "max_attempts": int(knob("session", "max_attempts", None, _runner.SESSION_MAX_ATTEMPTS, cfg)),
        "runner": {"poll_seconds": float(rn.get("poll_seconds", 5.0)),
                   "max_rounds": int(rn.get("max_rounds", 20000)),
                   "timeout_multiplier": float(rn.get("timeout_multiplier", 1.0)),
                   "fanout_warmup_s": float(rn.get("fanout_warmup_s", 0.0))},
    }
