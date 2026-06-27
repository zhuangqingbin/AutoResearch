"""self_review regime 漂移 warn 单测 —— ctx['regime_drift'] → warn(非 fail)。无网络。

覆盖 spec §D:drift reason 传入 → 出 warn;无则不影响(老路不破)。
"""
from __future__ import annotations

from autoresearch.learning.self_review import review


def _ctx(**extra):
    base = {"finalists": [], "n_cards_expected": 1, "n_cards_present": 1}
    base.update(extra)
    return base


def test_drift_reason_emits_warn():
    r = review(_ctx(regime_drift="当日 regime=trend ≠ 校准主导 range,建议重校准"))
    assert any(x["check"] == "regime 漂移" and x["severity"] == "warn" for x in r["failures"])
    assert r["ok"] is True                      # warn 不致命


def test_no_drift_key_no_finding():
    r = review(_ctx())
    assert not any(x["check"] == "regime 漂移" for x in r["failures"])


def test_empty_drift_no_finding():
    r = review(_ctx(regime_drift=""))           # 空串 falsy → 不出
    assert not any(x["check"] == "regime 漂移" for x in r["failures"])
