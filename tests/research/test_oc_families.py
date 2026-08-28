#!/usr/bin/env python3
"""隔夜集中信号普查 · 16 格族定义 + 读数渲染(零网络、合成面板/事件表)。

本文件锁的是**格定义**,不是统计 —— 格错了整份读数就是错的,而且错得很像对的:
时点差一天、单位差 1000 倍、人口门多一个 `buyable_c1`,读数表照样印得整整齐齐。
所以这里的用例大多是**变异探针**:把口径改坏,断言必须变红。

`core.py` 归属主 A(并发实施)。它还没落地时,本文件按 `CONTRACT.md` 装一份等价桩;
落地后自动用真身。所有断言只依赖契约行为,不依赖桩的实现细节。
"""
from __future__ import annotations

import importlib
import json
import re
import sys
import types

import pandas as pd
import pytest

# ── core 桩(必须在 import families/render 之前装好)────────────────────────────────────

_CORE = "autoresearch.research.overnight_census.core"
_NEED = ("exec_tier", "classify_seat", "adjust_ex_div", "CAP_FLOOR_YI", "COST_PP",
         "CI_LOWER_PP", "JUDGE_YEARS", "MIN_EVENTS", "MIN_DAYS_EVENT", "MIN_DAYS_WIDE",
         "MIN_YEARS_SAME_SIGN", "SEAT_MATCH_MIN")


def _stub_exec_tier(limit, last_time, open_times, fd_amount, amount):
    if limit is None or str(limit) not in ("U", "Z"):
        return None
    if str(limit) == "Z":
        return "T0"
    late = False
    try:
        late = str(int(float(last_time))).zfill(6) >= "143000"
    except (TypeError, ValueError):
        late = False
    opened = False
    try:
        opened = float(open_times) >= 1
    except (TypeError, ValueError):
        opened = False
    thin = False
    try:
        thin = float(amount) > 0 and float(fd_amount) / float(amount) <= 0.10
    except (TypeError, ValueError, ZeroDivisionError):
        thin = False
    return "T1" if (late or opened or thin) else "T2"


def _stub_classify_seat(exalter, youzi):
    s = "" if exalter is None else str(exalter)
    if s == "机构专用":
        return "inst"
    if s in ("沪股通专用", "深股通专用"):
        return "north"
    return "youzi" if any(k.strip() and k.strip() in s for k in (youzi or ())) else "other"


def _stub_adjust_ex_div(open_t2, close_t1, stk_div, cash_div_tax):
    try:
        c = float(close_t1)
    except (TypeError, ValueError):
        return None
    if not c > 0 or pd.isna(c):
        return None
    o = float(open_t2 or 0.0)
    s = 0.0 if stk_div is None or pd.isna(stk_div) else float(stk_div)
    d = 0.0 if cash_div_tax is None or pd.isna(cash_div_tax) else float(cash_div_tax)
    return ((o * (1.0 + s) + d) / c - 1.0) * 100.0


def _install_core_stub() -> None:
    try:
        real = importlib.import_module(_CORE)
        if all(hasattr(real, a) for a in _NEED):
            return
    except ImportError:
        pass
    pkg = importlib.import_module("autoresearch.research.overnight_census")
    m = types.ModuleType(_CORE)
    m.COST_PP, m.CI_LOWER_PP = 0.15, 0.15
    m.JUDGE_YEARS = ("2022", "2023", "2024", "2025")
    m.MIN_YEARS_SAME_SIGN, m.MIN_EVENTS = 3, 300
    m.MIN_DAYS_EVENT, m.MIN_DAYS_WIDE = 60, 200
    m.CAP_FLOOR_YI, m.SEAT_MATCH_MIN = 30.0, 0.30
    m.exec_tier, m.classify_seat, m.adjust_ex_div = (_stub_exec_tier, _stub_classify_seat,
                                                     _stub_adjust_ex_div)
    sys.modules[_CORE] = m
    pkg.core = m


_install_core_stub()

from autoresearch.research.overnight_census import core, families, render  # noqa: E402

# ── 合成面板 ──────────────────────────────────────────────────────────────────────

DAYS = ["20260803", "20260804", "20260805", "20260806", "20260807",
        "20260810", "20260811", "20260812"]
CODES = ["000001", "000002", "600000", "600519"]
TS = {c: f"{c}.{'SZ' if c.startswith('0') else 'SH'}" for c in CODES}

#: 设计稿 §3.3 的格清单 —— **写死**。少一格 / 多一格 / 改名 都必须让这条用例变红。
EXPECTED_FAMILIES = {
    "F1a", "F1b", "F1c",
    "F2a", "F2b", "F2c1", "F2c2", "F2d1", "F2d2", "F2e",
    "F3a", "F3b", "F3c", "F3d", "F3e",
    "F4",
}
EXPECTED_MAIN = {"F1a", "F2a", "F2b", "F3a", "F3b", "F4"}


