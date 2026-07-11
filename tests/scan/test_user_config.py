#!/usr/bin/env python3
"""scan_config.json 用户配置层 —— 白名单加载 + ScanConfig 映射 + frame --json 回显。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §4.2。
plan: docs/plans/2026-07-11-pinned-config-plan.md Task 1(全波地基)。

白名单外顶层键 / funnel·pinned·reuse 白名单外子键 → raise(防拼写错静默失效);缺文件 → {}
(=现行为,parity)。`agents`/`l4_gate` 内部结构本层不校验(消费方——Task 2 workflow / Task 3+
gate registry——各自解释其形状)。
"""
from __future__ import annotations

import json

import pytest

from autoresearch.scan.config import ScanConfig
from autoresearch.scan.user_config import (
    _strip_jsonc,
    apply_to_scan_config,
    load_pinned,
    load_user_config,
)


def test_strip_jsonc_removes_comments_keeps_strings():
    """// 行注释与 /* */ 块注释剥离;字符串内的 // 原样保留。"""
    import json
    src = '''{
      // 顶层说明
      "funnel": {"recall_channels": ["a", "b"]},  // 行尾说明
      /* 块注释 */
      "reuse": {"max_age_days": 4}
    }'''
    assert json.loads(_strip_jsonc(src)) == {"funnel": {"recall_channels": ["a", "b"]},
                                             "reuse": {"max_age_days": 4}}
    # 字符串内的 // 不被误删
    assert json.loads(_strip_jsonc('{"pinned": {"cap": 5}, "x": "a//b"}'))["x"] == "a//b"


def test_load_user_config_parses_jsonc(tmp_path):
    """带 // 说明的真 scan_config.json 能正常加载 + 白名单校验。"""
    p = tmp_path / "scan_config.json"
    p.write_text('{\n  // 召回通道整编\n  "funnel": {"recall_channels": ["composite", "value"]}\n}',
                 encoding="utf-8")
    assert load_user_config(p)["funnel"]["recall_channels"] == ["composite", "value"]


def test_load_pinned_parses_jsonc(tmp_path):
    """带 // 说明的真 pinned.json(空 active 列表)→ kept 空,不炸。"""
    p = tmp_path / "pinned.json"
    p.write_text('[\n  // 保送票清单(每条 {code,note,expires});空=无保送\n]', encoding="utf-8")
    assert load_pinned("2026-07-11", path=p) == {"kept": [], "expired": []}

_VALID_FULL = {
    "agents": {"l4_card": {"model": "opus", "effort": "high"}},
    "l4_gate": {"name": "conviction_floor_quota", "params": {"quota": 8}},
    "funnel": {
        "recall_channels": ["composite", "momentum", "value"],
        "channel_quotas": {"momentum": 200},
        "channel_floors": {"momentum": 40},
    },
    "pinned": {"cap": 5, "ttl_days": 10},
    "redteam_prob": 0.33,
    "reuse": {"max_age_days": 3, "price_delta_pct": 2.0},
}


# ───────────────────────── load_user_config:合法文件全键 ─────────────────────────


def test_load_user_config_valid_file_all_keys(tmp_path):
    p = tmp_path / "scan_config.json"
    p.write_text(json.dumps(_VALID_FULL), encoding="utf-8")
    assert load_user_config(p) == _VALID_FULL


# ───────────────────────── load_user_config:坏键 raise ─────────────────────────


