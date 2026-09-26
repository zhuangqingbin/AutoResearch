#!/usr/bin/env python3
"""运行旋钮 knob() + l0/l2/sector/funnel 新键(2026-08-11 配置单一事实源波)。

三件套之三(白名单 `load_user_config` + 消费点接线 + 本测试锁)。优先级恒为
**显式 CLI/形参 > scan_config > 内建默认**;白名单外键/错型 load 即 raise。
消费点:`frame.build_market_frame`(L0 单一代码路径)、`universe.run`(meta 记实际生效值)、
`prelude.run_prelude`(regime_aware,生产路缺省 True)、`sector/reuse|pack.main`。
"""
from __future__ import annotations

import json
import math

import pytest

from autoresearch.scan.user_config import knob, load_user_config

# ───────────────────────── knob():解析优先级 ─────────────────────────


def test_knob_explicit_wins_over_config():
    assert knob("l0", "cap_floor_yi", 25.0, 30.0, cfg={"l0": {"cap_floor_yi": 20}}) == 25.0


def test_knob_config_fills_none():
    assert knob("l0", "cap_floor_yi", None, 30.0, cfg={"l0": {"cap_floor_yi": 20}}) == 20


def test_knob_default_when_block_or_key_missing():
    assert knob("l0", "cap_floor_yi", None, 30.0, cfg={}) == 30.0
    assert knob("l0", "cap_floor_yi", None, 30.0, cfg={"l0": {}}) == 30.0


def test_knob_false_zero_are_valid_config_values():
    """falsy ≠ 缺省:include_bj=false / min_amount_yi=0 必须原样生效,不得被 or 链吞掉。"""
    assert knob("l0", "include_bj", None, True, cfg={"l0": {"include_bj": False}}) is False
    assert knob("l0", "min_amount_yi", None, 1.0, cfg={"l0": {"min_amount_yi": 0}}) == 0


def test_knob_reads_default_path(monkeypatch, tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"sector": {"reuse_ttl_days": 3}}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", p)
    assert knob("sector", "reuse_ttl_days", None, 5) == 3


def test_knob_bad_config_falls_back_with_warning(monkeypatch, tmp_path, capsys):
    """坏配置 → 响亮警告 + 内建默认(配置层故障不挡确定性扫描,但降级必须留痕)。"""
    p = tmp_path / "scan_config.jsonc"
    p.write_text('{"bogus_top": 1}', encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", p)
    assert knob("l0", "cap_floor_yi", None, 30.0) == 30.0
    assert "scan_config 读取失败" in capsys.readouterr().err


# ───────────────────────── 白名单:新块 ─────────────────────────


def test_new_blocks_whitelisted(tmp_path):
    raw = {"l0": {"cap_floor_yi": 30, "include_bj": True, "source": "tushare",
                  "min_amount_yi": 0, "min_list_days": 0},
           "l2": {"sector_cap": 0.20, "knife_cap": True,
                  "sector_seats": {"enabled": True, "per_sector": 2, "max_sectors": 3}},
           "sector": {"reuse_ttl_days": 5, "max_briefs": 6},
           "funnel": {"regime_aware": True, "recall_n": 1000, "l2_n": 200},
           # 入场门 + A/R 分级总开关(2026-09-24 可买性对齐 §2.6,v4.0)——白名单只负责
           # 「开关存在且类型对」,解析+默认值兜底留在消费侧
           # `relative_buy.configured_tiering()`(见该函数自己的测试)。
           "relative_buy": {"tiering": True},
           # L4 卡数(2026-09-26 用户需求):唯一算法 scan/l4/card_count.effective_caps
           "l4": {"max_cards": 13, "budget_flags": True}}
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert load_user_config(p) == raw


def test_weighted_budget_keys_are_whitelisted(tmp_path):
    raw = {
        "budgets": {
            "run_weighted_warn": 7_000_000,
            "run_weighted_target": 5_000_000,
        }
    }
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert load_user_config(p) == raw


@pytest.mark.parametrize("value", [True, 0, -1])
@pytest.mark.parametrize("key", ["run_weighted_warn", "run_weighted_target"])
def test_weighted_budget_values_must_be_positive_numbers(tmp_path, key, value):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"budgets": {key: value}}), encoding="utf-8")
    with pytest.raises(ValueError, match=key):
        load_user_config(p)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("key", ["run_weighted_warn", "run_weighted_target"])
def test_weighted_budget_values_must_be_finite(tmp_path, key, value):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"budgets": {key: value}}), encoding="utf-8")
    with pytest.raises(ValueError, match=key):
        load_user_config(p)


