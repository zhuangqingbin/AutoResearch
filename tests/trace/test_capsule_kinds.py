"""capsule 去 scan 化 —— 同一套法证机制必须对 `stock-research` 一样成立(D6.2)。

task: `.superpowers/sdd/2026-08-31-stock-research-p0-p1/task-12-brief.md`。

`begin_run` 此前硬拒非 `scan-market` 的 kind,而整个 capsule 机制(工作区、事件链、
checkpoint、完整性、冻结)其实**一个字都不 scan 专属**。这里的用例锁三件事:
路径按 kind 分家、`begin_run` 收 `stock-research` 并要求调用方自带 bootstrap、
finalize 用 **analyze** 的 profile 展开 expected(不是 scan 的)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.common import workspace as ws
from autoresearch.common.run_identity import RunContract
from autoresearch.contracts import stages as vocab
from autoresearch.trace import capsule as capsule_mod
from tests.forensic_fixtures import redirect_roots

DATE = "2026-08-27"
NOW = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)
RUN_ID = "20260827T010203456789Z"


def _prepare_analyze_run(
    analysis_date,
    *,
    config=None,
    run_id=None,
    engine=None,
    workspace_path=None,
    session_ref=None,
    now=None,
):
    """T12 的桩 bootstrap —— 字段齐即可(真身是 T13 的 `analyze/run_bootstrap`)。"""
    echo = dict(config or {})
    return RunContract.build(
        analysis_date=analysis_date,
        user_config={"mode": echo.get("mode", "FULL"), "ticker": echo.get("ticker", "")},
        pinned={},
        data_policy={},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="abc1234",
        git_dirty=False,
        dirty_paths=[],
        run_kind="stock-research",
        engine=engine or ws.ENGINE,
        workspace_path=workspace_path,
        session_ref=session_ref,
        run_id=run_id,
        now=now,
    )


@pytest.fixture()
def tmp_ws(tmp_path, monkeypatch):
    redirect_roots(monkeypatch, tmp_path)
    return tmp_path


def _begin_analyze(*, mode="FULL", now=NOW):
    return capsule_mod.begin_run(
        "stock-research",
        DATE,
        ws.ENGINE,
        {"mode": mode, "ticker": "300308.SZ"},
        now=now,
        bootstrap=_prepare_analyze_run,
    )


# ---------------------------------------------------------------- 路径按 kind 分家


def test_run_root_by_kind(tmp_ws):
    assert "analyze_runs" in str(ws.run_root("stock-research", RUN_ID))
    assert ws.scan_run_root(RUN_ID) == ws.run_root("scan-market", RUN_ID)
    assert ws.run_root("scan-market", RUN_ID) != ws.run_root("stock-research", RUN_ID)


def test_run_root_rejects_an_unregistered_kind(tmp_ws):
    with pytest.raises(ValueError, match="run kind"):
        ws.run_root("sector-research", RUN_ID)


def test_reports_root_by_kind(tmp_ws):
    assert ws.run_reports_root("scan-market").name == "scan"
    assert ws.run_reports_root("stock-research").name == "analyze"
    assert capsule_mod.ledger_path("stock-research").parts[-3:] == (
        "analyze",
        "_ledger",
        capsule_mod.LEDGER_NAME,
    )
    # kwarg 缺省必须仍是 scan —— 全仓既有调用点(scan/ledger_views、测试)不传 kind。
    assert capsule_mod.ledger_path() == capsule_mod.ledger_path("scan-market")
    assert capsule_mod.failed_root().name == "_failed"
    assert capsule_mod.failed_root("stock-research").parent.name == "analyze"


def test_a_stock_research_run_id_does_not_redirect_scan_paths(tmp_ws, monkeypatch):
    """单票研究的 run_id 在场时,scan 的 staging 根**照旧**是历史根。

    否则 `analyze/slim_io._load_l1_row` 会去 `scan_runs/<那个 id>/` 里找
    `L1_scored_full.csv`,永远落空 —— L1 复用静默死掉,没人看得见。
    """
    handle = _begin_analyze()
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)

    assert ws.active_run_kind() == "stock-research"
    assert ws.scan_root() == ws.context_root() / "scan"
    assert ws.scan_input_dir(DATE) == ws.context_root()


def test_an_absent_spool_still_reads_as_a_scan_run(tmp_ws, monkeypatch):
    """只设 env 不建目录(scan 侧大量夹具的形状)必须逐字保持旧行为。"""
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", RUN_ID)

    assert ws.active_run_kind() == "scan-market"
    assert ws.scan_root() == ws.scan_run_root(RUN_ID) / "staging"


# ---------------------------------------------------------------- begin_run


def test_begin_run_accepts_stock_research(tmp_ws):
    handle = _begin_analyze()

    assert handle.contract.run_kind == "stock-research"
    assert "analyze_runs" in str(handle.workspace)
    assert handle.workspace.is_dir()
    assert handle.staging == handle.workspace / "staging" / DATE
    assert capsule_mod.load_run(handle.run_id) == handle


def test_begin_run_still_rejects_an_unregistered_kind(tmp_ws):
    with pytest.raises(ValueError, match="unsupported run kind"):
        capsule_mod.begin_run("sector-research", DATE, ws.ENGINE, {}, now=NOW)


def test_begin_run_refuses_a_non_scan_kind_without_a_bootstrap(tmp_ws):
    """trace 不 import analyze —— 所以非 scan 的 kind 必须自带 bootstrap,而不是
    让 capsule 悄悄拿 scan 的那个去建一份 `scan-market` 契约。"""
    with pytest.raises(ValueError, match="must supply its own bootstrap"):
        capsule_mod.begin_run("stock-research", DATE, ws.ENGINE, {}, now=NOW)


def test_begin_run_rejects_a_bootstrap_that_builds_the_wrong_kind(tmp_ws):
    with pytest.raises(ValueError, match="bootstrap built a 'scan-market' contract"):
        capsule_mod.begin_run(
            "stock-research",
            DATE,
            ws.ENGINE,
            {},
            now=NOW,
            bootstrap=lambda date, **kwargs: RunContract.build(
                analysis_date=date,
                user_config={},
                pinned={},
                data_policy={},
                stage_budgets={},
                artifact_schema_versions={},
                git_sha="abc1234",
                git_dirty=False,
                dirty_paths=[],
                run_kind="scan-market",
                engine=kwargs["engine"],
                workspace_path=ws.scan_run_root(kwargs["run_id"]),
                run_id=kwargs["run_id"],
                now=kwargs["now"],
            ),
        )


def test_two_kinds_can_hold_the_same_run_id_without_colliding(tmp_ws):
    """两个池子独立 —— 同一时刻起的两趟(理论上同 run_id)不该互相认领。"""
    analyze = _begin_analyze()
    scan = capsule_mod.begin_run("scan-market", DATE, ws.ENGINE, {}, now=NOW)

    assert analyze.run_id == scan.run_id
    assert analyze.workspace != scan.workspace
    # 先命中的是 RUN_SPOOLS 的第一个 kind(scan-market);两份契约都能各自读回来。
    assert capsule_mod.load_run(scan.run_id).contract.run_kind == "scan-market"


# ---------------------------------------------------------------- finalize / profile


def _finalize_after_harvest(handle, *, status="INTERRUPTED"):
    capsule_mod.checkpoint(
        handle.run_id, "harvest", "SUCCEEDED", [], {"blocks": 17}
    )
    return capsule_mod.finalize(
        handle.run_id,
        status,
        error={"error_type": "StoppedAfterHarvest", "reason": "T12 合成用例"},
        now=NOW,
    )


def test_finalize_uses_analyze_profile(tmp_ws):
    handle = _begin_analyze()
    _finalize_after_harvest(handle)

    profile = json.loads(
        (handle.capsule / "verification/profile.json").read_text(encoding="utf-8")
    )
    assert profile["kind"] == "stock-research"
    assert tuple(profile["expected_stages"]) == vocab.ANALYZE_STAGES
    assert profile["replayable_stages"] == []

    expected = json.loads(
        (handle.capsule / "verification/expected.json").read_text(encoding="utf-8")
    )
    keys = {item["key"] for item in expected["items"]}
    assert keys >= {f"stage:{stage}" for stage in vocab.ANALYZE_STAGES}
    assert not [k for k in keys if k.startswith("stage:l")], (
        f"analyze 的 expected 里出现了 scan 阶段:{sorted(keys)}"
    )
    assert "agent:l4-card" not in keys and "agent:company-intel" in keys


def test_lite_mode_expected_stages_skip_the_intel_and_write_legs(tmp_ws):
    handle = _begin_analyze(mode="LITE")
    _finalize_after_harvest(handle)

    profile = json.loads(
        (handle.capsule / "verification/profile.json").read_text(encoding="utf-8")
    )
    assert profile["mode"] == "LITE"
    assert tuple(profile["expected_stages"]) == vocab.ANALYZE_LITE_STAGES

    expected = json.loads(
        (handle.capsule / "verification/expected.json").read_text(encoding="utf-8")
    )
    keys = {item["key"] for item in expected["items"]}
    assert "stage:intel" not in keys and "stage:write" not in keys


def test_analyze_run_owes_no_captured_command_logs(tmp_ws):
    """analyze 不套 traced 壳 —— 日志是 NOT_EXPECTED,不是 MISSING。

    恒判缺失会让每一趟单票研究都报「证据不全」= 假警报。
    """
    handle = _begin_analyze(mode="LITE")
    _finalize_after_harvest(handle)

    expected = json.loads(
        (handle.capsule / "verification/expected.json").read_text(encoding="utf-8")
    )
    logs = {
        item["key"]: item["disposition"]
        for item in expected["items"]
        if item["key"].startswith("log:")
    }
    assert logs, "日志规则整条不见了 —— 那是把问题藏起来,不是解决"
    assert set(logs.values()) == {"NOT_EXPECTED"}


def test_analyze_finalize_never_replays_scan_stages(tmp_ws):
    """replay 表按 kind 派发:给 analyze 套 scan 的 argv 会真的去跑一趟全市场扫描。"""
    from autoresearch.trace import replay as replay_mod

    assert replay_mod.default_stage_specs(DATE, kind="stock-research") == ()
    assert replay_mod.default_stage_specs(DATE) != ()

    handle = _begin_analyze(mode="LITE")
    _finalize_after_harvest(handle)
    payload = json.loads(
        (handle.capsule / "verification/replay.json").read_text(encoding="utf-8")
    )
    assert payload["replayability"] == "NONE"


def test_analyze_ledger_and_failed_root_are_separate_from_scan(tmp_ws):
    handle = _begin_analyze(mode="LITE")
    result = _finalize_after_harvest(handle)

    assert result.final_path == capsule_mod.failed_root("stock-research") / handle.run_id
    rows = capsule_mod.read_valid_ledger(kind="stock-research")
    assert [row["run_id"] for row in rows] == [handle.run_id]
    assert capsule_mod.read_valid_ledger() == [], "analyze 的 run 漏进了 scan 的账本"

    facts = capsule_mod.verify(handle.run_id)
    assert facts["integrity_ok"] is True
    assert facts["root_ledger_ok"] is True
    assert facts["replayability"] == "NONE"


def test_resolve_run_kind_reads_the_spool_then_the_ledger(tmp_ws):
    handle = _begin_analyze(mode="LITE")
    assert capsule_mod.resolve_run_kind(handle.run_id) == "stock-research"
    # 没有任何现场的 run_id 落回 scan-market(kind 化之前唯一存在过的 kind)。
    assert capsule_mod.resolve_run_kind("20260827T010203456790Z") == "scan-market"