def make_panel(days=None, codes=None, over=None) -> pd.DataFrame:
    """全网格合成面板(列名照 CONTRACT 的 `panel.build_panel`)。

    `over` = {(date, code): {列: 值}} 的逐格覆盖;`in_pop` 恒由 ST/北交所/市值/可买四条重算,
    不让测试写出一个自相矛盾的面板(那会让「人口门」类断言变成假绿灯)。
    """
    days, codes = days or DAYS, codes or CODES
    rows = []
    for di, d in enumerate(days):
        for ci, c in enumerate(codes):
            rows.append({
                "date": d, "code": c, "ts_code": TS.get(c, f"{c}.SZ"),
                "gap_pp": 0.5 * (ci + 1) - 0.25 * (di % 3), "rel_gap_pp": 0.1 * (ci + 1),
                "buyable_c1": True, "unsellable_o2": False,
                "close_d": 10.0, "amount_d": 100_000.0,          # 千元 → 1 亿元
                "t1_pct_chg": 1.0, "t1_pos_in_range": 0.5, "exec_ok": True,
                "pct_5d_pp": 3.0, "turnover_rate": 1.0, "total_mv_yi": 100.0,
                "vol20": 2.0, "limit_5d": False, "is_st": False,
            })
    p = pd.DataFrame(rows)
    for (d, c), vals in (over or {}).items():
        m = (p["date"] == d) & (p["code"] == c)
        for k, v in vals.items():
            p.loc[m, k] = v
    p["in_pop"] = (~p["is_st"].astype(bool)
                   & ~p["ts_code"].str.endswith(".BJ")
                   & (p["total_mv_yi"] >= core.CAP_FLOOR_YI)
                   & p["buyable_c1"].astype(bool))
    return p


def empty_events() -> dict[str, pd.DataFrame]:
    return {k: pd.DataFrame() for k in ("disclosure", "share_float", "dividend", "top_inst",
                                        "block_trade", "moneyflow", "hk_hold", "limit")}


def cell(cells: list[dict], family: str) -> dict:
    return next(c for c in cells if c["family"] == family)


def pairs(cells: list[dict], family: str) -> set[tuple[str, str]]:
    r = cell(cells, family)["rows"]
    return set() if r.empty else set(zip(r["date"], r["code"], strict=False))


def limit_row(day: str, code: str, *, limit="U", limit_times=1, last_time="145500",
              open_times=0, fd_amount=1e6, amount=1e9, industry="半导体") -> dict:
    return {"trade_date": day, "ts_code": TS[code], "code": code, "limit": limit,
            "limit_times": limit_times, "last_time": last_time, "open_times": open_times,
            "fd_amount": fd_amount, "amount": amount, "industry": industry}


def top_inst_row(day: str, code: str, exalter: str, net_buy: float) -> dict:
    return {"trade_date": day, "ts_code": TS[code], "code": code, "exalter": exalter,
            "buy": max(net_buy, 0.0), "sell": 0.0, "net_buy": net_buy}


YOUZI = {"章盟主", "上海溧阳路"}


# ── 1. 格清单 ─────────────────────────────────────────────────────────────────────

def test_cell_inventory_is_exactly_the_designed_16():
    cells = families.all_cells(make_panel(), empty_events(), youzi=YOUZI)
    assert [c["family"] for c in cells] == list(families.CELL_ORDER)
    assert {c["family"] for c in cells} == EXPECTED_FAMILIES
    assert len(cells) == 16
    assert {c["family"] for c in cells if c["kind"] == "main"} == EXPECTED_MAIN
    assert sum(1 for c in cells if c["kind"] == "main") == 6
    assert sum(1 for c in cells if c["kind"] == "exploratory") == 10


def test_timing_and_sample_kind_labels():
    cells = families.all_cells(make_panel(), empty_events(), youzi=YOUZI)
    for c in cells:
        assert c["timing"] == ("X" if c["family"].startswith("F3") else "R")
        assert c["sample_kind"] == ("wide" if c["family"] == "F4" else "event")
        assert {"date", "code", "gap_pp", "rel_gap_pp"}.issubset(c["rows"].columns)
    assert cell(cells, "F3a")["tier"] == "T1"
    assert cell(cells, "F3b")["tier"] == "T0"
    assert cell(cells, "F3c")["tier"] == "T1"
    assert cell(cells, "F3e")["tier"] == "T2"
    assert all("生产不可见" in c["notes"] for c in cells if c["family"].startswith("F3"))


# ── 2. 时点对齐(本模块最容易错的地方)──────────────────────────────────────────────

def test_x_family_joins_next_day_limit_table():
    """X 族读 **D+1** 的 `limit_list_d`。

    合成集里 `000001` 只在 DAYS[1] 涨停、`000002` 只在 DAYS[0] 涨停:
      * 读对(D+1)→ F3a 里出现 (DAYS[0], 000001);`000002` 无 D 可挂(DAYS[0] 没有前一日)→ 不出现;
      * 读错(D 当日)→ 会出现 (DAYS[1], 000001) 与 (DAYS[0], 000002)。两种错都被下面三条挡住。
    """
    ev = empty_events()
    ev["limit"] = pd.DataFrame([limit_row(DAYS[1], "000001"), limit_row(DAYS[0], "000002")])
    got = pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F3a")
    assert (DAYS[0], "000001") in got                     # D+1 = DAYS[1] 涨停 → 扫描日 DAYS[0]
    assert (DAYS[1], "000001") not in got                 # 读成 D 当日表就会命中这条
    assert not any(c == "000002" for _, c in got)         # 它的涨停日没有对应的 D


