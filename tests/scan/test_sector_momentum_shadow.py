"""EXP-2 `recall_sector_momentum` 影子通道契约(Wave12-T20)。

预注册于 2026-08-01(`exp_20260801_recall_sector_momentum`),但**数据腿从未实现**:
`registered_channels()` 里根本没有 `sector_momentum`,`observations` 至今 `[]`。这是本仓库
的 FN-1 家族(消费者在等一个没人生产的产物)。本文件锁住接线后的两件事:

1. **通道语义**:按**板块动量**(申万口径行业的 `pct_60d` 中位,与 `l2_stratify` 的
   `sector_mom` 同一事实源)排上涨侧 top-k;**绝不用当日个股涨幅**(`pct_1d`)——
   2026-07-24 实证「追当日大涨是负价值」(旧尺 `fwd_2_oc` 口径超额 −3.67pp,t=−11.91;
   此处仅作**历史读数**引用,不是现行主尺读数)。
2. **零副作用五点自查**(「默认不启用必须连副作用一起不启用」,Wave4 事件桶 floor=10 判例):
   ① 不进 `scan_config.recall_channels`(生产启用路数不变)
   ② 不碰 `DEFAULT_FLOORS`(Σ 不变)③ 不碰 `merit_need` ④ 不产 lane 标签
   ⑤ 不写生产 finalists / 不改主 `L1_channels.csv`、`L1_recall_top1000.csv`。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from autoresearch.scan.recall import CHANNEL_DEFAULTS, build, registered_channels
from autoresearch.scan.recall.l2_stratify import (
    DEFAULT_FLOORS,
    STYLE_CHANNELS,
    _style_masks,
    stratified_l2,
)

_REPO = Path(__file__).resolve().parents[2]
_SCAN_CONFIG = _REPO / ".claude" / "skills" / "scan-market" / "scan_config.jsonc"

CHANNEL = "sector_momentum"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """`write_shadow_variants` 的 `capfloor20` 是唯一重取数变体(真调 `build_market_frame`
    → tushare)。首轮 RED 实测它把本文件跑到 350s 并真的联网了(「测试禁挂真网」)。
    这里把取数入口打成必抛 —— 生产代码本来就 try/except 兜底它,其余零成本变体不受影响。
    """
    def _boom(*_a, **_k):
        raise AssertionError("测试不许联网取 universe(capfloor20 变体)")

    monkeypatch.setattr("autoresearch.scan.universe.build_market_frame", _boom)


def _frame(n: int = 8) -> pd.DataFrame:
    """两个行业 × 四只票。**强行业 = 弱个股当日涨幅**,让两种排序给出相反答案。

    电子:pct_60d = [30, 26, 22, 18] → 中位 24(强板块),但 pct_1d 全部 ≤ 0.4;
    银行:pct_60d = [-8, -9, -10, -11] → 中位 −9.5(弱板块),pct_1d 里有 9.8 / 9.5 两只
    当日大涨。按板块动量排 → 电子四只在前;按当日涨幅排 → 银行两只在前。
    """
    return pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004",
                 "600001", "600002", "600003", "600004"],
        "industry": ["电子"] * 4 + ["银行"] * 4,
        "composite": [50.0, 49.0, 48.0, 47.0, 60.0, 59.0, 58.0, 57.0],
        "pct_60d": [30.0, 26.0, 22.0, 18.0, -8.0, -9.0, -10.0, -11.0],
        "pct_1d": [0.4, 0.1, -0.2, 0.3, 9.8, 9.5, -1.0, 0.2],
    })[:n]


# ── 注册契约 ────────────────────────────────────────────────────────────────


def test_sector_momentum_channel_is_registered():
    assert CHANNEL in registered_channels()


def test_sector_momentum_floor_is_zero():
    """floor=0 = 「不占 quota」的实现:`quota_union` 只把每路 top-floor 无条件保护,
    floor=0 ⇒ 本路一票都不受保护、挤不掉任何生产票(即便哪天被误启用)。"""
    assert CHANNEL_DEFAULTS[CHANNEL].floor == 0


def test_sector_momentum_quota_is_a_real_k_not_zero():
    """quota 是**每路 top-k 截断**(`recall_select` 直接把它当 `k` 喂 `build(n)(...)`)。
    写 0 → `gate_rank(...).head(0)` → 恒空长表 → 仪器天天产出「什么都没有」= 正是本 task
    要修的 FN-1 病(预注册了、观测永远是 0)。故 quota 必须 > 0;隔离靠 floor=0 + 不进
    生产启用列表,不靠把 k 掐成 0。"""
    assert CHANNEL_DEFAULTS[CHANNEL].quota > 0


# ── 通道语义:板块动量,不是当日涨幅 ────────────────────────────────────────


def test_ranks_by_sector_momentum_not_daily_move():
    out = build(CHANNEL)(_frame(), "2026-08-05", 4)
    assert list(out["code"]) == ["000001", "000002", "000003", "000004"], (
        "必须按板块动量(行业 pct_60d 中位)排上涨侧;若退化成按当日个股涨幅 pct_1d 排,"
        "银行的 600001/600002(当日 +9.8/+9.5)会排到最前——那正是 2026-07-24 被实证为"
        "负价值的做法")


def test_downside_sectors_are_excluded_entirely():
    """上涨侧 only:板块动量 ≤ 0 的行业整体不召回(k 给够也不该补满)。"""
    out = build(CHANNEL)(_frame(), "2026-08-05", 8)
    assert set(out["code"]) == {"000001", "000002", "000003", "000004"}


def test_within_sector_order_is_deterministic_and_not_pct_1d():
    """同一板块内 4 只票板块动量完全相同 —— 并列层必须由确定性第二键排开,且第二键
    不得是 `pct_1d`(否则「板块动量选行业、当日涨幅选个股」= 铁律从后门溜回来)。"""
    frame = _frame(4).copy()
    frame["pct_1d"] = [-5.0, -4.0, 9.9, -3.0]     # 000003 当日大涨
    out = build(CHANNEL)(frame, "2026-08-05", 2)
    assert "000003" not in list(out["code"]), "并列层第二键不得是当日涨幅"


def test_missing_columns_degrade_to_empty_frame():
    for drop in ("industry", "pct_60d"):
        frame = _frame().drop(columns=[drop])
        out = build(CHANNEL)(frame, "2026-08-05", 5)
        assert list(out.columns) == ["code", "channel_rank", "channel_score"]
        assert len(out) == 0, f"缺 {drop} → 空帧降级(与其余 12 路同契约)"


# ── 零副作用五点自查 ────────────────────────────────────────────────────────


def test_side_effect_1_not_in_production_recall_channels():
    """① 生产启用路数不变(读真配置文件,不读被 conftest 隔离掉的默认路径)。"""
    from autoresearch.scan.user_config import load_user_config
    cfg = load_user_config(_SCAN_CONFIG)
    enabled = (cfg.get("funnel") or {}).get("recall_channels") or []
    assert CHANNEL not in enabled
    assert len(enabled) == 8, f"生产启用路数应为 8,实为 {len(enabled)}: {enabled}"


def test_side_effect_2_default_floors_untouched():
    """② `DEFAULT_FLOORS` 的键集与 Σ 都不变 —— Wave4 判例:加一个 floor=10 的桶会把
    `merit_need` 从 107 变 97,每天恰好多 10 行 `l2_lane_reserved`(三个真消费者)。"""
    assert set(DEFAULT_FLOORS) == {"趋势", "健康", "反转", "价值", "成长", "吸筹", "主力", "事件"}
    assert sum(DEFAULT_FLOORS.values()) == 93


def test_side_effect_3_merit_need_unchanged():
    """③ `merit_need = l2_n − Σfloor` 是 Σfloor 的函数 —— 上一条锁 Σ,这条锁它真没被
    别的路径改掉:200 − 93 = 107(2026-07-11 事故里那个 107)。"""
    assert 200 - sum(DEFAULT_FLOORS.values()) == 107


def test_side_effect_4_no_lane_label_produced():
    """④ 不产 lane 标签:`STYLE_CHANNELS` 没有映射到本路的桶 ⇒ `_style_masks` 对
    `recall_channels="sector_momentum"` 的行全 False ⇒ 它永远不会因本路被打 `lane`。"""
    assert all(CHANNEL not in names for names in STYLE_CHANNELS.values())
    masks = _style_masks(pd.Series([CHANNEL, f"momentum|{CHANNEL}", ""]))
    assert not masks["趋势"].iloc[0] and not masks["健康"].iloc[0]
    assert masks["趋势"].iloc[1], "momentum 本身该照旧命中趋势桶(本路不该改变别人的语义)"


def test_side_effect_4b_stratified_l2_never_labels_sector_momentum_row_as_lane():
    """④ 活体版:一帧里全是只被 sector_momentum 召回的票 → 分层采样后没有任何一行的
    `selection_reason` 是 `lane`(它们只能凭 merit / backfill 进)。"""
    n = 40
    frame = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "industry": ["电子"] * n,
        "composite": [float(n - i) for i in range(n)],
        "recall_channels": [CHANNEL] * n,
    })
    out = stratified_l2(frame, l2_n=10, sector_cap_frac=1.0)
    assert CHANNEL not in STYLE_CHANNELS.get("趋势", ())
    assert "lane" not in set(out["selection_reason"])


def test_side_effect_5_shadow_variant_writes_only_shadow_long_table(tmp_path):
    """⑤ 影子变体只落 `shadow/L1_channels_plus_sectormom.csv`;生产 `L1_channels.csv` /
    `L1_recall_top1000.csv` 由 `universe.run` 写,`write_shadow_variants` 一个字节都不碰。"""
    from autoresearch.scan.universe import write_shadow_variants

    n = 60
    scored = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "name": [f"T{i}" for i in range(n)],
        "industry": (["电子"] * 30) + (["银行"] * 30),
        "composite": [float(n - i) for i in range(n)],
        "pct_60d": ([20.0] * 30) + ([-20.0] * 30),
        "pct_1d": [0.1] * n,
    })
    recall = scored.copy()
    recall["recall_channels"] = "composite"
    recall["n_channels"] = 1
    recall["best_rank"] = range(1, n + 1)

    outdir = tmp_path / "day"
    outdir.mkdir()
    # 生产文件先放两个哨兵,跑完必须逐字节不变
    (outdir / "L1_channels.csv").write_text("SENTINEL-A", encoding="utf-8")
    (outdir / "L1_recall_top1000.csv").write_text("SENTINEL-B", encoding="utf-8")

    names = write_shadow_variants(
        outdir, scored, recall, "2026-08-05", recall_n=50, l2_n=20,
        l2_floors=None, l2_sector_cap=1.0,
        l2_cols=["l2_rank", "code", "name", "industry", "composite"],
        recall_mode="multi", recall_channels=["composite"],
    )

    assert "plus_sectormom" in names
    long_table = outdir / "shadow" / "L1_channels_plus_sectormom.csv"
    assert long_table.exists(), "影子长表必须落盘,否则 channel_audit --variant 无从裁决"
    got = pd.read_csv(long_table, dtype={"code": str})
    assert set(got.columns) == {"channel", "code", "channel_rank", "channel_score"}
    assert CHANNEL in set(got["channel"]), "长表里必须真有本路的行(空表 = 仪器没接线)"

    assert (outdir / "L1_channels.csv").read_text(encoding="utf-8") == "SENTINEL-A"
    assert (outdir / "L1_recall_top1000.csv").read_text(encoding="utf-8") == "SENTINEL-B"
    assert not (outdir / "finalists.csv").exists()


def test_shadow_variant_is_a_superset_of_the_day_enabled_lanes(tmp_path):
    """反事实必须是「当日实际启用路 + 本路」,不是「全部注册路」——Wave4 在 pre_healthy
    上踩过这个坑(混进当日根本没跑的停用路,反事实失真)。"""
    from autoresearch.scan.universe import write_shadow_variants

    n = 40
    scored = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "industry": ["电子"] * n,
        "composite": [float(n - i) for i in range(n)],
        "pct_60d": [10.0] * n,
        "pct_1d": [0.1] * n,
        "amount_yi": [3.0] * n,
        "turnover": [2.0] * n,
        "vol_ratio": [1.2] * n,
    })
    recall = scored.copy()
    recall["recall_channels"] = "composite"
    recall["n_channels"] = 1
    recall["best_rank"] = range(1, n + 1)
    outdir = tmp_path / "day"
    outdir.mkdir()
    write_shadow_variants(outdir, scored, recall, "2026-08-05", recall_n=30, l2_n=10,
                          l2_floors=None, l2_sector_cap=1.0,
                          l2_cols=["l2_rank", "code", "composite"],
                          recall_mode="multi", recall_channels=["composite", "heat"])
    got = pd.read_csv(outdir / "shadow" / "L1_channels_plus_sectormom.csv", dtype={"code": str})
    assert set(got["channel"]) == {"composite", "heat", CHANNEL}


@pytest.mark.parametrize("variant", ["plus_event", "plus_sectormom"])
def test_two_shadow_variants_do_not_clobber_each_other(tmp_path, variant):
    """两条影子路各自独立落盘,互不覆盖(`shared_instrument` 但 `不得混样本`)。"""
    from autoresearch.scan.universe import write_shadow_variants

    n = 40
    scored = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "industry": ["电子"] * n,
        "composite": [float(n - i) for i in range(n)],
        "pct_60d": [10.0] * n, "pct_1d": [0.1] * n,
        "ev_pos": [1.0] * n, "ev_hard": [1.0] * n,
    })
    recall = scored.copy()
    recall["recall_channels"] = "composite"
    recall["n_channels"] = 1
    recall["best_rank"] = range(1, n + 1)
    outdir = tmp_path / "day"
    outdir.mkdir()
    write_shadow_variants(outdir, scored, recall, "2026-08-05", recall_n=30, l2_n=10,
                          l2_floors=None, l2_sector_cap=1.0,
                          l2_cols=["l2_rank", "code", "composite"],
                          recall_mode="multi", recall_channels=["composite"])
    path = outdir / "shadow" / f"L1_channels_{variant}.csv"
    assert path.exists()
    got = pd.read_csv(path, dtype={"code": str})
    other = {"plus_event": CHANNEL, "plus_sectormom": "event"}[variant]
    assert other not in set(got["channel"]), "两个变体的样本不得互相污染"
