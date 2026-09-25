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
            # fix round 1 #2:控制组同一天也有个非零开盘位移(+1%)——证明 ctrl 取的是「同一天」,
            # 不是恰好每天都是 0 的常数(此前 OLD1/OLD2 全程持平,取错一天的 ctrl 照样算出 0)。
            # OLD2 仍保持完全不变,两只控制票不再对称。
            if code == OLD1 and d == "20260615":
                open_ = p * 1.01
            rows.append({"ts_code": code, "open": open_, "high": p, "low": p, "close": p,
                         "pre_close": p, "change": 0.0, "pct_chg": 0.0, "vol": 1.0, "amount": 1000.0})
        pd.DataFrame(rows).to_parquet(daily / f"{d}.parquet")
    return daily


def _fetch_index_weight(endpoint, params):
    end = params["end_date"]
    members = [OLD1, OLD2] + ([ADD] if end >= "20260601" else [])
    return pd.DataFrame({"index_code": params["index_code"], "con_code": members,
                         "trade_date": end[:6] + ("29" if end < "20260601" else "30"), "weight": 1.0})


def _calendar_full(endpoint, params):
    """假日历:声称 `DAYS` 的全部 22 天都是真交易日——用来在既有湖完整时把 `shortfall` 钉在 0,
    也复用给「湖漏一天」的测试(那条测试删掉一个 parquet,日历仍声称原本 22 天都该在)。"""
    return pd.DataFrame({"cal_date": DAYS})


def test_census_labels_e_minus_1_negative_for_the_add(tmp_path, monkeypatch):
    daily = _lake(tmp_path, monkeypatch)
    doc = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_index_weight,
                         indexes={"000300.SH": "沪深300"}, calendar_fetch=_calendar_full)
    assert doc["events"] == [{"A": "20260529", "E": "20260612", "indexes": ["沪深300"]}]
    add_tbl = doc["tables"]["add"]
    assert set(add_tbl) >= {"1.A-1", "2.A", "3.run", "5.E-1", "6.E"}
    # exc(5.E-1) = gap_ADD(day13) - ctrl(day13):
    #   gap_ADD  = open_ADD(20260615)/close_ADD(20260612) - 1 = 29.4/30.0 - 1  = -2.0%
    #   ctrl     = mean(gap_OLD1(day13), gap_OLD2(day13))
    #            = mean(10.10/10.0 - 1, 20.0/20.0 - 1) = mean(+1.0%, 0.0%)     = +0.5%
    #   exc      = -2.0% - 0.5%                                                = -2.5%
    assert add_tbl["5.E-1"]["n"] == 1 and add_tbl["5.E-1"]["exc_pp"] == pytest.approx(-2.5, abs=0.05)
    assert add_tbl["2.A"]["exc_pp"] == pytest.approx(0.0, abs=1e-9)
    assert doc["by_index"]["沪深300"]["add"]["5.E-1"]["n"] == 1
    # fix round 1 #1:单指数单调样 → 每个相位只有一个 (E, index) 格,n_cells=1(≤2),t_cell 必须
    # 报 None——不是拿一个基于单点方差算出来的数字来冒充「有统计意义」。
    assert add_tbl["5.E-1"]["n_cells"] == 1 and add_tbl["5.E-1"]["t_cell"] is None
    # fix round 1 #3:湖内窗口天数与 `trade_cal`(这里的假日历同样声称 22 天都在)完全一致 → 0 缺口。
    assert doc["calendar_gaps"][0]["shortfall"] == 0


def test_render_mentions_phase_table_and_caveats(tmp_path, monkeypatch):
    daily = _lake(tmp_path, monkeypatch)
    doc = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_index_weight,
                         indexes={"000300.SH": "沪深300"}, calendar_fetch=_calendar_full)
    md = cen.render(doc)
    assert "5.E-1" in md and "同指数未变动成分股" in md and "未扣成本" in md
    assert "交易日连续性" in md and "缺口" in md


