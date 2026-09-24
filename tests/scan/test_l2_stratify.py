"""L2 分层多样性采样器(l2_stratify)单测。design: 2026-06-25-l2-stratified-sampler。

覆盖:floor 保底 / sector_cap / 多 channel 归桶 / sector-neutral 排序 / 回落(无分层)/ 行数。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.scan.recall.l2_stratify import (
    DEFAULT_FLOORS,
    sector_neutral,
    select_l2,
    stratified_l2,
)


def _universe(n=600, seed=7):
    rng = np.random.default_rng(seed)
    # 桶存在即需覆盖(纵使生产默认关):healthy=2026-07-03 第10路、event=Wave4 第11路、
    # lowturn=2026-08-22 批 A 新路(独立「低位转强」桶 floor 8)。
    chans = ["composite", "momentum", "reversal", "value", "growth", "accumulation",
             "main_fund", "heat", "healthy", "event", "lowturn"]
    inds = rng.choice(["半导体", "白酒", "医药", "电力", "煤炭", "汽车"], n)
    # 每只随机命中 1–3 路(composite 保底)
    rc = []
    for _ in range(n):
        k = rng.integers(1, 4)
        picks = set(rng.choice(chans, k)) | {"composite"}
        rc.append("|".join(sorted(picks)))
    return pd.DataFrame({
        "code": [f"{600000+i:06d}" for i in range(n)],
        "industry": inds,
        "composite": rng.uniform(0, 100, n),
        "recall_channels": rc,
    })


def test_returns_exactly_l2n():
    out = stratified_l2(_universe(600), l2_n=200)
    assert len(out) == 200
    assert out["code"].is_unique


def test_small_universe_returns_all():
    out = stratified_l2(_universe(120), l2_n=200)
    assert len(out) == 120


def test_floors_guaranteed_per_style():
    """每个风格桶在最终 200 中 ≥ floor(成员充足时)。"""
    from autoresearch.scan.recall.l2_stratify import STYLE_CHANNELS
    df = _universe(800)
    out = select_l2(df, 200)[0]
    sets = out["recall_channels"].fillna("").map(lambda s: set(str(s).split("|")))
    for st, chs in STYLE_CHANNELS.items():
        have = sets.map(lambda cs, c=set(chs): bool(cs & c)).sum()
        assert have >= DEFAULT_FLOORS[st], f"{st} 仅 {have} < floor {DEFAULT_FLOORS[st]}"


def test_disabled_channel_buckets_have_zero_floor():
    """长效契约(Wave4 Review Round 1 C-1):**通道没启用 → 它的 L2 桶 floor 必须为 0**。

    为什么:`stratified_l2` 先算 `merit_need = l2_n − Σfloor`。桶恒无成员时 floor 补位循环
    确实静默跳过,但 `merit_need` 已经先被减掉 —— 那批名额改从 floor/回填进场,于是被打上
    `l2_lane_reserved=True`(「配额救回,非有机进场」)。该标签有三个真消费者:
    `agents/l3_select._row_lane`(渲染进喂 l3-rank 的表,reserved 行被搬进 `lane:floor` 块
    并殿后)、`agents/l4_card.force_full_card`(决定满卡 vs 早停)、
    `learning/retro.floor_experiment`(floor vs merit vs cut 的闭环账本分组)。
    真数据实测:事件桶 floor=10 时,29 个扫描日**每天恰好 10 行** `l2_lane_reserved` 翻转、
    24–61 行 `l2_rank` 变位 —— 一个挂着「默认不启用」牌子的通道天天在改生产 LLM 输入。

    豁免 `吸筹`:accumulation 于 2026-07-11 经 channel_audit 累计裁决退役(unique 超额 T2
    −0.21%),但它的桶(floor=12)未同步撤 —— **既有欠账,本轮只记账不修**(改它会动今天的
    L2 输出 = 另一次 parity 破坏,须独立立项 + 29 日对拍)。给它显式豁免,防新欠账再混进来。
    """
    import json
    import re
    from pathlib import Path

    from autoresearch.scan.recall.l2_stratify import STYLE_CHANNELS
    raw = Path(".claude/skills/scan-market/scan_config.jsonc").read_text(encoding="utf-8")
    enabled = set(json.loads(re.sub(r"//.*", "", raw)).get("funnel", {}).get("recall_channels") or [])
    assert enabled, "scan_config.funnel.recall_channels 读不到 = 本测试失去意义"
    legacy_debt = {"吸筹"}                       # accumulation 2026-07-11 退役未撤桶(只记账)
    for style, chans in STYLE_CHANNELS.items():
        if style in legacy_debt or (set(chans) & enabled):
            continue
        assert DEFAULT_FLOORS.get(style, 0) == 0, (
            f"通道 {chans} 未在 scan_config.funnel.recall_channels 里启用,但桶「{style}」"
            f"floor={DEFAULT_FLOORS.get(style)} > 0 —— 会抬高 Σfloor、压低 merit_need,"
            f"每天静默改 l2_lane_reserved(l3 表/满卡门/retro 账本三处消费)"
        )


def test_sector_cap_enforced():
    out = stratified_l2(_universe(800), l2_n=200, sector_cap_frac=0.20)
    top = out["industry"].value_counts().iloc[0]
    assert top <= int(np.floor(0.20 * 200)), f"最大行业 {top} 破 cap 40"


def test_no_floors_is_sn_top():
    """floors={} + cap 关 → 纯 sector-neutral composite top-N(确定性)。"""
    df = _universe(500)
    out = stratified_l2(df, l2_n=200, floors={}, sector_cap_frac=1.0)
    sn = sector_neutral(df["composite"], df["industry"])
    want = set(df.assign(_s=sn.to_numpy()).nlargest(200, "_s")["code"].str.zfill(6))
    assert set(out["code"]) == want
    assert not out["l2_lane_reserved"].any()      # 无分层 → 无 reserved


def test_lane_reserved_flag_present():
    out = select_l2(_universe(800), 200)[0]
    assert out["l2_lane_reserved"].sum() > 0       # floor 救回若干
    assert "l2_rank" in out.columns and out["l2_rank"].tolist() == list(range(1, 201))


def test_sector_neutral_demeans_within_industry():
    df = _universe(300)
    sn = sector_neutral(df["composite"], df["industry"])
    # 每个行业组内 sn 均值 ≈ 0
    g = pd.DataFrame({"sn": sn.to_numpy(), "ind": df["industry"].to_numpy()}).groupby("ind")["sn"].mean()
    assert g.abs().max() < 1e-9


def test_reversal_confirm_feeds_reversal_bucket():
    """重开 reversal_confirm(2026-08-21)后它的独有召回必须入「反转」桶,否则零 floor 保护——
    侦察实测的真缺口(design §5.2)。"""
    from autoresearch.scan.recall.l2_stratify import STYLE_CHANNELS
    assert set(STYLE_CHANNELS["反转"]) == {"reversal", "reversal_confirm"}


def _universe_with_knives(n=600, seed=11):
    rng = np.random.default_rng(seed)
    df = _universe(n, seed)
    df["pct_60d"] = rng.uniform(-60, 60, n)            # 约一半落刀
    return df


def test_knife_cap_none_is_parity():
    df = _universe_with_knives()
    a = stratified_l2(df, l2_n=200)
    b = stratified_l2(df, l2_n=200, knife_cap_share=None)
    pd.testing.assert_frame_equal(a, b)
    # fix round 1 (2026-09-25, controller review #1):knife_cap_swap is a new column added
    # alongside the cap machinery — under knife_cap_share=None it must be present and all-False,
    # not just "equal to itself" (assert_frame_equal above proves the no-arg call and the
    # explicit-None call agree with each other, not that the column's value is correct).
    assert "knife_cap_swap" in b.columns
    assert not b["knife_cap_swap"].any()


def test_knife_cap_limits_merit_and_backfill_but_exempts_reversal_buckets():
    from autoresearch.scan.recall.l2_stratify import KNIFE_CAP_EXEMPT_STYLES
    df = _universe_with_knives()
    out = stratified_l2(df, l2_n=200, knife_cap_share=0.10)
    knife = out["pct_60d"] < -20
    merit = out["selection_reason"].isin(["merit", "backfill"])
    assert knife[merit].mean() <= 0.10 + 1 / merit.sum()          # 配额取整误差
    lane_rows = out[(out["selection_reason"] == "lane") & ~out["selection_detail"].isin(KNIFE_CAP_EXEMPT_STYLES)]
    if len(lane_rows):
        assert (lane_rows["pct_60d"] < -20).mean() <= 0.10 + 1 / len(lane_rows)
    assert (out["selection_detail"] == "knife_cap").sum() > 0     # 会变的量:真有非落刀行顶上来
    assert len(out) == 200 and out["code"].is_unique


def test_knife_cap_share_one_is_no_op():
    df = _universe_with_knives()
    a = stratified_l2(df, l2_n=200)
    b = stratified_l2(df, l2_n=200, knife_cap_share=1.0)
    assert set(a["code"]) == set(b["code"])


def test_knife_cap_lane_step_swap_is_marked():
    """Controller review #1 (2026-09-25): `_detail_for` was only ever called for merit/backfill,
    so a skipped-and-replaced falling knife inside a non-exempt FLOOR bucket (the `lane` step)
    left no trace anywhere — `owed["lane"]` was incremented and never read. `menu_replay`'s A7
    metric counted `selection_detail == "knife_cap"`, which floor-bucket replacement rows never
    carry (their detail is, and must stay, the bucket name — `edge_census.py` groups lane rows
    by that name). Fix: a dedicated `knife_cap_swap` boolean column, set True on the replacing
    row in all three steps (merit/lane/backfill), independent of what `selection_detail` says.

    This test deliberately constructs a frame where the "价值" (non-exempt) floor bucket can
    only be satisfied by falling-knife rows unless the cap forces a swap:
    - 300 filler rows (shared industries with the value rows, so sector-neutral demeaning pins
      the value rows' rank far below the merit cutoff — verified empirically, not just reasoned)
      fill the merit core and leave "价值" untouched by it (`have == 0` when the floor step
      reaches "价值").
    - 12 "价值" rows that are falling knives (pct_60d well below -20) — with `knife_cap_share=0.0`
      (strictest possible cap, quota=0), every one of them is skipped by the lane step.
    - 12 more "价值" rows that are NOT falling knives, ranked just below the 12 knives — these are
      the only remaining "价值"-tagged candidates, so the floor step (which must still fill its
      need=12) can only complete by admitting exactly these 12 replacements.
    """
    rows = []
    for i in range(300):
        rows.append({"code": f"9{i:05d}", "industry": f"ind{i % 15}", "composite": 90 - i * 0.05,
                     "recall_channels": "composite|momentum", "pct_60d": 5.0})
    for i in range(12):                                # falling knives the cap must skip
        rows.append({"code": f"3{i:05d}", "industry": f"ind{i % 15}", "composite": 2.0,
                     "recall_channels": "value", "pct_60d": -30.0})
    for i in range(12):                                # the only rows left to replace them
        rows.append({"code": f"4{i:05d}", "industry": f"ind{i % 15}", "composite": 1.0,
                     "recall_channels": "value", "pct_60d": 5.0})
    df = pd.DataFrame(rows)

    out = stratified_l2(df, l2_n=200, knife_cap_share=0.0)
    assert len(out) == 200 and out["code"].is_unique          # headcount still filled, not shrunk

    val = out[out["recall_channels"] == "value"]
    assert len(val) == 12                                      # floor not shrunk either
    assert (val["pct_60d"] >= -20).all()                       # every knife candidate got swapped out
    assert val["selection_reason"].eq("lane").all()
    assert val["selection_detail"].eq("价值").all()             # detail still the bucket name (not overwritten)
    assert val["knife_cap_swap"].all()                          # the replacing rows ARE now marked


def test_knife_cap_exempts_reversal_lane_rows_from_the_cap():
    """2026-09-24 addendum §1/§6/§7(task-23 dispatch,P39 sibling finding):probe ② on the
    existing `test_knife_cap_limits_merit_and_backfill_but_exempts_reversal_buckets` confirms
    the addendum's prediction — emptying `KNIFE_CAP_EXEMPT_STYLES` leaves that test **green**,
    because its `lane_rows` population is filtered by the very same set the production code
    reads, so mutating the set moves the assertion's population and the capped behaviour in
    the same direction and hides the change.

    Tracing further than the addendum anticipated: with that test's `_universe_with_knives()`
    fixture (`floors=None` → `DEFAULT_FLOORS`), the 反转/低位转强 floors are always satisfied
    "for free" by rows that also rank into merit/backfill on raw composite — the exempt LANE
    rows a positive assertion would need to read are empirically **zero** there (verified by
    hand: that test's `selection_detail` value_counts for the lane step is `{"成长": 2}` only,
    no 反转/低位转强 row ever takes the lane path). No assertion phrased against that fixture
    can exercise the exemption at all.

    This test hand-builds a frame instead (same recipe as `test_knife_cap_lane_step_swap_is_
    marked` above, mirrored onto an EXEMPT style) where the "反转" floor can only be reached
    through the lane step, with 12 falling-knife candidates ranked ABOVE 12 non-knife
    replacements — so "exempt" and "not exempt" are forced to different, checkable outcomes:
    exempt → the 12 knives win the floor, none swapped; not-exempt → `knife_cap_share=0.0`
    forces all 12 out and the 12 non-knife replacements win instead. Manually confirmed this
    goes red with `KNIFE_CAP_EXEMPT_STYLES` emptied (both assertions below flip: `pct_60d`
    stops being < −20 and `knife_cap_swap` stops being all-False) — the positive assertion the
    addendum asked for, and the fixture the existing test's own data could not support.
    """
    rows = []
    for i in range(300):                              # fills the merit core; none tagged 反转
        rows.append({"code": f"9{i:05d}", "industry": f"ind{i % 15}", "composite": 90 - i * 0.05,
                     "recall_channels": "composite|momentum", "pct_60d": 5.0})
    for i in range(12):                                # 反转 falling knives — must win the floor if exempt
        rows.append({"code": f"5{i:05d}", "industry": f"ind{i % 15}", "composite": 2.0,
                     "recall_channels": "reversal", "pct_60d": -30.0})
    for i in range(12):                                # 反转 non-knives, ranked just below — would only
        rows.append({"code": f"6{i:05d}", "industry": f"ind{i % 15}", "composite": 1.0,   # win if NOT exempt
                     "recall_channels": "reversal", "pct_60d": 5.0})
    df = pd.DataFrame(rows)

    out = stratified_l2(df, l2_n=200, knife_cap_share=0.0)      # strictest possible cap (quota=0)
    assert len(out) == 200 and out["code"].is_unique

    val = out[out["recall_channels"] == "reversal"]
    assert len(val) == 12                                        # floor met either way
    assert val["selection_reason"].eq("lane").all()
    assert val["selection_detail"].eq("反转").all()
    # The positive assertion the addendum asked for: an exempt style keeps its falling knives —
    # the cap never touches them, so nothing here is a "swap". Empty `KNIFE_CAP_EXEMPT_STYLES`
    # and both lines below flip (non-knife replacements win instead, all marked swapped).
    assert (val["pct_60d"] < -20).all()
    assert not val["knife_cap_swap"].any()