def test_r_family_joins_same_day_top_inst():
    """R 族读 **D 当日**的 `top_inst`(D 盘后已知)—— 不左移也不右移。"""
    ev = empty_events()
    ev["top_inst"] = pd.DataFrame([top_inst_row(DAYS[2], "000001", "章盟主专用", 5e6)])
    got = pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F2a")
    assert got == {(DAYS[2], "000001")}


def test_f1_event_family_matches_event_date_two_days_ahead():
    """事件族按**事件日 == D+2** 反查扫描日:解禁日在 DAYS[4] → 建仓夜是 DAYS[2]。"""
    ev = empty_events()
    ev["share_float"] = pd.DataFrame([{"ts_code": TS["000001"], "code": "000001",
                                       "ann_date": DAYS[0], "float_date": DAYS[4],
                                       "float_ratio": 3.0, "holder_name": "甲"}])
    got = pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F1b")
    assert got == {(DAYS[2], "000001")}


def test_f1b_ratio_threshold_and_holder_dedup():
    """`float_ratio` 逐持有人求和后判 1%;完全相同的重复行不重复计。"""
    base = {"ts_code": TS["000001"], "code": "000001", "ann_date": DAYS[0],
            "float_date": DAYS[4], "holder_name": "甲", "float_ratio": 0.4}
    other = dict(base, holder_name="乙", float_ratio=0.7)
    ev = empty_events()
    ev["share_float"] = pd.DataFrame([base, dict(base), other])       # 甲 重复两次
    got = pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F1b")
    assert got == {(DAYS[2], "000001")}                               # 0.4 + 0.7 = 1.1% ≥ 1%
    ev["share_float"] = pd.DataFrame([base, dict(base)])              # 只有 0.4%
    assert pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F1b") == set()


# ── 3. F1a 必须用 pre_date(变异探针)────────────────────────────────────────────────

def _disclosure(pre: str, actual: str) -> pd.DataFrame:
    return pd.DataFrame([{"ts_code": TS["000001"], "code": "000001", "end_date": "20260630",
                          "ann_date": DAYS[0], "pre_date": pre, "actual_date": actual}])


def test_f1a_uses_pre_date_not_actual_date():
    """`pre_date`(D 已知的预约披露日)↔ `actual_date`(事后才知道)互换 → 主格 rows 必须改变。

    这是本波最贵的一条:拿 `actual_date` 建仓 = 用明天的信息选今天的票,读数会好看得离谱
    而且**看不出来**(表照印,只是每格都多了一点未来)。
    """
    ev = empty_events()
    ev["disclosure"] = _disclosure(pre=DAYS[2], actual=DAYS[4])
    a = pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F1a")
    ev["disclosure"] = _disclosure(pre=DAYS[4], actual=DAYS[2])       # 互换
    b = pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F1a")
    assert a == {(DAYS[0], "000001")}
    assert b == {(DAYS[2], "000001")}
    assert a != b


def test_f1a_pit_filter_drops_records_announced_after_d():
    """`ann_date > D` 的记录在 D 当时还不存在 —— 不得进主格。"""
    ev = empty_events()
    df = _disclosure(pre=DAYS[4], actual=DAYS[4])
    df.loc[:, "ann_date"] = DAYS[3]                        # 公告日 > 扫描日 DAYS[2]
    ev["disclosure"] = df
    assert pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F1a") == set()
    df.loc[:, "ann_date"] = DAYS[1]                        # 公告日 ≤ 扫描日 → 可用
    ev["disclosure"] = df
    assert pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F1a") == {(DAYS[2], "000001")}


# ── 4. F3 人口门不要求 buyable_c1 ──────────────────────────────────────────────────

def test_f3_population_keeps_sealed_stock_that_all_other_cells_must_drop():
    """一只 D+1 封板(`buyable_c1=False`)的票:**必须在 F3a 里**(它正是生产看不见的镜像人口),
    **不得**出现在任何 F1/F2/F4 格里(那些格量的是产品真能买到的东西)。"""
    day, code = DAYS[2], "000001"
    panel = make_panel(over={(day, code): {"buyable_c1": False}})
    ev = empty_events()
    ev["limit"] = pd.DataFrame([limit_row(DAYS[3], code)])                       # D+1 封板
    ev["top_inst"] = pd.DataFrame([top_inst_row(day, code, "章盟主专用", 5e6)])    # 同日也上榜
    ev["disclosure"] = pd.DataFrame([{"ts_code": TS[code], "code": code, "ann_date": DAYS[0],
                                      "end_date": "20260630", "pre_date": DAYS[4],
                                      "actual_date": DAYS[4]}])
    cells = families.all_cells(panel, ev, youzi=YOUZI)
    assert (day, code) in pairs(cells, "F3a")
    for fam in ("F1a", "F1b", "F1c", "F2a", "F2b", "F2c1", "F2c2", "F2d1", "F2d2", "F2e", "F4"):
        assert (day, code) not in pairs(cells, fam), fam