def test_main_writes_only_under_research_report_root(tmp_path, monkeypatch):
    daily = _lake(tmp_path, monkeypatch)
    out = tmp_path / "research" / "index_rebalance_census.md"
    monkeypatch.setattr(cen, "_default_fetch", lambda: _fetch_index_weight)
    monkeypatch.setattr(cen, "_production_calendar_fetch", _calendar_full)
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
    # 截断没有被悄悄拿去改写口径:同一算法、同一控制组,算出来还是同一个 5.E-1(-2.5pp,推导见
    # test_census_labels_e_minus_1_negative_for_the_add 顶部注释——两条测试共用同一份 `_lake()`)。
    assert doc["tables"]["add"]["5.E-1"]["n"] == 1
    assert doc["tables"]["add"]["5.E-1"]["exc_pp"] == pytest.approx(-2.5, abs=0.05)
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


# ── fix round 1 #1:t_cell 是预注册口径的核心防线(股票行不是独立样本;要按 (调样, 指数) 格聚合,
# 不能只按指数聚合)。上面所有测试都只有单次调样、单个指数 → 每个相位恒 n_cells=1,`t_cell` 恒
# None——从没有一条测试真正让 `cells = g.groupby(["E", "index"])` 的 `["E", ...]` 那部分起作用。
# 下面这份独立小 fixture:两次调样(6 月 + 12 月)× 两个指数,四个 (E, index) 格各给一支不同幅度
# 的调入票、控制组保持平坦(ctrl≡0,与本测试的关注点——格聚合——正交,不需要再纠缠 fix #2 的
# 控制组位移)。四个格的 exc 分别是 -2%/-6%/+2%/-4%,由此手算(并用独立脚本核对过,见 fix round 1
# 报告):mean=-2.5%,std(ddof=1)=3.4157e-2,t_cell = -0.025/(0.034157/sqrt(4)) ≈ -1.464 → 四舍
# 到 -1.46。若把 `cells` 的分组键从 ["E","index"] 弱化成 ["index"](只按指数合并、丢掉调样批次),
# 两次调样会被合并成每指数一格,格数掉到 2(≤2)→ `_stats` 的 `len(cells) > 2` 门槛不通过 → t_cell
# 变 None——与本测试断言的具体数字直接冲突,已用独立脚本对 `_stats` 打过这个突变并确认变红。
_TWO_CYCLE_OLD1, _TWO_CYCLE_OLD2 = "600000.SH", "600036.SH"
_ADD_A_JUN, _ADD_B_JUN = "600301.SH", "600501.SH"          # 6 月:沪深300 −2% / 中证500 −6%
_ADD_A_DEC, _ADD_B_DEC = "600302.SH", "600502.SH"          # 12 月:沪深300 +2% / 中证500 −4%
_TWO_CYCLE_TICKERS = (_TWO_CYCLE_OLD1, _TWO_CYCLE_OLD2, _ADD_A_JUN, _ADD_B_JUN, _ADD_A_DEC, _ADD_B_DEC)
_TWO_CYCLE_BASE = 30.0
_TWO_CYCLE_PERTURB = {_ADD_A_JUN: 0.98, _ADD_B_JUN: 0.94, _ADD_A_DEC: 1.02, _ADD_B_DEC: 0.96}
_TWO_CYCLE_DAYS = [d.strftime("%Y%m%d") for d in pd.bdate_range("2026-05-20", "2026-12-18")]
_E_JUN, _E_DEC = "20260612", "20261211"                    # second_friday(2026,6)/(2026,12)


def _lake_two_cycles(tmp_path, monkeypatch):
    daily = tmp_path / "lake" / "daily"
    daily.mkdir(parents=True)
    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    i_jun, i_dec = _TWO_CYCLE_DAYS.index(_E_JUN), _TWO_CYCLE_DAYS.index(_E_DEC)
    day_after_jun, day_after_dec = _TWO_CYCLE_DAYS[i_jun + 1], _TWO_CYCLE_DAYS[i_dec + 1]
    for d in _TWO_CYCLE_DAYS:
        rows = []
        for code in _TWO_CYCLE_TICKERS:
            p = 10.0 if code == _TWO_CYCLE_OLD1 else (20.0 if code == _TWO_CYCLE_OLD2 else _TWO_CYCLE_BASE)
            open_ = p
            if code in _TWO_CYCLE_PERTURB:
                trigger = day_after_jun if code in (_ADD_A_JUN, _ADD_B_JUN) else day_after_dec
                if d == trigger:
                    open_ = p * _TWO_CYCLE_PERTURB[code]
            rows.append({"ts_code": code, "open": open_, "high": p, "low": p, "close": p,
                         "pre_close": p, "change": 0.0, "pct_chg": 0.0, "vol": 1.0, "amount": 1000.0})
        pd.DataFrame(rows).to_parquet(daily / f"{d}.parquet")
    return daily


