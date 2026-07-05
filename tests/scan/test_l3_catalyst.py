"""催化事件聚合(增减持/回购/调研)→ L3_catalyst.csv + cat 徽标。合成,fetch_fn 注入,无网络。

spec: 2026-07-05 wave §WS-B1/B2。07-05 实测三端点均有权限;07-03 病灶 30/30 卡"无明确催化"。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.agents.l3_catalyst import cat_label, catalyst_counts, harvest_catalyst

_DATE = "2026-07-03"


def _fetch(endpoint: str, params: dict) -> pd.DataFrame:
    if endpoint == "stk_holdertrade":
        return pd.DataFrame([
            {"ts_code": "000001.SZ", "ann_date": params["ann_date"], "in_de": "IN"},
            {"ts_code": "000001.SZ", "ann_date": params["ann_date"], "in_de": "DE"},
            {"ts_code": "999999.SH", "ann_date": params["ann_date"], "in_de": "IN"},   # 非目标票,应被滤掉
        ])
    if endpoint == "repurchase":
        return pd.DataFrame([
            {"ts_code": "000001.SZ", "ann_date": params["ann_date"], "proc": "实施"},
            {"ts_code": "000002.SZ", "ann_date": params["ann_date"], "proc": "股东大会通过"},
        ])
    if endpoint == "stk_surv":
        return pd.DataFrame([{"ts_code": "000002.SZ", "trade_date": params["trade_date"]}])
    raise AssertionError(endpoint)


def test_harvest_and_counts(tmp_path):
    df = harvest_catalyst(_DATE, ["000001", "000002"], root=tmp_path,
                          days=["20260702", "20260703"], fetch_fn=_fetch)
    df = df.set_index("code")
    assert df.at["000001", "holder_in"] == 2 and df.at["000001", "holder_de"] == 2   # 2 天累计
    assert df.at["000001", "rep_impl"] == 2 and df.at["000001", "surv_n"] == 0
    assert df.at["000002", "rep_plan"] == 2 and df.at["000002", "surv_n"] == 2
    assert (tmp_path / _DATE / "L3_catalyst.csv").exists()                            # 落 staging


def test_cat_label():
    assert cat_label({"rep_impl": 1, "rep_plan": 0, "holder_in": 2, "holder_de": 1, "surv_n": 5}) \
        == "回购1(实施)·增持2·调研5·减持1"
    assert cat_label({"rep_impl": 0, "rep_plan": 1, "holder_in": 0, "holder_de": 0, "surv_n": 0}) \
        == "回购1(预案)"
    assert cat_label({"rep_impl": 0, "rep_plan": 0, "holder_in": 0, "holder_de": 0, "surv_n": 0}) == ""


def test_counts_pure_empty():
    out = catalyst_counts({"stk_holdertrade": [], "repurchase": [], "stk_surv": []}, {"000001"})
    assert list(out.columns) == ["code", "rep_impl", "rep_plan", "holder_in", "holder_de", "surv_n"]
    assert out.set_index("code").loc["000001"].sum() == 0


def _mk_scan(root, cat_rows):
    d = root / _DATE
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"code": "000001", "name": "甲", "industry": "电子", "composite": 80.0,
                   "main_net_ratio": 0.05, "pct_60d": 10.0, "pe": 30.0}]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame(cat_rows).to_csv(d / "L3_catalyst.csv", index=False)
    return d


def test_l3_table_cat_flag_on_and_parity(tmp_path):
    from autoresearch.scan.agents.l3_select import l3_table_md
    _mk_scan(tmp_path, [{"code": "000001", "rep_impl": 1, "rep_plan": 0,
                         "holder_in": 2, "holder_de": 0, "surv_n": 3}])
    md = l3_table_md(_DATE, root=tmp_path, cat_flag=True)
    assert "cat" in md and "回购1(实施)·增持2·调研3" in md
    assert "📣催化列" in md and "减持≥2" in md                 # 图例 + 禁则
    assert "📣催化列" not in l3_table_md(_DATE, root=tmp_path)   # 默认关 = parity


def test_funnel_brief_cat_mark(tmp_path):
    from autoresearch.scan.agents.l4_card import compose_funnel_brief
    d = _mk_scan(tmp_path, [{"code": "000001", "rep_impl": 0, "rep_plan": 0,
                             "holder_in": 0, "holder_de": 2, "surv_n": 0}])
    pd.DataFrame([{"code": "000001", "name": "甲", "industry": "电子"}]).to_csv(
        d / "L1_recall_top1000.csv", index=False)
    brief = compose_funnel_brief("000001", d)
    assert "📣催化事件" in brief and "减持2" in brief
    (d / "L3_catalyst.csv").unlink()                             # presence-gated:无 staging 无行
    assert "📣催化事件" not in compose_funnel_brief("000001", d)
