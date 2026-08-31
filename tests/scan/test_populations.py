"""反事实人口(G3,2026-08-28 §2.4):两层人口 + 阶段 KPI。

判据围绕「这段代码如果写错了、写反了、或者干脆删掉,测试会不会红」写。四件真风险:

① **两套口径**:主尺读数必须与 `research.edge_census` 同源(同日同家族六位小数相等)——
   删掉买腿 `entry_tradable` 过滤就会分叉,这里有一条专门的探针把分叉量出来;
② **`UNKNOWN` 被折成 `False`**:老 run 只有 finalists,没人证明过它没进 L2;折成 False
   等于替一份不存在的证据作证;
③ **偷读共享 staging**:同数据日重跑会原地覆盖它(实测 08-13/08-18 被影子回放改写)。
   测试往共享 staging 里放一份**内容相反**的决策文件,读了就红;
④ **一枚总 `complete` 冻死 fwd5/10**:gap 成熟不得让 `fwd_10_oc` 停在缺失。

全部 `tmp_path` + 合成湖 + 合成冻结 run,零网络;`tests/scan/conftest.py` 的写保护护栏
另外挡住任何落到真实 `reports_*` 的写。
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from autoresearch.common import ruler as _ruler, workspace as ws
from autoresearch.research import edge_census as ec
from autoresearch.scan import populations as P

DAYS = ["20260803", "20260804", "20260805", "20260806", "20260807", "20260810",
        "20260811", "20260812", "20260813", "20260814", "20260817", "20260818",
        "20260819", "20260820", "20260821", "20260824", "20260825"]
#: 80 只票 —— `_winners` 的截面门是 `edge_census.MIN_CROSS_SECTION`(50),少于它不算。
CODES = [f"{600000 + i:06d}" for i in range(78)] + ["001283", "300857"]
FLAT = {"open": 10.0, "close": 10.0, "high": 10.5, "low": 9.5, "pct_chg": 0.0}


# ───────────────────────── 合成湖 ─────────────────────────

def _lake(tmp_path, *, days=None, gaps=None, sealed=(), fwd5=None, fwd10=None):
    """合成湖:默认全平(gap=0),`gaps` 指定某些票在 D+2 开盘相对 D+1 收盘的跳空。

    锚点固定在 `DAYS[0]` 作为分析日 D:D+1=`DAYS[1]`、D+2=`DAYS[2]`、D+5=`DAYS[5]`、
    D+10=`DAYS[10]`。这样每把尺的成熟日在测试里是**可数的**,不是猜的。
    """
    days = list(days if days is not None else DAYS)
    root = tmp_path / "lake" / "daily"
    root.mkdir(parents=True, exist_ok=True)
    for i, day in enumerate(days):
        rows = []
        for code in CODES:
            bar = dict(FLAT)
            if i == 2 and gaps and code in gaps:            # D+2 开盘 → gap_c1_o2
                bar["open"] = 10.0 * (1 + gaps[code])
                bar["high"] = max(bar["high"], bar["open"] * 1.01)
            if i == 5 and fwd5 and code in fwd5:            # D+5 收盘 → fwd_5_oc
                bar["close"] = 10.0 * (1 + fwd5[code])
            if i == 10 and fwd10 and code in fwd10:         # D+10 收盘 → fwd_10_oc
                bar["close"] = 10.0 * (1 + fwd10[code])
            if i == 1 and code in sealed:
                # T+1 收盘封涨停 → 买不进(`buyable_c1=False`)。主板 600xxx 板幅 10%。
                bar |= {"close": 11.0, "high": 11.0, "low": 10.9, "pct_chg": 10.0}
            rows.append({"ts_code": f"{code}.SH", "trade_date": day,
                         "vol": 1e5, "amount": 1e6, **bar})
        pd.DataFrame(rows).to_parquet(root / f"{day}.parquet", index=False)
    return root


# ───────────────────────── 合成冻结 run ─────────────────────────

def _write_csv(path, header, rows):
    lines = [",".join(header)]
    lines += [",".join("" if v is None else str(v) for v in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(tmp_path, run_id="20260803_2100", date="2026-08-03", *, base_rel="trace/staging",
         top1000=None, l2=None, kept=None, cut=None, judged=None, finalists=None,
         bench=None, pinned=(), seats=(), ratings=None, early=None, e6=None,
         scored_full=True, dup_code=None, shared_e6=None):
    """一个最小的**已发布** run。`base_rel` 决定冻结副本落在哪一级。"""
    run = tmp_path / ws.reports_root() / "scan" / run_id
    run.mkdir(parents=True, exist_ok=True)
    (run / "manifest.json").write_text(json.dumps(
        {"analysis_date": date, "run_id": f"{run_id.replace('_', 'T')}00000Z"}),
        encoding="utf-8")
    base = run / base_rel
    base.mkdir(parents=True, exist_ok=True)

    recalled = list(top1000 if top1000 is not None else CODES[:40])
    if scored_full:
        _write_csv(base / "L1_scored_full.csv",
                   ["rank", "recalled", "code", "name", "industry", "composite"],
                   [[i + 1, c in recalled, c, f"名{c}", "钢铁" if i % 2 else "银行",
                     100 - i] for i, c in enumerate(CODES)])
    rows = [[c, f"名{c}", "钢铁" if CODES.index(c) % 2 else "银行", 90 - i,
             "composite", 1, i + 1, c in pinned, ""]
            for i, c in enumerate(recalled)]
    if dup_code:
        rows.append(list(rows[[r[0] for r in rows].index(dup_code)]))
    _write_csv(base / "L1_recall_top1000.csv",
               ["code", "name", "industry", "composite", "recall_channels",
                "n_channels", "best_rank", "pinned", "pinned_note"], rows)

    if l2 is not None:
        _write_csv(base / "L2_gbdt_top200.csv",
                   ["l2_rank", "selection_reason", "code", "name", "industry", "composite"],
                   [[i + 1, "merit", c, f"名{c}", "钢铁", 90 - i] for i, c in enumerate(l2)])
    if kept is not None:
        _write_csv(base / "_l3_pass1_kept.csv", ["code", "selection_reason"],
                   [[c, "merit"] for c in kept])
    if cut is not None:
        _write_csv(base / "_l3_pass1_cut.csv", ["code"], [[c] for c in cut])
    if judged is not None:
        _write_csv(base / "L3_judged_full.csv",
                   ["code", "name", "sector", "conviction", "lane", "finalist"],
                   [[c, f"名{c}", "钢铁", 70, "pinned" if c in pinned else "healthy",
                     c in (finalists or ())] for c in judged])
    if finalists is not None:
        _write_csv(base / "L3_fine_finalists.csv",
                   ["ticker", "code", "name", "sector", "conviction", "lane", "guard"],
                   [[f"{c}.SH", c, f"名{c}", "钢铁", 70,
                     "pinned" if c in pinned else "healthy",
                     P.COMPOSITE_SEAT_GUARD if c in seats else ""] for c in finalists])
    if bench is not None:
        _write_csv(base / "_l3_bench.csv", ["code", "guard"], [[c, ""] for c in bench])
    if ratings is not None:
        (base / "_final_ratings.json").write_text(json.dumps(ratings), encoding="utf-8")
    if early is not None:
        (base / "_early_stop.json").write_text(json.dumps(early), encoding="utf-8")
    if e6 is not None:
        (base / "_relative_buy_decision.json").write_text(
            json.dumps({"date": date, "mode": "active", **e6}), encoding="utf-8")
    if shared_e6 is not None:
        shared = tmp_path / ws.scan_root() / date
        shared.mkdir(parents=True, exist_ok=True)
        (shared / "_relative_buy_decision.json").write_text(
            json.dumps({"date": date, "mode": "active", **shared_e6}), encoding="utf-8")
    return run


def _full_run(tmp_path, run_id="20260803_2100", date="2026-08-03", **kw):
    """一个「全链在场」的 run:L1→L2→pass1→L3→L4→E6 每一级都有冻结产物。"""
    top = CODES[:40]
    defaults = {
        "top1000": top, "l2": top[:20], "kept": top[:10], "cut": top[10:20],
        "judged": top[:8], "finalists": top[:4], "bench": top[4:8],
        "ratings": {top[0]: "Overweight", top[1]: "Hold",
                    top[2]: "Underweight", top[3]: "Sell"},
        "early": {top[2]: {"phase": "P3", "reason": "基本面恶化"}},
        "e6": {"buys": [{"code": top[0], "rank": 1}],
               "candidates": [{"code": top[0], "eligible": True, "rank": 1},
                              {"code": top[1], "eligible": True, "rank": 2},
                              {"code": top[3], "eligible": False, "rank": None}]},
    }
    defaults.update(kw)
    return _run(tmp_path, run_id, date, **defaults)


# ───────────────────────── 行数与来源 ─────────────────────────

def test_universe_rows_match_frozen_scored_full(tmp_path, monkeypatch):
    """L0 表的行 = `L1_scored_full.csv` 全部过门股(实测 4315 行),**不是** 1000 行。

    读错文件(拿 top1000 当分母)会让 Recall@1000 恒等于 1 —— 这条断言就是那道防线。
    """
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = _full_run(tmp_path)
    table, meta = P.build_universe(run, lake_daily=lake)
    assert len(table) == len(CODES) == meta["n_rows"]
    assert meta["n_in_l1"] == 40                      # top1000 成员数,不是全表
    assert set(table.columns) >= {"code", "sector", "composite", "rank", "in_l1"}


def test_population_rows_match_frozen_top1000(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    table, meta = P.build_population(_full_run(tmp_path), lake_daily=lake)
    assert len(table) == 40 == meta["n_rows"] == meta["n_source_rows"]
    assert int(table["in_l1"].sum()) == 40


def test_duplicate_source_row_is_counted_not_silently_deduped(tmp_path, monkeypatch):
    """tushare 盘后灌数窗口会给同一 code 多写一行(实测 `20260826_2120`:1000 行 / 999 码,
    重复的是 601665)。dict 会静默吃掉它 —— 静默去重正是那次事故的前半段。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = _full_run(tmp_path, dup_code=CODES[3])
    table, meta = P.build_population(run, lake_daily=lake)
    assert meta["n_source_rows"] == 41 and meta["n_rows"] == 40 == len(table)
    assert meta["counts"]["duplicate_source_rows"] == 1


