import numpy as np
import pandas as pd
import pytest

from autoresearch.learning import retro


def test_selftest():
    assert retro._selftest() == 0


def test_winner_follows_fwd2_not_fwd1():
    """主归因主尺=fwd_2_oc:T+1 大涨但 T+2 回吐的票不是赢家;反之才是。"""
    n = 40
    realized = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "fwd_1_oo": [0.08] + [0.0] * (n - 1),            # 000000 只赢在 T+1
        "fwd_2_oc": [0.0] + [0.06] + [0.001] * (n - 2),  # 000001 赢在 T+2(主尺)
        "fwd_5_oc": [np.nan] * n,
        "buyable": [True] * n,
    })
    l1 = pd.DataFrame({"code": realized["code"], "composite": 0.5, "recalled": False})
    attr = retro.attribute_frame(l1, realized, buylist={})
    w = attr[attr["winner"]]
    assert set(w["code"]) == {"000001"}


# ───────────────────────── L3 两遍法防漏归因(refine_l3_bucket/l3_bench_shadow/pass1_cut_winners,plan 2026-07-12 Task 5) ─────────────────────────
#
# design: docs/plans/2026-07-12-l3-merge-plan.md Task 5
# L3 两遍法产两个影子账本:pass1 分诊即切的 `_l3_pass1_cut.csv`、l3-rank judged 但未晋级 finalist
# 的 `_l3_bench.csv`。①原先笼统落 `recalled_cut` 的赢家现按命中哪个影子文件细分归因;
# ②retro_input 加两行"收窄没吃好票"的日常法庭读数。姿势 presence-gated(读 staging 文件 × attr 帧)。


def test_refine_l3_bucket_noop_when_both_shadow_files_absent(tmp_path):
    """presence-gated 核心契约:两影子文件都不存在(旧日期/two_pass 关闭)→ recalled_cut 原样不变。"""
    attr = pd.DataFrame({"code": ["000001"], "bucket": ["recalled_cut"]})
    out = retro.refine_l3_bucket(attr, tmp_path)
    assert out.loc[0, "bucket"] == "recalled_cut"


def test_refine_l3_bucket_bench_hit_relabels_l3_bench(tmp_path):
    """命中 _l3_bench.csv 的 recalled_cut 行 → 细分为 l3_bench;未命中的行原样保留。"""
    pd.DataFrame({"code": ["000001"], "conviction": [80]}).to_csv(tmp_path / "_l3_bench.csv", index=False)
    attr = pd.DataFrame({"code": ["000001", "000002"], "bucket": ["recalled_cut", "recalled_cut"]})
    out = retro.refine_l3_bucket(attr, tmp_path)
    assert out.loc[out["code"] == "000001", "bucket"].iloc[0] == "l3_bench"
    assert out.loc[out["code"] == "000002", "bucket"].iloc[0] == "recalled_cut"


def test_refine_l3_bucket_pass1_cut_hit_relabels_pass1_cut(tmp_path):
    """命中 _l3_pass1_cut.csv 的 recalled_cut 行 → 细分为 pass1_cut(bench 文件缺失,部分在场亦生效)。"""
    pd.DataFrame({"code": ["000002"]}).to_csv(tmp_path / "_l3_pass1_cut.csv", index=False)
    attr = pd.DataFrame({"code": ["000001", "000002"], "bucket": ["recalled_cut", "recalled_cut"]})
    out = retro.refine_l3_bucket(attr, tmp_path)
    assert out.loc[out["code"] == "000002", "bucket"].iloc[0] == "pass1_cut"
    assert out.loc[out["code"] == "000001", "bucket"].iloc[0] == "recalled_cut"


def test_refine_l3_bucket_bench_takes_priority_over_pass1_cut(tmp_path):
    """同票两文件皆命中(结构上不该发生,纵深防御)→ bench 优先序更高。"""
    pd.DataFrame({"code": ["000001"]}).to_csv(tmp_path / "_l3_bench.csv", index=False)
    pd.DataFrame({"code": ["000001"]}).to_csv(tmp_path / "_l3_pass1_cut.csv", index=False)
    attr = pd.DataFrame({"code": ["000001"], "bucket": ["recalled_cut"]})
    out = retro.refine_l3_bucket(attr, tmp_path)
    assert out.loc[0, "bucket"] == "l3_bench"


