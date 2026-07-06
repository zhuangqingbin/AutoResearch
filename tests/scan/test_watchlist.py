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


def test_express_candidates_and_append(tmp_path):
    """触发直通车:触发* 且不在 finalists → 追加(lane=watchlist_trigger);幂等;已在则跳。

    spec: docs/specs/2026-07-02-scan-portfolio-memory-design.md §1
    """
    from autoresearch.scan.watchlist import append_express, express_candidates
    d = tmp_path / "2026-07-02"
    d.mkdir()
    pd.DataFrame([
        {"code": "300476", "name": "胜宏科技", "status": "触发", "detail": "close_above:314=yes",
         "narrative": "中报beat+站回多头", "born": "2026-06-30", "expiry": "2026-08-14"},
        {"code": "600000", "name": "已入围", "status": "触发", "detail": "", "narrative": "",
         "born": "2026-06-30", "expiry": ""},
        {"code": "000002", "name": "未触发", "status": "临近", "detail": "", "narrative": "",
         "born": "2026-06-30", "expiry": ""},
    ]).to_csv(d / "watchlist_status.csv", index=False)
    pd.DataFrame([{"code": "600000", "name": "已入围", "sector": "银行"}]).to_csv(
        d / "finalists.csv", index=False)
    pd.DataFrame([{"code": "300476", "industry": "PCB"}]).to_csv(
        d / "L1_scored_full.csv", index=False)
    ex = express_candidates(d)
    assert len(ex) == 1 and ex.iloc[0]["code"] == "300476"
    assert ex.iloc[0]["lane"] == "watchlist_trigger" and ex.iloc[0]["sector"] == "PCB"
    assert "观察单触发" in ex.iloc[0]["thesis"]
    assert append_express(d) == 1
    fin = pd.read_csv(d / "finalists.csv", dtype={"code": str})
    assert "300476" in set(fin["code"].astype(str).str.zfill(6)) and len(fin) == 2
    assert append_express(d) == 0                              # 幂等


def test_express_missing_staging_empty(tmp_path):
    from autoresearch.scan.watchlist import express_candidates
    assert len(express_candidates(tmp_path)) == 0


def test_check_remind_gradient_and_by_date():
    wl = pd.DataFrame([
        _wl("000010", [{"kind": "close_above", "value": 10}, {"kind": "ma_bull"},
                       {"kind": "money_pos"}]),                                   # 2/3 yes → 提醒(2/3)
        _wl("000011", [{"kind": "close_above", "value": 10},
                       {"kind": "by_date", "date": "2026-07-04", "text": "中报"}]),  # 机判全 yes+日期锚 → 触发(待人工项)
    ])
    l1 = _l1([
        {"code": "000010", "close": 11.0, "ma_bull": 1, "main_net_ratio": -0.1, "cmf_20": 0.1},
        {"code": "000011", "close": 11.0, "ma_bull": 1, "main_net_ratio": 0.1, "cmf_20": 0.1},
    ])
    st = check(wl, l1, "2026-07-02").set_index("code")
    assert st.at["000010", "status"] == "提醒(2/3)"
    assert st.at["000010", "k"] == 2 and st.at["000010", "n"] == 3
    assert st.at["000011", "status"] == "触发(待人工项)"
    assert "by_date:2026-07-04(⏰临期)" in st.at["000011", "detail"]      # T-3 内标临期
    st2 = check(wl, l1, "2026-07-06").set_index("code")
    assert "⏰已到期待确认" in st2.at["000011", "detail"]


def test_since_born_and_fire():
    row = _wl("000012", [{"kind": "ma_bull"}])
    row["born_price"] = "10.0"
    wl = pd.DataFrame([row])
    l1 = _l1([{"code": "000012", "close": 12.0, "ma_bull": 0,
               "main_net_ratio": 0.1, "cmf_20": 0.1}])
    st = check(wl, l1, "2026-07-02").set_index("code")
    assert abs(st.at["000012", "since_born"] - 0.20) < 1e-9
    assert bool(st.at["000012", "fire"])                                  # +20% 未触发 → 🔥
    s = render_watchlist_block(check(wl, l1, "2026-07-02"))
    assert "🔥" in s and "+20%" in s and "stock-research lite" in s        # C4 文案同步换名


def test_backfill_born_price_from_lake(tmp_path):
    import pandas as _pd

    from autoresearch.scan.watchlist import backfill_born_price
    lake = tmp_path / "daily"
    lake.mkdir()
    _pd.DataFrame([{"ts_code": "300476.SZ", "open": 300.0, "close": 310.0}]).to_parquet(
        lake / "20260630.parquet", index=False)
    wl_path = tmp_path / "watchlist.csv"
    _pd.DataFrame([{**_wl("300476", [{"kind": "ma_bull"}]), "born": "2026-06-30"}]).to_csv(
        wl_path, index=False)
    assert backfill_born_price(path=wl_path, lake=lake) == 1
    wl = load_watchlist(wl_path)
    assert float(wl.iloc[0]["born_price"]) == 310.0


def test_mark_new_vs_prev_day():
    from autoresearch.scan.watchlist import mark_new
    today = pd.DataFrame([{"code": "000001", "k": 2, "n": 3}, {"code": "000002", "k": 1, "n": 2}])
    prev = pd.DataFrame([{"code": "000001", "k": 1, "n": 3}, {"code": "000002", "k": 1, "n": 2}])
    out = mark_new(today, prev).set_index("code")
    assert bool(out.at["000001", "new_k"]) and not bool(out.at["000002", "new_k"])
    out2 = mark_new(today, None).set_index("code")            # 无前日 → 全 False(防首日全🆕噪声)
    assert not out2["new_k"].any()


def test_migrate_manual_dates_to_by_date(tmp_path):
    import json as _json

    import pandas as _pd

    from autoresearch.scan.watchlist import migrate_by_date
    row = _wl("000013", [{"kind": "money_pos"},
                         {"kind": "manual", "text": "08-29中报净利同比转正"}])
    wl_path = tmp_path / "watchlist.csv"
    _pd.DataFrame([row]).to_csv(wl_path, index=False)
    assert migrate_by_date(path=wl_path) == 1
    conds = _json.loads(load_watchlist(wl_path).iloc[0]["conds"])
    bd = [c for c in conds if c["kind"] == "by_date"]
    assert bd and bd[0]["date"] == "2026-08-29" and "中报" in bd[0]["text"]
    assert migrate_by_date(path=wl_path) == 0                             # 幂等