def test_six_digit_codes_keep_leading_zeros(tmp_path, monkeypatch):
    """`001283` 被 pandas 读成 `1283` 是实测过的坑;前导零只有 stdlib csv + zfill 一道防线。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = _full_run(tmp_path, top1000=["001283", *CODES[:20]])
    table, _meta = P.build_population(run, lake_daily=lake)
    assert "001283" in set(table["code"])
    assert "1283" not in set(table["code"])


def test_never_reads_shared_staging(tmp_path, monkeypatch):
    """共享 staging 里放一份**内容相反**的决策文件:读了它 `is_buy` 就会变 True。

    实测 2026-08-13/08-18 两天 brief 印 BLOCKED,而共享 staging 的决策文件被后来的影子
    回放改写成 `buys=[688766]`。把共享 staging 加回读盘链(哪怕只当最后兜底)必红。
    """
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = _full_run(tmp_path, e6=None,
                    shared_e6={"buys": [{"code": CODES[0], "rank": 1}],
                               "candidates": [{"code": CODES[0], "eligible": True,
                                               "rank": 1}]})
    table, meta = P.build_population(run, lake_daily=lake)
    row = table.set_index("code").loc[CODES[0]]
    assert pd.isna(row["is_buy"]) and pd.isna(row["e6_candidate"])
    assert "_relative_buy_decision.json" in meta["missing"]
    # 每条来源标签都必须落在 run 目录内的三级冻结副本里,一条都不许来自共享 staging
    assert set(meta["origin"].values()) <= set(P.FROZEN_BASES)


def test_reads_trace_whitelist_copy_for_pre_0826_runs(tmp_path, monkeypatch):
    """08-26 之前的 run 没有 `trace/staging/`,L0/L1 只在 `trace/` 白名单副本里
    (实测 `20260825_2149`)。不认这一级 = 一半历史 run 拿不到人口。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = _full_run(tmp_path, base_rel="trace")
    table, meta = P.build_population(run, lake_daily=lake)
    assert len(table) == 40
    assert meta["origin"]["L1_recall_top1000.csv"] == "trace"


