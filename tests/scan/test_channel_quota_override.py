#!/usr/bin/env python3
"""channel_quotas/channel_floors override 接线(scan_config.json funnel advisory 消费入口)。

design: .superpowers/sdd/task-8-brief.md。override 语义:`effective = {n: dataclasses.replace(
CHANNEL_DEFAULTS[n], quota=q?, floor=f?) for n in names}`,build 与 quota_union 都吃 effective;
两者均 None(默认)→ effective 逐值等于 CHANNEL_DEFAULTS(parity,不许破 tests/scan/test_parity.py)。

`recall_select` 实际形参名(:215)是 `recall_mode`/`recall_channels`(brief 示例用 `mode`/`channels`
系笔误,已同步为真名);heat 通道排序主轴是 `amount_yi`,brief 示例 fixture 未列,此处补列。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan import universe

DATE = "2026-07-09"


def _scored(n=300) -> pd.DataFrame:
    df = pd.DataFrame({"code": [f"{i:06d}" for i in range(n)],
                       "name": ["x"] * n, "composite": range(n, 0, -1),
                       "momentum": range(n), "value": range(n)})
    df["amount_yi"] = range(n, 0, -1)      # heat 通道排序主轴(真实签名需要此列)
    return df


def test_shadow_variants_pass_channel_overrides_through(monkeypatch, tmp_path):
    """影子透传回归锁(7cc7f51/T9-11 review Important#3):pre_healthy/capfloor20 内部 recall_select
    必须吃到 channel_quotas/floors,否则反事实被配额差异污染。spy 捕获 kwargs,不跑真漏斗。"""
    seen = []

    def _spy_recall(scored, date, recall_n, recall_mode, channels=None, **kw):
        seen.append(kw)
        return scored.head(0), {}

    monkeypatch.setattr(universe, "recall_select", _spy_recall)
    monkeypatch.setattr("autoresearch.scan.recall.l2_stratify.select_l2",
                        lambda df, n, **kw: (df.head(0), {}))
    monkeypatch.setattr(universe, "build_market_frame",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no network in test")))
    universe.write_shadow_variants(tmp_path, _scored(), _scored().head(5), DATE,
                                   recall_n=50, l2_n=10, l2_floors=None, l2_sector_cap=0.2,
                                   l2_cols=["code"], recall_mode="multi",
                                   channel_quotas={"heat": 5}, channel_floors={"heat": 1})
    assert seen, "pre_healthy 分支未调用 recall_select(spy 未触发)"
    for kw in seen:
        assert kw.get("channel_quotas") == {"heat": 5} and kw.get("channel_floors") == {"heat": 1}


def test_funnel_overlay_fills_only_none_and_explicit_wins(monkeypatch):
    """FN-1 第三修:run 直调路径(prelude/universe.main)的 scan_config 兜底——None 的键补齐,显式恒优先。"""
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda: {"funnel": {"recall_channels": ["composite"],
                                            "channel_quotas": {"heat": 150}}})
    rc, q, f = universe._funnel_overlay(None, None, None)
    assert rc == ["composite"] and q == {"heat": 150} and f is None
    rc2, q2, f2 = universe._funnel_overlay(["momentum"], {"heat": 99}, {"heat": 1})
    assert (rc2, q2, f2) == (["momentum"], {"heat": 99}, {"heat": 1})


def test_funnel_overlay_missing_config_is_parity(monkeypatch):
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config", lambda: {})
    assert universe._funnel_overlay(None, None, None) == (None, None, None)


def test_quota_override_shrinks_channel():
    """channel_quotas={"heat": 10} 把 heat 通道的召回候选数(L1_channels 长表行数)砍到 ≤10。"""
    df = _scored()
    _, per_d = universe.recall_select(df, DATE, recall_n=200, recall_mode="multi",
                                      recall_channels=["composite", "heat"])
    _, per_c = universe.recall_select(df, DATE, recall_n=200, recall_mode="multi",
                                      recall_channels=["composite", "heat"],
                                      channel_quotas={"heat": 10})
    heat_default = per_d[per_d["channel"] == "heat"]
    heat_cut = per_c[per_c["channel"] == "heat"]
    assert len(heat_cut) <= 10 < len(heat_default)


def test_no_override_is_parity():
    """channel_quotas=None、channel_floors=None(默认)→ 逐值复现无 override 的输出(parity 锚)。"""
    df = _scored()
    a, _ = universe.recall_select(df, DATE, recall_n=200, recall_mode="multi",
                                  recall_channels=["composite", "heat"])
    b, _ = universe.recall_select(df, DATE, recall_n=200, recall_mode="multi",
                                  recall_channels=["composite", "heat"],
                                  channel_quotas=None, channel_floors=None)
    assert a["code"].tolist() == b["code"].tolist()


def test_floor_override_protects_low_composite_channel_leader():
    """channel_floors 覆盖:heat 通道 rank-1(低 composite、高成交额)靠 floor≥1 才能挤过 recall_n 裁剪。

    单一 channel=["heat"](quota 200 默认 > n=20,故 floor 是唯一变量):code000019 的 composite
    全场最低(=1)但 amount_yi(heat_score)全场最高 → floor=0 时被 recall_n 按 composite 裁掉,
    floor≥1 时作为 heat 通道保底强制进场,不看 composite。
    """
    n = 20
    df = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "name": ["x"] * n,
        "composite": list(range(n, 0, -1)),   # code000000 composite 最高 … code000019 最低(=1)
        "momentum": range(n), "value": range(n),
        "amount_yi": list(range(1, n + 1)),    # code000019 heat_score(成交额)最高 → heat rank1
    })
    off, _ = universe.recall_select(df, DATE, recall_n=5, recall_mode="multi",
                                    recall_channels=["heat"], channel_floors={"heat": 0})
    on, _ = universe.recall_select(df, DATE, recall_n=5, recall_mode="multi",
                                   recall_channels=["heat"], channel_floors={"heat": 1})
    assert "000019" not in off["code"].tolist()   # floor=0:无保底,低 composite 被裁
    assert "000019" in on["code"].tolist()        # floor=1:heat rank1 保底进场,不看 composite