def test_weighted_budget_target_cannot_exceed_warn(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(
        json.dumps(
            {
                "budgets": {
                    "run_weighted_warn": 5_000_000,
                    "run_weighted_target": 5_000_001,
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="target.*warn"):
        load_user_config(p)


@pytest.mark.parametrize("block,bad", [
    ("l0", {"cap_floor": 30}),          # 拼写错(少 _yi)
    ("l2", {"cap": 0.2}),
    ("sector", {"ttl": 5}),
    ("funnel", {"regimeaware": True}),
])
def test_new_blocks_unknown_subkey_raises(tmp_path, block, bad):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({block: bad}), encoding="utf-8")
    with pytest.raises(ValueError, match=block):
        load_user_config(p)


@pytest.mark.parametrize("raw", [
    {"l0": {"cap_floor_yi": "30"}},
    {"l0": {"include_bj": 1}},
    {"l0": {"source": "akshare"}},
    {"l0": {"min_list_days": -1}},
    {"funnel": {"regime_aware": "yes"}},
    {"funnel": {"recall_n": 0}},
    {"sector": {"reuse_ttl_days": -1}},
    {"l2": {"sector_cap": True}},        # bool 不是 number
    {"l4": {"max_cards": 0}},            # 卡数须正整数
    {"l4": {"budget_flags": "yes"}},
])
def test_knob_type_violations_raise(tmp_path, raw):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="非法"):
        load_user_config(p)


# ───────────────────────── universe.run 接线(spy,不跑真漏斗) ─────────────────────────


def _patch_cfg(monkeypatch, cfg):
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: cfg)


def test_run_resolves_l0_knobs_from_config(monkeypatch):
    """None 形参从 config 补齐,并以**具体值**传给 build_market_frame(meta 可复现凭据)。"""
    from autoresearch.scan import universe
    seen: dict = {}

    def _spy_bmf(d, **kw):
        seen.update(kw)
        raise RuntimeError("stop-after-l0")

    monkeypatch.setattr(universe, "build_market_frame", _spy_bmf)
    _patch_cfg(monkeypatch, {"l0": {"cap_floor_yi": 20, "include_bj": False},
                             "funnel": {"regime_aware": True}})
    with pytest.raises(RuntimeError, match="stop-after-l0"):
        universe.run("2026-01-05")
    assert seen["cap_floor_yi"] == 20.0
    assert seen["include_bj"] is False
    assert seen["source"] == "tushare"          # 缺键 → 内建默认


def test_run_explicit_args_beat_config(monkeypatch):
    from autoresearch.scan import universe
    seen: dict = {}

    def _spy_bmf(d, **kw):
        seen.update(kw)
        raise RuntimeError("stop-after-l0")

    monkeypatch.setattr(universe, "build_market_frame", _spy_bmf)
    _patch_cfg(monkeypatch, {"l0": {"cap_floor_yi": 20, "include_bj": False}})
    with pytest.raises(RuntimeError, match="stop-after-l0"):
        universe.run("2026-01-05", cap_floor_yi=25.0, include_bj=True)
    assert seen["cap_floor_yi"] == 25.0
    assert seen["include_bj"] is True


def test_run_parity_without_config(monkeypatch):
    """缺文件 = 内建默认(30 亿/纳北交所/tushare/门关)——逐字节 parity 的根。"""
    from autoresearch.scan import universe
    seen: dict = {}

    def _spy_bmf(d, **kw):
        seen.update(kw)
        raise RuntimeError("stop-after-l0")

    monkeypatch.setattr(universe, "build_market_frame", _spy_bmf)
    _patch_cfg(monkeypatch, {})
    with pytest.raises(RuntimeError, match="stop-after-l0"):
        universe.run("2026-01-05")
    assert seen == {"cap_floor_yi": 30.0, "include_bj": True, "source": "tushare",
                    "l0_min_amount_yi": 0.0, "l0_min_list_days": 0}


# ───────────────────────── prelude:regime_aware 生产路缺省 True ─────────────────────────


def test_prelude_regime_fallback_is_true(monkeypatch):
    """prelude 生产路:缺配置/缺键 → True(历史缺省);config false → 关;CLI False 恒优先。"""
    _patch_cfg(monkeypatch, {})
    assert knob("funnel", "regime_aware", None, True) is True
    _patch_cfg(monkeypatch, {"funnel": {"regime_aware": False}})
    assert knob("funnel", "regime_aware", None, True) is False
    assert knob("funnel", "regime_aware", False, True) is False   # CLI --no-regime-aware


# ───────────────────────── 白名单:召回权重档(weight_profile/preference_weights) ─────────────────────────


def test_weight_profile_keys_whitelisted(tmp_path):
    raw = {"funnel": {"weight_profile": "preference", "preference_weights": {
        "momentum": 0.20, "tech": 0.15, "volprice": 0.15, "fund_main": 0.15, "chip": 0.05,
        "north": 0.05, "growth": 0.05, "value": 0.05, "fund_retail": -0.05, "rz": 0.0}}}
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert load_user_config(p) == raw


@pytest.mark.parametrize("bad", ["prefer", "", 1, None])
def test_weight_profile_must_be_calibrated_or_preference(tmp_path, bad):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"funnel": {"weight_profile": bad}}), encoding="utf-8")
    with pytest.raises(ValueError, match="weight_profile"):
        load_user_config(p)