# ───────────────────────── 正交 flags / UNKNOWN ≠ False ─────────────────────────

def test_orthogonal_flags_keep_multiple_identities(tmp_path, monkeypatch):
    """一只票可以**同时**是 finalist + 📌 + BUY。`recommendations.csv` 的互斥 `role`
    只会留下一个身份 —— 这正是本表存在的理由。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    code = CODES[0]
    run = _full_run(tmp_path, pinned=(code,))
    table, _meta = P.build_population(run, lake_daily=lake)
    row = table.set_index("code").loc[code]
    assert bool(row["is_finalist"]) and bool(row["is_pinned"]) and bool(row["is_buy"])
    assert bool(row["in_l1"]) and bool(row["in_l2"]) and bool(row["pass1_kept"])
    assert row["terminal_stage"] == "E6" and row["terminal_disposition"] == "BUY"


@pytest.mark.parametrize("index,stage,disposition", [
    (0, "E6", "BUY"),                     # rank1
    (1, "E6", "ELIGIBLE_NOT_BOUGHT"),     # 在池但没被挑
    (3, "E6", "INELIGIBLE"),              # 进了 E6 候选但被硬门否
    (2, "L4", "REJECTED"),                # 早停 + UW,没进 E6 候选
    (5, "L3", "BENCH"),                   # judged 但不是 finalist
    (12, "L2", "CUT_AT_PASS1"),           # 进菜单、pass1 切掉
    (25, "L1", "CUT_AT_L2"),              # 进 top1000、没进菜单
])
def test_terminal_stage_reconstructs_each_level(tmp_path, monkeypatch, index, stage,
                                                disposition):
    """正交 flags + terminal 两列能还原每一级的去留。改反任何一级的判据都会红。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    table, _meta = P.build_population(_full_run(tmp_path), lake_daily=lake)
    row = table.set_index("code").loc[CODES[index]]
    assert (row["terminal_stage"], row["terminal_disposition"]) == (stage, disposition)


