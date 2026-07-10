"""per-channel 前向归因 channel_edge + evaluate L1 段 + ratings 兜底。NO network(合成)。"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.stage_eval import channel_edge


def _recall():
    # 5 只,带 recall_channels provenance(| 分隔)
    return pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004", "000005"],
        "recall_channels": ["composite|heat", "heat", "composite", "composite|momentum", "heat"],
        "n_channels": [2, 1, 1, 2, 1],
    })


def _realized():
    # 全市场(含 2 只未召回 000006/000007 以定全市场中位);000002 不可买
    # fwd_2_oc 数值刻意与 fwd_5_oc 不同(证明 t2 是独立计算,非别名)。
    return pd.DataFrame({
        "code": ["000001", "000002", "000003", "000004", "000005", "000006", "000007"],
        "fwd_1_oo": [0.05, 0.03, -0.02, 0.01, 0.04, 0.00, -0.01],
        "fwd_2_oc": [0.04, 0.02, -0.01, 0.03, 0.06, 0.01, -0.02],
        "fwd_5_oc": [0.10, 0.06, -0.04, 0.02, 0.08, 0.00, -0.02],
        "buyable": [True, False, True, True, True, True, True],
    })


def test_channel_edge_unique_membership_buyable_excess():
    ce = channel_edge(_recall(), _realized())
    assert list(ce.columns) == ["channel", "n_recalled", "n_unique", "n_unbuyable",
                                "mean_excess_t2", "unique_excess_t2", "hit_rate_t2",
                                "mean_excess_t5", "unique_excess_t5", "mean_excess_t1", "hit_rate_t5"]
    heat = ce[ce["channel"] == "heat"].iloc[0]
    # 全市场 fwd_5_oc 中位 = 0.02。heat members=000001/000002/000005,000002 不可买被剔。
    assert heat["n_recalled"] == 3 and heat["n_unique"] == 2 and heat["n_unbuyable"] == 1
    # mean_excess_t5(heat,buyable 000001/000005)=((0.10-0.02)+(0.08-0.02))/2 = 0.07
    assert abs(heat["mean_excess_t5"] - 0.07) < 1e-9
    # unique=只 heat 一路(000002 不可买、000005 可买)→ 仅 000005:0.08-0.02=0.06
    assert abs(heat["unique_excess_t5"] - 0.06) < 1e-9
    assert heat["hit_rate_t5"] == 1.0
    # composite unique=000003 一只:-0.04-0.02 = -0.06
    comp = ce[ce["channel"] == "composite"].iloc[0]
    assert abs(comp["unique_excess_t5"] - (-0.06)) < 1e-9
    # momentum 无独占票(000004 是 composite|momentum)→ unique_excess_t5 None
    mom = ce[ce["channel"] == "momentum"].iloc[0]
    assert mom["unique_excess_t5"] is None or pd.isna(mom["unique_excess_t5"])
    # 降序:heat(0.06) 排在 composite(-0.06) 前
    order = ce["channel"].tolist()
    assert order.index("heat") < order.index("composite")

    # ── t2 主尺镜像(全市场 fwd_2_oc 中位 = 0.02;数值与 t5 独立,验证非别名)──
    # mean_excess_t2(heat,buyable 000001/000005)=((0.04-0.02)+(0.06-0.02))/2 = 0.03
    assert abs(heat["mean_excess_t2"] - 0.03) < 1e-9
    # unique_excess_t2(heat,仅 000005)=0.06-0.02=0.04
    assert abs(heat["unique_excess_t2"] - 0.04) < 1e-9
    assert heat["hit_rate_t2"] == 1.0
    # composite unique_excess_t2=000003 一只:-0.01-0.02=-0.03
    assert abs(comp["unique_excess_t2"] - (-0.03)) < 1e-9
    # momentum 无独占票 → unique_excess_t2 同样 None
    assert mom["unique_excess_t2"] is None or pd.isna(mom["unique_excess_t2"])
    # 主排序切 t2:heat(0.04) 仍排在 composite(-0.03) 前(即便某路 t5/t2 排名不同也按 t2 走)
    assert order.index("heat") < order.index("composite")


def test_channel_edge_emits_t2_columns():
    """Step 1 契约测试(brief 逐字):channel_eval 生产端加 t2 列。"""
    ev = channel_edge(_recall(), _realized())
    assert {"unique_excess_t2", "hit_rate_t2"} <= set(ev.columns)


def test_channel_edge_empty_or_no_provenance():
    assert channel_edge(pd.DataFrame(), _realized()).empty
    assert channel_edge(pd.DataFrame({"code": ["000001"]}), _realized()).empty  # 无 recall_channels 列


def test_evaluate_writes_l1_channel_block(tmp_path):
    sdir = tmp_path / "2026-06-20"
    sdir.mkdir(parents=True)
    _recall().assign(composite=[90, 80, 70, 60, 50]).to_csv(sdir / "L1_recall_top1000.csv", index=False)
    from autoresearch.learning.stage_eval import evaluate
    res = evaluate("2026-06-20", scan_root=tmp_path, realized=_realized())
    assert "L1" in res["stages"]
    assert len(res["stages"]["L1"]["by_channel"]) >= 3              # composite/heat/momentum…
    assert "ic_n_channels_t5" in res["stages"]["L1"]
    assert "ic_n_channels_t2" in res["stages"]["L1"]
    assert (sdir / "retro" / "channel_eval.csv").exists()
    ce = pd.read_csv(sdir / "retro" / "channel_eval.csv")
    assert "unique_excess_t5" in ce.columns and "heat" in set(ce["channel"])
    assert "unique_excess_t2" in ce.columns


def test_render_has_l1_channel_section():
    from autoresearch.learning.stage_eval import render_stage_eval
    res = {"date": "2026-06-20", "n_realized": 5, "stages": {"L1": {
        "by_channel": [{"channel": "heat", "n_unique": 2, "unique_excess_t2": 0.06, "hit_rate_t2": 1.0,
                        "unique_excess_t5": 0.06, "hit_rate_t5": 1.0},
                       {"channel": "composite", "n_unique": 1, "unique_excess_t2": -0.06, "hit_rate_t2": 0.0,
                        "unique_excess_t5": -0.06, "hit_rate_t5": 0.0}],
        "ic_n_channels_t2": 0.10, "ic_n_channels_t5": 0.12}}}
    md = "\n".join(render_stage_eval(res))
    assert "L1 多路召回" in md and "heat" in md and "+6.0%" in md


def test_ratings_from_details_parses_and_filters(tmp_path):
    from autoresearch.learning.stage_eval import _ratings_from_details
    d = tmp_path / "2026-06-20" / "details"
    d.mkdir(parents=True)
    (d / "000001.md").write_text("dash\n**Rating**: Overweight\n更多", encoding="utf-8")
    (d / "000002.md").write_text("**Rating**：Hold\n", encoding="utf-8")          # 全角冒号
    (d / "000003.md").write_text("**Rating**: Banana\n", encoding="utf-8")        # 非五档→剔
    out = _ratings_from_details("2026-06-20", scan_root=tmp_path)
    assert out == {"000001": "Overweight", "000002": "Hold"}


def test_ratings_from_details_missing_dir(tmp_path):
    from autoresearch.learning.stage_eval import _ratings_from_details
    assert _ratings_from_details("2026-06-20", scan_root=tmp_path) == {}
