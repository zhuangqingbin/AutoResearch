"""锁生产 scan_config.jsonc 的召回键(2026-08-21 重开 reversal_confirm):配置是单一事实源,
测试锁住「重开了」这件事本身,防 revert/手滑把它再摘掉而无人知。"""
from __future__ import annotations

from pathlib import Path

from autoresearch.scan.user_config import load_user_config

CFG = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "scan-market" / "scan_config.jsonc"


def test_reversal_confirm_reopened_with_quota():
    cfg = load_user_config(CFG)
    funnel = cfg["funnel"]
    assert "reversal_confirm" in funnel["recall_channels"]
    assert "reversal" in funnel["recall_channels"]                 # A/B 两路同时活体
    assert funnel["channel_quotas"]["reversal_confirm"] == 150
    # 36 日版六键仍全写(注册表默认回落陷阱,见 jsonc 注释)
    for k, v in {"value": 312, "momentum": 188, "heat": 112,
                 "healthy": 112, "growth": 112, "main_fund": 150}.items():
        assert funnel["channel_quotas"][k] == v