def test_unknown_is_not_false_on_legacy_run(tmp_path, monkeypatch):
    """老 run 只有 finalists:没人证明过这些票**没进** L2/pass1/E6。

    把 `pd.NA` 折成 `False` 就是替一份不存在的证据作证 —— 折叠必红。
    """
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = _run(tmp_path, top1000=CODES[:40], finalists=CODES[:4])
    table, meta = P.build_population(run, lake_daily=lake)
    row = table.set_index("code").loc[CODES[0]]
    for flag in ("in_l2", "pass1_kept", "l3_judged", "e6_candidate", "is_buy"):
        assert pd.isna(row[flag]), f"{flag} 应为 UNKNOWN,不是 False"
    assert bool(row["is_finalist"]) is True          # 能证明的只有 finalist membership
    assert row["terminal_stage"] == "L3" and row["terminal_disposition"] == "FINALIST"
    assert meta["flag_unknown"]["in_l2"] == 40
    assert {"L2_gbdt_top200.csv", "_relative_buy_decision.json"} <= set(meta["missing"])


def test_unknown_does_not_become_cut_at_l2(tmp_path, monkeypatch):
    """没进 finalists 的那些票,在只有 finalists 的老 run 里终点是 `L1/UNKNOWN`,
    **不是** `CUT_AT_L2` —— 后者是一个我们没有证据的断言。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = _run(tmp_path, top1000=CODES[:40], finalists=CODES[:4])
    table, _meta = P.build_population(run, lake_daily=lake)
    row = table.set_index("code").loc[CODES[30]]
    assert (row["terminal_stage"], row["terminal_disposition"]) == ("L1", "UNKNOWN")


def test_orphan_downstream_code_enters_with_in_l1_false(tmp_path, monkeypatch):
    """下游冒出 L1 之外的票(实测早期 run 有 45 只)不许静默丢:一只被 BUY 过的孤儿
    不进表,`e6_buy_minus_pool` 就少了它,而 counts 一切正常。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    orphan = CODES[70]
    run = _full_run(tmp_path, top1000=CODES[:40],
                    finalists=[*CODES[:4], orphan])
    table, meta = P.build_population(run, lake_daily=lake)
    assert meta["counts"]["orphans"] == 1
    row = table.set_index("code").loc[orphan]
    assert bool(row["in_l1"]) is False and bool(row["is_finalist"]) is True


# ───────────────────────── 口径同源(防两套尺) ─────────────────────────

def _finalist_mean(table, ruler=P.MAIN):
    mask = table["is_finalist"].fillna(False) & P._plain(table)
    value, _n, _t = P._family_mean(table, mask, ruler)
    return value


def test_main_ruler_matches_edge_census_to_six_decimals(tmp_path, monkeypatch):
    """同一天、同一家族,本表的主尺读数必须与 `edge_census.daily_stats` 六位小数相等。

    这是**防「两套口径」的关键探针**:前向收益走同一份 `factor_lab.forward_returns`、
    买腿折叠走同一个 `ruler.entry_tradable`。任何一处自己另算,这条就红。
    家族里**故意放了一只 T+1 封涨停买不进的票**,让买腿过滤真的有活干。
    """
    monkeypatch.chdir(tmp_path)
    gaps = {CODES[0]: 0.03, CODES[1]: -0.01, CODES[2]: 0.08, CODES[3]: 0.25}
    lake = _lake(tmp_path, gaps=gaps, sealed=(CODES[3],))
    run = _full_run(tmp_path)
    table, _meta = P.build_population(run, lake_daily=lake)

    days = ec.lake_trade_days(lake)
    frame = ec.forward_frame(ec.load_lake_pivots(days, lake), days, DAYS[0])
    census = ec.daily_stats(frame, set(CODES[:4]), P.MAIN)
    assert round(_finalist_mean(table), 6) == round(census["mean"], 6)


def test_entry_filter_is_load_bearing_for_that_parity(tmp_path, monkeypatch):
    """把买腿过滤删掉会得到**另一个数**:上一条断言因此不是一句空话。

    `CODES[3]` T+1 收盘封涨停(买不进,gap +25%),不过滤就会被算进家族均值。
    """
    monkeypatch.chdir(tmp_path)
    gaps = {CODES[0]: 0.03, CODES[1]: -0.01, CODES[2]: 0.08, CODES[3]: 0.25}
    lake = _lake(tmp_path, gaps=gaps, sealed=(CODES[3],))
    table, _meta = P.build_population(_full_run(tmp_path), lake_daily=lake)
    filtered = _finalist_mean(table)
    unfiltered = float(pd.to_numeric(
        table.set_index("code").loc[list(CODES[:4]), P.MAIN]).mean())
    assert not np.isclose(filtered, unfiltered)
    assert bool(table.set_index("code").loc[CODES[3], "buyable_c1"]) is False


# ───────────────────────── 分尺成熟,互不阻塞 ─────────────────────────