def test_refine_l3_bucket_only_touches_recalled_cut_rows(tmp_path):
    """码恰好也在影子文件里,但 bucket 本就不是 recalled_cut(如 caught)→ 不被误改。"""
    pd.DataFrame({"code": ["000001"]}).to_csv(tmp_path / "_l3_bench.csv", index=False)
    attr = pd.DataFrame({"code": ["000001"], "bucket": ["caught"]})
    out = retro.refine_l3_bucket(attr, tmp_path)
    assert out.loc[0, "bucket"] == "caught"


def test_l3_bench_shadow_absent_file_returns_none(tmp_path):
    assert retro.l3_bench_shadow(pd.DataFrame({"code": ["000001"], "fwd_2_oc": [0.1]}), tmp_path) is None


def test_l3_bench_shadow_top5_by_conviction_vs_finalists_mean(tmp_path):
    """按 conviction 降序取 top-5(超过 top_n 的低 conviction 行不计入),对照 finalists 均值。"""
    bench = pd.DataFrame({"code": [f"{i:06d}" for i in range(1, 8)],
                          "conviction": [90, 85, 80, 75, 70, 65, 60]})
    bench.to_csv(tmp_path / "_l3_bench.csv", index=False)
    pd.DataFrame({"code": ["000010", "000011"]}).to_csv(tmp_path / "finalists.csv", index=False)
    attr = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(1, 8)] + ["000010", "000011"],
        # 000006/000007(conviction 65/60)排在 top-5 外,给极端值确保没被算进均值
        "fwd_2_oc": [0.10, 0.08, 0.06, 0.04, 0.02, 999.0, -999.0, 0.05, 0.03],
    })
    bs = retro.l3_bench_shadow(attr, tmp_path)
    assert bs["n_bench"] == 7 and bs["n_bench_top"] == 5 and bs["n_bench_top_realized"] == 5
    assert bs["bench_top_mean_fwd2"] == round((0.10 + 0.08 + 0.06 + 0.04 + 0.02) / 5, 5)
    assert bs["n_finalists_realized"] == 2
    assert bs["finalists_mean_fwd2"] == round((0.05 + 0.03) / 2, 5)


def test_l3_bench_shadow_missing_conviction_degrades_to_file_order(tmp_path):
    """缺 conviction 列 → 不排序,退化为文件前 top_n 行。"""
    pd.DataFrame({"code": ["000001", "000002", "000003"]}).to_csv(tmp_path / "_l3_bench.csv", index=False)
    attr = pd.DataFrame({"code": ["000001", "000002", "000003"], "fwd_2_oc": [0.10, 0.20, 0.30]})
    bs = retro.l3_bench_shadow(attr, tmp_path, top_n=2)
    assert bs["n_bench"] == 3 and bs["n_bench_top"] == 2
    assert bs["bench_top_mean_fwd2"] == round((0.10 + 0.20) / 2, 5)


def test_l3_bench_shadow_missing_finalists_reports_none_finalists_mean(tmp_path):
    """finalists.csv 缺失(纵深防御)→ finalists 侧 None,但 bench 侧读数仍照算不因此不渲染。"""
    pd.DataFrame({"code": ["000001"], "conviction": [80]}).to_csv(tmp_path / "_l3_bench.csv", index=False)
    attr = pd.DataFrame({"code": ["000001"], "fwd_2_oc": [0.05]})
    bs = retro.l3_bench_shadow(attr, tmp_path)
    assert bs["bench_top_mean_fwd2"] == 0.05
    assert bs["finalists_mean_fwd2"] is None and bs["n_finalists_realized"] == 0


