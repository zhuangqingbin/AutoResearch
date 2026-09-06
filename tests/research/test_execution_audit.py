"""C5 端到端:合成湖 + 快照 + 券商成交 + 成本模型 → 三模式分别汇总、覆盖表、排他输出。"""
import csv
import hashlib
import json
from decimal import Decimal

import pandas as pd
import pytest

from autoresearch.research import execution_audit as ea, execution_import as imp

POLICY = {"cost_model_version": "ashare_test_v1", "venue": "SSE", "effective_from": "2023-08-28",
          "commission_rate": "0.00025", "minimum_commission": "5", "tax_rate": "0.0005",
          "tax_sides": ["SELL"], "transfer_fee_rate": "0.00001", "slippage_bps": "200",
          "order_merge": "per_order"}
TRADE_COLS = ["account", "trade_date", "trade_time", "code", "ts_code", "name", "side", "biz_type",
              "price", "qty", "amount", "commission", "stamp_tax", "transfer_fee", "other_fee",
              "net_amount", "balance_after", "trade_id", "source_kind", "source_file", "sources",
              "ingested_at"]


def _bar(code, o, h, lo, c, pct):
    return {"ts_code": f"{code}.SH" if code.startswith("6") else f"{code}.SZ", "open": o, "high": h,
            "low": lo, "close": c, "pct_chg": pct, "amount": 1000.0}


@pytest.fixture()
def world(tmp_path):
    lake = tmp_path / "lake" / "daily"
    lake.mkdir(parents=True)
    pd.DataFrame([_bar("600000", 9.9, 10.1, 9.8, 10.0, 0.5), _bar("300001", 9.9, 10.1, 9.8, 10.0, 0.5)]) \
        .to_parquet(lake / "20260901.parquet")
    # 0902 = 买腿日:600000 正常;300001 收盘封涨停(20cm,收=高)
    pd.DataFrame([_bar("600000", 10.1, 10.4, 10.0, 10.2, 2.0), _bar("300001", 11.0, 12.0, 11.0, 12.0, 20.0)]) \
        .to_parquet(lake / "20260902.parquet")
    pd.DataFrame([_bar("600000", 10.5, 10.6, 10.4, 10.5, 2.9), _bar("300001", 12.5, 12.6, 12.4, 12.5, 4.2)]) \
        .to_parquet(lake / "20260903.parquet")

    runs = tmp_path / "runs"
    for name, status in (("20260901-0901_2100", "ACTIONABLE"),
                         ("20260901-0902_2000", "LATE_REVALIDATION_REQUIRED")):
        (runs / name).mkdir(parents=True)
        (runs / name / "manifest.json").write_text(json.dumps({"analysis_date": "2026-09-01", "execution": {
            "schema_version": 1, "analysis_date": "2026-09-01", "data_as_of": "2026-09-01",
            "first_available_session": "2026-09-02", "exec_lag": 0, "staleness_sessions": 1,
            "actionability_status": status, "ready_quality": "measured", "ready_source": "gate4_approved",
            "calendar_quality": "trade_cal", "timezone_assumed": "Asia/Shanghai",
            "exec_decision_cutoff": "14:45", "exchange_cutoff": "14:57",
            "decision_approved_at": "2026-09-01T21:00:00+08:00", "brief_written_at": None}}),
            encoding="utf-8")

    snaps = tmp_path / "snapshots.csv"
    fields = list(imp.SNAPSHOT_FIELDS)
    base = {"schema_version": 1, "engine": "claude", "venue": "SSE", "session_date": "2026-09-02",
            "decision_at": "2026-09-02T14:45:00+08:00", "market_event_at": "2026-09-02T14:44:58+08:00",
            "provider_published_at": "2026-09-02T14:44:59+08:00", "received_at": "2026-09-02T14:45:00+08:00",
            "persisted_at": "2026-09-02T14:45:01+08:00", "timezone": "Asia/Shanghai",
            "previous_close": "10.00", "volume_so_far": "1000", "amount_so_far": "10100",
            "suspended": "false", "limit_down_price": "9.00", "price_adjustment_basis": "none",
            "timestamp_precision": "second", "quality_flags": ""}
    rows = [dict(base, snapshot_id="s1", run_id="20260901-0901_2100", code="600000", last="10.10",
                 high_so_far="10.40", low_so_far="10.00", limit_up_price="11.00",
                 source_observation_id="o1", payload_hash="a" * 64),
            dict(base, snapshot_id="s2", run_id="20260901-0902_2000", code="600000", last="10.10",
                 high_so_far="10.40", low_so_far="10.00", limit_up_price="11.00",
                 source_observation_id="o2", payload_hash="b" * 64),
            dict(base, snapshot_id="s3", run_id="20260901-0901_2100", code="300001", last="12.00",
                 high_so_far="12.00", low_so_far="11.00", limit_up_price="12.00",
                 source_observation_id="o3", payload_hash="c" * 64)]
    with snaps.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

    trades = tmp_path / "trades.csv"
    fee = {"commission": "5", "stamp_tax": "0", "transfer_fee": "0.01", "other_fee": "0"}
    trows = [
        dict(account="A1", trade_date="2026-09-02", trade_time="14:59:30", code="600000", ts_code="600000.SH",
             name="X", side="BUY", biz_type="买入", price="10.2", qty="100", amount="1020", **fee,
             net_amount="-1025.01", balance_after="", trade_id="t1", source_kind="gtht", source_file="a.csv",
             sources="gtht", ingested_at="2026-09-05T00:00:00"),
        {"account": "A1", "trade_date": "2026-09-03", "trade_time": "09:30:00", "code": "600000",
         "ts_code": "600000.SH", "name": "X", "side": "SELL", "biz_type": "卖出", "price": "10.5",
         "qty": "100", "amount": "1050", "commission": "5", "stamp_tax": "0.525", "transfer_fee": "0.01",
         "other_fee": "0", "net_amount": "1044.465", "balance_after": "", "trade_id": "t2",
         "source_kind": "gtht", "source_file": "a.csv", "sources": "gtht", "ingested_at": "2026-09-05T00:00:00"},
        dict(account="A1", trade_date="2026-09-02", trade_time="14:59:40", code="300001", ts_code="300001.SZ",
             name="Y", side="BUY", biz_type="买入", price="12.0", qty="100", amount="1200", **fee,
             net_amount="-1205.01", balance_after="", trade_id="t3", source_kind="gtht", source_file="a.csv",
             sources="gtht", ingested_at="2026-09-05T00:00:00"),
        {"account": "A1", "trade_date": "2026-09-03", "trade_time": "", "code": "600000",
         "ts_code": "600000.SH", "name": "X", "side": "OTHER", "biz_type": "红利", "price": "", "qty": "",
         "amount": "30", "commission": "", "stamp_tax": "", "transfer_fee": "", "other_fee": "",
         "net_amount": "30", "balance_after": "", "trade_id": "t4", "source_kind": "gtht",
         "source_file": "a.csv", "sources": "gtht", "ingested_at": "2026-09-05T00:00:00"},
    ]
    with trades.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_COLS)
        w.writeheader()
        for r in trows:
            w.writerow({k: r.get(k, "") for k in TRADE_COLS})

    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(POLICY), encoding="utf-8")
    return {"tmp": tmp_path, "lake": lake, "runs": runs, "snaps": snaps, "trades": trades, "policy": policy}