def test_gap_matures_while_fwd10_stays_pending(tmp_path, monkeypatch):
    """`outcome.py` 一枚总 `complete` 会在 D+2 之后永久跳过重算,`fwd_5/10` 因此冻成
    缺失。这里 gap 成熟、fwd5/fwd10 各自 PENDING —— 三条腿互不阻塞。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, days=DAYS[:4])          # 湖只到 D+3
    table, meta = P.build_population(_full_run(tmp_path), lake_daily=lake)
    assert set(table[f"status_{P.MAIN}"]) == {P.MATURE}
    assert set(table["status_fwd_5_oc"]) == {P.PENDING}
    assert set(table["status_fwd_10_oc"]) == {P.PENDING}
    assert table[f"matures_on_{P.MAIN}"].iloc[0] == DAYS[2]
    assert meta["matures_on"]["fwd_10_oc"] is None      # 还不知道那天是哪天,不猜


def test_fwd10_turns_mature_once_the_lake_reaches_d10(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, days=DAYS[:11], fwd10={CODES[0]: 0.07})
    table, meta = P.build_population(_full_run(tmp_path), lake_daily=lake)
    row = table.set_index("code").loc[CODES[0]]
    assert row["status_fwd_10_oc"] == P.MATURE
    assert row["fwd_10_oc"] == pytest.approx(0.07)
    assert meta["matures_on"]["fwd_10_oc"] == DAYS[10]


def test_unavailable_is_not_pending_for_a_halted_name(tmp_path, monkeypatch):
    """成熟日在湖里、这只票就是没数(停牌/退市)→ `UNAVAILABLE`(不会变),
    与 `PENDING`(还没到那天,会变)是两件事。折成一个「缺失」= 要么每天重算整段
    历史,要么永远等一个不会来的值。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    frame = pd.read_parquet(lake / f"{DAYS[2]}.parquet")
    frame = frame[frame["ts_code"] != f"{CODES[0]}.SH"]      # D+2 停牌
    frame.to_parquet(lake / f"{DAYS[2]}.parquet", index=False)
    table, _meta = P.build_population(_full_run(tmp_path), lake_daily=lake)
    indexed = table.set_index("code")
    assert indexed.loc[CODES[0], f"status_{P.MAIN}"] == P.UNAVAILABLE
    assert indexed.loc[CODES[1], f"status_{P.MAIN}"] == P.MATURE


def test_build_reruns_while_any_column_is_pending(tmp_path, monkeypatch):
    """gap 成熟不得让整份结果被跳过 —— 只要还有一列 PENDING 就必须重算。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, days=DAYS[:4])
    _full_run(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    assert P.build(reports_root=root, lake_daily=lake)["built"] == 1
    again = P.build(reports_root=root, lake_daily=lake)
    assert again["built"] == 1 and again["skipped"] == 0        # fwd5/10 还 PENDING
    full = _lake(tmp_path, days=DAYS)                          # 湖补到 D+10 之后
    assert P.build(reports_root=root, lake_daily=full)["built"] == 1
    assert P.build(reports_root=root, lake_daily=full)["skipped"] == 1


# ───────────────────────── 落盘:原子 + byte 稳定 ─────────────────────────

def test_write_is_atomic_and_byte_stable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    _full_run(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    P.build(reports_root=root, lake_daily=lake)
    target = P.population_path("20260803_2100", root)
    first = target.read_bytes()
    P.build(reports_root=root, lake_daily=lake)          # 已全熟 → 跳过
    target.unlink()
    P.build(reports_root=root, lake_daily=lake)
    assert target.read_bytes() == first
    assert not list(target.parent.glob("*.tmp"))
    assert json.loads(target.with_suffix(".meta.json").read_text())["n_rows"] == 40


# ───────────────────────── 同日多 run 的 selected view ─────────────────────────

def _two_runs_same_day(tmp_path, lake):
    """同一分析日两个 run:早的 finalist 是 gap=0 的票,晚的是 gap=+3% 的票。"""
    _full_run(tmp_path, "20260803_1800", finalists=CODES[10:14], judged=CODES[10:18],
              bench=CODES[14:18], l2=CODES[:20], kept=CODES[10:20], cut=CODES[:10],
              ratings={}, early={}, e6={"buys": [], "candidates": []})
    _full_run(tmp_path, "20260803_2100")
    root = tmp_path / ws.reports_root() / "scan"
    P.build(reports_root=root, lake_daily=lake)
    return root


def test_select_run_takes_the_last_of_the_session(tmp_path, monkeypatch):
    """同日多 run 时取**当天最后发布**的那个(附录 A 探针的 legacy 口径)。颠倒它
    会换掉整张 stage_rulers 的读数 —— 所以它有一条专门的断言。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, gaps=dict.fromkeys(CODES[:4], 0.03))
    root = _two_runs_same_day(tmp_path, lake)
    _uni, sessions, _meta = P._load_tables(root)
    assert sessions["2026-08-03"]["run_key"].iloc[0] == "20260803_2100"


