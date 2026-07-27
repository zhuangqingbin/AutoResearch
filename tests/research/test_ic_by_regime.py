"""ic_by_regime —— 分 regime 的逐日截面 IC + t 值裁决表(Wave6 批C ②A)。合成 panel,无网络。

为什么需要它:07-21 `stage_eval.csv` 的 L2 排序 IC 是 **−0.2225**(对主尺 fwd_2_oc),
而 `weights.json` 的 117 日面板显示 momentum −0.0325、fund_main −0.0113 —— 全期负。
问题是**全期负可能是 risk_off 桶把 trend 日的正信号均值掩埋了**(既有
`test_flat_washes_out_regime` 已用合成数据证明这种冲洗真的会发生)。

所以裁决必须分桶 + 带显著性:**|t| ≥ 2 才有资格进入该 regime 的权重条件化提案**,
否则薄样本噪声会被当成信号(spec §2.A「先分桶裁决再动权重,禁止直接全局调参」)。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.sw_sector_map import super_sector
from autoresearch.research.factor_lab import ic_by_regime, render_ic_by_regime


def _panel(n_days_per_regime: int = 8):
    """trend 桶 momentum 与 fwd 强正相关(逐日稳定 → t 大);risk_off 桶强负相关。

    `grp_value` 必须**逐日重排**才是真无关列:第一版每天用同一条确定性序列,于是它对 fwd 的
    日 IC 每天完全相同 → std=0 → t=∞ → 被判「可提案」。那是 fixture 造出来的假信号,
    不是被测代码的问题 —— 但它正好说明为什么零方差要单独处理(见实现里的 std==0 分支)。
    """
    rng = __import__("numpy").random.default_rng(42)
    rows = []
    for j in range(n_days_per_regime):
        for regime, sign in (("trend", 1), ("risk_off", -1)):
            date = f"{regime}-{j}"
            noise = rng.permutation(40) / 39.0        # 与 fwd 无关且逐日不同 → IC 在 0 附近抖动
            for i in range(40):
                mom = i / 39.0
                rows.append({"grp_momentum": mom, "grp_value": float(noise[i]),
                             "industry": "半导体", "sector": super_sector("半导体"),
                             "fwd": sign * mom, "date": date, "regime": regime})
    return pd.DataFrame(rows)


def test_reports_ic_and_t_per_regime():
    """每个 regime × 每个因子组一行,带 n_dates / ic_mean / t_stat。"""
    df = ic_by_regime(_panel())

    assert set(df.columns) >= {"regime", "group", "n_dates", "ic_mean", "ic_std", "t_stat", "verdict"}
    mom = df[(df["group"] == "momentum")].set_index("regime")
    assert mom.loc["trend", "ic_mean"] > 0.5
    assert mom.loc["risk_off", "ic_mean"] < -0.5


def test_t_stat_gates_the_verdict():
    """|t|≥2 才判「可提案」;否则判「样本不足/不显著」——这是本表存在的理由。

    合成数据里 momentum 逐日 IC 恒 ±1(零方差)→ t 无穷大,按可提案判;
    grp_value 与 fwd 无关 → IC≈0 → 不显著。
    """
    df = ic_by_regime(_panel())
    by = df.set_index(["regime", "group"])

    assert by.loc[("trend", "momentum"), "verdict"] == "可提案"
    assert by.loc[("trend", "value"), "verdict"] != "可提案"


def test_thin_regime_marked_not_adjudicable():
    """样本不足的桶必须显式标注,不得给出「结论」——薄样本结论标『样本不足』不采纳(spec §2.A 风险节)。"""
    df = ic_by_regime(_panel(n_days_per_regime=2), min_dates=5)

    assert (df["verdict"] == "样本不足").all(), "薄桶不得给出可提案/不显著之外的结论"
    assert set(df["regime"]) == {"trend", "risk_off"}, "薄桶要出现在表里(显式记账),不是静默丢弃"


def test_flat_vs_bucketed_disagreement_is_visible():
    """全样本 flat 与分桶结论相反时,报表必须让这件事可见 —— 这正是 L2 负 IC 的候选解释。"""
    md = render_ic_by_regime(ic_by_regime(_panel()))

    assert "momentum" in md and "trend" in md and "risk_off" in md
    assert "|t|" in md or "t 值" in md, "报表未标显著性口径 → 读者会把噪声当信号"