def test_f3_still_applies_st_bj_and_cap_floor():
    """F3 松的只有 `buyable_c1` —— ST / 北交所 / 市值地板一条都不松。"""
    day = DAYS[2]
    panel = make_panel(over={(day, "000001"): {"is_st": True},
                             (day, "000002"): {"total_mv_yi": core.CAP_FLOOR_YI - 1}})
    ev = empty_events()
    ev["limit"] = pd.DataFrame([limit_row(DAYS[3], "000001"), limit_row(DAYS[3], "000002"),
                                limit_row(DAYS[3], "600000")])
    got = pairs(families.all_cells(panel, ev, youzi=YOUZI), "F3a")
    assert got == {(day, "600000")}


# ── 5. n_sealed_dropped ───────────────────────────────────────────────────────────

def test_n_sealed_dropped_counts_what_the_population_gate_removed():
    """08-08 判例:被 `buyable_c1` 剔掉的多是继续封板的票,剔了会美化账本 → 必须显式计数。"""
    day = DAYS[2]
    panel = make_panel(over={(day, "000002"): {"buyable_c1": False}})
    ev = empty_events()
    ev["top_inst"] = pd.DataFrame([top_inst_row(day, "000001", "章盟主专用", 5e6),
                                   top_inst_row(day, "000002", "章盟主专用", 5e6)])
    c = cell(families.all_cells(panel, ev, youzi=YOUZI), "F2a")
    assert set(zip(c["rows"]["date"], c["rows"]["code"], strict=False)) == {(day, "000001")}
    assert c["n_sealed_dropped"] == 1


# ── 6. 单位换算(忘了折算就会误判的构造)────────────────────────────────────────────

def test_top_inst_net_buy_yuan_vs_amount_thousand_yuan():
    """`net_buy` 是**元**、面板 `amount_d` 是**千元**。忘了 ×1000 → 比值大 1000 倍。

    `000002` 的真实占比 0.01%(远不到 3%),但漏折算会算成 10% → 被误选。
    `000001` 是真达标的对照(5%),保证这条用例不是靠「全都不选」蒙混过关。
    """
    day = DAYS[2]
    panel = make_panel(over={(day, "000001"): {"amount_d": 100_000.0},      # 1e8 元
                             (day, "000002"): {"amount_d": 1_000_000.0}})   # 1e9 元
    ev = empty_events()
    ev["top_inst"] = pd.DataFrame([
        top_inst_row(day, "000001", "章盟主专用", 5e6),      # 5e6 / 1e8 = 5%   → 选
        top_inst_row(day, "000002", "章盟主专用", 1e5),      # 1e5 / 1e9 = 0.01% → 不选
    ])                                                       # 漏折算:1e5/1e6 = 10% → 会误选
    assert pairs(families.all_cells(panel, ev, youzi=YOUZI), "F2a") == {(day, "000001")}


def test_block_trade_wan_yuan_vs_amount_thousand_yuan():
    """`block_trade.amount` 是**万元**、`vol` 是**万股**:VWAP = amount/vol(同表不换算),
    但与面板成交额比大小前必须双双折算到元。漏折算 → 大宗金额小 10000 倍,永远过不了 1% 量门。"""
    day = DAYS[2]
    panel = make_panel(over={(day, "000001"): {"amount_d": 100_000.0, "close_d": 9.5}})
    ev = empty_events()
    ev["block_trade"] = pd.DataFrame([{"trade_date": day, "ts_code": TS["000001"],
                                       "code": "000001", "price": 10.0, "vol": 20.0,
                                       "amount": 200.0}])   # 200 万元 = 2e6 元 = 成交额 2%
    cells = families.all_cells(panel, ev, youzi=YOUZI)
    assert pairs(cells, "F2d1") == {(day, "000001")}         # VWAP 10 ≥ close 9.5 → 溢价
    assert pairs(cells, "F2d2") == set()


def test_block_trade_discount_cell_needs_five_percent():
    day = DAYS[2]
    panel = make_panel(over={(day, "000001"): {"amount_d": 100_000.0, "close_d": 11.0}})
    ev = empty_events()
    ev["block_trade"] = pd.DataFrame([{"trade_date": day, "ts_code": TS["000001"],
                                       "code": "000001", "price": 10.0, "vol": 20.0,
                                       "amount": 200.0}])   # VWAP 10 vs close 11 → 折 9.1%
    cells = families.all_cells(panel, ev, youzi=YOUZI)
    assert pairs(cells, "F2d2") == {(day, "000001")}
    assert pairs(cells, "F2d1") == set()


def test_block_trade_amount_gate_blocks_small_tickets():
    day = DAYS[2]
    panel = make_panel(over={(day, "000001"): {"amount_d": 100_000.0, "close_d": 9.5}})
    ev = empty_events()
    ev["block_trade"] = pd.DataFrame([{"trade_date": day, "ts_code": TS["000001"],
                                       "code": "000001", "price": 10.0, "vol": 0.5,
                                       "amount": 5.0}])     # 5 万元 = 成交额 0.05% < 1%
    assert pairs(families.all_cells(panel, ev, youzi=YOUZI), "F2d1") == set()


