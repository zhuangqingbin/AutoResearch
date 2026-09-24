"""menu_replay:用已落盘的 score_<group> 列复刻生产 composite(同一段数学),再重放 L1′/L2′ 形状。"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.scoring import _PRIOR_WEIGHTS, composite_score
from tests.scan._synth_universe import synth_universe


def _saved_full():
    """模拟 L1_scored_full.csv:composite_score 的输出 + rank/recalled 列。"""
    df = composite_score(synth_universe(n=300, seed=5), _PRIOR_WEIGHTS)
    df = df.sort_values("composite", ascending=False).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    df.insert(1, "recalled", df["rank"] <= 120)
    return df


def test_recompute_composite_matches_production_within_csv_rounding():
    """容差 0.15,不是 brief 原稿的 0.06 —— 0.06 在本 seed(row 32)与三个真实 staging 日
    (09-01/09-07/09-17,各 4200+ 行、真行业条件化权重)上都会假红:`score_<group>` 落盘四舍
    五入到 1 位小数(±0.0005 每组)经加权求和被 raw→100 分放大到 ≤0.025,叠加"重算值"与
    "落盘值"各自独立 round(.,1) 的边界效应,单行最坏情况可达 0.1(理论上界 0.05+0.025+0.05=
    0.125)——三个真实交易日实测最大值恰好都是 0.10000000000000853,从未见 >0.1,故 0.15 留有
    安全边际。这不是 recompute_composite 的 bug:用未经落盘取整的满精度 groups 重算,逐行与
    生产 composite 位级相同(已用 debug 脚本核过);唯一误差源就是 CSV 只存了 1 位小数这一点,
    brief 自己的注释也是这么说的,只是原稿的数字估紧了。"""
    from autoresearch.research.menu_replay import recompute_composite
    full = _saved_full()
    again = recompute_composite(full, _PRIOR_WEIGHTS)
    assert (again - full["composite"]).abs().max() <= 0.15     # score_* 落盘取 1 位小数(见上,实测上界 0.1)


def test_recompute_composite_changes_with_a_flipped_weight():
    """变异探针:paired with the reproduction test above — a tool that just echoed back the
    saved `composite` column would also pass the reproduction test, so that test alone can't
    tell "correct replay" from "always returns the saved column". This one can: flipping a
    weight's sign must move the recomputed composite, because it is really recomputing."""
    from autoresearch.research.menu_replay import recompute_composite
    full = _saved_full()
    w2 = {"meta": {}, "weights": {"__global__": {**_PRIOR_WEIGHTS["weights"]["__global__"], "momentum": -0.30}}}
    assert (recompute_composite(full, w2) - full["composite"]).abs().max() > 1.0


def test_replay_l1_keeps_other_channels_and_swaps_composite_top(tmp_path):
    from autoresearch.research.menu_replay import replay_l1
    full = _saved_full()
    channels = pd.DataFrame({"channel": ["value"] * 10 + ["composite"] * 20,
                             "code": list(full["code"].iloc[200:210]) + list(full["code"].iloc[:20]),
                             "channel_rank": list(range(1, 11)) + list(range(1, 21)),
                             "channel_score": 1.0})
    new = -full["composite"]                          # 完全反转的新分
    l1p = replay_l1(full, channels, new, composite_quota=20)
    assert set(full["code"].iloc[200:210]) <= set(l1p["code"])          # value 路保留
    assert set(full["code"].iloc[-20:]) <= set(l1p["code"])             # 新 composite 前 20 = 旧倒数 20
    assert not (set(full["code"].iloc[:20]) & set(l1p["code"]))          # 旧 composite 前 20 出局
    assert {"recall_channels", "n_channels", "composite"} <= set(l1p.columns)


def _all_composite_channels(full: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"channel": ["composite"] * len(full), "code": full["code"],
                         "channel_rank": range(1, len(full) + 1), "channel_score": 1.0})


def test_replay_l2_does_not_forward_unwired_params_to_select_l2():
    """`select_l2` 今天没有 `knife_cap_share` 形参(Task 11 才加),`replay_l2` 只能在**自己的**
    签名里占位收 `knife_cap_share`/`sector_seats`(给 Task 16 接线用),绝不能把它们透传进
    `select_l2` —— 传了就是 TypeError。本测试把两个参数都喂非默认值,证明调用不炸、且真的
    重放出 l2_n 行。"""
    from autoresearch.research.menu_replay import replay_l1, replay_l2
    full = _saved_full()
    l1p = replay_l1(full, _all_composite_channels(full), full["composite"], composite_quota=len(full))
    l2p = replay_l2(l1p, l2_n=50, knife_cap_share=0.1, sector_seats=3)
    assert len(l2p) == 50
    assert "selection_reason" in l2p.columns


def test_metrics_degrades_to_none_only_for_the_column_that_is_actually_missing():
    """A1 的输入列 `pct_20d` 缺失 → 该项报 `None`,不得抛异常、不得编一个 0 出来;缺列不该
    连累 A2/A3(它们靠 pct_60d,与 pct_20d 无关)——两者独立降级,不是整表塌成 None。"""
    from autoresearch.research.menu_replay import metrics, replay_l1
    full = _saved_full().drop(columns=["pct_20d"])
    l1p = replay_l1(full, _all_composite_channels(full), full["composite"], composite_quota=len(full))
    out = metrics(full, l1p, l1p.head(20), l1_old=full.head(10), l2_old=full.head(10))
    assert out["A1_spearman_composite_pct20d"] is None
    assert out["A2_top20_knife"] is not None
    assert out["A3_l1_knife_new"] is not None
