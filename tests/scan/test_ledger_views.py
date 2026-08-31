"""运行日历三视图(2026-08-28 §2.4 G2):runs / session_calendar / market。

判据围绕这张表存在的理由写,不围绕它的实现写:

① **不许压扁** —— 同日失败→成功两次尝试都要在 `runs.csv`,且 session 选中成功那个;
   周末跑的 run 不许消失(它的 `run_local_date` 根本不是交易日);
② **`NO_RUN` ≠ 0-BUY** —— 混一桶就等于拿"根本没开工的日子"去证明"空仓正确";
③ **口径同源** —— 四把尺必须走 `edge_census` 的湖读取与 `factor_lab._board_limit`,
   本文件因此喂**真 parquet**、不打桩取数层:自己另写一套加载器/板制度的话,
   688 那只 20cm 票会被误判成封板,`n_buyable_c1` 当场对不上;
④ **半张表比没有表危险** —— 中途 kill 之后旧 view 必须完整;
⑤ **引擎隔离** —— 两个引擎各写各根,一个字节都不许越界。

夹具全部落 `tmp_path`(conftest 的 PRODUCTION-PATH-GUARD 会把任何越界写变成
`PermissionError`),真实 `reports_claude/` 全程只读不碰。
"""
from __future__ import annotations

import json
import pathlib
from datetime import date, timedelta

import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import exec_anchor as anchor, ledger_views as lv
from autoresearch.trace.atomic import canonical_json, sha256_bytes

SESSIONS = ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]


@pytest.fixture(autouse=True)
def _no_calendar_leakage_between_tests():
    """`exec_anchor` 的进程级日历缓存按 (start,end) 键 —— 两个 tmp_path 用同一个窗口时,
    第二个测试会读到第一个测试那个湖的日历。清掉,否则用例之间会互相"帮忙"。"""
    anchor._SESSION_CACHE.clear()
    yield
    anchor._SESSION_CACHE.clear()


# ───────────────────────── 夹具 ─────────────────────────

def _scan(tmp_path) -> pathlib.Path:
    return tmp_path / ws.reports_root() / "scan"


def _execution(analysis_date, *, first, approved, status=anchor.ACTIONABLE,
               exec_lag=0, ready_quality="measured") -> dict:
    """一个 G1 时间锚块。写进 manifest 的 `execution` —— `read_execution` 原样返回它,
    所以这里控制的是**真代码路径的输入**,不是被打桩掉的输出。"""
    return {"schema_version": anchor.EXECUTION_SCHEMA_VERSION,
            "analysis_date": analysis_date, "brief_written_at": approved,
            "decision_approved_at": approved, "first_available_session": first,
            "exec_lag": exec_lag, "staleness_sessions": None if exec_lag is None else exec_lag + 1,
            "actionability_status": status, "ready_source": "gate4_approved",
            "ready_quality": ready_quality, "timezone_assumed": anchor.MARKET_TZ}


def _publish(tmp_path, name, *, analysis_date, execution=None,
             capsule_run_id="20260826T111713280877Z", buys=(), finalists=("603317",),
             market_pack=None, business_status="SUCCEEDED", run_mode="FULL") -> pathlib.Path:
    """一个最小的已发布 run 目录(`published_runs` 认它需要目录名合法 + manifest)。"""
    run = _scan(tmp_path) / name
    (run / "trace" / "staging").mkdir(parents=True)
    manifest = {"analysis_date": analysis_date, "generated_at": f"{analysis_date}T20:00:00",
                "business_status": business_status, "evidence_status": "EVIDENCE_COMPLETE"}
    if capsule_run_id:
        manifest["run_id"] = capsule_run_id
    if execution is not None:
        manifest["execution"] = execution
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    base = run / "trace" / "staging"
    (base / "finalists.csv").write_text(
        "code,name,sector,lane,guard,conviction\n"
        + "".join(f"{c},名{c},测试行业,reversion,,55\n" for c in finalists), encoding="utf-8")
    (base / "_relative_buy_decision.json").write_text(json.dumps({
        "mode": "active", "rule_version": "e6.v3.0", "blocked": False,
        "buys": [{"code": c, "rank": 1} for c in buys],
        "candidates": [{"code": c, "eligible": True, "rank": i + 1}
                       for i, c in enumerate(finalists)]}), encoding="utf-8")
    if run_mode:
        (base / "run_mode.json").write_text(json.dumps({"mode": run_mode}), encoding="utf-8")
    if market_pack is not None:
        (base / "market_pack.json").write_text(json.dumps(market_pack), encoding="utf-8")
    return run


def _lake(tmp_path, table: dict[str, list[tuple]]) -> pathlib.Path:
    """真 parquet 数据湖。`table = {"20260824": [(code, open, high, low, close, pct_chg), …]}`。

    **不打桩 `load_lake_pivots`**:打了桩就测不到"四把尺真的走共享读取层"这件事。
    """
    root = tmp_path / "lake" / "daily"
    root.mkdir(parents=True, exist_ok=True)
    for day, rows in table.items():
        frame = pd.DataFrame([{
            "ts_code": f"{code}.{'SH' if code[0] == '6' else 'SZ'}",
            "open": o, "high": h, "low": low, "close": c, "pct_chg": pct, "amount": 1e5,
        } for code, o, h, low, c, pct in rows])
        frame.to_parquet(root / f"{day}.parquet")
    return root


