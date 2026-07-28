"""保送持仓判断账本(Wave7 P1):持仓的卖/持判断事后看对不对。

与选股复盘物理分表 —— 2026-07-17「保送不算」裁定管的是漏斗选股口径(retro/t1_review/
L3 edge),本表问的是另一个问题。保送票是全流程唯一"判断错了会真赔钱"的一档,此前
完全没有度量:07-27 的 300857 从 Sell 被双复核折回 Underweight,那次折回救对了还是
救错了,没有任何账本回答得了。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning import pinned_ledger as P


def _day(root, date, *, pinned_rows, ratings, attribution, ensemble=None):
    d = root / date
    (d / "details").mkdir(parents=True)
    (d / "retro").mkdir(parents=True)
    pd.DataFrame(pinned_rows).to_csv(d / "finalists.csv", index=False)
    for code, r in ratings.items():
        (d / "details" / f"{code}.md").write_text(
            f"〔卡契约 v3〕\n# 决策卡\n**Rating**: {r}\n", encoding="utf-8")
    pd.DataFrame(attribution).to_csv(d / "retro" / "attribution.csv", index=False)
    for code, rec in (ensemble or {}).items():
        (d / f"_ensemble_{code}.json").write_text(json.dumps(rec), encoding="utf-8")
    return d


def _attr(rows):
    """rows = [(code, fwd_2_oc), ...] → attribution 帧(市场中位由全部行算)。"""
    return [{"code": c, "fwd_2_oc": v} for c, v in rows]


# ── 判定口径 ──────────────────────────────────────────────────────────────


def test_verdict_bearish_right_when_underperforms():
    """看空持仓 → 它相对市场跌了 = 判对(躲过相对下跌)。"""
    assert P.verdict_of("Underweight", -0.05) == "判对"
    assert P.verdict_of("Sell", -0.001) == "判对"


def test_verdict_bearish_wrong_only_beyond_fly_line():
    """卖飞线 +2pp:比 0 宽一点(免得把「和大盘差不多」记成卖飞),又远小于一个涨停。"""
    assert P.verdict_of("Underweight", 0.01) == "中性"
    assert P.verdict_of("Underweight", 0.02) == "判错(卖飞)"
    assert P.verdict_of("Sell", 0.11) == "判错(卖飞)"


def test_verdict_hold_is_a_falsifiable_claim_for_a_holding():
    """对持仓而言 Hold 不是弃权,它是「继续持有」这个主张 —— 同样可以被证伪。"""
    assert P.verdict_of("Hold", 0.03) == "判对"
    assert P.verdict_of("Hold", -0.05) == "判错(该走没走)"
    assert P.verdict_of("Hold", -0.01) == "中性"


def test_verdict_none_when_data_missing():
    assert P.verdict_of("Hold", None) is None
    assert P.verdict_of("", 0.1) is None


def test_market_benchmark_uses_median_of_full_universe():
    """用中位不用均值(涨停尾巴会把均值拽偏),用全量 attribution 不用 finalist 子集。"""
    attr = pd.DataFrame(_attr([("000001", 0.01), ("000002", 0.02),
                               ("000003", 0.03), ("000004", 9.99)])).set_index("code")
    assert P.market_fwd2(attr) == 0.025
    assert P.market_fwd2(None) is None


# ── 折回效果(sell_review 那条腿唯一的度量)────────────────────────────────


def test_fold_verdict_credits_a_rescue():
    """原判 Sell 会被判错(卖飞),折回后的 Hold 判对 → 折对。"""
    assert P.fold_verdict_of("Sell", "Hold", 0.05) == "折对"


def test_fold_verdict_blames_a_bad_rescue():
    """原判 Sell 本来判对(相对跌了),折成 Hold 反而判错 → 折错。"""
    assert P.fold_verdict_of("Sell", "Hold", -0.05) == "折错"


def test_fold_verdict_none_without_a_fold():
    assert P.fold_verdict_of("Sell", "Sell", -0.05) is None
    assert P.fold_verdict_of(None, "Hold", -0.05) is None
    assert P.fold_verdict_of("Sell", "Hold", None) is None


# ── roll:端到端 ──────────────────────────────────────────────────────────


def test_roll_only_takes_pinned_lane(tmp_path):
    """只收 lane=pinned 行 —— 真实精选归 buy_ledger/t1_review,两表永不交叉。"""
    _day(tmp_path, "2026-07-21",
         pinned_rows=[{"code": "300857", "name": "协创", "lane": "pinned", "conviction": 41},
                      {"code": "600988", "name": "赤峰", "lane": "growth", "conviction": 73}],
         ratings={"300857": "Underweight", "600988": "Hold"},
         attribution=_attr([("300857", -0.02), ("600988", 0.05),
                            ("000001", 0.0), ("000002", 0.02)]))

    df = P.roll(scan_root=tmp_path)

    assert list(df["code"]) == ["300857"]
    assert df.iloc[0]["l3_conviction"] == 41.0


def test_roll_computes_excess_and_verdict(tmp_path):
    _day(tmp_path, "2026-07-21",
         pinned_rows=[{"code": "300857", "name": "协创", "lane": "pinned", "conviction": 41}],
         ratings={"300857": "Underweight"},
         attribution=_attr([("300857", -0.08), ("000001", 0.0), ("000002", 0.02)]))

    r = P.roll(scan_root=tmp_path).iloc[0]

    assert r["market_fwd_2"] == 0.0            # 中位 = 三行的中间值
    assert abs(r["excess"] + 0.08) < 1e-9
    assert r["verdict"] == "判对"


def test_roll_records_fold_and_its_outcome(tmp_path):
    """双复核折回进账:Sell→Underweight,事后该票相对市场大涨 → 折对(救回一次误卖)。"""
    _day(tmp_path, "2026-07-21",
         pinned_rows=[{"code": "300857", "name": "协创", "lane": "pinned", "conviction": 41}],
         ratings={"300857": "Sell"},
         attribution=_attr([("300857", 0.12), ("000001", 0.0), ("000002", 0.02)]),
         ensemble={"300857": {"code": "300857", "ratings": ["Sell", "Underweight", "Underweight"],
                              "median": "Underweight", "spread": 1, "degraded": False,
                              "trigger": "sell_review", "n_runs": 3}})

    r = P.roll(scan_root=tmp_path).iloc[0]

    assert r["rating"] == "Underweight", "终评必须是折回后的值(final_ratings 要含 ensemble 腿)"
    assert r["fold_from"] == "Sell" and r["fold_to"] == "Underweight"
    assert r["fold_verdict"] == "折平", "两档都被判卖飞 → 折回没改变结论,记折平"
    assert r["trigger"] == "sell_review"


def test_roll_immature_rows_are_kept_but_unjudged(tmp_path):
    """fwd 未成熟的当天卡也要进表(看得见「今天判了什么」),只是不给判定。"""
    d = tmp_path / "2026-07-27"
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "300857", "name": "协创", "lane": "pinned", "conviction": 41}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details" / "300857.md").write_text("**Rating**: Sell\n", encoding="utf-8")

    df = P.roll(scan_root=tmp_path)

    assert len(df) == 1 and df.iloc[0]["verdict"] is None
    assert "未成熟" in "\n".join(P.render(df))


def test_render_empty_says_so(tmp_path):
    assert "无保送持仓记录" in "\n".join(P.render(P.roll(scan_root=tmp_path)))


def test_render_flags_thin_sample(tmp_path):
    """n<10 必须自标样本不足 —— 本表只攒数据,不改任何规则。"""
    _day(tmp_path, "2026-07-21",
         pinned_rows=[{"code": "300857", "name": "协创", "lane": "pinned", "conviction": 41}],
         ratings={"300857": "Underweight"},
         attribution=_attr([("300857", -0.08), ("000001", 0.0), ("000002", 0.02)]))
    md = "\n".join(P.render(P.roll(scan_root=tmp_path)))
    assert "样本不足" in md
