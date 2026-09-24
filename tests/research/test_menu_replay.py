"""menu_replay:用已落盘的 score_<group> 列复刻生产 composite(同一段数学),再重放 L1′/L2′ 形状。"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.scoring import _PRIOR_WEIGHTS, composite_score, falling_knife_mask
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


def test_replay_l2_forwards_knife_cap_and_sector_seats_and_they_take_effect():
    """2026-09-24 §2.2/§2.3(Task 16):`replay_l2` 现在真的把 `knife_cap_share`/`sector_seats`
    透传进 `select_l2` —— 取代同名旧锁 `test_replay_l2_does_not_forward_unwired_params_to_
    select_l2`(那条测的是"绝不透传";`select_l2` 当时还没有 `knife_cap_share` 形参)。本测试
    锁反方向的不变量:传了就必须生效,不是仍旧被吞掉。

    `knife_cap_share=0.0` → merit/backfill(非豁免桶)一只落刀都不许进,L2′ 落刀占比必须砸到
    0,且必须真发生过至少一次顶替(`knife_cap_swap`)—— 证明这个 seed 下样本里本就有落刀
    候选会被跳过,不是巧合地一个都没有(帽不是摆设)。

    `sector_seats` 在 `full` 参数缺省时退回 `l1p` 自己找行(brief 里的姊妹测试
    `test_replay_l2_applies_knife_cap_and_sector_seats` 总是显式给 `full`,这里刻意不给,
    覆盖 `full if full is not None else l1p` 那条默认分支):座位码选一个已在 `l1p` 里、原本会
    正常参与竞争的普通行,断言它被摘出竞争池、全程直通拼回末尾(`sector_seat=True`、
    `l2_lane_reserved=True`),总行数 = l2_n + 座位数。"""
    from autoresearch.research.menu_replay import replay_l1, replay_l2
    full = _saved_full()
    l1p = replay_l1(full, _all_composite_channels(full), full["composite"], composite_quota=len(full))

    l2_capped = replay_l2(l1p, l2_n=50, knife_cap_share=0.0, sector_seats=None)
    assert len(l2_capped) == 50
    assert "selection_reason" in l2_capped.columns
    assert not falling_knife_mask(l2_capped).fillna(False).any()
    assert l2_capped["knife_cap_swap"].astype(bool).sum() > 0

    seat_code = str(l1p["code"].iloc[200])
    l2_seated = replay_l2(l1p, l2_n=50, sector_seats=[{"code": seat_code, "industry": "电力"}])
    assert len(l2_seated) == 51
    seat_rows = l2_seated[l2_seated["sector_seat"].astype(bool)]
    assert list(seat_rows["code"]) == [seat_code]
    assert bool(seat_rows["l2_lane_reserved"].iloc[0])


def test_replay_l2_applies_knife_cap_and_sector_seats():
    """Task 16 brief §Step 1(追加):`sector_seats` 给了 `full` 时,座位码即便不在 `l1p` 里也能
    从 `full` 取到真实行补进来(镜像 `_inject_pinned_l1` 的"打标 / 从 scored 现取"两分支)。"""
    from autoresearch.research.menu_replay import recompute_composite, replay_l1, replay_l2
    full = _saved_full()
    channels = pd.DataFrame({"channel": "value", "code": full["code"].iloc[:50],
                             "channel_rank": range(1, 51), "channel_score": 1.0})
    l1p = replay_l1(full, channels, recompute_composite(full, _PRIOR_WEIGHTS), composite_quota=100)
    seats = [{"code": full["code"].iloc[-1], "industry": "电力"}]
    l2p = replay_l2(l1p, l2_n=40, knife_cap_share=0.05, sector_seats=seats, full=full)
    assert "sector_seat" in l2p.columns and l2p["sector_seat"].astype(bool).sum() == 1
    assert len(l2p) == 41


def test_a7b_margin_reads_uncapped_merit_core_not_post_cap_share():
    """2026-09-25 addendum §1(P39):A7b 余量必须是 `L0_knife − 帽关掉时 merit 核的落刀占比`,
    不能偷懒读 `L0_knife − A4_l2_knife_new`(帽生效**之后**的 L2 占比)——后者在帽真正咬下去
    的时候恰好被压到 0,余量因此会在最该报警的时刻读得最健康,与这道门的用途相反。

    用既有的强制 `knife_cap_share=0.0` 手法(逼真落刀行全被顶替,`A4_l2_knife_new` 必为 0)
    构造 A4/A7b 的分歧:`floors=None`(DEFAULT_FLOORS,与生产同源)+ `l2_n=200` 让 merit 核
    (99 个名额)与 floor/回填(101 个名额)都非空,证明本测试确实过滤到了 `selection_reason
    =="merit"`,不是偷懒读了整个 L2′。"""
    from autoresearch.research.menu_replay import (
        merit_core_knife_share,
        metrics,
        replay_l1,
        replay_l2,
    )
    full = _saved_full()
    l1p = replay_l1(full, _all_composite_channels(full), full["composite"], composite_quota=len(full))

    l2_capped = replay_l2(l1p, l2_n=200, knife_cap_share=0.0, sector_seats=None)
    l2_uncapped = replay_l2(l1p, l2_n=200, knife_cap_share=None, sector_seats=None)

    # 前提核验:这份合成宇宙里 merit 核不是全部,也不是空的(混进 floor/回填的行会读到
    # 另一个数,这两个断言防的就是那种悄悄偷懒)。
    assert (l2_uncapped["selection_reason"] == "merit").sum() not in (0, len(l2_uncapped))

    merit_uncapped = merit_core_knife_share(l2_uncapped)
    out = metrics(full, l1p, l2_capped, l1_old=full.head(10), l2_old=full.head(10),
                 merit_core_knife_uncapped=merit_uncapped)

    assert out["A4_l2_knife_new"] == 0.0                    # 帽=0.0 把 L2 落刀砸到 0(既有断言同款手法)
    assert merit_uncapped is not None and merit_uncapped > 0  # merit 核天然有落刀,不是巧合地一个都没有
    assert out["A7b_merit_core_knife_uncapped"] == merit_uncapped
    # 核心断言:A7b 的分子不等于 A4(帽后占比)——一个悄悄等于 A4_l2_knife_new 的实现会
    # 让这一行失败,那正是 P39 明令禁止的偷懒写法。
    assert out["A7b_merit_core_knife_uncapped"] != out["A4_l2_knife_new"]
    assert out["A7b_margin"] == round(out["L0_knife"] - merit_uncapped, 4)


def test_merit_core_knife_share_ignores_non_merit_rows():
    """`merit_core_knife_share` 必须只读 `selection_reason=="merit"`——喂它整个 L2′(含
    floor/回填/席位)会答错问题。构造一份 merit 核 100% 干净(0 落刀)、但 backfill 桶里塞满
    落刀行的 L2′:混读会算出 >0,只读 merit 必须算出 0。"""
    from autoresearch.research.menu_replay import merit_core_knife_share
    l2 = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(6)],
        "pct_60d": [10.0, 20.0, 30.0, -40.0, -50.0, -60.0],           # 后三行是落刀
        "selection_reason": ["merit", "merit", "merit", "backfill", "backfill", "backfill"],
    })
    assert merit_core_knife_share(l2) == 0.0                          # 混着喂全表:仍只算 merit 三行,answer=0
    # 对照:只喂 backfill(没有一行 merit)→ 函数按定义读不到 merit 行,必须降级 None,
    # 不能把这三行落刀(pct_60d 全 <−20)错当成"merit 核 100% 落刀"读出来。
    assert merit_core_knife_share(l2[l2["selection_reason"] == "backfill"]) is None


def test_merit_core_knife_share_degrades_to_none_without_signal():
    """缺 `selection_reason` 列、或列在但没有一行是 merit(如 floors 配置吃掉了全部
    merit_need)→ `None`,不得编 0(0 是"核过、真干净",不是"没东西可核")。"""
    from autoresearch.research.menu_replay import merit_core_knife_share
    assert merit_core_knife_share(pd.DataFrame({"pct_60d": [-30.0]})) is None
    assert merit_core_knife_share(pd.DataFrame({"pct_60d": [-30.0], "selection_reason": ["backfill"]})) is None


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
