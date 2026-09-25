# tests/scan/test_index_events.py
"""指数调样事件表(design 2026-09-25 §2.2):相位、生效日推导、白名单、缺席≠否。全部离线(注入假取数)。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache, contracts
from autoresearch.scan import index_events as ie

TDS = ["20261127", "20261130", "20261201", "20261202", "20261203", "20261204", "20261207", "20261208",
       "20261209", "20261210", "20261211", "20261214", "20261215", "20261216", "20261217", "20261218"]
A, E = "20261127", "20261211"


@pytest.fixture(autouse=True)
def _clean():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


@pytest.fixture
def lake(tmp_path, monkeypatch):
    root = tmp_path / "lake"
    monkeypatch.setattr(cache, "LAKE", root)
    monkeypatch.setattr(cache, "_real_today", lambda: "20261210")
    return root


def test_second_friday_and_rule_dates():
    assert ie.second_friday(2026, 12) == "20261211" and ie.second_friday(2026, 6) == "20260612"
    assert ie.rule_eff_close_date("20261127") == "20261211"
    assert ie.rule_eff_close_date("20260529") == "20260612"
    assert ie.rule_eff_close_date("20260909") is None          # 临时调整无规则日


def test_eff_close_from_three_sources():
    assert ie.eff_close_from("20261211", "after_close", A, TDS) == ("20261211", "csindex")
    assert ie.eff_close_from("20261214", "from_date", A, TDS) == ("20261211", "csindex")   # 起生效 → 前一交易日
    assert ie.eff_close_from(None, None, A, TDS) == ("20261211", "rule")                  # 11 月公告 → 规则兜底
    assert ie.eff_close_from(None, None, "20260909", TDS) == (None, "none")


@pytest.mark.parametrize("scan_date,expect", [
    ("2026-11-26", None),                        # 公告前 → 不落表
    ("2026-11-27", "announced_runup"),
    ("2026-12-09", "announced_runup"),           # E−2
    ("2026-12-10", "passive_close_eve"),         # E−1:守卫相位
    ("2026-12-11", "effective"),
    ("2026-12-16", "post"),                      # E+3
    ("2026-12-17", None),                        # E+4 → 出窗
])
def test_phase_for(scan_date, expect):
    assert ie.phase_for(scan_date, A, E, TDS) == expect


def test_phase_unknown_eff_when_no_effective_date():
    assert ie.phase_for("2026-12-10", A, None, TDS) == "unknown_eff"


def _list(items):
    return lambda endpoint, params: pd.DataFrame(items, columns=ie_list_cols())


def ie_list_cols():
    from autoresearch.data.sources.csindex import LIST_COLS
    return LIST_COLS


def _detail_rows(ann_id, publish_date, content, rows):
    from autoresearch.data.sources.csindex import DETAIL_COLS
    base = {"ann_id": ann_id, "publish_date": publish_date, "title": "关于调整指数样本的公告",
            "content_text": content, "attachment_url": "u"}
    return pd.DataFrame([{**base, **r} for r in rows], columns=DETAIL_COLS)


ROSTER = [
    {"index_code": "000300", "index_name": "沪深300", "side": "add", "code": "600221", "name": "海航控股"},
    {"index_code": "000300", "index_name": "沪深300", "side": "drop", "code": "600188", "name": "兖矿能源"},
    {"index_code": "000905", "index_name": "中证500", "side": "add", "code": "600188", "name": "兖矿能源"},   # 跨指数迁移:同票两行
    {"index_code": "000009", "index_name": "上证380", "side": "add", "code": "603092", "name": "德力佳"},      # 非白名单
]


def _fetch_detail(ann_id_to_frame):
    return lambda endpoint, params: ann_id_to_frame[str(params["ann_id"])]


def test_build_filters_whitelist_and_labels_phase(lake):
    fl = _list([["3007001", "关于调整沪深300、中证500等指数样本的公告", "20261127", "index_rebalance"]])
    fd = _fetch_detail({"3007001": _detail_rows("3007001", "20261127",
                                                "上述调整将于2026年12月11日收市后生效。", ROSTER)})
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert list(df.columns) == ie.EVENT_COLS
    assert set(df.index_code) == {"000300", "000905"}                       # 上证380 行不进表
    assert len(df) == 3 and (df.phase == "passive_close_eve").all()
    assert (df.eff_close_date == "20261211").all() and (df.source == "csindex").all()
    migrate = df[df.code == "600188"]
    assert set(migrate.side) == {"drop", "add"}                              # 迁移不合并,两行都在
    assert df.flow_adv_days.isna().all()                                     # B3 之前恒空 = 未计算


def test_build_uses_rule_date_when_text_has_no_date(lake):
    fl = _list([["3007002", "关于调整沪深300等指数样本的公告", "20261127", "index_rebalance"]])
    fd = _fetch_detail({"3007002": _detail_rows("3007002", "20261127", "调整名单见附件。", ROSTER[:1])})
    df = ie.build_index_events("2026-12-01", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert df.iloc[0].to_dict()["eff_close_date"] == "20261211" and df.iloc[0]["source"] == "rule"
    assert df.iloc[0]["phase"] == "announced_runup"


def test_build_unknown_eff_row_is_kept_with_its_own_phase(lake):
    fl = _list([["3006227", "关于沪深300等指数样本临时调整的公告", "20260909", "index_rebalance"]])
    fd = _fetch_detail({"3006227": _detail_rows("3006227", "20260909", "自东兴证券、信达证券退市日起调整", ROSTER[:1])})
    df = ie.build_index_events("2026-09-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert df.iloc[0]["phase"] == "unknown_eff" and pd.isna(df.iloc[0]["eff_close_date"])


def test_build_returns_empty_frame_when_no_whitelist_events(lake):
    fl = _list([["3006244", "关于调整三板成指样本股的公告", "20260918", "index_rebalance"]])
    fd = _fetch_detail({"3006244": _detail_rows("3006244", "20260918", "x", ROSTER[3:])})
    df = ie.build_index_events("2026-09-20", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert df is not None and df.empty and list(df.columns) == ie.EVENT_COLS     # 源可达无事件 = 空表,不是 None


def test_build_returns_none_and_records_degradation_when_list_unreachable(lake):
    def boom(endpoint, params):
        raise RuntimeError("csindex down")
    df = ie.build_index_events("2026-12-10", today="20261210", fetch_list=boom, trading_days=TDS)
    assert df is None
    assert any(r["endpoint"] == "csindex_rebalance_list" for r in contracts.degradations())


def test_build_on_past_date_reuses_latest_list_snapshot_instead_of_fetching(lake):
    calls = []

    def fl(endpoint, params):
        calls.append(1)
        return pd.DataFrame([["3007001", "关于调整沪深300等指数样本的公告", "20261127", "index_rebalance"]],
                            columns=ie_list_cols())
    fd = _fetch_detail({"3007001": _detail_rows("3007001", "20261127", "x", ROSTER[:1])})
    ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    df = ie.build_index_events("2026-12-01", today="20261201", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert len(calls) == 1 and len(df) == 1                                  # 补跑:读湖里最新快照,不取网


def test_detail_is_fetched_once_per_announcement(lake):
    calls = []
    fl = _list([["3007001", "关于调整沪深300等指数样本的公告", "20261127", "index_rebalance"]])

    def fd(endpoint, params):
        calls.append(params["ann_id"])
        return _detail_rows("3007001", "20261127", "x", ROSTER[:1])
    ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    ie.build_index_events("2026-12-10", today="20261210", fetch_list=fl, fetch_detail=fd, trading_days=TDS)
    assert calls == ["3007001"]                                              # 第二次命中 <ann_id>@*.parquet


def test_harvest_writes_no_file_when_source_unreachable(lake, tmp_path):
    d = tmp_path / "2026-12-10"
    d.mkdir()

    def boom(endpoint, params):
        raise RuntimeError("csindex down")
    df = ie.harvest_index_events("2026-12-10", d, today="20261210", fetch_list=boom, trading_days=TDS)
    assert df is None and not (d / ie.INDEX_EVENTS_FILENAME).exists()           # 世界①:不可达 = 不落文件


def test_harvest_writes_header_only_file_when_reachable_without_events(lake, tmp_path):
    d = tmp_path / "2026-09-20"
    d.mkdir()
    fl = _list([["3006244", "关于调整三板成指样本股的公告", "20260918", "index_rebalance"]])
    fd = _fetch_detail({"3006244": _detail_rows("3006244", "20260918", "x", ROSTER[3:])})
    df = ie.harvest_index_events("2026-09-20", d, today="20261210", fetch_list=fl, fetch_detail=fd,
                                 trading_days=TDS)
    assert df is not None and df.empty and (d / ie.INDEX_EVENTS_FILENAME).exists()   # 世界②:可达无事件 = 落表头
    reloaded = ie.load_index_events(d)
    assert reloaded is not None and reloaded.empty                              # 落盘后读回仍可分辨于「缺席」


def test_write_load_by_code_and_absence_semantics(tmp_path):
    d = tmp_path / "2026-12-10"
    d.mkdir()
    assert ie.load_index_events(d) is None and ie.events_by_code(None) is None      # 缺文件 = 缺席
    ie.write_index_events(d, pd.DataFrame(columns=ie.EVENT_COLS))
    empty = ie.load_index_events(d)
    assert empty is not None and empty.empty and ie.events_by_code(empty) == {}     # 空表 = 可达无事件
    rows = pd.DataFrame([{"code": "600221", "index_code": "000300", "index_name": "沪深300", "side": "add",
                          "ann_date": A, "eff_close_date": E, "phase": "passive_close_eve",
                          "source": "csindex", "flow_adv_days": None}], columns=ie.EVENT_COLS)
    ie.write_index_events(d, rows)
    by = ie.events_by_code(ie.load_index_events(d))
    assert list(by) == ["600221"] and by["600221"][0]["phase"] == "passive_close_eve"
    assert by["600221"][0]["eff_close_date"] == E                                  # 读回仍是字符串


def test_trading_days_window_falls_back_to_weekdays_and_records_it(monkeypatch):
    import autoresearch.data.tushare_source as ts_src
    monkeypatch.setattr(ts_src, "_pro", lambda: (_ for _ in ()).throw(RuntimeError("no token")))
    days, source = ie.trading_days_window("2026-12-10", before=7, after=7)
    assert source == "weekday_approx" and "20261210" in days and "20261212" not in days   # 周六不在
    assert any(r["endpoint"] == "trade_cal" for r in contracts.degradations())
