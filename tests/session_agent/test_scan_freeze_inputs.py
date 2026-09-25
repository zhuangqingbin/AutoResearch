from __future__ import annotations

import json
from types import SimpleNamespace

from autoresearch.session_agent import domain_ops

_PREFERENCE_WEIGHTS = {"momentum": 0.20, "tech": 0.15, "volprice": 0.15, "fund_main": 0.15,
                       "chip": 0.05, "north": 0.05, "growth": 0.05, "value": 0.05,
                       "fund_retail": -0.05, "rz": 0.0}


def _current(tmp_path):
    return SimpleNamespace(staging=tmp_path)


def test_freeze_scan_runtime_inputs_manifest_carries_bumped_schema_version(monkeypatch, tmp_path):
    """M4(2026-09-25 终审):`_session_inputs/manifest.json` 在 `weight_profile_error` 键加入前
    的 schema_version=1 形状恰是两键 `{"schema_version", "present"}`(见
    docs/superpowers/plans/2026-09-24-buyability-realignment.md 的改动前后对照)。新键让这份
    identity 快照的形状变了却没挪版本号——本测试锁定「变形必须伴随版本号」这条契约:
    即便 preference 档冻结干净成功(`weight_profile_error` 恒 None),新键仍必须让读者能从
    `schema_version` 一眼看出这不是老形状。
    """
    monkeypatch.setattr(
        "autoresearch.scan.user_config.load_user_config",
        lambda path=None: {"funnel": {"weight_profile": "preference",
                                      "preference_weights": _PREFERENCE_WEIGHTS}},
    )
    result = domain_ops._freeze_scan_runtime_inputs(_current(tmp_path))
    assert result["schema_version"] == 2
    assert result["weight_profile_error"] is None
    assert result["present"]["L1_weight_profile.json"] is True

    on_disk = json.loads((tmp_path / "_session_inputs" / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == result


def test_freeze_scan_runtime_inputs_calibrated_profile_still_has_the_key(monkeypatch, tmp_path):
    """calibrated 档(不写 L1_weight_profile.json)也走同一份 `value` 字面量,`weight_profile_
    error` 键必须仍在(恒 None)——不是只有 preference 档才有第三个键,形状对所有档一致。"""
    monkeypatch.setattr(
        "autoresearch.scan.user_config.load_user_config",
        lambda path=None: {"funnel": {"weight_profile": "calibrated"}},
    )
    result = domain_ops._freeze_scan_runtime_inputs(_current(tmp_path))
    assert result["schema_version"] == 2
    assert result["weight_profile_error"] is None
    assert "L1_weight_profile.json" not in result["present"]