# ── 7. 缺表 / 缺列 ────────────────────────────────────────────────────────────────

def test_missing_tables_give_empty_rows_and_a_note_not_an_exception():
    """一张表缺席只该让**那一格**空,并在 `notes` 留痕 —— 不抛、不伪造为零集。"""
    cells = families.all_cells(make_panel(), empty_events(), youzi=YOUZI)
    for c in cells:
        if c["family"] == "F4":
            continue                                        # F4 只吃面板,不吃事件表
        assert c["rows"].empty, c["family"]
        assert "数据缺席" in c["notes"], c["family"]
    assert not cell(cells, "F4")["rows"].empty


def test_missing_events_dict_entirely():
    cells = families.all_cells(make_panel(), {}, youzi=YOUZI)
    assert len(cells) == 16


def test_load_events_on_empty_lake(tmp_path):
    ev = families.load_events(lake_root=tmp_path)
    assert set(ev) == {"disclosure", "share_float", "dividend", "top_inst", "block_trade",
                       "moneyflow", "hk_hold", "limit"}
    assert all(df.empty for df in ev.values())
    cov = families.events_coverage(ev)
    assert all(c["n_files"] == 0 and not c["exists"] for c in cov.values())


def test_load_events_recovers_trade_date_from_partition_filename(tmp_path):
    """湖里的历史 `top_inst` 分区是**窄表**,没有 `trade_date` 列 —— 日期只在文件名里。
    补不出日期的分区会让整个 F2 族静默空掉,所以这条锁死文件名回填。"""
    d = tmp_path / "top_inst"
    d.mkdir(parents=True)
    pd.DataFrame([{"ts_code": "000001.SZ", "exalter": "机构专用",
                   "buy": 1.0, "sell": 0.0, "net_buy": 1.0}]).to_parquet(d / "20260805.parquet")
    ev = families.load_events(lake_root=tmp_path)
    assert ev["top_inst"]["trade_date"].tolist() == ["20260805"]
    assert ev["top_inst"]["code"].tolist() == ["000001"]


def test_panel_missing_required_column_raises():
    p = make_panel().drop(columns=["rel_gap_pp"])
    with pytest.raises(ValueError, match="rel_gap_pp"):
        families.all_cells(p, empty_events(), youzi=YOUZI)


# ── 8. F3 分层 / 共振 / F2 席位 / F4 ────────────────────────────────────────────────

def test_f3_tier_split_across_cells():
    """同一天四只票分别是 首板T1 / 炸板 / 二板T1 / 早封T2 —— 四格各拿一只,互不串。"""
    ev = empty_events()
    ev["limit"] = pd.DataFrame([
        limit_row(DAYS[3], "000001", limit_times=1, last_time="145500"),          # T1 首板
        limit_row(DAYS[3], "000002", limit="Z", limit_times=None, last_time=None),  # T0 炸板
        limit_row(DAYS[3], "600000", limit_times=3, open_times=2),                # T1 二板+
        limit_row(DAYS[3], "600519", limit_times=1, last_time="093000",
                  open_times=0, fd_amount=9e8, amount=1e9),                       # T2 一字厚单
    ])
    cells = families.all_cells(make_panel(), ev, youzi=YOUZI)
    assert pairs(cells, "F3a") == {(DAYS[2], "000001")}
    assert pairs(cells, "F3b") == {(DAYS[2], "000002")}
    assert pairs(cells, "F3c") == {(DAYS[2], "600000")}
    assert pairs(cells, "F3e") == {(DAYS[2], "600519")}


def test_f3d_is_a_same_day_paired_delta():
    """共振增量必须是**同日**配对 delta:共振成员减当日孤板均值,不是两个日期集合的裸均值差。"""
    ev = empty_events()
    ev["limit"] = pd.DataFrame([
        limit_row(DAYS[3], "000001", industry="半导体"),
        limit_row(DAYS[3], "000002", industry="半导体"),
        limit_row(DAYS[3], "600000", industry="半导体"),      # 半导体 3 家 = 共振
        limit_row(DAYS[3], "600519", industry="白酒"),        # 白酒 1 家 = 孤板
    ])
    panel = make_panel()
    c = cell(families.all_cells(panel, ev, youzi=YOUZI), "F3d")
    rows = c["rows"]
    assert set(rows["code"]) == {"000001", "000002", "600000"}
    lone = panel[(panel["date"] == DAYS[2]) & (panel["code"] == "600519")]["gap_pp"].iloc[0]
    for _, r in rows.iterrows():
        assert r["gap_pp"] == pytest.approx(r["gap_raw_pp"] - lone)
    assert "增量" in c["notes"]


def test_f3d_without_a_lone_board_that_day_is_empty_not_zero():
    ev = empty_events()
    ev["limit"] = pd.DataFrame([limit_row(DAYS[3], c, industry="半导体")
                                for c in ("000001", "000002", "600000", "600519")])
    c = cell(families.all_cells(make_panel(), ev, youzi=YOUZI), "F3d")
    assert c["rows"].empty and "配对" in c["notes"]