def test_l3_bench_shadow_unrealized_fwd_reports_none_not_zero(tmp_path):
    """fwd_2_oc 未成熟(缺列)→ mean=None(不是 0),n_realized=0。"""
    pd.DataFrame({"code": ["000001"], "conviction": [80]}).to_csv(tmp_path / "_l3_bench.csv", index=False)
    attr = pd.DataFrame({"code": ["000001"]})     # 无 fwd_2_oc 列
    bs = retro.l3_bench_shadow(attr, tmp_path)
    assert bs["bench_top_mean_fwd2"] is None and bs["n_bench_top_realized"] == 0


def test_l3_bench_shadow_splits_escort_lanes_from_headline(tmp_path):
    """pr_20260716_002 账本侧:lane∈{pinned,watchlist_trigger,carryover} 的 finalist 行
    不进 finalists 头条均值(L3 对它们没有选择权),escorted_* 单独照实回显;
    lane 列缺失的老数据退化为全量(上方既有测试即回归锚)。"""
    pd.DataFrame({"code": ["000001"], "conviction": [80]}).to_csv(tmp_path / "_l3_bench.csv", index=False)
    pd.DataFrame({"code": ["000010", "000011", "000012", "000013"],
                  "lane": ["healthy", "pinned", "carryover", "watchlist_trigger"]}
                 ).to_csv(tmp_path / "finalists.csv", index=False)
    attr = pd.DataFrame({"code": ["000001", "000010", "000011", "000012", "000013"],
                         "fwd_2_oc": [0.05, 0.04, -0.10, -0.20, np.nan]})
    bs = retro.l3_bench_shadow(attr, tmp_path)
    assert bs["n_finalists_realized"] == 1 and bs["finalists_mean_fwd2"] == 0.04   # 真选头条不含保送
    assert bs["n_escorted"] == 3 and bs["n_escorted_realized"] == 2                # 000013 缺价不计 realized
    assert bs["escorted_mean_fwd2"] == round((-0.10 + -0.20) / 2, 5)


def test_pass1_cut_winners_absent_file_returns_none(tmp_path):
    assert retro.pass1_cut_winners(pd.DataFrame({"code": ["000001"], "winner": [True]}), tmp_path) is None


def test_pass1_cut_winners_counts_using_module_winner_definition(tmp_path):
    """沿用 attribute_frame 现成的 winner 列;不在 cut 集合里的赢家不计入。"""
    pd.DataFrame({"code": ["000001", "000002", "000003"]}).to_csv(tmp_path / "_l3_pass1_cut.csv", index=False)
    attr = pd.DataFrame({"code": ["000001", "000002", "000003", "000004"],
                        "winner": [True, False, True, True]})   # 000004 非 cut 集合成员,不计
    pc = retro.pass1_cut_winners(attr, tmp_path)
    assert pc["n_cut"] == 3 and pc["n_winners"] == 2


def test_write_retro_input_includes_l3_shrink_section_when_bench_present(tmp_path):
    """presence-gated 端到端:_l3_bench.csv 在场 → retro_input.md 含「L3 收窄防漏体检」节 + bench 行。"""
    sdir = tmp_path / "context" / "scan" / "2026-07-12"
    sdir.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"], "conviction": [80]}).to_csv(sdir / "_l3_bench.csv", index=False)
    pd.DataFrame({"code": ["000002"]}).to_csv(sdir / "finalists.csv", index=False)
    n = 20
    attr = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "fwd_1_oo": [0.0] * n, "fwd_2_oc": [0.0] * n, "fwd_5_oc": [np.nan] * n,
        "buyable": [True] * n, "winner": [False] * n, "bucket": [""] * n,
        "recalled_flag": [False] * n, "in_l1": [True] * n, "bought": [False] * n,
        "tradable": [True] * n,
    })
    p = retro.write_retro_input("2026-07-12", attr, scan_root=tmp_path / "context" / "scan")
    text = p.read_text(encoding="utf-8")
    assert "## L3 收窄防漏体检" in text
    assert "L3 bench top-1" in text