def _run(world, experiment_id="EXEC_T1"):
    return ea.run(experiment_id=experiment_id, snapshots=world["snaps"], trades=world["trades"],
                  policy=world["policy"], runs_root=world["runs"], lake_daily=world["lake"],
                  max_age_seconds=60, parent=world["tmp"] / "out", engine="claude")


def _rows(path):
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_three_modes_are_reported_separately_and_never_merged(world):
    out = _run(world)
    metrics = _rows(out / "daily_metrics.csv")
    modes = {(m["evidence_mode"], m["session"]) for m in metrics}
    assert modes == {("EOD_PROXY", "2026-09-02"), ("SNAPSHOT_SIMULATED", "2026-09-02"),
                     ("OBSERVED_FILL", "2026-09-02")}
    assert all(m["evidence_mode"] in {"EOD_PROXY", "SNAPSHOT_SIMULATED", "OBSERVED_FILL"} for m in metrics)


def test_late_run_snapshot_goes_to_coverage_not_denominator(world):
    out = _run(world)
    coverage = json.loads((out / "coverage.json").read_text(encoding="utf-8"))
    excluded = coverage["snapshots"]["not_in_denominator"]
    assert [e["snapshot_id"] for e in excluded] == ["s2"]
    assert excluded[0]["reason"] == "RUN_LATE_REVALIDATION_REQUIRED"
    sim = [r for r in _rows(out / "assessments.csv") if r["evidence_mode"] == "SNAPSHOT_SIMULATED"]
    assert {r["run_id"] for r in sim} == {"20260901-0901_2100"}


