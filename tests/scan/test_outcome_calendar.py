"""交易日历完整性(Task C1,2026-09-12 独立 P0):T+1/T+2 只能由可信交易日历定义。

design: .superpowers/sdd/2026-09-12-outcome-trading-calendar-integrity/task-C1-brief.md §2/§4

病灶(修复前):`outcome.market_frame` 用湖分区**排序后的位置**取 T+1/T+2 —— 湖缺某天,
后面的文件就顶替成了"T+2",一笔隔夜交易被错记成跨越缺口的多日持仓。本文件锁的是修复后
的契约:日期由**注入的可信日历**(`(start,end)->(sessions,quality)`,与
`exec_anchor.trading_sessions` 同形状)先解析,行情只按解析出的精确日期去读;弱日历
(`lake_partitions`/`weekday_heuristic`)、空日历、请求异常、分析日非交易日、T+2 未到,
一律不放行——只有 `quality=="trade_cal"` 才可信。

覆盖验收矩阵 C01–C09(C10–C14 属 Task C2/C3 的 schema/CSV/migration,不在本文件范围,
也不在 Task C1 范围内)。全部用 `tmp_path` 合成湖 + 合成日历,不读真实 `lake/`、不发网络。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import outcome

# ───────────────────────── 合成湖 ─────────────────────────

def _write_day(root: Path, day: str, bars: dict[str, dict]) -> None:
    """`root/<day>.parquet`;`bars = {code6: {open,high,low,close,pct_chg,amount}}`。"""
    root.mkdir(parents=True, exist_ok=True)
    rows = [{"ts_code": f"{code}.SZ", **bar} for code, bar in bars.items()]
    pd.DataFrame(rows).to_parquet(root / f"{day}.parquet", index=False)


def _bar(o: float, h: float, lo: float, c: float, pct: float, amount: float = 1.0e8) -> dict:
    return {"open": o, "high": h, "low": lo, "close": c, "pct_chg": pct, "amount": amount}


# ───────────────────────── 合成日历 ─────────────────────────

def _calendar(sessions_dashed: list[str], quality: str = "trade_cal"):
    """合成 `(start,end)->(sessions,quality)`;只按请求范围过滤,不做其它推断——
    与 `exec_anchor.trading_sessions` 同形状,`sessions` 为 `YYYY-MM-DD` 已排序去重。"""
    days = sorted(set(sessions_dashed))

    def cal(start: str, end: str):
        return ([d for d in days if start <= d <= end], quality)

    return cal


def _raising_calendar(exc: Exception):
    def cal(start: str, end: str):
        raise exc
    return cal


def _business_days(start: str, end: str, closed: frozenset[str] = frozenset()) -> list[str]:
    """连续自然日窗口内的工作日(`YYYY-MM-DD`),排除 `closed`——测试合成日历的原料
    (不查真实节假日;只是"确定性、可控"的一份合成交易日历,不代表真交易所)。"""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    out: list[str] = []
    cur = s
    while cur <= e:
        iso = cur.isoformat()
        if cur.weekday() < 5 and iso not in closed:
            out.append(iso)
        cur += timedelta(days=1)
    return out


# ───────────────────────── 合成 run(供 compute_outcome/exec_anchor_frame 用) ─────────────────────────

def _run(tmp_path: Path, run_id: str = "20260901_2100", date_str: str = "2026-09-01",
         codes: tuple[str, ...] = ("000001",)) -> Path:
    run = tmp_path / ws.reports_root() / "scan" / run_id
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps(
        {"analysis_date": date_str, "run_id": "20260901T210000000000Z",
         "generated_at": f"{date_str}T21:00:00"}), encoding="utf-8")
    base = run / "trace" / "staging"
    base.mkdir(parents=True)
    lines = "code,name,sector,lane,guard,conviction\n" + "".join(
        f"{c},测试{c},行业{i},,,50\n" for i, c in enumerate(codes))
    (base / "finalists.csv").write_text(lines, encoding="utf-8")
    (base / "_final_ratings.json").write_text(
        json.dumps(dict.fromkeys(codes, "Hold")), encoding="utf-8")
    (base / "_relative_buy_decision.json").write_text(json.dumps(
        {"mode": "active", "rule_version": "e6.v2.0", "blocked": False,
         "buys": [], "candidates": []}), encoding="utf-8")
    return run


# A wide, gap-free business-day calendar reused by most tests (95 real calendar days
# around the 2026-09 fixture dates — comfortably covers T+1..T+10 and the predecessor
# lookback with zero synthetic holidays, so tests only exercise one variable at a time).
_WIDE_SESSIONS = _business_days("2026-08-01", "2026-11-05")


# ───────────────────────── C01 / brief §4 bullet 1(逐字反例) ─────────────────────────

def test_c01_missing_t2_partition_does_not_drift_to_a_later_file(tmp_path):
    """D=20260901,湖有 0901(D)/0902(T+1)/0907(更晚的分区) 但缺 0903(T+2)。

    旧实现:湖分区排序取位置 2 → 会把 0907 冒充成 T+2(一笔隔夜错记成跨 4 天持仓)。
    新实现:T+2 必须仍是日历算出的 0903;该日无湖分区 → MISSING_MARKET_DATA,不漂移。
    """
    root = tmp_path / "lake" / "daily"
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    _write_day(root, "20260902", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})
    # 20260903 故意不写(T+2 缺)
    _write_day(root, "20260907", {"000001": _bar(10.8, 11.2, 10.6, 11.0, 2.0)})
    cal = _calendar(_WIDE_SESSIONS)

    fr, meta = outcome.market_frame("2026-09-01", lake_daily=root, calendar=cal,
                                    today="2026-09-11")

    assert fr is None
    assert meta["outcome_status"] == outcome.MISSING_MARKET_DATA
    assert meta["t1"] == "20260902"
    assert meta["t2"] == "20260903"          # 目标不漂移到 0907
    assert "20260903" in meta["missing_sessions"]
    assert meta["calendar_quality"] == "trade_cal"


# ───────────────────────── C02 / brief §4 bullet 1(缺 T+1 对偶反例) ─────────────────────────

def test_c02_missing_t1_partition_leaves_no_valid_main_return(tmp_path):
    """缺 T+1(0902),T+2(0903)与更晚分区都在 —— 目标仍是 0902/0903,主收益不可有效。"""
    root = tmp_path / "lake" / "daily"
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    # 20260902(T+1)故意不写
    _write_day(root, "20260903", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})
    _write_day(root, "20260904", {"000001": _bar(10.8, 11.2, 10.6, 11.0, 2.0)})
    cal = _calendar(_WIDE_SESSIONS)

    fr, meta = outcome.market_frame("2026-09-01", lake_daily=root, calendar=cal,
                                    today="2026-09-11")

    assert fr is None
    assert meta["outcome_status"] == outcome.MISSING_MARKET_DATA
    assert meta["t1"] == "20260902"
    assert meta["t2"] == "20260903"
    assert "20260902" in meta["missing_sessions"]


# ───────────────────────── C03:弱日历质量,即使日期恰好对齐也不放行 ─────────────────────────

@pytest.mark.parametrize("weak_quality", ["lake_partitions", "weekday_heuristic", "", "stub"])
def test_c03_weak_calendar_quality_is_rejected_even_when_dates_align(tmp_path, weak_quality):
    """`sessions` 逐字与真日历一致,只是 `quality` 不是 `trade_cal` —— 仍必须 UNVERIFIED_CALENDAR。

    不得因为日期"恰好对上"便提高可信度(§2.1 第2条,brief 原话)。
    """
    root = tmp_path / "lake" / "daily"
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    _write_day(root, "20260902", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})
    _write_day(root, "20260903", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})
    cal = _calendar(_WIDE_SESSIONS, quality=weak_quality)

    fr, meta = outcome.market_frame("2026-09-01", lake_daily=root, calendar=cal,
                                    today="2026-09-11")

    assert fr is None
    assert meta["outcome_status"] == outcome.UNVERIFIED_CALENDAR
    assert meta["calendar_quality"] == weak_quality
    assert meta["reason"]


# ───────────────────────── C04:周末 + 长假,交易日历必须准确跳过 ─────────────────────────

def test_c04_weekend_is_skipped_by_the_trusted_calendar(tmp_path):
    """D = 2026-08-28(五)→ T+1 必须是下周一 08-31,不是 08-29(周六)。"""
    cal = _calendar(_WIDE_SESSIONS)
    resolved = outcome.resolve_outcome_sessions("2026-08-28", calendar=cal, today="2026-09-11")
    assert resolved["status"] == "OK"
    assert resolved["t1"] == "20260831"
    assert resolved["t2"] == "20260901"


def test_c04_multiday_holiday_is_skipped_by_the_trusted_calendar(tmp_path):
    """模拟一段跨越工作日的长假(09-28~10-07,十个自然日全部休市)—— T+1/T+2 必须是
    假期结束后的头两个交易日,不能被短窗错位成假期中间的某天。"""
    closed = frozenset(_business_days("2026-09-28", "2026-10-07"))
    sessions = [d for d in _WIDE_SESSIONS if d not in closed]
    cal = _calendar(sessions)

    resolved = outcome.resolve_outcome_sessions("2026-09-25", calendar=cal, today="2026-11-01")

    expected_after = [d for d in sessions if d > "2026-09-25"][:2]
    assert resolved["t1"] == expected_after[0].replace("-", "")
    assert resolved["t2"] == expected_after[1].replace("-", "")
    assert resolved["t1"] > "20261007"       # 确认真的跳过了整段假期


# ───────────────────────── C05:日历异常 / 空范围 / 分析日无效,原因各自可见 ─────────────────────────

def test_c05_calendar_exception_is_unverified_with_a_visible_reason(tmp_path):
    cal = _raising_calendar(RuntimeError("tushare token missing"))
    fr, meta = outcome.market_frame("2026-09-01", lake_daily=tmp_path / "lake" / "daily",
                                    calendar=cal, today="2026-09-11")
    assert fr is None
    assert meta["outcome_status"] == outcome.UNVERIFIED_CALENDAR
    assert "tushare token missing" in meta["reason"]


def test_c05_empty_calendar_is_unverified(tmp_path):
    cal = _calendar([])   # quality 声称 trade_cal,但一个 session 都没有
    fr, meta = outcome.market_frame("2026-09-01", lake_daily=tmp_path / "lake" / "daily",
                                    calendar=cal, today="2026-09-11")
    assert fr is None
    assert meta["outcome_status"] == outcome.UNVERIFIED_CALENDAR
    assert meta["reason"]


def test_c05_analysis_date_not_a_trading_day_is_invalid(tmp_path):
    """2026-08-29 是周六,可信日历里没有这一天 —— 状态是"分析日无效",不是"日历不可信"。"""
    cal = _calendar(_WIDE_SESSIONS)
    fr, meta = outcome.market_frame("2026-08-29", lake_daily=tmp_path / "lake" / "daily",
                                    calendar=cal, today="2026-09-11")
    assert fr is None
    assert meta["outcome_status"] == outcome.INVALID_ANALYSIS_DATE
    assert meta["reason"]


def test_c05_incomplete_range_is_unverified_not_invalid_date(tmp_path):
    """日历只答得出一天、既不含 D 本身也没有任何 D 之后的 session —— 两个失败条件同时
    成立(覆盖不足 ∧ D 不在日历里),必须是覆盖不足先判(UNVERIFIED_CALENDAR),不能被
    "D 不在日历里"抢先判成 INVALID_ANALYSIS_DATE(§2.1 第3条:「不能据弱/窄日历下此
    结论」)。

    夹具刻意不含 D("2026-08-31" ≠ D)——旧夹具 `_calendar(["2026-09-01"])` 把 D 自己
    放进了返回列表,"D not in sessions" 分支因此永远不会触发,调换 `resolve_outcome_sessions`
    里两个 `if` 判定的先后顺序也不会让那个版本的测试变红(已用变异探针验证,见 C1 fix
    report)。这一版才是真的锁住判定顺序的反例。
    """
    cal = _calendar(["2026-08-31"])
    fr, meta = outcome.market_frame("2026-09-01", lake_daily=tmp_path / "lake" / "daily",
                                    calendar=cal, today="2026-09-11")
    assert fr is None
    assert meta["outcome_status"] == outcome.UNVERIFIED_CALENDAR


# ───────────────────────── C06:T+2 尚未到,即便测试环境里已有"未来"分区 ─────────────────────────

def test_c06_future_lake_partitions_do_not_mature_early(tmp_path):
    root = tmp_path / "lake" / "daily"
    cal = _calendar(_WIDE_SESSIONS)
    # 故意把 T+1/T+2 的分区都写好(模拟测试/回填环境里意外存在的"未来"文件)。
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    _write_day(root, "20260902", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})
    _write_day(root, "20260903", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})

    fr, meta = outcome.market_frame("2026-09-01", lake_daily=root, calendar=cal,
                                    today="2026-09-01")   # today = D 当天,T+2 显然没到

    assert fr is None
    assert meta["outcome_status"] == outcome.PENDING_SESSION
    assert meta["t1"] == "20260902" and meta["t2"] == "20260903"


# ───────────────────────── C07:分区齐全,但个股停牌/缺价 → 行级 null ─────────────────────────

def test_c07_suspended_stock_gets_row_level_null_not_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "lake" / "daily"
    cal = _calendar(_WIDE_SESSIONS)
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0),
                                  "000002": _bar(20.0, 20.5, 19.8, 20.2, 1.0)})
    _write_day(root, "20260902", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})   # 000002 当天停牌
    _write_day(root, "20260903", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0),
                                  "000002": _bar(20.2, 20.6, 20.0, 20.4, 1.0)})
    run = _run(tmp_path, codes=("000001", "000002"))

    doc = outcome.compute_outcome(run, lake_daily=root, calendar=cal, today="2026-09-11")

    assert doc is not None
    row1 = doc["rows"]["000001"]
    row2 = doc["rows"]["000002"]
    assert row1[outcome.MAIN] is not None
    assert row2[outcome.MAIN] is None            # 停牌 → null,不是 0、不是已成交
    assert row2["t1_close"] is None


# ───────────────────────── C08:迟到执行锚的前驱用日历,不滑到更早的湖分区 ─────────────────────────
#
# Fix round 1(review finding 1):`exec_anchor_frame` 现在返回 `(fr, meta)`,`meta` 必须让
# 三个曾经统统坍缩成 `None` 的状态互相可辨:①根本没有 exec_lag(正常 run,无需算)
# ②前驱 session 本身在可信日历里都定位不出来 ③前驱定位到了,但它自己的 T+1/T+2 行情
# 缺失/未成熟/日历不可信——这第三种直接复用 `market_frame` 对前驱日算出的
# `outcome_status`,与主尺五态同一份词表。`exec_outcome_status` 恒与主尺 `outcome_status`
# 正交:各自独立调用各自的 `market_frame`,互不传染。

def test_c08_late_anchor_predecessor_never_slides_to_an_earlier_lake_file(tmp_path):
    """真前驱(08-26)缺湖;更早的 08-25 反而有文件 —— 绝不能滑过去冒用它。

    前驱日期本身被可信日历正确定位(08-26),只是它自己的湖分区缺失 ——
    对应三个坍缩状态里的第③种:`exec_outcome_status == MISSING_MARKET_DATA`。
    """
    root = tmp_path / "lake" / "daily"
    cal = _calendar(_WIDE_SESSIONS)
    _write_day(root, "20260825", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    # 20260826(真前驱)故意不写
    _write_day(root, "20260827", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})
    _write_day(root, "20260828", {"000001": _bar(10.8, 11.2, 10.6, 11.0, 2.0)})
    execution = {"first_available_session": "2026-08-27", "exec_lag": 1}

    fr, meta = outcome.exec_anchor_frame(execution, lake_daily=root, calendar=cal,
                                         today="2026-09-11")

    assert fr is None      # 前驱 08-26 缺湖 → 无法核验,exec_gap_c1_o2 应保持 null,不得回退到 08-25
    assert meta["exec_outcome_status"] == outcome.MISSING_MARKET_DATA
    assert meta["reason"]


def test_c08_late_anchor_predecessor_resolves_correctly_when_lake_has_it(tmp_path):
    """正例:前驱日真有湖数据时,反事实帧必须用日历算出的那一天,不是巧合对上。

    成功路径:`exec_outcome_status == MATURE`(与主尺共用同一份状态词表)。
    """
    root = tmp_path / "lake" / "daily"
    cal = _calendar(_WIDE_SESSIONS)
    _write_day(root, "20260826", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})   # 真前驱
    _write_day(root, "20260827", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})  # first_available_session
    _write_day(root, "20260828", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})  # 前驱的 T+2
    execution = {"first_available_session": "2026-08-27", "exec_lag": 1}

    fr, meta = outcome.exec_anchor_frame(execution, lake_daily=root, calendar=cal,
                                         today="2026-09-11")

    assert fr is not None
    assert "000001" in fr.index
    assert fr.loc["000001", outcome.MAIN] == pytest.approx(10.5 / 10.4 - 1.0)
    assert meta["exec_outcome_status"] == outcome.MATURE


def test_c08_no_exec_lag_returns_none_without_touching_calendar(tmp_path):
    """正常 run(`exec_lag` 为 0/缺失)—— 与主帧逐字相同,不必另算,函数应直接返回 None。

    对应三个坍缩状态里的第①种:`exec_outcome_status is None`(不适用,不是失败)——
    这与②③用主尺同一份状态词表字符串必须是可辨的两类值(`None` vs 字符串)。
    """
    cal = _calendar(_WIDE_SESSIONS)
    fr1, meta1 = outcome.exec_anchor_frame(
        {"first_available_session": "2026-08-27", "exec_lag": 0},
        lake_daily=tmp_path / "lake" / "daily", calendar=cal, today="2026-09-11")
    assert fr1 is None and meta1["exec_outcome_status"] is None

    fr2, meta2 = outcome.exec_anchor_frame(
        None, lake_daily=tmp_path / "lake" / "daily", calendar=cal, today="2026-09-11")
    assert fr2 is None and meta2["exec_outcome_status"] is None


def test_c08_predecessor_itself_unverifiable_on_a_weak_calendar(tmp_path):
    """预驱日期的**定位**本身就失败(日历质量不可信)—— 对应三个坍缩状态里的第②种,
    必须与"定位到了、只是它自己缺湖"(第③种,`MISSING_MARKET_DATA`)是不同的状态值,
    否则调用方无法区分"日历没答对"和"日历答对了但当天没数据"这两类完全不同的问题。
    """
    cal = _calendar(_WIDE_SESSIONS, quality="lake_partitions")   # 弱质量 → 前驱定位不出来
    execution = {"first_available_session": "2026-08-27", "exec_lag": 1}

    fr, meta = outcome.exec_anchor_frame(execution, lake_daily=tmp_path / "lake" / "daily",
                                         calendar=cal, today="2026-09-11")

    assert fr is None
    assert meta["exec_outcome_status"] == outcome.UNVERIFIED_CALENDAR
    assert meta["reason"]


def test_exec_anchor_failure_never_downgrades_the_mature_main_ruler(tmp_path, monkeypatch):
    """正交性(review finding 1 末条):exec 侧算不出来,绝不能把已经判定 MATURE 的
    主尺拖下水——两者是各自独立的 `market_frame` 调用,互不传染。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "lake" / "daily"
    cal = _calendar(_WIDE_SESSIONS)
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    _write_day(root, "20260902", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})
    _write_day(root, "20260903", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})
    run = _run(tmp_path, codes=("000001",))
    # 手工植入一个"迟到"execution 块,指向一个湖里完全没有数据的窗口——exec 侧必然
    # MISSING_MARKET_DATA,但不该动主尺(0901 → 0902/0903)一根毫毛。
    late_target = next(d for d in _WIDE_SESSIONS if d > "2026-10-01")
    man = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    man["execution"] = {"schema_version": 1, "analysis_date": "2026-09-01",
                        "first_available_session": late_target, "exec_lag": 1,
                        "actionability_status": "LATE_REVALIDATION_REQUIRED",
                        "ready_quality": "measured"}
    (run / "manifest.json").write_text(json.dumps(man), encoding="utf-8")

    doc = outcome.compute_outcome(run, lake_daily=root, calendar=cal, today="2026-11-01")

    assert doc is not None
    row = doc["rows"]["000001"]
    # `_num()` 落盘前四舍五入到 6 位小数,容差必须比这更松,否则是断言写法的问题不是行为的问题。
    assert row[outcome.MAIN] == pytest.approx(10.5 / 10.4 - 1.0, abs=1e-6)  # 主尺分毫不受影响
    assert row["exec_gap_c1_o2"] is None                               # exec 侧确实拿不到数
    assert row["exec_outcome_status"] == outcome.MISSING_MARKET_DATA


