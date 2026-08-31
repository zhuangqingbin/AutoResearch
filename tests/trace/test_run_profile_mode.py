"""模式词汇必须只有一份 —— 「该有什么」的分母不许有两个版本。

spec: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.2 K3

`run_profile.MODES` 曾只有 3 个而 `run_mode.MODES` 有 4 个(多 `SENTINEL_PINNED`)。
两份词汇不同源的后果不是「少一个字符串」:`scan_profile(mode="SENTINEL_PINNED")`
直接 `ValueError`,持仓哨兵趟在完整性里落进未知分支。
"""

from __future__ import annotations

import pytest

from autoresearch.scan import run_mode
from autoresearch.scan.run_profile import (
    MODES,
    SENTINEL_SKIPPED_ROLES,
    SENTINEL_SKIPPED_STAGES,
    scan_profile,
)


def test_modes_match_run_mode_vocabulary():
    # 两份 mode 词汇必须同源:run_profile 少一个 SENTINEL_PINNED 会让
    # 「持仓哨兵趟」在完整性里落进未知分支。
    assert set(MODES) == set(run_mode.MODES)


def test_sentinel_empty_does_not_owe_l4_roles():
    prof = scan_profile(mode="SENTINEL_EMPTY", business_status="SUCCEEDED")
    assert "l4" not in prof.expected_stages
    assert "l4-card" not in [r for r in prof.agent_roles if prof.role_expected(r)]
    assert "l4-intel" not in [r for r in prof.agent_roles if prof.role_expected(r)]


def test_sentinel_pinned_still_owes_l4_roles():
    # 持仓哨兵**跑** L4(持仓票必须出卡),它不是「跳过 L4」的那种哨兵。
    prof = scan_profile(mode="SENTINEL_PINNED", business_status="SUCCEEDED")
    assert "l4" in prof.expected_stages
    assert "l4-card" in prof.agent_roles
    assert prof.role_expected("l4-card") is True


def test_only_sentinel_empty_triggers_the_skip_sets():
    """跳过集合的触发条件是**逐字** SENTINEL_EMPTY,不是 `startswith("SENTINEL")`。

    把判断放宽成前缀匹配会把持仓哨兵一起吞掉 —— 持仓票于是永远拿不到当日决策卡,
    而完整性还会说「本来就不该有」(`STAGES.md`「哨兵 vs 持仓」)。
    """
    assert "l4" in SENTINEL_SKIPPED_STAGES
    assert {"l4-card", "l4-intel"} <= SENTINEL_SKIPPED_ROLES
    pinned = scan_profile(mode="SENTINEL_PINNED")
    assert set(SENTINEL_SKIPPED_STAGES) <= set(pinned.expected_stages)
    assert all(pinned.role_expected(r) for r in ("l4-card", "l4-intel"))


def test_unknown_mode_still_raises():
    with pytest.raises(ValueError):
        scan_profile(mode="SENTINEL_MAYBE")
