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
    chans = ["composite", "momentum", "reversal", "value", "growth", "accumulation", "main_fund", "heat", "healthy", "event"]  # healthy: 2026-07-03 第10路; event: Wave4 第11路(桶存在即需覆盖,纵使生产默认关)
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