def test_load_user_config_unknown_top_key_raises(tmp_path):
    p = tmp_path / "scan_config.json"
    p.write_text(json.dumps({"typo_key": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="typo_key"):
        load_user_config(p)


@pytest.mark.parametrize("block,bad", [
    ("funnel", {"recall_channels": ["momentum"], "bogus_sub": 1}),
    ("pinned", {"cap": 5, "bogus_sub": 1}),
    ("reuse", {"max_age_days": 3, "bogus_sub": 1}),
])
def test_load_user_config_unknown_sub_key_raises(tmp_path, block, bad):
    p = tmp_path / "scan_config.json"
    p.write_text(json.dumps({block: bad}), encoding="utf-8")
    with pytest.raises(ValueError, match="bogus_sub"):
        load_user_config(p)


# ───────────────────────── load_user_config:缺文件空 dict ─────────────────────────


def test_load_user_config_missing_file_returns_empty(tmp_path):
    assert load_user_config(tmp_path / "nope.json") == {}


def test_load_user_config_default_path_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # 无 .claude/skills/scan-market/scan_config.json → {}
    assert load_user_config() == {}


# ───────────────────────── apply_to_scan_config:funnel 映射既有字段,其余整块挂新字段 ─────────────────────────


def test_apply_to_scan_config_maps_funnel_to_existing_fields():
    sc = apply_to_scan_config(_VALID_FULL, ScanConfig())
    assert sc.recall_channels == ["composite", "momentum", "value"]
    assert sc.channel_quotas == {"momentum": 200}
    assert sc.channel_floors == {"momentum": 40}


def test_apply_to_scan_config_maps_new_fields():
    sc = apply_to_scan_config(_VALID_FULL, ScanConfig())
    assert sc.agents == _VALID_FULL["agents"]
    assert sc.l4_gate == _VALID_FULL["l4_gate"]
    assert sc.pinned == _VALID_FULL["pinned"]
    assert sc.redteam_prob == 0.33
    assert sc.reuse == _VALID_FULL["reuse"]


def test_apply_to_scan_config_partial_funnel_leaves_rest_default():
    sc = apply_to_scan_config({"funnel": {"recall_channels": ["value"]}}, ScanConfig())
    assert sc.recall_channels == ["value"]
    assert sc.channel_quotas is None
    assert sc.channel_floors is None


def test_apply_to_scan_config_empty_cfg_is_parity():
    assert apply_to_scan_config({}, ScanConfig()) == ScanConfig()


# ───────────────────────── frame --json:user_config 回显 + run meta 落盘 ─────────────────────────


def test_frame_json_echoes_user_config_block(monkeypatch, tmp_path, capsys):
    """无 scan_config.json → user_config={} 仍回显块 + 落 context/scan/<date>/user_config_echo.json。"""
    from autoresearch.scan import frame as scan_frame
    from tests.scan._synth_universe import synth_universe

    df = synth_universe(n=30, seed=1)
    monkeypatch.setattr(scan_frame, "build_market_frame",
                        lambda d, **kw: (df, {"universe_raw": 30, "universe": 30, "after_gate_a": 30}))
    monkeypatch.setattr("autoresearch.macro.state.load_macro_state",
                        lambda today, regime_today=None, path=None:
                        (None, "无 macro_state.json → 只用日频 pack"), raising=True)
    monkeypatch.chdir(tmp_path)   # 隔离:防止真实 context/scan/<date> 历史 staging 被写入

    rc = scan_frame.main(["2026-07-11", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"user_config"' in out

    echo = tmp_path / "context" / "scan" / "2026-07-11" / "user_config_echo.json"
    assert echo.exists()
    assert json.loads(echo.read_text(encoding="utf-8")) == {}


def test_frame_json_echo_reflects_real_config(monkeypatch, tmp_path, capsys):
    """真有 scan_config.json → 回显值与文件一致(run meta = 可复现凭据)。"""
    from autoresearch.scan import frame as scan_frame
    from tests.scan._synth_universe import synth_universe

    df = synth_universe(n=30, seed=2)
    monkeypatch.setattr(scan_frame, "build_market_frame",
                        lambda d, **kw: (df, {"universe_raw": 30, "universe": 30, "after_gate_a": 30}))
    monkeypatch.setattr("autoresearch.macro.state.load_macro_state",
                        lambda today, regime_today=None, path=None:
                        (None, "无 macro_state.json → 只用日频 pack"), raising=True)
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "scan_config.jsonc").write_text(json.dumps({"redteam_prob": 0.2}), encoding="utf-8")

    rc = scan_frame.main(["2026-07-12", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"redteam_prob": 0.2' in out

    echo = tmp_path / "context" / "scan" / "2026-07-12" / "user_config_echo.json"
    assert json.loads(echo.read_text(encoding="utf-8")) == {"redteam_prob": 0.2}