def test_write_retro_input_partial_presence_only_pass1_cut_renders_that_line(tmp_path):
    """部分在场:只有 _l3_pass1_cut.csv(bench 未落地/旧日期)→ 该节仍渲染,只含 pass1_cut 那一行。"""
    sdir = tmp_path / "context" / "scan" / "2026-07-12"
    sdir.mkdir(parents=True)
    pd.DataFrame({"code": ["000003", "000004"]}).to_csv(sdir / "_l3_pass1_cut.csv", index=False)
    n = 20
    attr = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "fwd_1_oo": [0.0] * n, "fwd_2_oc": [0.0] * n, "fwd_5_oc": [np.nan] * n,
        "buyable": [True] * n, "winner": [i == 3 for i in range(n)], "bucket": [""] * n,
        "recalled_flag": [False] * n, "in_l1": [True] * n, "bought": [False] * n,
        "tradable": [True] * n,
    })
    p = retro.write_retro_input("2026-07-12", attr, scan_root=tmp_path / "context" / "scan")
    text = p.read_text(encoding="utf-8")
    assert "## L3 收窄防漏体检" in text
    assert "pass1_cut 中 T+2 赢家数:1/2" in text
    assert "L3 bench top-" not in text


def test_write_retro_input_omits_l3_shrink_section_when_both_absent(tmp_path):
    """presence-gated 反向:两影子文件都缺 → retro_input.md 不含该节(老路不破)。"""
    sdir = tmp_path / "context" / "scan" / "2026-07-12"
    sdir.mkdir(parents=True)
    n = 20
    attr = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(n)],
        "fwd_1_oo": [0.0] * n, "fwd_2_oc": [0.0] * n, "fwd_5_oc": [np.nan] * n,
        "buyable": [True] * n, "winner": [False] * n, "bucket": [""] * n,
        "recalled_flag": [False] * n, "in_l1": [True] * n, "bought": [False] * n,
        "tradable": [True] * n,
    })
    p = retro.write_retro_input("2026-07-12", attr, scan_root=tmp_path / "context" / "scan")
    assert "L3 收窄防漏体检" not in p.read_text(encoding="utf-8")


# ══ Wave7:主尺没数时必须响亮失败,不产出空归因 ═════════════════════════════
#
# 2026-07-28 上午实锤:`pending_days` 的成熟判据按**日期**算(D+2 交易日 ≤ today),
# 盘后跑对、盘中跑就错 —— 那天上午跑 07-24,D+2 正是当天、收盘未发布,realized
# **有行但 fwd_2_oc 全 NaN**,原判据(帧非空)放行,一路算出 universe=0/赢家=0 的
# 空归因,而 nightly_close 还报「归因 4/4 日 ✓」。
# 隔壁 t1_review._fetch_prices 早有正确规矩(「未结算 → ValueError,绝不静默返回空帧」)。


def test_attribute_raises_when_main_ruler_all_nan(tmp_path, monkeypatch):
    import pandas as pd

    import autoresearch.learning.retro as R
    d = tmp_path / "2026-07-24"
    d.mkdir(parents=True)
    pd.DataFrame([{"code": f"{i:06d}", "close": 10.0} for i in range(200)]).to_csv(
        d / "L1_scored_full.csv", index=False)
    # 有行,但主尺全 NaN(= D+2 收盘未发布的真实形状)
    monkeypatch.setattr(R, "realized_returns", lambda date, **k: pd.DataFrame(
        {"code": [f"{i:06d}" for i in range(200)], "fwd_2_oc": [float("nan")] * 200}))

    with pytest.raises(RuntimeError, match="未发布|fwd_2_oc"):
        R.attribute("2026-07-24", scan_root=tmp_path)


def test_attribute_message_names_the_wait(tmp_path, monkeypatch):
    """报错要说清「等什么、等到什么时候」—— 否则夜间任务的失败行没人看得懂。"""
    import pandas as pd

    import autoresearch.learning.retro as R
    d = tmp_path / "2026-07-24"
    d.mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "close": 10.0}]).to_csv(
        d / "L1_scored_full.csv", index=False)
    monkeypatch.setattr(R, "realized_returns", lambda date, **k: pd.DataFrame(
        {"code": ["000001"], "fwd_2_oc": [float("nan")]}))

    with pytest.raises(RuntimeError) as ei:
        R.attribute("2026-07-24", scan_root=tmp_path)
    assert "17:00" in str(ei.value) and "空归因" in str(ei.value)
