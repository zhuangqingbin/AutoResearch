"""指数调样隔夜尺普查 CLI(design 2026-09-25 附录 A 的可复现版):合成湖 + 假 index_weight,零网络。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.data import cache
from autoresearch.research import index_rebalance_census as cen

# 2026-06 调样:A=05-29(周五),E=06-12(第二个周五)。合成 22 个交易日,只做 6 月这一段。
DAYS = ["20260525", "20260526", "20260527", "20260528", "20260529", "20260601", "20260602", "20260603",
        "20260604", "20260605", "20260608", "20260609", "20260610", "20260611", "20260612", "20260615",
        "20260616", "20260617", "20260618", "20260619", "20260622", "20260623"]
ADD, OLD1, OLD2 = "600221.SH", "600000.SH", "600036.SH"


def _lake(tmp_path, monkeypatch):
    daily = tmp_path / "lake" / "daily"
    daily.mkdir(parents=True)
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    for d in DAYS:
        px = {OLD1: 10.0, OLD2: 20.0, ADD: 30.0}
        rows = []
        for code, p in px.items():
            open_ = p
            if code == ADD and d == "20260615":        # 生效日次日:调入票开盘 −2%(被动收盘后回吐)
                open_ = p * 0.98
            rows.append({"ts_code": code, "open": open_, "high": p, "low": p, "close": p,
                         "pre_close": p, "change": 0.0, "pct_chg": 0.0, "vol": 1.0, "amount": 1000.0})
        pd.DataFrame(rows).to_parquet(daily / f"{d}.parquet")
    return daily


def _fetch_index_weight(endpoint, params):
    end = params["end_date"]
    members = [OLD1, OLD2] + ([ADD] if end >= "20260601" else [])
    return pd.DataFrame({"index_code": params["index_code"], "con_code": members,
                         "trade_date": end[:6] + ("29" if end < "20260601" else "30"), "weight": 1.0})


def test_census_labels_e_minus_1_negative_for_the_add(tmp_path, monkeypatch):
    daily = _lake(tmp_path, monkeypatch)
    doc = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_index_weight,
                         indexes={"000300.SH": "沪深300"})
    assert doc["events"] == [{"A": "20260529", "E": "20260612", "indexes": ["沪深300"]}]
    add_tbl = doc["tables"]["add"]
    assert set(add_tbl) >= {"1.A-1", "2.A", "3.run", "5.E-1", "6.E"}
    assert add_tbl["5.E-1"]["n"] == 1 and add_tbl["5.E-1"]["exc_pp"] == pytest.approx(-2.0, abs=0.05)
    assert add_tbl["2.A"]["exc_pp"] == pytest.approx(0.0, abs=1e-9)
    assert doc["by_index"]["沪深300"]["add"]["5.E-1"]["n"] == 1


def test_render_mentions_phase_table_and_caveats(tmp_path, monkeypatch):
    daily = _lake(tmp_path, monkeypatch)
    doc = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_index_weight,
                         indexes={"000300.SH": "沪深300"})
    md = cen.render(doc)
    assert "5.E-1" in md and "同指数未变动成分股" in md and "未扣成本" in md


def test_main_writes_only_under_research_report_root(tmp_path, monkeypatch):
    daily = _lake(tmp_path, monkeypatch)
    out = tmp_path / "research" / "index_rebalance_census.md"
    monkeypatch.setattr(cen, "_default_fetch", lambda: _fetch_index_weight)
    rc = cen.main(["--start", "2026-06", "--end", "2026-06", "--lake-daily", str(daily), "--out", str(out),
                   "--index", "000300.SH=沪深300"])
    assert rc == 0 and out.exists() and (out.parent / "_index_rebalance_census.json").exists()


# ── Task 1 遗留发现的补充覆盖:`index_weight` 契约有 required_cols、无 min_rows 下限,一份读到
# 一半的成分股页(如 300 只读到 150 只)会正常通过校验。本普查逐月做差,截断页会读成一堆假调入/
# 假调出——这条测试锁住的不变量是:截断必须在产物里**可见**(行数 + 标记),但**不能**被本仪器
# 悄悄拿去改写统计(那是另一个更容易犯的错:见到「可疑」就自作主张丢弃观测,反而制造了第二种
# 静默失真)。因此沪深300 5 月末给满 300 行、6 月末只给 150 行(仍完整保留 ADD/OLD1/OLD2 三只有
# 真实行情的票),与顶部测试逐位同构,可复用同一份算出来的 5.E-1 数字做「统计没被截断悄悄改写」
# 的断言。
def _fetch_truncated_cur(endpoint, params):
    end = params["end_date"]
    phantom_prev = [f"9{i:05d}.SH" for i in range(298)]
    phantom_cur = [f"9{i:05d}.SH" for i in range(147)]
    members = ([OLD1, OLD2] + phantom_prev if end < "20260601"     # 5 月末:满 300 行
               else [OLD1, OLD2, ADD] + phantom_cur)               # 6 月末:半截,只有 150 行
    return pd.DataFrame({"index_code": params["index_code"], "con_code": members,
                         "trade_date": end[:6] + ("29" if end < "20260601" else "30"), "weight": 1.0})


def test_truncated_snapshot_is_visible_but_stats_are_unchanged(tmp_path, monkeypatch):
    daily = _lake(tmp_path, monkeypatch)
    doc = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_truncated_cur,
                         indexes={"000300.SH": "沪深300"})
    assert doc["snapshot_sizes"] == [
        {"E": "20260612", "index": "沪深300", "prev_n": 300, "cur_n": 150, "included": True, "suspect": True}
    ]
    # 截断没有被悄悄拿去改写口径:同一算法、同一控制组,算出来还是同一个 5.E-1。
    assert doc["tables"]["add"]["5.E-1"]["n"] == 1
    assert doc["tables"]["add"]["5.E-1"]["exc_pp"] == pytest.approx(-2.0, abs=0.05)
    md = cen.render(doc)
    assert "⚠" in md and "300" in md and "150" in md


def test_missing_snapshot_is_not_confused_with_a_suspect_one(tmp_path, monkeypatch):
    """源不可达(两份快照都是空)与「有数据但可疑地少」是两个不同的世界,不能编码成同一个值。"""
    daily = _lake(tmp_path, monkeypatch)

    def _fetch_empty(endpoint, params):
        return pd.DataFrame(columns=["index_code", "con_code", "trade_date", "weight"])

    doc = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_empty,
                         indexes={"000300.SH": "沪深300"})
    assert doc["snapshot_sizes"] == [
        {"E": "20260612", "index": "沪深300", "prev_n": 0, "cur_n": 0, "included": False, "suspect": False}
    ]
    assert doc["events"] == [{"A": "20260529", "E": "20260612", "indexes": []}]