def test_f2_seat_classes_do_not_leak_into_each_other():
    day = DAYS[2]
    ev = empty_events()
    ev["top_inst"] = pd.DataFrame([
        top_inst_row(day, "000001", "章盟主专用", 5e6),
        top_inst_row(day, "000002", "机构专用", 5e6),
        top_inst_row(day, "600000", "深股通专用", 5e6),
    ])
    cells = families.all_cells(make_panel(), ev, youzi=YOUZI)
    assert pairs(cells, "F2a") == {(day, "000001")}
    assert pairs(cells, "F2b") == {(day, "000002")}
    assert pairs(cells, "F2c1") == {(day, "600000")}


def test_f2_dedupes_on_the_economic_key_before_summing():
    """`reason`/`side` 造成的重复展示不重复计钱 —— 两条完全相同的经济行只算一次。

    2.0% 的单笔重复算成 4.0% 就会跨过 3% 门:去重坏了这条会变红。
    """
    day = DAYS[2]
    row = top_inst_row(day, "000001", "章盟主专用", 2e6)      # 2e6 / 1e8 = 2% < 3%
    ev = empty_events()
    ev["top_inst"] = pd.DataFrame([dict(row, reason="日涨幅偏离值达 7%"),
                                   dict(row, reason="换手率达 20%")])
    assert pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F2a") == set()
    ev["top_inst"] = pd.DataFrame([dict(row, reason="A"), dict(row, reason="B", net_buy=2.1e6)])
    assert pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F2a") == {(day, "000001")}


def test_f1c_gap_is_ex_div_corrected_and_keeps_the_raw_value():
    """除权日开盘天然带缺口 —— 不校正就是在量「除权」而不是「隔夜」。"""
    ev = empty_events()
    ev["dividend"] = pd.DataFrame([{"ts_code": TS["000001"], "code": "000001",
                                    "ann_date": DAYS[0], "ex_date": DAYS[4],
                                    "div_proc": "实施", "stk_div": 0.0,
                                    "cash_div_tax": 0.5, "end_date": "20251231"}])
    panel = make_panel()
    c = cell(families.all_cells(panel, ev, youzi=YOUZI), "F1c")
    assert set(zip(c["rows"]["date"], c["rows"]["code"], strict=False)) == {(DAYS[2], "000001")}
    r = c["rows"].iloc[0]
    close_t1 = panel[(panel["date"] == DAYS[3]) & (panel["code"] == "000001")]["close_d"].iloc[0]
    open_t2 = close_t1 * (1 + r["gap_raw_pp"] / 100.0)
    assert r["gap_pp"] == pytest.approx(core.adjust_ex_div(open_t2, close_t1, 0.0, 0.5))
    assert r["gap_pp"] > r["gap_raw_pp"]                    # 现金分红补回来必然抬高


def test_f1c_skips_unimplemented_plans():
    ev = empty_events()
    ev["dividend"] = pd.DataFrame([{"ts_code": TS["000001"], "code": "000001",
                                    "ann_date": DAYS[0], "ex_date": DAYS[4],
                                    "div_proc": "预案", "stk_div": 0.0,
                                    "cash_div_tax": 0.5, "end_date": "20251231"}])
    assert pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F1c") == set()


def test_f4_conditions_and_wide_sample_kind():
    day = DAYS[2]
    panel = make_panel(over={
        (day, "000001"): {"pct_5d_pp": 3.0},                       # 命中
        (day, "000002"): {"pct_5d_pp": 9.0},                       # 涨太多
        (day, "600000"): {"pct_5d_pp": 3.0, "limit_5d": True},     # 近 5 日有涨停
        (day, "600519"): {"pct_5d_pp": 3.0, "turnover_rate": 99.0, "vol20": 99.0},
    })
    c = cell(families.all_cells(panel, empty_events(), youzi=YOUZI), "F4")
    got = {(d, k) for d, k in zip(c["rows"]["date"], c["rows"]["code"], strict=False)
           if d == day}
    assert got == {(day, "000001")}
    assert c["sample_kind"] == "wide"


def test_rank_cells_respect_min_cross_section(monkeypatch):
    """「当日前 2%」在 4 只票的横截面上等于「当日最大值」—— 默认门下该日不选。"""
    day = DAYS[2]
    ev = empty_events()
    ev["moneyflow"] = pd.DataFrame([
        {"trade_date": day, "ts_code": TS[c], "code": c,
         "buy_elg_amount": v, "sell_elg_amount": 0.0}
        for c, v in zip(CODES, [1000.0, 10.0, 10.0, 10.0], strict=False)])
    assert pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F2e") == set()
    monkeypatch.setattr(families, "MIN_CROSS_SECTION", 2)
    assert pairs(families.all_cells(make_panel(), ev, youzi=YOUZI), "F2e") == {(day, "000001")}


# ── 9. F5 成交层切分 ──────────────────────────────────────────────────────────────