@pytest.mark.parametrize("pw", [
    {"momentum": 0.2},                                   # 缺键
    {"momentum": 0.2, "tech": 0.1, "volprice": 0.1, "fund_main": 0.1, "chip": 0.0, "north": 0.0,
     "growth": 0.0, "value": 0.0, "fund_retail": 0.0, "rz": 0.0, "extra": 1.0},   # 多键
    {"momentum": math.nan, "tech": 0.1, "volprice": 0.1, "fund_main": 0.1, "chip": 0.0, "north": 0.0,
     "growth": 0.0, "value": 0.0, "fund_retail": 0.0, "rz": 0.0},                 # 非有限
])
def test_preference_weights_must_be_exactly_the_ten_groups(tmp_path, pw):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"funnel": {"preference_weights": pw}}), encoding="utf-8")
    with pytest.raises(ValueError, match="preference_weights"):
        load_user_config(p)


def test_preference_weights_all_zero_is_rejected(tmp_path):
    """M3(2026-09-25 终审):键集全对、逐个有限,但绝对值之和为零的权重必须被拒——不然
    `combine_group_scores` 除以 `wabs.replace(0, nan)` 全 NaN,composite 全 NaN,下游
    `sector_neutral` 的 `fillna(-1e18)` 会让 L2 退化成输入行序,且没有任何测试会变红
    (两处都在验证范围外)。唯一挡这条路的就是本函数——键集/有限性校验都过、但十个权重
    全零的配置块此前会静默通过。"""
    pw = {"momentum": 0.0, "tech": 0.0, "volprice": 0.0, "fund_main": 0.0, "chip": 0.0,
          "north": 0.0, "growth": 0.0, "value": 0.0, "fund_retail": 0.0, "rz": 0.0}
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"funnel": {"preference_weights": pw}}), encoding="utf-8")
    with pytest.raises(ValueError, match="preference_weights"):
        load_user_config(p)


# ───────────────────────── 活体验收:生产 l2 floors / knife_cap(2026-09-24 §2.2) ─────────────────────────


def test_production_config_l2_shape_knobs():
    """生产配置(2026-09-24 §2.2):健康 25 / 反转 6 / 低位转强 6,落刀帽开。"""
    from pathlib import Path
    cfg = load_user_config(Path(".claude/skills/scan-market/scan_config.jsonc"))
    l2 = cfg["l2"]
    assert l2["knife_cap"] is True
    assert l2["floors"]["健康"] == 25 and l2["floors"]["反转"] == 6 and l2["floors"]["低位转强"] == 6
    assert sum(l2["floors"].values()) == 103


# ───────────────────────── 白名单:日历第三腿(2026-09-25 指数调样事件 §2.3) ─────────────────────────


def test_calendar_block_whitelisted(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"calendar": {"index_rebalance": True}}), encoding="utf-8")
    assert load_user_config(p) == {"calendar": {"index_rebalance": True}}


@pytest.mark.parametrize("bad", ["yes", 1, None, {"enabled": True}])
def test_calendar_index_rebalance_must_be_bool(tmp_path, bad):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"calendar": {"index_rebalance": bad}}), encoding="utf-8")
    with pytest.raises(ValueError, match="index_rebalance"):
        load_user_config(p)


def test_calendar_unknown_subkey_raises(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"calendar": {"index_rebalanc": True}}), encoding="utf-8")
    with pytest.raises(ValueError, match="calendar"):
        load_user_config(p)