def test_stage_rulers_reads_the_selected_run_not_the_earlier_one(tmp_path, monkeypatch):
    """两个 run 的 finalist 家族收益不同;颠倒 selected run 会让这个数变成另一个。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, gaps=dict.fromkeys(CODES[:4], 0.03))
    root = _two_runs_same_day(tmp_path, lake)
    rulers = P.stage_rulers(reports_root=root)
    row = rulers[(rulers["metric"] == "l3_finalist_minus_bench")
                 & (rulers["session"] == "2026-08-03")].iloc[0]
    assert row["value"] == pytest.approx(0.03, abs=1e-9)     # 晚的那个 run
    assert row["value"] != pytest.approx(0.0, abs=1e-9)      # 早的那个是 0


# ───────────────────────── 阶段 KPI ─────────────────────────

def _rulers_for(tmp_path, lake, *runs_kw):
    root = tmp_path / ws.reports_root() / "scan"
    for kw in runs_kw:
        _full_run(tmp_path, **kw)
    P.build(reports_root=root, lake_daily=lake)
    frame = P.stage_rulers(reports_root=root)
    return frame[frame["session"] == "ALL"].set_index("metric")


def test_stage_rulers_long_schema_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    _full_run(tmp_path)
    P.build(reports_root=root, lake_daily=lake)
    frame = P.stage_rulers(reports_root=root)
    assert list(frame.columns) == [
        "session", "stage", "metric", "value", "n_days", "n_names", "coverage",
        "ci_low", "ci_high", "status", "metric_definition_version"]
    assert set(frame["metric"]) >= {
        "l1_recall_at_1000", "l2_keep_rate", "l3_finalist_minus_bench",
        "l4_reject_value_gap", "l4_reject_value_fwd10", "e6_buy_minus_pool",
        "exec_line_in_minus_out", "chain_buyday_minus_zeroday"}


def test_l1_recall_counts_only_eligible_and_buyable_winners(tmp_path, monkeypatch):
    """分母 = 当日 L0 eligible ∧ 可买的事后 top-decile 赢家;分子 = 其中被 L1 召回的。

    8 只赢家里 4 只在 top1000 → recall 0.5。把分母换成 top1000 会得到 1.0。
    """
    monkeypatch.chdir(tmp_path)
    winners_in = CODES[:4]
    winners_out = CODES[60:64]
    lake = _lake(tmp_path, gaps=dict.fromkeys([*winners_in, *winners_out], 0.09))
    got = _rulers_for(tmp_path, lake, {})
    assert got.loc["l1_recall_at_1000", "value"] == pytest.approx(0.5)
    assert got.loc["l1_recall_at_1000", "n_names"] == 8
    baseline = 40 / len(CODES)
    assert got.loc["l1_recall_at_1000_lift", "value"] == pytest.approx(0.5 / baseline)


def test_l2_keep_rate_measures_winners_surviving_into_the_menu(tmp_path, monkeypatch):
    """L1 里的事后赢家有多少活到 L2:4 只赢家在 top1000,其中 2 只在 L2(top20)。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, gaps=dict.fromkeys([CODES[0], CODES[1], CODES[25], CODES[26]], 0.09))
    got = _rulers_for(tmp_path, lake, {})
    assert got.loc["l2_keep_rate", "value"] == pytest.approx(0.5)


def test_l3_metric_drops_pinned_and_composite_seats(tmp_path, monkeypatch):
    """📌 保送与 composite 席位**不是排序的产物**(2026-07-16 判例)。把它们算进
    finalist 家族,量到的是持仓/兜底席位的表现,不是 L3 的排序能力。"""
    monkeypatch.chdir(tmp_path)
    gaps = {CODES[0]: 0.02, CODES[1]: 0.02, CODES[2]: 0.20, CODES[3]: 0.20}
    lake = _lake(tmp_path, gaps=gaps)
    got = _rulers_for(tmp_path, lake, {"pinned": (CODES[2],), "seats": (CODES[3],)})
    # 剔干净 → 0.02 − 0.00;不剔 → (0.02+0.02+0.20+0.20)/4 = 0.11
    assert got.loc["l3_finalist_minus_bench", "value"] == pytest.approx(0.02)


def test_l4_reject_value_compares_rejected_against_comparable_full_cards(tmp_path,
                                                                        monkeypatch):
    """被否决家族(早停 ∨ UW/Sell)− 可比满卡家族(Hold/OW/Buy 且未早停)。
    **负 = 否决对了**,且只是 observational。"""
    monkeypatch.chdir(tmp_path)
    gaps = {CODES[0]: 0.04, CODES[1]: 0.06, CODES[2]: -0.02, CODES[3]: 0.00}
    lake = _lake(tmp_path, gaps=gaps)
    got = _rulers_for(tmp_path, lake, {})
    # 否决 = {CODES[2] 早停+UW, CODES[3] Sell} 均值 −0.01;满卡 = {OW 0.04, Hold 0.06} 均值 0.05
    assert got.loc["l4_reject_value_gap", "value"] == pytest.approx(-0.06)
    assert got.loc["l4_reject_value_gap", "metric_definition_version"] == "g3.v1"
    assert got.loc["l4_reject_value_fwd10", "metric_definition_version"] == "g3.v1+block10"


