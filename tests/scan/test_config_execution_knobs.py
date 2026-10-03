"""P2 扩容 · execution 块:运营入场截止时刻与过期滞后(2026-09-27)。两处 14:45 收成一个键。"""
from __future__ import annotations

import json
from datetime import time


def _cfg(tmp_path, monkeypatch, cfg: dict) -> None:
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", p)


def test_entry_cutoff_and_expiry_lag_follow_config(tmp_path, monkeypatch):
    from autoresearch.scan import exec_anchor, overseas

    _cfg(tmp_path, monkeypatch, {"execution": {"entry_cutoff": "15:00", "expiry_lag_sessions": 5}})
    assert exec_anchor.entry_cutoff() == time(15, 0)
    assert overseas.entry_cutoff() == time(15, 0)
    assert exec_anchor.expiry_lag_sessions() == 5
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert exec_anchor.entry_cutoff() == time(14, 45) and exec_anchor.expiry_lag_sessions() == 3


def test_bad_clock_string_is_rejected_at_load(tmp_path):
    from autoresearch.scan.user_config import load_user_config
    import pytest

    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"execution": {"entry_cutoff": "3pm"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="非法"):
        load_user_config(p)
