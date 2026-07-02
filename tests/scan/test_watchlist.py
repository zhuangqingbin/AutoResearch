"""watchlist 结构化观察单:load/ingest/check 四态/render。合成 fixture,无网络。

spec: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.1
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan.watchlist import (
    WATCHLIST_COLS,
    check,
    ingest_verify,
    load_watchlist,
    render_watchlist_block,
)


def _wl(code, conds, invalidation=None, expiry="2026-12-31", name="X"):
    return {"code": code, "name": name, "born": "2026-06-30", "expiry": expiry,
            "source": "skeptic", "narrative": "n", "conds": json.dumps(conds, ensure_ascii=False),
            "invalidation": json.dumps(invalidation or [], ensure_ascii=False), "note": ""}


def _l1(rows):
    return pd.DataFrame(rows)


def test_load_missing_returns_empty_with_cols(tmp_path):
    df = load_watchlist(tmp_path / "nope.csv")
    assert list(df.columns) == list(WATCHLIST_COLS) and len(df) == 0


def test_ingest_verify_drafts_and_dedupes(tmp_path):
    scan_dir = tmp_path / "2026-06-30"
    scan_dir.mkdir()
    pd.DataFrame([
        {"code": "300476", "verdict": "降级", "bull": "b", "bear": "r",
         "trigger": "8月中报第五次miss或跌破200日线298.52", "consensus": "2/3"},
        {"code": "600000", "verdict": "维持", "bull": "b", "bear": "r", "trigger": "", "consensus": "3/3"},
    ]).to_csv(scan_dir / "verify.csv", index=False)
    wl_path = tmp_path / "watchlist.csv"
    assert ingest_verify(scan_dir, wl_path) == 1          # 只草拟 降级 行
    wl = load_watchlist(wl_path)
    assert len(wl) == 1 and wl.iloc[0]["code"] == "300476"
    assert "中报" in wl.iloc[0]["narrative"] and wl.iloc[0]["born"] == "2026-06-30"
    assert ingest_verify(scan_dir, wl_path) == 0          # 幂等去重
    assert len(load_watchlist(wl_path)) == 1


def test_check_four_states_plus_manual_unknown():
    wl = pd.DataFrame([
        _wl("000001", [{"kind": "close_above", "value": 10}]),                       # 全 yes → 触发
        _wl("000002", [{"kind": "close_above", "value": 10}, {"kind": "ma_bull"}]),  # 1 yes 1 no → 临近
        _wl("000003", [{"kind": "close_above", "value": 10}]),                       # 0 yes → 待触发
        _wl("000004", [{"kind": "ma_bull"}], invalidation=[{"kind": "close_below", "value": 3}]),  # 失效
        _wl("000005", [{"kind": "ma_bull"}], expiry="2026-01-01"),                   # 过期 → 失效
        _wl("000006", [{"kind": "close_above", "value": 10},
                       {"kind": "manual", "text": "中报毛利止跌"}]),                  # 机判全 yes + manual → 触发(待人工项)
        _wl("000007", [{"kind": "money_pos"}]),                                      # 不在 L1 → unknown → 待触发
    ])
    l1 = _l1([
        {"code": "000001", "close": 11.0, "ma_bull": 1, "main_net_ratio": 0.1, "cmf_20": 0.1},
        {"code": "000002", "close": 11.0, "ma_bull": 0, "main_net_ratio": 0.1, "cmf_20": 0.1},
        {"code": "000003", "close": 5.0, "ma_bull": 1, "main_net_ratio": 0.1, "cmf_20": 0.1},
        {"code": "000004", "close": 2.0, "ma_bull": 1, "main_net_ratio": 0.1, "cmf_20": 0.1},
        {"code": "000005", "close": 11.0, "ma_bull": 1, "main_net_ratio": 0.1, "cmf_20": 0.1},
        {"code": "000006", "close": 11.0, "ma_bull": 1, "main_net_ratio": 0.1, "cmf_20": 0.1},
    ])
    st = check(wl, l1, "2026-07-02").set_index("code")
    assert st.at["000001", "status"] == "触发"
    assert st.at["000002", "status"] == "临近"
    assert st.at["000003", "status"] == "待触发"
    assert st.at["000004", "status"] == "失效"
    assert st.at["000005", "status"] == "失效"
    assert st.at["000006", "status"] == "触发(待人工项)"
    assert st.at["000007", "status"] == "待触发" and "unknown" in st.at["000007", "detail"]


def test_render_triggered_first_and_empty():
    st = pd.DataFrame([
        {"code": "1", "name": "乙", "status": "待触发", "detail": "d", "narrative": "n",
         "born": "2026-06-30", "expiry": "2026-08-14"},
        {"code": "2", "name": "甲", "status": "触发", "detail": "d", "narrative": "n",
         "born": "2026-06-30", "expiry": "2026-08-14"},
    ])
    s = render_watchlist_block(st)
    assert "👀 观察单日检" in s and s.index("甲") < s.index("乙")
    assert render_watchlist_block(st.iloc[0:0]) == ""