def test_snapshot_fill_follows_the_closing_auction_rule(world):
    out = _run(world)
    sim = {r["code"]: r for r in _rows(out / "assessments.csv") if r["evidence_mode"] == "SNAPSHOT_SIMULATED"}
    assert sim["600000"]["entry_state"] == "FILLED"
    assert sim["600000"]["fill_rule_version"] == "close_auction_limit_v1"
    assert sim["600000"]["decision_at"] == "2026-09-02T14:45:00+08:00"
    # 300001 快照 last == limit_up → 入场条件 LIMIT_UP_QUEUE → 未提交
    assert sim["300001"]["entry_verdict"] == "UNKNOWN"
    assert sim["300001"]["entry_state"] == "NOT_SUBMITTED"


def test_eod_proxy_uses_the_main_ruler_and_the_buyable_flag(world):
    out = _run(world)
    eod = {r["code"]: r for r in _rows(out / "assessments.csv") if r["evidence_mode"] == "EOD_PROXY"}
    assert eod["600000"]["entry_state"] == "FILLED"
    assert Decimal(eod["600000"]["gross_return"]) == pytest.approx(Decimal("10.5") / Decimal("10.2") - 1)
    assert eod["300001"]["entry_state"] == "NO_FILL" and eod["300001"]["entry_reason"] == "LIMIT_UP_SEALED"


def test_observed_fill_realizes_only_with_a_sell_leg(world):
    out = _run(world)
    obs = {r["code"]: r for r in _rows(out / "assessments.csv") if r["evidence_mode"] == "OBSERVED_FILL"}
    assert obs["600000"]["exit_state"] == "FILLED"
    net = Decimal(obs["600000"]["net_return_realized"])
    # (1050 − 5.535) − (1020 + 5.01) = 19.455;/ 1025.01
    assert net == pytest.approx(Decimal("19.455") / Decimal("1025.01"))
    assert obs["300001"]["exit_state"] == "UNKNOWN" and obs["300001"]["net_return_realized"] == ""


def test_dividend_rows_are_not_legs(world):
    fills, errors = imp.load_trades(world["trades"])
    assert {f["fill_id"] for f in fills} == {"t1", "t2", "t3"} and errors == []


def test_output_is_exclusive_and_manifest_hashes_match(world):
    out = _run(world)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["outputs"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest
    assert manifest["cost_model_version"] == "ashare_test_v1"
    with pytest.raises(FileExistsError):
        _run(world)


def test_missing_fees_are_not_zero(world):
    rows = _rows(world["trades"])
    rows[0]["commission"] = ""
    with world["trades"].open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_COLS)
        w.writeheader()
        w.writerows(rows)
    out = _run(world, "EXEC_T2")
    obs = {r["code"]: r for r in _rows(out / "assessments.csv") if r["evidence_mode"] == "OBSERVED_FILL"}
    assert obs["600000"]["entry_reason"] == "FEES_MISSING" and obs["600000"]["net_return_realized"] == ""


def test_foreign_engine_snapshot_is_rejected_not_silently_used(world):
    rows = _rows(world["snaps"])
    rows[0]["engine"] = "codex"
    with world["snaps"].open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(imp.SNAPSHOT_FIELDS))
        w.writeheader()
        w.writerows(rows)
    ok, errors = imp.load_snapshots(world["snaps"], engine="claude")
    assert {r["snapshot_id"] for r in ok} == {"s2", "s3"}
    assert errors[0]["snapshot_id"] == "s1" and "I02" in errors[0]["error_code"]


def test_cli_writes_under_reports_root_and_refuses_overwrite(world, monkeypatch):
    from autoresearch.common import workspace as ws
    monkeypatch.setattr(ws, "reports_root", lambda: world["tmp"] / "rpt")
    monkeypatch.setattr(ws, "detect_engine", lambda environ=None: "claude")
    argv = ["--experiment-id", "EXEC_CLI", "--snapshots", str(world["snaps"]), "--trades", str(world["trades"]),
            "--policy", str(world["policy"]), "--runs-root", str(world["runs"]), "--lake-daily", str(world["lake"])]
    assert ea.main(argv) == 0
    assert (world["tmp"] / "rpt" / "research" / "execution" / "EXEC_CLI" / "readout.md").exists()
    assert ea.main(argv) == 2
    assert not list(world["tmp"].glob("**/context*")), "不得写进 context 根"


def test_experiment_id_is_validated_before_any_directory_is_made(world):
    with pytest.raises(ValueError):
        ea.create_output_dir("../escape", parent=world["tmp"] / "out")
    assert not (world["tmp"] / "out").exists() or not list((world["tmp"] / "out").iterdir())