def test_e6_metric_compares_buy_against_the_eligible_pool_only(tmp_path, monkeypatch):
    """BUY − **其余 eligible 池成员**;已被判 ineligible 的票不进分母(§1.4 E6 行)。"""
    monkeypatch.chdir(tmp_path)
    gaps = {CODES[0]: 0.05, CODES[1]: 0.01, CODES[3]: -0.30}
    lake = _lake(tmp_path, gaps=gaps)
    got = _rulers_for(tmp_path, lake, {})
    # 池内只有 CODES[1](0.01);CODES[3] 是 ineligible,混进来会得到 +0.195
    assert got.loc["e6_buy_minus_pool", "value"] == pytest.approx(0.04)


def test_exec_line_metric_stays_inside_the_overnight_buy_candidates(tmp_path, monkeypatch):
    """执行线只在**前夜 BUY 候选**(E6 eligible 池)内比,不拿它量报告候选质量。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, gaps={CODES[0]: 0.05, CODES[1]: 0.01},
                 sealed=(CODES[1],))
    root = tmp_path / ws.reports_root() / "scan"
    _full_run(tmp_path)
    P.build(reports_root=root, lake_daily=lake)
    _uni, sessions, _meta = P._load_tables(root)
    table = sessions["2026-08-03"].set_index("code")
    # 封涨停 → 买不进 → 执行线不成立;它同时被买腿过滤剔出家族,所以两侧不会都在场
    assert table.loc[CODES[1], "exec_ok"] == 0.0
    assert table.loc[CODES[0], "exec_ok"] == 1.0
    got = P.stage_rulers(reports_root=root)
    row = got[(got["metric"] == "exec_line_in_minus_out") & (got["session"] == "ALL")]
    assert row["status"].iloc[0] in {P.MATURE, P.UNAVAILABLE}


# ───────────────────────── 0-BUY 日绝不能被凭空造出来 ─────────────────────────

def test_missing_e6_file_is_never_counted_as_a_zero_buy_day(tmp_path, monkeypatch):
    """没跑 / 决策文件读不到的日子,系统并**没有决定空仓**。把它们算进 0-BUY 家族,
    等于给空仓成绩单塞进一批从来没有发生过的决定(§1.4:「`NO_RUN` 不能算 0-BUY」)。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, gaps=dict.fromkeys(CODES[:4], 0.02))
    root = tmp_path / ws.reports_root() / "scan"
    _full_run(tmp_path, "20260803_2100", "2026-08-03")                  # BUY 日
    _full_run(tmp_path, "20260804_2100", "2026-08-04", e6=None)         # E6 缺席
    P.build(reports_root=root, lake_daily=lake)
    _uni, sessions, _meta = P._load_tables(root)
    assert P.zero_buy_status(sessions["2026-08-04"]) == "UNKNOWN_E6"
    got = P.stage_rulers(reports_root=root)
    row = got[(got["metric"] == "chain_buyday_minus_zeroday")
              & (got["session"] == "ALL")].iloc[0]
    assert row["status"] == P.UNAVAILABLE and pd.isna(row["value"])


def test_real_zero_buy_day_is_measured(tmp_path, monkeypatch):
    """真有 E6 决策且 `buys=[]` → 才是 0-BUY 日,可以进对照。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, gaps=dict.fromkeys(CODES[:4], 0.02))
    root = tmp_path / ws.reports_root() / "scan"
    _full_run(tmp_path, "20260803_2100", "2026-08-03")
    _full_run(tmp_path, "20260804_2100", "2026-08-04",
              e6={"buys": [], "candidates": [{"code": CODES[1], "eligible": True,
                                              "rank": 1}]})
    P.build(reports_root=root, lake_daily=lake)
    _uni, sessions, _meta = P._load_tables(root)
    assert P.zero_buy_status(sessions["2026-08-03"]) == "BUY_DAY"
    assert P.zero_buy_status(sessions["2026-08-04"]) == "ZERO_BUY"
    got = P.stage_rulers(reports_root=root)
    row = got[(got["metric"] == "chain_buyday_minus_zeroday")
              & (got["session"] == "ALL")].iloc[0]
    assert row["status"] == P.MATURE and row["n_days"] == 2


def test_zero_buy_status_never_invents_a_reason_for_an_empty_table(tmp_path):
    assert P.zero_buy_status(pd.DataFrame()) == "NO_RUN"


# ───────────────────────── 统计口径 ─────────────────────────

def test_interval_reuses_date_cluster_bootstrap(tmp_path):
    """区间必须来自 `common.stats.date_cluster_bootstrap`(paired unit = 扫描日),
    不许在本模块里再写一套行级 bootstrap。"""
    rows = [{"session": d, "value": v} for d, v in zip(DAYS[:6], [0.01, 0.02, -0.01, 0.00, 0.03, 0.01],
                                                    strict=True)]
    interval = P._interval(rows, P.MAIN)
    assert interval.n_clusters == 6 and "date_cluster_bootstrap" in interval.method
    assert interval.lo < interval.point < interval.hi


def test_overlapping_horizons_use_block_clusters(tmp_path):
    """`fwd_10_oc` 的相邻分析日窗口重叠 10 个 session → 区间按 block 聚簇,
    不是逐日。block=1 会把重叠样本当独立样本,区间窄到假。"""
    rows = [{"session": d, "value": 0.01 * i} for i, d in enumerate(DAYS[:12])]
    daily = P._interval(rows, P.MAIN)
    blocked = P._interval(rows, "fwd_10_oc")
    assert daily.n_clusters == 12 and blocked.n_clusters == 2
    assert P._definition_version("fwd_10_oc") == "g3.v1+block10"
    assert P._definition_version(P.MAIN) == "g3.v1"


def test_single_day_gets_no_interval(tmp_path, monkeypatch):
    """一天的数据没有跨日方差 —— 给它一个区间就是伪精确(`stats` 的既定语义)。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, gaps=dict.fromkeys(CODES[:4], 0.02))
    got = _rulers_for(tmp_path, lake, {})
    assert pd.isna(got.loc["l3_finalist_minus_bench", "ci_low"])
    assert got.loc["l3_finalist_minus_bench", "n_days"] == 1