def test_exec_layer_split_matches_panel_exec_ok():
    day = DAYS[2]
    panel = make_panel(over={(day, "000002"): {"exec_ok": False},
                             (day, "600519"): {"exec_ok": False}})
    ev = empty_events()
    ev["top_inst"] = pd.DataFrame([top_inst_row(day, c, "章盟主专用", 5e6) for c in CODES])
    c = cell(families.all_cells(panel, ev, youzi=YOUZI), "F2a")
    w, wo = families.exec_layer_split(c["rows"], panel, family="F2a")
    assert set(w["rows"]["code"]) == {"000001", "600000"}
    assert set(wo["rows"]["code"]) == {"000002", "600519"}
    assert len(w["rows"]) + len(wo["rows"]) == len(c["rows"])
    assert w["family"].startswith("F2a") and "with" in w["family"]


def test_exec_layer_split_without_exec_ok_column_leaves_a_note():
    panel = make_panel().drop(columns=["exec_ok"])
    rows = pd.DataFrame({"date": [DAYS[2]], "code": ["000001"], "gap_pp": [1.0],
                         "rel_gap_pp": [0.5]})
    w, wo = families.exec_layer_split(rows, panel)
    assert w["rows"].empty and wo["rows"].empty
    assert "数据缺席" in w["notes"] and "数据缺席" in wo["notes"]


# ── 10. 渲染 ──────────────────────────────────────────────────────────────────────

def _stats(mean, *, n_events=500, n_days=120, lo=None, hi=None, yearly=None):
    return {"n_events": n_events, "n_days": n_days, "mean_pp": mean,
            "median_pp": mean, "hit": 0.55, "ci_low_pp": lo, "ci_high_pp": hi,
            "net_pp": None if mean is None else mean - core.COST_PP,
            "yearly": yearly or dict.fromkeys(core.JUDGE_YEARS, mean),
            "yearly_sign_ok": True, "half1_pp": mean, "half2_pp": mean,
            "halves_sign_ok": True, "freq_per_week": 4.2, "sample_kind": "event"}


def _rendered_cells():
    cells = families.all_cells(make_panel(), empty_events(), youzi=YOUZI)
    spec = {"F1a": ("正证据", _stats(0.42, lo=0.20, hi=0.70)),
            "F2a": ("显著负", _stats(-0.55, lo=-0.90, hi=-0.20)),
            "F2b": ("未证", _stats(0.05, lo=-0.30, hi=0.40)),
            "F3a": ("样本不足", _stats(None, n_events=3, n_days=2))}
    out = []
    for c in cells:
        v, st = spec.get(c["family"], ("未证", _stats(0.01, lo=-0.2, hi=0.2)))
        out.append({**c, "stats": st, "verdict": v})
    return out


def test_render_markdown_prints_the_multiplicity_line_and_four_verdicts():
    md = render.render_markdown(_rendered_cells(), {})
    assert re.search(r"正证据 \d+ 格 / 共 \d+ 格\(主格 \d+\)", md)
    assert "不显著 ≠ 有 alpha" in md
    # CONTRACT 里逐字钉死的那一行(ASCII 括号/分号,不是全角)
    assert "正证据 1 格 / 共 16 格(主格 6);不显著 ≠ 有 alpha" in md
    for v in ("正证据", "显著负", "未证", "样本不足"):
        assert v in md


def test_render_markdown_flags_significant_negatives_and_ends_with_disclaimer():
    md = render.render_markdown(_rendered_cells(), {})
    neg_line = next(ln for ln in md.splitlines()
                    if ln.startswith("| F2a ") and "显著负" in ln)
    assert render.NEG_MARK in neg_line
    assert md.rstrip().endswith(render.DISCLAIMER)
    assert md.count(render.DISCLAIMER) == 1


def test_render_markdown_counts_track_the_verdicts():
    """变异探针:多判一格正证据,表头那行数字必须跟着变(否则它是个装饰)。"""
    cells = _rendered_cells()
    before = render.render_markdown(cells, {})
    cells[list(families.CELL_ORDER).index("F2b")]["verdict"] = "正证据"
    after = render.render_markdown(cells, {})
    assert "正证据 1 格 / 共 16 格" in before
    assert "正证据 2 格 / 共 16 格" in after


def test_render_markdown_sections_and_meta():
    meta = {"lake_first": "20220302", "lake_last": "20260827", "n_days": 1091,
            "engine": "claude", "run_id": "oc_20260828", "git_sha": "abc1234",
            "seat": {"match_rate": 0.42, "fallback": False, "n": 100, "n_youzi": 20,
                     "n_inst": 30, "n_north": 5},
            "coverage": {"top_inst": {"n_files": 464, "coverage": 0.457, "missing_days": 551,
                                      "first": "20220601", "last": "20260805", "n_rows": 1}},
            "drops": {"clip": 12},
            "f5": [{"family": "F2a", "with": _stats(0.3), "without": _stats(-0.2)}]}
    md = render.render_markdown(_rendered_cells(), meta)
    assert "## 1. 主格" in md and "## 2. exploratory" in md
    assert "## 3. F5" in md and "## 4. meta" in md
    assert "20220302" in md and "abc1234" in md and "top_inst" in md
    assert "0.50" in md                                   # F5 增量 = 0.3 − (−0.2)
    assert str(core.COST_PP) in md