# ───────────────────────── C09:主尺成熟,5/10 日窗口缺日 → 主尺保留,旁列 null ─────────────────────────

def test_c09_mature_main_ruler_survives_a_missing_5_10_day_window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "lake" / "daily"
    cal = _calendar(_WIDE_SESSIONS)
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    _write_day(root, "20260902", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})
    _write_day(root, "20260903", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})
    # 刻意不写 T+5(20260908)/T+10(20260915)—— 模拟窗口内缺日,而不是"还没到"。
    run = _run(tmp_path, codes=("000001",))

    doc = outcome.compute_outcome(run, lake_daily=root, calendar=cal, today="2026-09-20")

    assert doc is not None
    row = doc["rows"]["000001"]
    assert row[outcome.MAIN] is not None          # 隔夜主尺不受拖累
    assert row["fwd_5_oc"] is None                # 旁列未核验 → null
    assert row["fwd_10_oc"] is None


def test_c09_mature_main_ruler_survives_5_10_day_window_not_yet_matured(tmp_path, monkeypatch):
    """孪生场景:文件本身不缺,只是 T+5/T+10 那天压根还没到——同样必须 null,不得提前成熟。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "lake" / "daily"
    cal = _calendar(_WIDE_SESSIONS)
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    _write_day(root, "20260902", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})
    _write_day(root, "20260903", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})
    _write_day(root, "20260904", {"000001": _bar(10.8, 11.2, 10.6, 11.0, 2.0)})
    run = _run(tmp_path, codes=("000001",))

    doc = outcome.compute_outcome(run, lake_daily=root, calendar=cal, today="2026-09-04")

    assert doc is not None
    row = doc["rows"]["000001"]
    assert row[outcome.MAIN] is not None
    assert row["fwd_5_oc"] is None
    assert row["fwd_10_oc"] is None


# ───────────────────────── 回归/契约:正常路径 + 默认日历接线 + ruling #6 ─────────────────────────

def test_happy_path_is_mature_with_the_correct_gap_value(tmp_path):
    """基线:数据齐全、日历可信、T+2 已到 —— 必须 MATURE,且 gap 值与手算一致(未被本次改动带偏)。"""
    root = tmp_path / "lake" / "daily"
    cal = _calendar(_WIDE_SESSIONS)
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    _write_day(root, "20260902", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})
    _write_day(root, "20260903", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})

    fr, meta = outcome.market_frame("2026-09-01", lake_daily=root, calendar=cal,
                                    today="2026-09-11")

    assert meta["outcome_status"] == outcome.MATURE
    assert fr.loc["000001", outcome.MAIN] == pytest.approx(10.5 / 10.4 - 1.0)
    assert meta["calendar_quality"] == "trade_cal"
    assert meta["calendar_digest"]
    assert meta["missing_sessions"] == []


def test_market_frame_defaults_calendar_to_exec_anchor_trading_sessions(tmp_path, monkeypatch):
    """context 项 5:`calendar=None` 时惰性默认到 `exec_anchor.trading_sessions`。"""
    from autoresearch.scan import exec_anchor as ea

    monkeypatch.setattr(ea, "trading_sessions", lambda start, end: (
        [d for d in _WIDE_SESSIONS if start <= d <= end], "trade_cal"))
    root = tmp_path / "lake" / "daily"
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    _write_day(root, "20260902", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})
    _write_day(root, "20260903", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})

    fr, meta = outcome.market_frame("2026-09-01", lake_daily=root, today="2026-09-11")

    assert meta["outcome_status"] == outcome.MATURE


def test_missing_trusted_calendar_source_blocks_maturity_ruling6(tmp_path, monkeypatch):
    """Ruling #6:没有可信日历来源(如缺 tushare token,`trading_sessions` 退到
    weekday_heuristic)→ 新结果一律 UNVERIFIED_CALENDAR,账本停止成熟,不静默放行。"""
    from autoresearch.scan import exec_anchor as ea

    monkeypatch.setattr(ea, "trading_sessions", lambda start, end: (
        _business_days(start, end), "weekday_heuristic"))
    root = tmp_path / "lake" / "daily"
    _write_day(root, "20260901", {"000001": _bar(10.0, 10.5, 9.8, 10.2, 2.0)})
    _write_day(root, "20260902", {"000001": _bar(10.2, 10.6, 10.0, 10.4, 2.0)})
    _write_day(root, "20260903", {"000001": _bar(10.5, 10.9, 10.3, 10.7, 2.0)})

    fr, meta = outcome.market_frame("2026-09-01", lake_daily=root, today="2026-09-11")

    assert fr is None
    assert meta["outcome_status"] == outcome.UNVERIFIED_CALENDAR


def test_resolve_outcome_sessions_is_a_pure_date_function(tmp_path):
    """`resolve_outcome_sessions` 不接受 `lake_daily`、不读任何路径 —— 纯日期解析。"""
    cal = _calendar(_WIDE_SESSIONS)
    result = outcome.resolve_outcome_sessions("2026-09-01", calendar=cal, today="2026-09-11")
    assert result["status"] == "OK"
    assert result["t1"] == "20260902" and result["t2"] == "20260903"
    assert result["calendar_quality"] == "trade_cal"
    assert result["calendar_digest"]
    assert "lake_daily" not in result