def _fetch_two_cycles(endpoint, params):
    end, code = params["end_date"], params["index_code"]
    members = [_TWO_CYCLE_OLD1, _TWO_CYCLE_OLD2]
    if code == "000300.SH":
        if end >= "20260601":
            members = members + [_ADD_A_JUN]
        if end >= "20261201":
            members = members + [_ADD_A_DEC]
    elif code == "000905.SH":
        if end >= "20260601":
            members = members + [_ADD_B_JUN]
        if end >= "20261201":
            members = members + [_ADD_B_DEC]
    return pd.DataFrame({"index_code": code, "con_code": members, "trade_date": end, "weight": 1.0})


def test_t_cell_pools_by_rebalance_and_index_not_index_alone(tmp_path, monkeypatch):
    daily = _lake_two_cycles(tmp_path, monkeypatch)
    doc = cen.run_census(start="2026-06", end="2026-12", lake_daily=daily, fetch=_fetch_two_cycles,
                         indexes={"000300.SH": "沪深300", "000905.SH": "中证500"})
    assert doc["events"] == [
        {"A": "20260529", "E": "20260612", "indexes": ["沪深300", "中证500"]},
        {"A": "20261127", "E": "20261211", "indexes": ["沪深300", "中证500"]},
    ]
    e1 = doc["tables"]["add"]["5.E-1"]
    assert e1["n"] == 4 and e1["n_cells"] == 4              # 两次调样 × 两个指数,一格一票
    assert e1["exc_pp"] == pytest.approx(-2.5, abs=1e-6)     # mean(-2,-6,+2,-4)% = -2.5%
    assert e1["t_cell"] == pytest.approx(-1.46, abs=0.01)


def test_calendar_gap_surfaces_a_missing_lake_day(tmp_path, monkeypatch):
    """`lake_trade_days()` 只列现存 parquet,不查真实交易日历(design docstring 明示)。窗口内
    删掉一天的 parquet,后面每个相位的位置偏移会整体挪位——本测试不断言那个挪位(修正它超出本
    任务范围),只锁住「这件事在产物里可见」:日历(声称原本 22 天都该在)比湖内实际天数多 1。"""
    daily = _lake(tmp_path, monkeypatch)
    (daily / "20260605.parquet").unlink()
    doc = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_index_weight,
                         calendar_fetch=_calendar_full, indexes={"000300.SH": "沪深300"})
    gap = doc["calendar_gaps"][0]
    assert gap["lake_days"] + 1 == gap["calendar_days"]
    assert gap["shortfall"] == 1
    md = cen.render(doc)
    assert "⚠ 缺1日" in md


def test_calendar_gap_is_none_when_calendar_is_unreachable(tmp_path, monkeypatch):
    """日历取不到(缺省 `calendar_fetch=None`,或查失败)→ `calendar_days`/`shortfall` 记 None,
    不能读成「查过=0 缺口」——「没检查」与「检查过、是好消息」是两个不同的世界。"""
    daily = _lake(tmp_path, monkeypatch)
    doc = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_index_weight,
                         indexes={"000300.SH": "沪深300"})               # calendar_fetch 缺省 → 不查
    gap = doc["calendar_gaps"][0]
    assert gap["calendar_days"] is None and gap["shortfall"] is None
    md = cen.render(doc)
    assert "未检查" in md

    def _raises(endpoint, params):
        raise RuntimeError("交易日历不可达(限频/断网)")

    doc2 = cen.run_census(start="2026-06", end="2026-06", lake_daily=daily, fetch=_fetch_index_weight,
                          calendar_fetch=_raises, indexes={"000300.SH": "沪深300"})
    assert doc2["calendar_gaps"][0]["calendar_days"] is None