def test_render_markdown_prints_every_cell_including_empty_ones():
    md = render.render_markdown(_rendered_cells(), {})
    for fam in families.CELL_ORDER:
        assert f"| {fam} |" in md


def test_render_json_is_serializable_and_carries_thresholds():
    payload = render.render_json(_rendered_cells(), {"engine": "claude"})
    s = json.dumps(payload, ensure_ascii=False)
    assert len(s) > 100
    assert payload["counts"]["n_cells"] == 16 and payload["counts"]["n_main"] == 6
    assert [c["family"] for c in payload["cells"]] == list(families.CELL_ORDER)
    assert payload["thresholds"]["COST_PP"] == core.COST_PP
    assert payload["cell_order"] == list(families.CELL_ORDER)
    assert all("rows" not in c for c in payload["cells"])


def test_render_handles_empty_input_without_pretending_it_measured_something():
    md = render.render_markdown([], {})
    assert "正证据 0 格 / 共 0 格(主格 0)" in md
    assert md.rstrip().endswith(render.DISCLAIMER)


def test_render_accepts_flat_stats_as_well_as_nested():
    """`stats` 平铺在格 dict 上(另一种组装方式)也要认 —— 两条路必须给同一张表。"""
    nested = _rendered_cells()
    flat = [{**{k: v for k, v in c.items() if k != "stats"}, **c["stats"]} for c in nested]
    assert render.render_markdown(flat, {}) == render.render_markdown(nested, {})


def test_render_json_counts_match_markdown_counts():
    cells = _rendered_cells()
    md, js = render.render_markdown(cells, {}), render.render_json(cells, {})
    assert f"正证据 {js['counts']['正证据']} 格 / 共 {js['counts']['n_cells']} 格" in md


# ── 11. 判读词表兼容(四态 / 五态 / oracle 三态)────────────────────────────────────

def test_render_never_silently_relabels_an_unknown_verdict():
    """`__main__` 走的是设计稿 §2.4 的**五态**(历史正候选/经济性证伪/显著有害/未决/数据不足)
    + oracle 三态,而 `core.judge` 给的是 CONTRACT 的四态。渲染层认不出的词必须**原样印**,
    绝不能悄悄改写成「样本不足」—— 那会把整张表变成一句谎话,而且看不出来
    (08-01「gate_status 读不懂加粗 → 17.4% 的卡被判三门全过」同一族)。
    """
    cells = _rendered_cells()
    five = {"F1a": "历史正候选", "F2a": "显著有害", "F2b": "经济性证伪", "F3a": "数据不足",
            "F3b": "ORACLE_EDGE", "F4": "未决"}
    for c in cells:
        if c["family"] in five:
            c["verdict"] = five[c["family"]]
    md = render.render_markdown(cells, {})
    for word in five.values():
        assert word in md, word
    assert "正证据 1 格 / 共 16 格(主格 6)" in md          # 历史正候选 归入正证据桶
    neg = next(ln for ln in md.splitlines() if ln.startswith("| F2a "))
    assert render.NEG_MARK in neg and "显著有害" in neg    # 显著有害 也要打 🚨
    assert "oracle 正读数 1 格" in md                      # ORACLE_EDGE 不进 H0
    js = render.render_json(cells, {})
    assert js["counts"]["by_verdict"]["历史正候选"] == 1
    assert js["counts"]["by_verdict"]["ORACLE_EDGE"] == 1
    assert js["counts"]["n_oracle_edge"] == 1


def test_render_verdict_bucketing_never_inflates_thin():
    """未知词进「未证」桶,不进「样本不足」桶(否则「没测出来」会被读成「没数据」)。"""
    cells = _rendered_cells()
    for c in cells:
        c["verdict"] = "某个没人见过的词"
    js = render.render_json(cells, {})
    assert js["counts"]["未证"] == 16 and js["counts"]["样本不足"] == 0


def test_f5_cells_mixed_into_the_list_do_not_inflate_the_cell_count():
    """`__main__` 把 F5 直接追加进 cells。它们是**条件层**不是第 17 格 ——
    「共 N 格」必须仍是 16,而且这些格要落进 F5 那一节而不是 exploratory 表。"""
    cells = _rendered_cells()
    extra = [{"family": "F5·F2a·with_exec_ok", "label": "F2a × with", "kind": "exploratory",
              "timing": "X", "tier": None, "sample_kind": "event", "notes": "",
              "n_sealed_dropped": 0, "stats": _stats(0.30), "verdict": "未决"},
             {"family": "F5·F2a·without_exec_ok", "label": "F2a × without", "kind": "exploratory",
              "timing": "X", "tier": None, "sample_kind": "event", "notes": "",
              "n_sealed_dropped": 0, "stats": _stats(-0.20), "verdict": "未决"}]
    md = render.render_markdown(cells + extra, {})
    assert "正证据 1 格 / 共 16 格(主格 6)" in md
    assert "| F2a | 500 | +0.30 | 500 | -0.20 | +0.50 |" in md
    assert "未提供" not in md
    js = render.render_json(cells + extra, {})
    assert js["counts"]["n_cells"] == 16 and js["n_f5"] == 2