def test_knob_calendar_index_rebalance_defaults_off():
    assert knob("calendar", "index_rebalance", None, False, cfg={}) is False
    assert knob("calendar", "index_rebalance", None, False, cfg={"calendar": {"index_rebalance": True}}) is True
    assert knob("calendar", "index_rebalance", False, False, cfg={"calendar": {"index_rebalance": True}}) is False


# ───────────────────────── 白名单:intel 死票门(2026-09-26 daily-engine §4 A3,默认关) ─────────────────────────


def test_l4_intel_skip_when_dead_whitelisted(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l4_intel": {"enabled": True, "max_queries": 20,
                                          "skip_when_dead": True}}), encoding="utf-8")
    assert load_user_config(p)["l4_intel"]["skip_when_dead"] is True


@pytest.mark.parametrize("bad", ["yes", 1, None, {"on": True}])
def test_l4_intel_skip_when_dead_must_be_bool(tmp_path, bad):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l4_intel": {"skip_when_dead": bad}}), encoding="utf-8")
    # 锚类型校验本身(「须为 boolean」),不是「未知子键」那条也会带上键名的报错
    with pytest.raises(ValueError, match=r"l4_intel\.skip_when_dead=.*须为 boolean"):
        load_user_config(p)


def test_production_config_keeps_the_intel_dead_gate_off():
    """冻结窗内默认关 = 逐字 parity;开旋钮是用户动作(派发接线在批 6 Task 3,冻结窗后)。"""
    from pathlib import Path
    cfg = load_user_config(Path(".claude/skills/scan-market/scan_config.jsonc"))
    assert cfg["l4_intel"]["skip_when_dead"] is False
    assert knob("l4_intel", "skip_when_dead", None, False, cfg={}) is False


# ───────────────────────── 白名单:flow_adv_days 描述字段(2026-09-25 §2.2 批 B3) ─────────────────────────


@pytest.mark.parametrize("bad", ["yes", 1, None, {"enabled": True}])
def test_calendar_index_rebalance_flow_must_be_bool(tmp_path, bad):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"calendar": {"index_rebalance_flow": bad}}), encoding="utf-8")
    with pytest.raises(ValueError, match="index_rebalance_flow"):
        load_user_config(p)


# ───────────────────────── 白名单:E6 第五门(2026-09-25 指数调样事件 §2.4) ─────────────────────────


def test_relative_buy_rebalance_gate_whitelisted_and_bool(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"relative_buy": {"rebalance_gate": True}}), encoding="utf-8")
    assert load_user_config(p) == {"relative_buy": {"rebalance_gate": True}}
    p.write_text(json.dumps({"relative_buy": {"rebalance_gate": "on"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="rebalance_gate"):
        load_user_config(p)


def test_configured_rebalance_gate_reads_config_and_degrades_loudly(monkeypatch, capsys):
    import autoresearch.scan.user_config as uc
    from autoresearch.scan.relative_buy import configured_rebalance_gate
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {"relative_buy": {"rebalance_gate": True}})
    assert configured_rebalance_gate() is True
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {})
    assert configured_rebalance_gate() is False                              # 缺键 = 关 = parity

    def boom(path=None):
        raise ValueError("bad config")
    monkeypatch.setattr(uc, "load_user_config", boom)
    assert configured_rebalance_gate() is False
    assert "rebalance_gate" in capsys.readouterr().err                       # 配置层故障留痕


# ───────────────────────── 活体验收:生产日历第三腿 + E6 第五门同开(2026-09-25 §4 批 B1/B2) ─────────────────────────


def test_production_config_index_rebalance_knobs_on():
    """生产配置:日历第三腿(`calendar.index_rebalance`)与 E6 第五门(`relative_buy.
    rebalance_gate`)同开——两根杆按设计「应同开同关」(各自的回滚杆注释里都这么写),
    这条测试锁的是"两个都真的开着",不是只锁一个,防止未来只回滚一半而没人注意到
    另一半仍在跑一条已经不对齐的组合。"""
    from pathlib import Path
    cfg = load_user_config(Path(".claude/skills/scan-market/scan_config.jsonc"))
    assert cfg["calendar"]["index_rebalance"] is True
    assert cfg["relative_buy"]["rebalance_gate"] is True


def test_retired_finalist_max_is_rejected_with_pointer(tmp_path):
    """两个键管一个数 = 漂移源:l3.finalist_max 退役,写了要当场指路到 l4.max_cards。"""
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l3": {"finalist_max": 10}}), encoding="utf-8")
    with pytest.raises(ValueError, match="l4.max_cards"):
        load_user_config(p)
