"""`scan_config.dossier`:覆盖档案层的运行时旋钮(2026-09-27 P3)。

dossier 层在分层守卫里低于 scan,经 `contracts.scan_config.knob` 读 config;缺键 = 各模块常量。
"""
from __future__ import annotations

from autoresearch.contracts import scan_config as _cfg_registry


def dossier_cfg() -> dict:
    from autoresearch.dossier import debt_slo as _slo, schema as _schema

    k = _cfg_registry.knob
    return {"pool_cap": int(k("dossier", "pool_cap", None, 30)),
            "recent_days": int(k("dossier", "recent_days", None, 20)),
            "entry_min": int(k("dossier", "entry_min", None, 2)),
            "init_per_night": int(k("dossier", "init_per_night", None, _slo.NIGHTLY_CAP)),
            "max_pending_age_days": int(k("dossier", "max_pending_age_days", None, _slo.MAX_PENDING_AGE_DAYS)),
            "throughput_window_days": int(k("dossier", "throughput_window_days", None, _slo.THROUGHPUT_WINDOW_DAYS)),
            "intel_gap_max_lines": int(k("dossier", "intel_gap_max_lines", None, 2)),
            "summary_cap": int(k("dossier", "summary_cap", None, _schema.SUMMARY_CAP)),
            "research_body_cap": int(k("dossier", "research_body_cap", None, _schema.RESEARCH_BODY_CAP)),
            "stale_days": int(k("dossier", "stale_days", None, _schema.STALE_DAYS))}
