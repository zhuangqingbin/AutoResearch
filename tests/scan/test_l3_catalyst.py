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