def _flat_lake(tmp_path, sessions, codes=("603317", "000002")):
    """每天平推的湖:所有票 open=high=low=close=10、pct_chg=0 → 四把尺恒为 0。"""
    return _lake(tmp_path, {day.replace("-", ""):
                            [(c, 10.0, 10.0, 10.0, 10.0, 0.0) for c in codes]
                            for day in sessions})


def _rows(path: pathlib.Path) -> list[dict]:
    return lv._load_csv(path)


def _capsule_ledger(tmp_path, rows: list[dict]) -> pathlib.Path:
    """一条合法的 append-only hash 链(`read_valid_ledger` 会校验 prev/row hash 与 revision)。"""
    path = lv._outcome.ledger_root(_scan(tmp_path)) / "run_capsules.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = "0" * 64
    lines = []
    for i, row in enumerate(rows, start=1):
        body = {**row, "prev_hash": previous, "revision": i}
        digest = sha256_bytes(canonical_json(body).encode("utf-8"))
        previous = digest
        lines.append(canonical_json({**body, "row_hash": digest}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ───────────────────────── ① 全部尝试都要活下来 ─────────────────────────

def test_same_day_retry_keeps_both_runs_and_selects_the_succeeded_one(tmp_path, monkeypatch):
    """同日失败→成功:两个 run 都在 `runs.csv`,session 选中**成功**那个。

    刻意让**失败**的那次批准时刻更晚 —— 只按"截止前最后一个"选就会选中它。
    要选对必须真的读 `business_status`。
    """
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    _publish(tmp_path, "20260825-0826_2000", analysis_date="2026-08-25",
             capsule_run_id="20260826T200000000000Z", buys=("603317",),
             execution=_execution("2026-08-25", first="2026-08-26",
                                  approved="2026-08-26T20:00:00"))
    _publish(tmp_path, "20260825-0826_2130", analysis_date="2026-08-25",
             capsule_run_id="20260826T213000000000Z", business_status="FAILED",
             execution=_execution("2026-08-25", first="2026-08-26",
                                  approved="2026-08-26T21:30:00", status=anchor.FAILED))
    lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")

    runs = _rows(lv.views_root(_scan(tmp_path)) / lv.RUNS_CSV)
    assert [r["report_dir_id"] for r in runs] == ["20260825-0826_2000", "20260825-0826_2130"]
    assert {r["analysis_date"] for r in runs} == {"2026-08-25"}      # 同日两行,没被压扁

    sessions = {r["session"]: r for r in _rows(lv.views_root(_scan(tmp_path)) / lv.SESSIONS_CSV)}
    row = sessions["2026-08-26"]
    assert row["selected_run_id"] == "20260825-0826_2000"
    assert row["status"] == lv.SESSION_SELECTED
    assert row["run_ids_ready_before_cutoff"] == (
        "20260825-0826_2000;20260825-0826_2130")                    # 失败那次也列出来


def test_weekend_run_survives_and_lands_on_the_next_session(tmp_path, monkeypatch):
    """周六跑的 run 不许消失:`run_local_date` 是周六(不是交易日),first available 是周一。"""
    monkeypatch.chdir(tmp_path)
    weekend_sessions = SESSIONS + ["2026-08-31"]
    _flat_lake(tmp_path, weekend_sessions)
    _publish(tmp_path, "20260828-0829_1100", analysis_date="2026-08-28",
             buys=("603317",),
             execution=_execution("2026-08-28", first="2026-08-31",
                                  approved="2026-08-29T11:00:00", exec_lag=0))
    lv.build(reports_root=_scan(tmp_path), now="2026-09-01T12:00:00+00:00")

    runs = _rows(lv.views_root(_scan(tmp_path)) / lv.RUNS_CSV)
    assert len(runs) == 1
    assert runs[0]["run_local_date"] == "2026-08-29"            # 周六,湖里根本没有这天
    assert date.fromisoformat(runs[0]["run_local_date"]).weekday() == 5

    sessions = {r["session"]: r for r in _rows(lv.views_root(_scan(tmp_path)) / lv.SESSIONS_CSV)}
    assert "2026-08-29" not in sessions and "2026-08-30" not in sessions
    assert sessions["2026-08-31"]["selected_run_id"] == "20260828-0829_1100"
    assert sessions["2026-08-28"]["status"] == lv.SESSION_NO_RUN


def test_failed_run_without_report_dir_still_gets_a_row(tmp_path, monkeypatch):
    """失败/中断的 run **没有报告目录**,只有 `_failed/` 认识它 —— 它不能从历史里消失。"""
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    failed = _scan(tmp_path) / "_failed" / "20260826T090000000000Z" / "capsule"
    failed.mkdir(parents=True)
    (failed / "failure.json").write_text(json.dumps(
        {"schema_version": 1, "run_id": "20260826T090000000000Z",
         "business_status": "INTERRUPTED"}), encoding="utf-8")
    _publish(tmp_path, "20260825-0826_2000", analysis_date="2026-08-25")
    lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")

    runs = {r["capsule_run_id"]: r for r in _rows(lv.views_root(_scan(tmp_path)) / lv.RUNS_CSV)}
    row = runs["20260826T090000000000Z"]
    assert row["business_status"] == "INTERRUPTED"
    assert row["report_dir_id"] == ""
    assert row["actionability_status"] == anchor.FAILED


def test_sentinel_day_counts_as_a_real_zero_buy_day(tmp_path, monkeypatch):
    """哨兵日的 0 有 `run_mode.json` 背书 —— 写空的话它会整天掉出 0-BUY 桶,
    又变回本模块要修的那个病(§R9「不从产物空否反推」的另一面)。"""
    monkeypatch.chdir(tmp_path)
    _three_kinds_of_day(tmp_path)
    run = _publish(tmp_path, "20260825-0825_2000", analysis_date="2026-08-25",
                   capsule_run_id="20260825T200000000000Z", finalists=(),
                   run_mode="SENTINEL_EMPTY",
                   execution=_execution("2026-08-25", first="2026-08-26",
                                        approved="2026-08-25T20:00:00"))
    (run / "trace" / "staging" / "finalists.csv").unlink()
    (run / "trace" / "staging" / "_relative_buy_decision.json").unlink()
    lv.build(reports_root=_scan(tmp_path), now="2026-08-31T12:00:00+00:00")

    runs = {r["report_dir_id"]: r for r in _rows(lv.views_root(_scan(tmp_path)) / lv.RUNS_CSV)}
    assert runs["20260825-0825_2000"]["run_mode"] == "SENTINEL_EMPTY"
    assert runs["20260825-0825_2000"]["n_buy"] == "0"          # 空产物 + 哨兵 = 真的 0
    buckets = lv.session_buckets(_scan(tmp_path))
    assert buckets["ZERO_BUY"] == [pytest.approx(0.10, abs=1e-6),
                                   pytest.approx(12 / 11 - 1, abs=1e-6)]


def test_missing_products_without_a_sentinel_file_stay_blank(tmp_path, monkeypatch):
    """没有 `run_mode.json` 背书时,空产物只能写空 —— "0 只买" 与 "读不到" 不是一回事。"""
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    run = _publish(tmp_path, "20260825-0826_2000", analysis_date="2026-08-25", run_mode="",
                   execution=_execution("2026-08-25", first="2026-08-26",
                                        approved="2026-08-26T20:00:00"))
    (run / "trace" / "staging" / "finalists.csv").unlink()
    (run / "trace" / "staging" / "_relative_buy_decision.json").unlink()
    lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")
    row = _rows(lv.views_root(_scan(tmp_path)) / lv.RUNS_CSV)[0]
    assert row["n_buy"] == "" and row["n_finalist"] == "" and row["run_mode"] == ""
    assert lv.session_buckets(_scan(tmp_path))["ZERO_BUY"] == []


def test_a_stale_failed_dir_does_not_overturn_a_published_run(tmp_path, monkeypatch):
    """`_failed/<id>/` 的残留目录不许把一个已发布 run 改判成失败。"""
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    _publish(tmp_path, "20260825-0826_2000", analysis_date="2026-08-25",
             capsule_run_id="20260826T200000000000Z",
             execution=_execution("2026-08-25", first="2026-08-26",
                                  approved="2026-08-26T20:00:00"))
    stale = _scan(tmp_path) / "_failed" / "20260826T200000000000Z" / "capsule"
    stale.mkdir(parents=True)
    (stale / "failure.json").write_text(json.dumps({"business_status": "FAILED"}),
                                        encoding="utf-8")
    lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")
    rows = _rows(lv.views_root(_scan(tmp_path)) / lv.RUNS_CSV)
    assert len(rows) == 1 and rows[0]["business_status"] == "SUCCEEDED"
    assert rows[0]["actionability_status"] == anchor.ACTIONABLE


def test_capsule_ledger_terminal_state_beats_the_optimistic_manifest(tmp_path, monkeypatch):
    """manifest 在发布早段就写死 `SUCCEEDED`;终态只有 capsule 账本知道 —— 它说了算。"""
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    _publish(tmp_path, "20260825-0826_2000", analysis_date="2026-08-25",
             capsule_run_id="20260826T200000000000Z",
             execution=_execution("2026-08-25", first="2026-08-26",
                                  approved="2026-08-26T20:00:00"))
    _capsule_ledger(tmp_path, [{
        "schema_version": 1, "run_id": "20260826T200000000000Z",
        "analysis_date": "2026-08-25", "engine": ws.ENGINE,
        "business_status": "FAILED", "evidence_status": "EVIDENCE_INCOMPLETE",
        "final_path": "reports/scan/_failed/20260826T200000000000Z",
        "root_hash": "0" * 64, "archive_hash": None, "durability": "LOCAL",
        "failure_class": "gate4", "archived_at": "2026-08-26T21:00:00"}])
    lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")

    runs = _rows(lv.views_root(_scan(tmp_path)) / lv.RUNS_CSV)
    assert len(runs) == 1 and runs[0]["business_status"] == "FAILED"
    assert runs[0]["actionability_status"] == anchor.FAILED
    sessions = {r["session"]: r for r in _rows(lv.views_root(_scan(tmp_path)) / lv.SESSIONS_CSV)}
    assert sessions["2026-08-26"]["status"] == lv.SESSION_NO_APPROVED_RUN


def test_legacy_run_without_capsule_id_is_marked_legacy(tmp_path, monkeypatch):
    """两套身份(报告目录名 / capsule run_id)缺一套时必须明写 —— 迁移进度也是读数。"""
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    _publish(tmp_path, "20260826_2000", analysis_date="2026-08-25", capsule_run_id="")
    lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")
    row = _rows(lv.views_root(_scan(tmp_path)) / lv.RUNS_CSV)[0]
    assert row["capsule_run_id"] == "" and row["identity_quality"] == "legacy"
    assert row["report_dir_id"] == "20260826_2000"
    # legacy 目录名首段是**跑动日**,不是数据日 —— 数据日只能从 manifest 读。
    assert row["analysis_date"] == "2026-08-25" and row["run_local_date"] == "2026-08-26"
    assert row["ready_quality"] == "estimated"           # generated_at 只是下界


# ───────────────────────── ② NO_RUN ≠ 0-BUY ─────────────────────────

def _three_kinds_of_day(tmp_path):
    """一个 0-BUY 日 + 一个 NO_RUN 日 + 一个 EXPIRED(未批准)日,市场读数各不相同。"""
    # gap = open[D+2]/close[D+1] − 1。逐日给不同的 open 让四桶的均值可分辨。
    opens = {"2026-08-24": 10.0, "2026-08-25": 10.0, "2026-08-26": 11.0,
             "2026-08-27": 12.0, "2026-08-28": 13.0}
    _lake(tmp_path, {day.replace("-", ""): [("603317", o, o, o, o, 0.0),
                                            ("000002", o, o, o, o, 0.0)]
                     for day, o in opens.items()})
    # 数据日 08-24 → 08-25 尾盘出手 → 08-26 开盘卖:gap = 11/10 − 1 = +10%
    _publish(tmp_path, "20260824-0824_2000", analysis_date="2026-08-24", buys=(),
             capsule_run_id="20260824T200000000000Z",
             execution=_execution("2026-08-24", first="2026-08-25",
                                  approved="2026-08-24T20:00:00"))
    # 数据日 08-26 的 run 迟到过期 → 08-27 是 NO_APPROVED_RUN
    _publish(tmp_path, "20260826-0829_2000", analysis_date="2026-08-26", buys=("603317",),
             capsule_run_id="20260829T200000000000Z",
             execution=_execution("2026-08-26", first="2026-08-27",
                                  approved="2026-08-29T20:00:00",
                                  status=anchor.EXPIRED, exec_lag=3))


def test_no_run_day_never_lands_in_the_zero_buy_bucket(tmp_path, monkeypatch):
    """把 NO_RUN 混进 0-BUY 桶 = 拿"根本没开工的日子"去证明"空仓正确"。"""
    monkeypatch.chdir(tmp_path)
    _three_kinds_of_day(tmp_path)
    lv.build(reports_root=_scan(tmp_path), now="2026-08-31T12:00:00+00:00")
    buckets = lv.session_buckets(_scan(tmp_path))

    # 读回来的是 view 里的 6 位小数(byte 稳定的代价),所以按绝对容差比。
    # 08-25 尾盘出手 → gap 记在数据日 08-24 那一行 = 11/10 − 1
    assert buckets["ZERO_BUY"] == [pytest.approx(0.10, abs=1e-6)]
    assert buckets["BUY"] == []                                  # 那只 BUY run 过期了
    # 08-27 未批准;它的市场读数来自前一个 session(08-26)那一行 = 13/12 − 1
    assert buckets[lv.SESSION_NO_APPROVED_RUN] == [pytest.approx(13 / 12 - 1, abs=1e-6)]
    assert buckets[lv.SESSION_NO_RUN] == [pytest.approx(12 / 11 - 1, abs=1e-6)]
    assert pytest.approx(0.10, abs=1e-6) not in buckets[lv.SESSION_NO_RUN]


def test_no_run_reason_is_left_empty_not_invented(tmp_path, monkeypatch):
    """机器**绝不编造** NO_RUN 的原因;要写理由去 append-only `notes.jsonl`。"""
    monkeypatch.chdir(tmp_path)
    _three_kinds_of_day(tmp_path)
    lv.build(reports_root=_scan(tmp_path), now="2026-08-31T12:00:00+00:00")
    for row in _rows(lv.views_root(_scan(tmp_path)) / lv.SESSIONS_CSV):
        if row["status"] == lv.SESSION_NO_RUN:
            assert row["selection_reason"] == "" and row["selected_run_id"] == ""


def test_unprovable_readiness_becomes_data_missing_not_no_run(tmp_path, monkeypatch):
    """证明不了何时可用的 run 不许静默消失 —— 消失会把那天写成"系统没开工"的强断言。"""
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    run = _publish(tmp_path, "20260825-0826_2000", analysis_date="2026-08-25")
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("generated_at")                 # 连估算的下界都没有 → first available 未知
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    health = lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")

    sessions = {r["session"]: r for r in _rows(lv.views_root(_scan(tmp_path)) / lv.SESSIONS_CSV)}
    row = sessions["2026-08-27"]                 # 跑动日 08-26 之后的第一个 session
    assert row["status"] == lv.SESSION_DATA_MISSING
    assert "20260825-0826_2000" in row["selection_reason"]
    assert health["blocked_by_data"] >= 1 and health["green"] is False


def test_line_withholds_bucket_means_below_min_n(tmp_path, monkeypatch):
    """<20 有效日只印「攒样本」—— 小样本均值会被当成结论读。"""
    monkeypatch.chdir(tmp_path)
    _three_kinds_of_day(tmp_path)
    lv.build(reports_root=_scan(tmp_path), now="2026-08-31T12:00:00+00:00")
    text = lv.line(_scan(tmp_path))
    assert f"0买日 攒样本 1/{lv.MIN_SESSION_N}" in text
    assert "市场 " not in text and "NO_RUN≠0买" in text


def test_line_prints_the_mean_once_a_bucket_matures(tmp_path, monkeypatch):
    """攒够 20 个有效日才开始展示(展示门槛,不是显著性门槛)。"""
    monkeypatch.chdir(tmp_path)
    days = [(date(2026, 6, 1) + timedelta(days=i)).isoformat() for i in range(30)]
    _flat_lake(tmp_path, days)                            # 平推市场 → 每天 gap = 0
    _publish(tmp_path, "20260601-0601_2000", analysis_date="2026-06-01",
             execution=_execution("2026-06-01", first="2026-06-02",
                                  approved="2026-06-01T20:00:00"))
    lv.build(reports_root=_scan(tmp_path), now="2026-07-01T12:00:00+00:00")
    text = lv.line(_scan(tmp_path))
    assert "没跑 " in text and "日 市场 +0.00pp" in text
    assert f"BUY日 攒样本 0/{lv.MIN_SESSION_N}" in text


def test_line_is_honest_before_the_first_build(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "空" in lv.line(_scan(tmp_path))


# ───────────────────────── ③ 四把尺:口径同源 ─────────────────────────

def _three_code_day(tmp_path):
    """一天的三只票,专门用来分辨两条买腿与两种板制度。

    600001  10cm 主板,T+1 涨 10% 且**收盘=最高**但开盘低 → 封板收盘(c1 腿买不进),
            开盘买得进(o1 腿在);
    688001  20cm 科创,同样涨 10%、开=收=最高 —— 用 10cm 板判就会被误当一字板,
            用 `_board_limit` 判则 10 < 19.6,两条腿都买得进;
    000002  平推对照。
    """
    _lake(tmp_path, {
        "20260824": [("600001", 10.0, 10.0, 10.0, 10.0, 0.0),
                     ("688001", 10.0, 10.0, 10.0, 10.0, 0.0),
                     ("000002", 10.0, 10.0, 10.0, 10.0, 0.0)],
        "20260825": [("600001", 10.5, 11.0, 10.4, 11.0, 10.0),     # 收=高 且 涨≈10cm 板
                     ("688001", 11.0, 11.0, 11.0, 11.0, 10.0),     # 开=收=高,但 20cm 板没封
                     ("000002", 10.0, 10.2, 9.8, 10.0, 0.0)],
        "20260826": [("600001", 12.1, 12.1, 12.1, 12.1, 10.0),
                     ("688001", 12.1, 12.1, 12.1, 12.1, 10.0),
                     ("000002", 10.5, 10.6, 10.4, 10.6, 5.0)],
    })


def test_board_limits_and_buy_legs_come_from_the_shared_definitions(tmp_path, monkeypatch):
    """`n_buyable_c1` / `n_buyable_o1` 是两个不同的人口,板幅按 `factor_lab._board_limit`。

    若把板幅写死 10%,688001 会被判成一字板 → 两个计数一起塌成 1/1,本用例当场红。
    """
    monkeypatch.chdir(tmp_path)
    _three_code_day(tmp_path)
    from autoresearch.research import edge_census as ec

    days = ec.lake_trade_days()
    row = lv.market_metrics("20260824", days, ec.load_lake_pivots(days))
    assert row["n_buyable_c1"] == 2                 # 600001 封板收盘 → 出局
    assert row["n_buyable_o1"] == 3                 # 谁都不是一字板
    assert row["status"] == lv.STATUS_MATURE
    # c1 腿人口 = {688001, 000002};gap = open[D+2]/close[D+1] − 1
    assert row["mean_c1o2"] == pytest.approx(((12.1 / 11.0 - 1) + (10.5 / 10.0 - 1)) / 2)
    # o1 腿人口多一只 600001(开盘 10.5 买得进)
    assert row["mean_o1o2"] == pytest.approx(
        ((12.1 / 10.5 - 1) + (12.1 / 11.0 - 1) + (10.5 / 10.0 - 1)) / 3)
    assert row["mean_c1c2"] == pytest.approx(((12.1 / 11.0 - 1) + (10.6 / 10.0 - 1)) / 2)
    assert row["mean_o1c2"] == pytest.approx(
        ((12.1 / 10.5 - 1) + (12.1 / 11.0 - 1) + (10.6 / 10.0 - 1)) / 3)


def test_impossible_moves_are_clipped_as_data_errors(tmp_path, monkeypatch):
    """`|gap| > GAP_CLIP` 在板制度下不可能 = 数据错;剔出均值但**不**剔出可买人口。"""
    monkeypatch.chdir(tmp_path)
    _lake(tmp_path, {
        "20260824": [("000001", 10.0, 10.0, 10.0, 10.0, 0.0),
                     ("000002", 10.0, 10.0, 10.0, 10.0, 0.0)],
        "20260825": [("000001", 10.0, 10.0, 10.0, 10.0, 0.0),
                     ("000002", 10.0, 10.0, 10.0, 10.0, 0.0)],
        "20260826": [("000001", 20.0, 20.0, 20.0, 20.0, 0.0),      # +100%:复权/灌数错
                     ("000002", 10.2, 10.2, 10.2, 10.2, 2.0)],
    })
    from autoresearch.research import edge_census as ec

    days = ec.lake_trade_days()
    row = lv.market_metrics("20260824", days, ec.load_lake_pivots(days))
    assert row["n_buyable_c1"] == 2                                  # 人口不动
    assert row["mean_c1o2"] == pytest.approx(0.02)                   # 只剩 000002
    assert lv._ruler.GAP_CLIP == 0.31


def test_market_row_is_pending_until_d2_lands(tmp_path, monkeypatch):
    """D+2 还没落湖 = **状态不是故障**,不写半截数字,也不算成熟欠账。"""
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, ["2026-08-24", "2026-08-25"])
    _publish(tmp_path, "20260824-0824_2000", analysis_date="2026-08-24",
             execution=_execution("2026-08-24", first="2026-08-25",
                                  approved="2026-08-24T20:00:00"))
    health = lv.build(reports_root=_scan(tmp_path), now="2026-08-25T12:00:00+00:00")
    market = {r["date"]: r for r in _rows(lv.views_root(_scan(tmp_path)) / lv.MARKET_CSV)}
    assert market["2026-08-24"]["status"] == lv.STATUS_PENDING
    assert market["2026-08-24"]["mean_c1o2"] == ""
    assert health["filled"] == 0 and health["pending"] == 2
    assert health["green"] is True                # PENDING 不是欠账:时间还没到


def test_zero_filled_is_not_green_when_the_lake_is_missing_a_session(tmp_path, monkeypatch):
    """「0 filled」只有在成熟欠账真的是 0 时才绿 —— 否则新任务会像 nightly-close 那样安静死掉。"""
    monkeypatch.chdir(tmp_path)
    # 湖只有 08-24 与 08-27;run 的就绪日 08-28 比湖还新 → 日历退到工作日启发,
    # 于是 08-25/08-26 是"日历里有、湖里没有、且比湖最后一天更早"的真缺口。
    _flat_lake(tmp_path, ["2026-08-24", "2026-08-27"])
    _publish(tmp_path, "20260824-0827_2000", analysis_date="2026-08-24",
             execution=_execution("2026-08-24", first="2026-08-28",
                                  approved="2026-08-27T20:00:00",
                                  status=anchor.EXPIRED, exec_lag=3))
    health = lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")
    market = {r["date"]: r for r in _rows(lv.views_root(_scan(tmp_path)) / lv.MARKET_CSV)}
    assert health["calendar_source"] == "weekday_heuristic"
    assert market["2026-08-25"]["status"] == lv.STATUS_UNAVAILABLE
    assert market["2026-08-26"]["status"] == lv.STATUS_UNAVAILABLE
    assert market["2026-08-28"]["status"] == lv.STATUS_PENDING     # 比湖还新,轮不到它
    assert health["filled"] == 0 and health["blocked_by_data"] >= 2
    assert health["green"] is False and "成熟欠账" in health["green_reason"]


def test_regime_and_temperature_are_presence_gated(tmp_path, monkeypatch):
    """读得到 `market_pack.json` 就填,读不到留空 —— 不推断、不继承邻日。"""
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    _publish(tmp_path, "20260824-0824_2000", analysis_date="2026-08-24",
             capsule_run_id="20260824T200000000000Z",
             market_pack={"regime": {"label": "range"}, "temperature": {"score": 45.3}},
             execution=_execution("2026-08-24", first="2026-08-25",
                                  approved="2026-08-24T20:00:00"))
    _publish(tmp_path, "20260825-0825_2000", analysis_date="2026-08-25",
             capsule_run_id="20260825T200000000000Z",
             execution=_execution("2026-08-25", first="2026-08-26",
                                  approved="2026-08-25T20:00:00"))
    lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")
    market = {r["date"]: r for r in _rows(lv.views_root(_scan(tmp_path)) / lv.MARKET_CSV)}
    assert market["2026-08-24"]["regime"] == "range"
    assert market["2026-08-24"]["temperature"] == "45.3"
    assert market["2026-08-25"]["regime"] == "" and market["2026-08-25"]["temperature"] == ""


# ───────────────────────── ④ 幂等 / 原子 / 半张表 ─────────────────────────

def test_views_are_byte_stable_across_rebuilds(tmp_path, monkeypatch):
    """view 里不写时间戳:同一批输入重复跑必须 byte 相同,否则"表变了"就不再是信号。"""
    monkeypatch.chdir(tmp_path)
    _three_kinds_of_day(tmp_path)
    root = lv.views_root(_scan(tmp_path))
    lv.build(reports_root=_scan(tmp_path), now="2026-08-31T00:00:00+00:00")
    first = {name: (root / name).read_bytes()
             for name in (lv.RUNS_CSV, lv.SESSIONS_CSV, lv.MARKET_CSV)}
    health_first = (root / lv.HEALTH_JSON).read_bytes()
    lv.build(reports_root=_scan(tmp_path), now="2026-09-01T00:00:00+00:00")
    assert all((root / name).read_bytes() == blob for name, blob in first.items())
    assert (root / lv.HEALTH_JSON).read_bytes() != health_first     # 时间戳只进 health


def test_crash_between_writes_leaves_the_previous_views_complete(tmp_path, monkeypatch):
    """先写 temp 再 replace:中途 kill 之后旧 view 必须**完整**(半张表长得像整张表)。"""
    monkeypatch.chdir(tmp_path)
    _three_kinds_of_day(tmp_path)
    root = lv.views_root(_scan(tmp_path))
    lv.build(reports_root=_scan(tmp_path), now="2026-08-31T00:00:00+00:00")
    before = {name: (root / name).read_bytes()
              for name in (lv.RUNS_CSV, lv.SESSIONS_CSV, lv.MARKET_CSV)}

    real_replace = pathlib.Path.replace

    def kill_on_market(self, target):
        if pathlib.Path(target).name == lv.MARKET_CSV:
            raise KeyboardInterrupt("kill -9 就在这一刻")
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", kill_on_market)
    _publish(tmp_path, "20260827-0827_2000", analysis_date="2026-08-27")
    with pytest.raises(KeyboardInterrupt):
        lv.build(reports_root=_scan(tmp_path), now="2026-09-01T00:00:00+00:00")
    # 只还原这一处(`monkeypatch.undo()` 会连 chdir 与生产路径护栏一起拆掉)
    monkeypatch.setattr(pathlib.Path, "replace", real_replace)

    assert (root / lv.MARKET_CSV).read_bytes() == before[lv.MARKET_CSV]   # 旧的那份还整着
    assert len(_rows(root / lv.MARKET_CSV)) == len(_rows(root / lv.SESSIONS_CSV))
    # 成功标记没有前移 —— 崩掉的一晚不许自称成功
    health = json.loads((root / lv.HEALTH_JSON).read_text(encoding="utf-8"))
    assert health["last_attempt_at"] == "2026-09-01T00:00:00+00:00"
    assert health["last_success_at"] == "2026-08-31T00:00:00+00:00"


def test_limit_only_narrows_the_calendar_never_the_run_facts(tmp_path, monkeypatch):
    """`--limit` 收窄的是贵的那一段(日历/市场行);run 事实永远全量,且窗口外的 run
    **不**因为掉出窗口就被记成"就绪时点不可证"。"""
    monkeypatch.chdir(tmp_path)
    _three_kinds_of_day(tmp_path)
    health = lv.build(reports_root=_scan(tmp_path), limit=2, now="2026-08-31T00:00:00+00:00")
    root = lv.views_root(_scan(tmp_path))
    assert len(_rows(root / lv.RUNS_CSV)) == 2
    assert [r["session"] for r in _rows(root / lv.SESSIONS_CSV)] == ["2026-08-27", "2026-08-28"]
    assert [r["date"] for r in _rows(root / lv.MARKET_CSV)] == ["2026-08-27", "2026-08-28"]
    assert health["blocked_by_data"] == 0


# ───────────────────────── ⑤ 引擎隔离 ─────────────────────────

def test_each_engine_writes_only_its_own_root(tmp_path, monkeypatch):
    """两个引擎各写各根;`reports_claude` 里的 run 绝不出现在 `reports_codex` 的 view 里。"""
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    monkeypatch.setattr(ws, "ENGINE", "claude")
    _publish(tmp_path, "20260825-0826_2000", analysis_date="2026-08-25",
             execution=_execution("2026-08-25", first="2026-08-26",
                                  approved="2026-08-26T20:00:00"))
    lv.build(now="2026-08-28T12:00:00+00:00")
    claude_view = tmp_path / "reports_claude" / "scan" / "_ledger" / "views" / lv.RUNS_CSV
    assert len(_rows(claude_view)) == 1
    claude_bytes = claude_view.read_bytes()

    monkeypatch.setattr(ws, "ENGINE", "codex")
    _publish(tmp_path, "20260827-0827_2000", analysis_date="2026-08-27",
             execution=_execution("2026-08-27", first="2026-08-28",
                                  approved="2026-08-27T20:00:00"))
    lv.build(now="2026-08-28T12:00:00+00:00")
    codex_view = tmp_path / "reports_codex" / "scan" / "_ledger" / "views" / lv.RUNS_CSV
    assert [r["report_dir_id"] for r in _rows(codex_view)] == ["20260827-0827_2000"]
    assert [r["engine"] for r in _rows(codex_view)] == ["codex"]
    assert claude_view.read_bytes() == claude_bytes         # 另一根一个字节没动


# ───────────────────────── 日历来源 / 备注 / CLI ─────────────────────────

def test_calendar_source_degrades_honestly_without_network(tmp_path, monkeypatch):
    """无网络时退化是允许的,**静默**退化不是:`source` 列要说清自己是谁。

    conftest 的 `_no_trade_cal_network` 掐掉了 tushare 那一级,所以这里走的是真回退路径。
    """
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    _publish(tmp_path, "20260825-0826_2000", analysis_date="2026-08-25",
             execution=_execution("2026-08-25", first="2026-08-26",
                                  approved="2026-08-26T20:00:00"))
    health = lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")
    rows = _rows(lv.views_root(_scan(tmp_path)) / lv.SESSIONS_CSV)
    assert {r["source"] for r in rows} == {"lake_partitions"}
    assert health["calendar_source"] == "lake_partitions"


def test_notes_are_append_only_and_build_never_reads_them(tmp_path, monkeypatch):
    """人写给人看的东西不许反向影响机器读数。"""
    monkeypatch.chdir(tmp_path)
    _three_kinds_of_day(tmp_path)
    root = lv.views_root(_scan(tmp_path))
    lv.build(reports_root=_scan(tmp_path), now="2026-08-31T00:00:00+00:00")
    before = {name: (root / name).read_bytes()
              for name in (lv.RUNS_CSV, lv.SESSIONS_CSV, lv.MARKET_CSV)}

    lv.add_note("2026-08-27", "机器没跑,我在开会", reports_root=_scan(tmp_path),
                now="2026-08-31T09:00:00+00:00")
    lv.add_note("20260827", "补一句", reports_root=_scan(tmp_path),
                now="2026-08-31T09:01:00+00:00")
    lines = lv.notes_path(_scan(tmp_path)).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2                                   # append,不覆盖
    assert [json.loads(x)["date"] for x in lines] == ["2026-08-27", "2026-08-27"]

    lv.build(reports_root=_scan(tmp_path), now="2026-08-31T10:00:00+00:00")
    assert all((root / name).read_bytes() == blob for name, blob in before.items())


def test_cli_build_note_and_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _three_kinds_of_day(tmp_path)
    assert lv.main(["build", "--now", "2026-08-31T00:00:00+00:00"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert json.loads(out[0])["ok"] is True and "运行日历" in out[-1]
    assert lv.main(["note", "2026-08-27", "机器", "没跑"]) == 0
    assert "机器 没跑" in lv.notes_path().read_text(encoding="utf-8")
    assert lv.main(["line"]) == 0
    assert "NO_RUN≠0买" in capsys.readouterr().out


def test_cli_note_requires_a_date_and_a_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        lv.main(["note", "2026-08-27"])


def test_runs_keyed_by_report_dir_not_capsule_id(tmp_path, monkeypatch):
    """🚨 实测:`capsule_run_id` **不唯一**,拿它当主键会静默吃掉同日重跑。

    `20260729_2105` / `20260730_0116` / `20260730_2132` 三个已发布 run 共享同一个
    `20260729T113300999873Z`(staging 按数据日键,重跑复用同一份 `run_contract.json`)。
    首次真跑时盘上 60 个 run 在视图里只剩 57 —— 正是本视图存在的理由要防的那件事。
    """
    monkeypatch.chdir(tmp_path)
    _flat_lake(tmp_path, SESSIONS)
    shared = "20260826T200000000000Z"
    for name, approved in (("20260825-0826_1900", "2026-08-26T19:00:00"),
                           ("20260825-0826_2000", "2026-08-26T20:00:00")):
        _publish(tmp_path, name, analysis_date="2026-08-25", capsule_run_id=shared,
                 execution=_execution("2026-08-25", first="2026-08-27", approved=approved))
    lv.build(reports_root=_scan(tmp_path), now="2026-08-28T12:00:00+00:00")
    rows = _rows(lv.views_root(_scan(tmp_path)) / lv.RUNS_CSV)

    assert len(rows) == 2, "同 capsule id 的两次尝试都必须在表里"
    assert {r["report_dir_id"] for r in rows} == {"20260825-0826_1900", "20260825-0826_2000"}
    # 重复不是静默去重,是**显式点名**:两个 run 指着同一个法证现场是证据层的真问题。
    assert {r["identity_quality"] for r in rows} == {"capsule", "shared_capsule_id"}