# ───────────────────────── 边界 / 引擎隔离 / CLI ─────────────────────────

def test_engine_roots_are_never_hardcoded():
    """引擎隔离:模块里不得出现 `reports_claude` / `context_codex` 这类裸根字面量,
    一律走 `workspace`。跨引擎根读一次就是把两个引擎的账混在一起。"""
    source = pathlib.Path(P.__file__).read_text(encoding="utf-8")
    for engine in ws.ENGINES:
        assert f"reports_{engine}" not in source
        assert f"context_{engine}" not in source


def test_missing_l1_sources_return_none_not_a_fabricated_table(tmp_path, monkeypatch):
    """缺源 → `None` + `reason`,不产出一张伪造的空表(空表会被下游读成「那天没人」)。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = tmp_path / ws.reports_root() / "scan" / "20260803_2100"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-08-03"}),
                                       encoding="utf-8")
    table, meta = P.build_population(run, lake_daily=lake)
    assert table is None and meta["reason"] == "NO_L1_TOP1000"
    table, meta = P.build_universe(run, lake_daily=lake)
    assert table is None and meta["reason"] == "NO_L1_SCORED_FULL"


def test_run_key_records_both_identities(tmp_path, monkeypatch):
    """G0 的 canonical identity(engine, capsule_run_id)尚未实施:文件名先用
    `report_dir_id`,但 capsule id 与 `identity_quality` 作为列一起落表,可无损重键。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = _full_run(tmp_path)
    table, meta = P.build_population(run, lake_daily=lake)
    assert P.run_key(run) == "20260803_2100" == meta["report_dir_id"]
    assert meta["capsule_run_id"] and meta["identity_quality"] == "full"
    assert set(table["capsule_run_id"]) == {meta["capsule_run_id"]}


def test_legacy_run_without_capsule_id_is_labelled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = _full_run(tmp_path)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-08-03"}),
                                       encoding="utf-8")
    _table, meta = P.build_population(run, lake_daily=lake)
    assert meta["identity_quality"] == "legacy" and meta["capsule_run_id"] == ""


def test_relative_columns_come_from_outcome_single_source(tmp_path, monkeypatch):
    """三个相对列分母各不相同、**列名即口径**;这里只确认它们真的来自 `outcome`
    那一份实现(自己重推一套 = 同一列名下两种含义)。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path, gaps={CODES[0]: 0.05})
    table, _meta = P.build_population(_full_run(tmp_path), lake_daily=lake)
    frame, meta = P.ruler_frame("2026-08-03", lake_daily=lake,
                                sectors=dict.fromkeys(CODES, "钢铁"))
    assert meta["n"] == len(CODES)
    expected = P._outcome._relative_columns(frame, dict.fromkeys(CODES, "钢铁"))
    got = table.set_index("code")
    for column in (_ruler.REL_MARKET, "excess_med_market"):
        assert got.loc[CODES[0], column] == pytest.approx(expected.loc[CODES[0], column])


def test_cli_build_rulers_and_size(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _lake(tmp_path)
    _full_run(tmp_path)
    assert P.main(["build"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0])["built"] == 1
    assert P.main(["rulers"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out.splitlines()[0])["rows"] > 0
    assert P.stage_rulers_path(tmp_path / ws.reports_root() / "scan").is_file()
    assert P.main(["size", "--run", "20260803_2100"]) == 0
    got = json.loads(capsys.readouterr().out.strip())
    assert got["populations"]["rows"] == 40 and got["universe"]["rows"] == len(CODES)
    assert got["populations"]["bytes"] > 0 and got["populations_build_s"] >= 0


def test_size_does_not_materialise_into_the_ledger(tmp_path, monkeypatch):
    """`size` 是量尺寸,不是物化 —— 它写 `TemporaryDirectory`,账本必须一个字节都没多。"""
    monkeypatch.chdir(tmp_path)
    lake = _lake(tmp_path)
    run = _full_run(tmp_path)
    P.measure_size(run, lake_daily=lake)
    assert not P.ledger_root(tmp_path / ws.reports_root() / "scan").exists()
