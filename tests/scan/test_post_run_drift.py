"""观测发布点接上身份漂移与相对预算带(2026-10-03 A3/A5)。

发布点是 `post_run.publish_run_observation`:usage 已到、报告已出、还没冻结。这里要钉住的是
「接线」而不是算法(算法在 `test_run_drift.py`):身份/形状/漂移/相对四块进观测 JSON 与
stage_result;summary「运行事实」一行与附录 E 都印出来;历史只读已发布根,测试不碰真实报告根;
成熟度终于数得到已发布的真实扫描,而不是永远只有当场一场。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.run_identity import RunContract, write_run_contract
from autoresearch.scan import run_drift
from autoresearch.scan.post_run import publish_run_observation
from autoresearch.trace import usage_harvest
from tests.scan.test_wave3_observation import DATE, _report_bundle, _scan

PROMPTS = {
    ".claude/agents/l4-card.md": "a" * 64,
    ".claude/agents/l4-intel.md": "b" * 64,
    ".codex/agents/l4_card.toml": "c" * 64,
    ".codex/agents/l4_intel.toml": "d" * 64,
}


def _row(agent, model, *, output, role="subagent", effort="max", version="2.1.287"):
    return {
        "role": role, "agent": agent, "model": model, "models": [model], "effort": effort,
        "host_version": version, "status": "SUCCEEDED", "messages": 6, "input": 10,
        "output": output, "cache_read": 1000, "cache_create": 100, "cache_create_5m": 100,
        "cache_create_1h": 0, "weighted_in": 1_000_000, "failure_count": 0, "retry_count": 0,
        "discarded": False, "estimated_usd": 3.0, "discarded_usd": 0.0,
    }


def _ledger(card="claude-opus-5-5", card_out=(50_000, 51_000, 52_000), version="2.1.287"):
    rows = [_row("(主会话)", "claude-opus-5-5", role="main", output=90_000, version=version)]
    rows += [_row("l4-card", card, output=o, version=version) for o in card_out]
    rows += [_row("l4-intel", "claude-sonnet-5-5", output=o, version=version)
             for o in (40_000, 42_000)]
    return usage_harvest.build_ledger(rows, source="fixture")


def _write_usage(scan: Path, ledger: dict) -> None:
    (scan / "_token_usage.json").write_text(json.dumps(ledger), encoding="utf-8")


def _contract(scan: Path) -> RunContract:
    contract = RunContract.build(
        analysis_date=DATE,
        user_config={},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={},
        artifact_schema_versions={"finalists": 1},
        git_sha="abc1234",
        git_dirty=False,
        dirty_paths=[],
        prompt_hashes=PROMPTS,
        now=datetime(2026, 7, 28, 12, 34, 56, 123456, tzinfo=timezone.utc),
    )
    write_run_contract(scan / "run_contract.json", contract)
    return contract


def _published(root: Path, folder: str, *, run_id: str, ledger: dict, date: str) -> None:
    """一份已发布观测:新格式(自带 usage_shape / identity);配置哈希不记,只让模型/版本说话。"""
    ident = run_drift.identity(ledger, prompt_hashes=PROMPTS, config_hash=None, engine=ws.ENGINE)
    payload = {"run_id": run_id, "analysis_date": date, "real_scan": True,
               "measurement_status": "MEASURED", "estimated_usd": 40.0,
               "weighted_input_proxy": 9_000_000, "interactive_wall_s": 3600,
               "cache_hit_rate": 0.9, "usage_shape": run_drift.usage_shape(ledger),
               "identity": ident}
    path = root / folder / "trace" / "_budget_observation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _history(root: Path, n: int, ledger: dict) -> None:
    for i in range(n):
        _published(root, f"2026091{i}-091{i}_2200", run_id=f"2026091{i}T120000000000Z",
                   ledger=ledger, date=f"2026-09-1{i}")


def test_observation_carries_identity_shape_drift_and_relative(tmp_path):
    scan = _scan(tmp_path)
    _contract(scan)
    _write_usage(scan, _ledger())
    root = tmp_path / "reports" / "scan"
    _history(root, 4, _ledger(card="claude-opus-5", card_out=(19_000, 19_000, 19_000),
                              version="2.1.270"))

    got = publish_run_observation(scan, real_scan=False, history_root=root)

    assert got["identity"]["agents"]["l4-card"]["models"] == ["claude-opus-5-5"]
    expected = {path for name in ("l4-card", "l4-intel")
                for path in run_drift._prompt_paths(ws.ENGINE, name)}
    assert set(got["identity"]["agent_prompts"]) == expected      # 本引擎的契约,不是另一个引擎的
    assert got["identity"]["code_sha"] == "abc1234"
    assert got["usage_shape"]["agents"]["l4-card"]["output_median"] == 51_000
    assert got["drift"]["status"] == "CHANGED"
    assert got["drift"]["baseline_run"] == "20260913T120000000000Z"
    assert {"kind": "model", "scope": "l4-card", "before": ["claude-opus-5"],
            "after": ["claude-opus-5-5"]} in got["drift"]["changes"]
    assert got["identity_canary"]["canary_required"] is True
    assert got["relative"]["status"] == "SPIKE" and got["relative"]["scope"] == "all"
    assert got["relative"]["worst"] == "output_median:l4-card"
    persisted = json.loads((scan / "_budget_observation.json").read_text(encoding="utf-8"))
    assert persisted["drift"] == got["drift"] and persisted["relative"] == got["relative"]


def test_summary_line_and_appendix_print_the_drift(tmp_path):
    scan = _scan(tmp_path)
    _contract(scan)
    _write_usage(scan, _ledger())
    root = tmp_path / "reports" / "scan"
    _history(root, 4, _ledger(card="claude-opus-5", card_out=(19_000, 19_000, 19_000)))
    report = tmp_path / "reports" / "scan" / "run-now"
    summary, appendix = _report_bundle(report)

    publish_run_observation(scan, report_dir=report, real_scan=False, history_root=root)

    line = summary.read_text(encoding="utf-8")
    assert "身份:CHANGED(1 项:l4-card 模型)" in line
    assert "相对(跨 cohort):SPIKE ×2.68(l4-card 输出中位)" in line
    detail = appendix.read_text(encoding="utf-8")
    assert "  - l4-card 模型:claude-opus-5 → claude-opus-5-5" in detail
    assert "- canary(research.drift):需要" in detail
    assert "- 相对预算带:SPIKE(跨 cohort的已发布真实扫描 4 场)" in detail


def test_stage_result_registers_the_two_new_readings(tmp_path):
    scan = _scan(tmp_path)
    _write_usage(scan, _ledger())

    got = publish_run_observation(scan, real_scan=False)

    stage = json.loads((scan / "stage_results" / "budget.json").read_text(encoding="utf-8"))
    assert stage["metrics"]["identity_drift"] == got["drift"]["status"] == "NO_BASELINE"
    assert stage["metrics"]["relative_band"] == got["relative"]["status"] == "NO_BASELINE"


def test_a_non_real_scan_without_an_explicit_root_never_reads_the_real_reports(
        tmp_path, monkeypatch):
    """测试与演练不得把仓库里真实的已发布报告读成自己的基线。"""
    from autoresearch.common import workspace as ws

    scan = _scan(tmp_path)
    _write_usage(scan, _ledger())
    monkeypatch.setattr(ws, "run_reports_root",
                        lambda kind: (_ for _ in ()).throw(AssertionError("read real root")))

    got = publish_run_observation(scan, real_scan=False)

    assert got["drift"]["status"] == "NO_BASELINE"


def test_a_real_scan_reads_the_published_root_by_default(tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws

    scan = _scan(tmp_path)
    _write_usage(scan, _ledger())
    root = tmp_path / "published"
    _history(root, 3, _ledger())
    monkeypatch.setattr(ws, "run_reports_root", lambda kind: root)

    got = publish_run_observation(scan, real_scan=True)

    assert got["drift"]["status"] == "SAME"
    assert got["relative"]["status"] == "NORMAL" and got["relative"]["scope"] == "cohort"


def test_maturity_counts_published_real_scans_plus_this_one(tmp_path):
    scan = _scan(tmp_path)
    _write_usage(scan, _ledger())
    root = tmp_path / "reports" / "scan"
    _history(root, 4, _ledger())

    got = publish_run_observation(scan, real_scan=True, history_root=root)

    assert got["maturity"]["n_real_scans"] == 5


def test_unreadable_published_observations_are_counted_not_silently_dropped(tmp_path):
    scan = _scan(tmp_path)
    _write_usage(scan, _ledger())
    root = tmp_path / "reports" / "scan"
    _history(root, 3, _ledger())
    bad = root / "20260901-0901_2200" / "trace" / "_budget_observation.json"
    bad.parent.mkdir(parents=True)
    bad.write_text("{torn", encoding="utf-8")

    got = publish_run_observation(scan, real_scan=False, history_root=root)

    assert "已发布预算观测 1 份读不动(未进基线)" in got["advisories"]
    assert got["relative"]["n_baseline"] == 3


def test_relative_thresholds_follow_the_budget_policy(tmp_path):
    scan = _scan(tmp_path)
    _write_usage(scan, _ledger(card_out=(60_000, 60_000, 60_000)))
    root = tmp_path / "reports" / "scan"
    _history(root, 3, _ledger(card_out=(50_000, 50_000, 50_000)))

    default = publish_run_observation(scan, real_scan=False, history_root=root)
    strict = publish_run_observation(
        scan, real_scan=False, history_root=root,
        budgets={"relative": {"window": 10, "min_runs": 3, "warn_ratio": 1.1,
                              "alarm_ratio": 1.15}})

    assert default["relative"]["status"] == "NORMAL"           # ×1.20 < 1.5
    assert strict["relative"]["status"] == "SPIKE"              # ×1.20 ≥ 1.15


def test_observation_carries_the_behavior_fingerprint_and_its_deviation(tmp_path):
    """A6:指纹在观测发布点算一次,与漂移、相对预算带同一个 observation、同两处渲染。"""
    from tests.scan.test_behavior_fingerprint import _typical

    scan = _scan(tmp_path)
    _write_usage(scan, _ledger())
    (scan / "_final_ratings.json").unlink()                 # 用指纹夹具自己的评级
    (scan / "decision_records.json").unlink()
    _typical(scan, hold=6, uw=2)
    root = tmp_path / "reports" / "scan"
    for i in range(3):
        run = root / f"2026091{i}-091{i}_2200" / "trace"
        _typical(run / "staging")
        (run / "_budget_observation.json").write_text(json.dumps(
            {"run_id": f"2026091{i}T120000000000Z", "analysis_date": f"2026-09-1{i}",
             "real_scan": True}), encoding="utf-8")
    report = tmp_path / "reports" / "scan" / "run-now"
    summary, appendix = _report_bundle(report)

    got = publish_run_observation(scan, report_dir=report, real_scan=False, history_root=root)

    assert got["fingerprint"]["n_cards"] == 8
    assert got["behavior"]["status"] == "DEVIATION" and got["behavior"]["n_baseline"] == 3
    assert "行为:DEVIATION(" in summary.read_text(encoding="utf-8")
    assert "  - 持有占比:0.75 vs 中位 0.25(差 +0.50)" in appendix.read_text(encoding="utf-8")
    stage = json.loads((scan / "stage_results" / "budget.json").read_text(encoding="utf-8"))
    assert stage["metrics"]["behavior"] == "DEVIATION"


# ── 复审 I-5 / M-4:观测腿失败不让发布失败,也不替失败编原因 ─────────────────────────


def _publish_with(tmp_path, monkeypatch, target, attr):
    scan = _scan(tmp_path)
    _write_usage(scan, _ledger())
    report = tmp_path / "reports" / "scan" / "run-now"
    _summary, appendix = _report_bundle(report)
    if target is not None:
        def boom(*args, **kwargs):
            raise ValueError("observability.fingerprint.window / min_runs 须为正整数且 min_runs <= window")
        monkeypatch.setattr(target, attr, boom)
    got = publish_run_observation(scan, report_dir=report, real_scan=False,
                                  history_root=tmp_path / "none")
    return scan, appendix, got


def test_a_broken_fingerprint_policy_is_unmeasured_and_never_aborts_publish(tmp_path, monkeypatch):
    from autoresearch.scan import behavior_fingerprint

    control_scan, _, _ = _publish_with(tmp_path / "control", monkeypatch, None, None)
    scan, appendix, got = _publish_with(tmp_path / "broken", monkeypatch, behavior_fingerprint, "policy")

    assert got["behavior"]["status"] == "UNMEASURED" and got["fingerprint"] is None
    assert any(a.startswith("行为指纹未计量:ValueError") for a in got["advisories"])
    for name in ("_relative_buy_decision.json", "_buyability.json", "_budget_observation.json"):
        assert (scan / name).exists() == (control_scan / name).exists(), name
    assert "- 行为指纹:—(未计量)" in appendix.read_text(encoding="utf-8")


def test_a_broken_identity_leg_is_unmeasured_and_never_aborts_publish(tmp_path, monkeypatch):
    scan, appendix, got = _publish_with(tmp_path, monkeypatch, run_drift, "identity")

    assert got["drift"]["status"] == "UNMEASURED" and got["relative"]["status"] == "UNMEASURED"
    assert got["identity"] is None
    assert any(a.startswith("身份 / 相对预算带未计量:ValueError") for a in got["advisories"])
    assert got["behavior"]["status"] in {"NO_BASELINE", "UNMEASURED"}   # 另一条腿照跑
    detail = appendix.read_text(encoding="utf-8")
    assert "- 相对预算带:—(未计量)" in detail and "可比的已发布真实扫描 0 场" not in detail
