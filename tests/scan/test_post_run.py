"""Idempotent post-run consumer receipts and selective replay."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.scan.outbox import OutboxEvent, emit_events
from autoresearch.scan.post_run import (
    consumer_status,
    initialize_consumer_state,
    load_consumer_receipts,
    publish_run_observation,
    run_consumers,
)

#: 2026-08-21 learning 层退役后 `SUBSCRIPTIONS["RUN_FINALIZED"]` 已空(13 个学习账本
#: consumer 全删)。下面这些用例测的是 **consumer 机制本身**(幂等/重试/backlog 计数),
#: 不是生产路由表,所以显式传一张合成订阅表 —— 换成生产表就变成"测一个空集合",
#: 断言全部退化成 0==0 的假绿灯。
_FAKE_ROUTES = {"RUN_FINALIZED": {"c_alpha", "c_beta"}}


def _scan_with_run_event(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    event = OutboxEvent.build(
        event_type="RUN_FINALIZED",
        analysis_date=scan.name,
        run_id="run-1",
        contract_hash=None,
        aggregate_id="run-1",
        payload={"n_buys": 0, "n_decisions": 1},
        created_at="2026-07-28T10:00:00Z",
    )
    emit_events(scan, [event])
    return scan, event


def test_initialize_consumer_state_is_atomic_and_idempotent(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)
    path = initialize_consumer_state(scan)
    before = path.read_bytes()
    assert initialize_consumer_state(scan).read_bytes() == before
    assert load_consumer_receipts(path) == {}
    assert not path.with_name("consumer_state.json.tmp").exists()


def test_successful_consumer_is_not_called_twice(tmp_path):
    scan, event = _scan_with_run_event(tmp_path)
    calls = []

    def handler(received, _scan):
        calls.append(received.event_id)

    first = run_consumers(scan, registry={"c_alpha": handler}, subscriptions=_FAKE_ROUTES)
    second = run_consumers(scan, registry={"c_alpha": handler}, subscriptions=_FAKE_ROUTES)
    assert first.succeeded == 1 and first.failed == 0
    assert second.skipped == 1 and second.succeeded == 0
    assert calls == [event.event_id]


def test_failed_consumer_can_be_retried_alone(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)

    def failing(_event, _scan):
        raise RuntimeError("boom")

    calls = []

    def succeeding(event, _scan):
        calls.append(event.event_id)

    first = run_consumers(scan, registry={"c_alpha": failing}, subscriptions=_FAKE_ROUTES)
    second = run_consumers(
        scan,
        registry={"c_alpha": succeeding},
        only={"c_alpha"},
        retry_failed=True,
        subscriptions=_FAKE_ROUTES,
    )
    assert first.failed == 1
    assert second.succeeded == 1
    assert calls
    receipt = next(iter(load_consumer_receipts(
        scan / "outbox" / "consumer_state.json"
    ).values()))
    assert receipt.status == "SUCCEEDED"
    assert receipt.attempts == 2


def test_one_consumer_failure_does_not_block_another(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)

    def failing(_event, _scan):
        raise RuntimeError("boom")

    calls = []

    def succeeding(event, _scan):
        calls.append(event.event_id)

    result = run_consumers(
        scan,
        registry={"c_alpha": failing, "c_beta": succeeding},
        subscriptions=_FAKE_ROUTES,
    )
    assert result.failed == 1 and result.succeeded == 1
    assert len(calls) == 1
    status = consumer_status(
        scan,
        registry={"c_alpha": failing, "c_beta": succeeding},
        subscriptions=_FAKE_ROUTES,
    )
    assert status["status"] == "BACKLOG"
    assert status["failed_consumers"] == ["c_alpha"]
    assert status["pending"] == 0


def test_only_filter_leaves_other_expected_consumer_pending(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)
    registry = {
        "c_alpha": lambda _event, _scan: None,
        "c_beta": lambda _event, _scan: None,
    }
    run_consumers(scan, registry=registry, only={"c_alpha"}, subscriptions=_FAKE_ROUTES)
    status = consumer_status(scan, registry=registry, subscriptions=_FAKE_ROUTES)
    assert status["status"] == "BACKLOG"
    assert status["pending"] == 1
    assert status["pending_consumers"] == ["c_beta"]


def test_pending_counts_event_consumer_pairs_not_unique_names(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)
    dossier_events = [
        OutboxEvent.build(
            event_type="DOSSIER_DELTA_READY",
            analysis_date=scan.name,
            run_id="run-1",
            contract_hash=None,
            aggregate_id=code,
            payload={"code": code, "rating": "Hold", "conviction": "50"},
            created_at="2026-07-28T10:00:00Z",
        )
        for code in ("000001", "000002", "000003")
    ]
    emit_events(scan, dossier_events)
    status = consumer_status(
        scan,
        registry={"dossier_delta": lambda _event, _scan: None},
    )
    assert status["pending"] == 3
    assert status["pending_consumers"] == ["dossier_delta"]


def test_load_rejects_tampered_receipt(tmp_path):
    scan, _ = _scan_with_run_event(tmp_path)
    run_consumers(
        scan,
        registry={"c_alpha": lambda _event, _scan: None},
        subscriptions=_FAKE_ROUTES,
    )
    path = scan / "outbox" / "consumer_state.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["receipts"][0]["status"] = "FAILED"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_consumer_receipts(path)


def test_status_cli_prints_deterministic_backlog_json(tmp_path, capsys):
    """CLI 用**生产订阅表**报 backlog —— 所以夹具必须发一个真的还有订阅者的事件。

    2026-08-21 learning 层退役后 `RUN_FINALIZED` 的 8 个学习账本 consumer 全删,只剩
    `DOSSIER_DELTA_READY → dossier_delta` 这一条真路由;继续用 RUN_FINALIZED 夹具会让本
    用例断言一个空集合(恒 OK/pending=0),变成永不变红的绿灯。
    """
    from autoresearch.scan.post_run import main

    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    emit_events(scan, [OutboxEvent.build(
        event_type="DOSSIER_DELTA_READY",
        analysis_date=scan.name,
        run_id="run-1",
        contract_hash=None,
        aggregate_id="000001",
        payload={"code": "000001", "rating": "Hold", "conviction": "50"},
        created_at="2026-07-28T10:00:00Z",
    )])
    assert main([str(scan), "status"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "BACKLOG"
    assert result["pending"] == 1
    assert result["pending_consumers"] == ["dossier_delta"]


def test_status_cli_reports_corrupt_control_file(tmp_path, capsys):
    from autoresearch.scan.post_run import main

    scan, _ = _scan_with_run_event(tmp_path)
    path = scan / "outbox" / "events.json"
    path.write_text("{", encoding="utf-8")
    assert main([str(scan), "status"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INVALID"
    assert "JSONDecodeError" in result["error"]


def test_observe_cli_enqueues_finalist_dossiers_without_dossier(tmp_path, monkeypatch, capsys):
    """`observe` 子命令流程里接线的插队建档(Wave9 R6):无档案 finalist 落 pending_init,
    且打印 `[dossier] 插队 N 只:...`。"""
    from autoresearch.scan import post_run
    from autoresearch.scan.post_run import main

    scan = tmp_path / "2026-07-29"
    scan.mkdir()
    (scan / "finalists.csv").write_text(
        "code,name,lane\n601211,国泰海通,healthy\n", encoding="utf-8")
    (scan / "_dossier_present.json").write_text("[]", encoding="utf-8")
    pool_path = tmp_path / "coverage_pool.json"
    pool_path.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool_path)

    assert main([str(scan), "observe"]) == 0
    out = capsys.readouterr().out
    assert "[dossier] 插队 1 只:601211" in out
    entries = json.loads(pool_path.read_text(encoding="utf-8"))["pending_init"]
    assert entries[0]["code"] == "601211" and entries[0]["priority"] == "finalist"


def test_observe_cli_silent_when_nothing_to_enqueue(tmp_path, monkeypatch, capsys):
    from autoresearch.scan import post_run
    from autoresearch.scan.post_run import main

    scan = tmp_path / "2026-07-29"
    scan.mkdir()
    (scan / "finalists.csv").write_text(
        "code,name,lane\n601211,国泰海通,healthy\n", encoding="utf-8")
    (scan / "_dossier_present.json").write_text('["601211"]', encoding="utf-8")
    pool_path = tmp_path / "coverage_pool.json"
    pool_path.write_text(json.dumps({"pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool_path)

    assert main([str(scan), "observe"]) == 0
    assert "[dossier]" not in capsys.readouterr().out


def test_observe_cli_survives_enqueue_failure(tmp_path, monkeypatch, capsys):
    """插队建档失败(如池路径解析炸掉)不该挡住成本观测本身的发布——观测才是 observe 的
    主职责,插队只是顺带的 advisory 优化。"""
    from autoresearch.scan import post_run
    from autoresearch.scan.post_run import main

    scan = tmp_path / "2026-07-29"
    scan.mkdir()
    (scan / "finalists.csv").write_text("code,name\n601211,国泰海通\n", encoding="utf-8")
    (scan / "_dossier_present.json").write_text("[]", encoding="utf-8")

    def _boom():
        raise RuntimeError("pool path resolution exploded")

    monkeypatch.setattr(post_run, "_pool_path", _boom)

    assert main([str(scan), "observe"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True


def _l4_book_with_running_task(tmp_path, date: str, code: str = "000001") -> tuple:
    """最小 task-book 夹具:一票 RUNNING、prompt/slim/card 三产物齐且 slim 合格 ——
    2026-08-12 型事故复现(卡已在盘,book 没收尾)。"""
    from autoresearch.scan.l4_tasks import initialize, preflight

    scan = tmp_path / date
    scan.mkdir(parents=True)
    ticker = f"{code}.SZ"
    (scan / f"_l4_prompt_{code}.md").write_text("# 任务包\n", encoding="utf-8")
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / f"{ticker}_{date}_slim.md").write_text(
        "\n".join([
            "## Verified market snapshot",
            "### Latest verified OHLCV row",
            "| Close | 12.34 |",
            "## Market context",
            "## Fundamentals overview",
            "x" * 5000,
        ]),
        encoding="utf-8",
    )
    (scan / "details").mkdir()
    (scan / "details" / f"{code}.md").write_text("# card", encoding="utf-8")

    book = initialize(
        date, [code], root=tmp_path, context_root=ctx,
        meta={code: {"ticker": ticker, "pinned": False}},
    )
    assert book["ok"], book
    preflight(book["path"], code)  # PENDING → RUNNING
    return scan, Path(book["path"])


def test_publish_run_observation_reconciles_task_book_before_decision(tmp_path):
    """挂点验证:调用后 book 中卡已在盘的 RUNNING 票翻 SUCCEEDED(recovered 标记) ——
    证明 reconcile 真在护照/决策**现算之前**跑了(不是没接线、也不是接在了下游)。
    contract 门是否因此放行由 Task 1.4 的 reconcile 用例覆盖。
    """
    scan, book_path = _l4_book_with_running_task(tmp_path, "2026-07-30")

    publish_run_observation(scan, real_scan=False)

    payload = json.loads(book_path.read_text(encoding="utf-8"))
    task = payload["tasks"]["000001"]
    assert task["status"] == "SUCCEEDED"
    assert task["recovered"] is True


def test_publish_run_observation_survives_reconcile_failure(tmp_path, capsys):
    """影子件失败纪律:reconcile 炸了也不能挡住护照/决策发布本身,只打一行 stderr。"""
    scan = tmp_path / "2026-07-31"
    scan.mkdir()
    # schema_version 不受支持 → reconcile 内部 _read() 必抛,验证 except 真包住了它
    (scan / "_l4_tasks.json").write_text(
        json.dumps({"schema_version": 999, "tasks": {}}), encoding="utf-8")

    publish_run_observation(scan, real_scan=False)  # 不得向上抛出

    assert "reconcile 失败" in capsys.readouterr().err
    assert (scan / "_relative_buy_decision.json").exists()  # 下游未被挡住


# ── P0-2:`decision_write` 显式模式参数(writer-1 write / writer-2 verify)──────────
def test_publish_run_observation_rejects_an_unknown_decision_write_mode(tmp_path):
    """合法值只有 {"write","verify"};非法值是调用方的编程契约违规,必须立即报错,不能
    被 `publish_run_observation` 的一堆内部 `contextlib.suppress` 悄悄吞掉。"""
    scan = tmp_path / "2026-08-01"
    scan.mkdir()
    with pytest.raises(ValueError, match="decision_write"):
        publish_run_observation(scan, real_scan=False, decision_write="clobber")


def test_publish_run_observation_write_mode_still_overwrites_unconditionally(tmp_path):
    """默认/显式 write 模式必须保持 P0-2 之前的行为:无条件原子覆盖(writer-1 的姿势),
    即便盘上那份和现算的不一样——这条守住向后兼容,防止改造 verify 时手滑把 write 也
    改成了幂等比较。"""
    from autoresearch.scan.relative_buy import DECISION_FILENAME
    from tests.scan.test_relative_buy import _RANK_CANDS, _build_scan

    scan = _build_scan(tmp_path, _RANK_CANDS, date="2026-08-02")
    stale = json.dumps({"stale": True, "blocked": True, "buys": []}, ensure_ascii=False)
    (scan / DECISION_FILENAME).write_text(stale, encoding="utf-8")

    publish_run_observation(scan, real_scan=False, decision_write="write")

    doc = json.loads((scan / DECISION_FILENAME).read_text(encoding="utf-8"))
    assert doc.get("stale") is None                 # 陈旧占位内容被真实覆盖
    assert doc["blocked"] is False
    assert not (scan / "_relative_buy_decision.mismatch.json").exists()


def test_publish_run_observation_verify_mode_never_overwrites_a_mismatch(tmp_path):
    """端到端穿线:`decision_write="verify"` 真的从 CLI 参数一路传到
    `relative_buy.safe_verify_decision`,而不是在半路被哪一层默认值悄悄吃掉。"""
    from autoresearch.scan.relative_buy import DECISION_FILENAME
    from tests.scan.test_relative_buy import _RANK_CANDS, _build_scan

    scan = _build_scan(tmp_path, _RANK_CANDS, date="2026-08-03")
    publish_run_observation(scan, real_scan=False, decision_write="write")  # writer-1
    original = (scan / DECISION_FILENAME).read_bytes()
    assert json.loads(original)["blocked"] is False

    # writer-2 之前,输入被换成会翻转 blocked 的状态(同 relative_buy 侧的造法)
    (scan / "run_health.json").write_text(json.dumps({
        "core_missing": ["L1_scored_full.csv"],
        "run_contract": {"status": "OK"},
        "stage_results": {"status": "OK", "failed": []},
        "decision_records": {"status": "OK"},
    }, ensure_ascii=False), encoding="utf-8")

    publish_run_observation(scan, real_scan=False, decision_write="verify")  # writer-2

    assert (scan / DECISION_FILENAME).read_bytes() == original   # 盘上那份分毫未动
    assert (scan / "_relative_buy_decision.mismatch.json").exists()
    with (scan / "gate_fires.csv").open(encoding="utf-8", newline="") as fh:
        import csv as _csv
        rows = list(_csv.DictReader(fh))
    assert any(r["check"] == "相对BUY决策文件·两次现算不一致" and r["severity"] == "fail"
               for r in rows)


def test_observe_cli_uses_verify_not_write(tmp_path, monkeypatch):
    """CLI `observe` 子命令是 writer-2(STAGES 步骤 5 最后一条命令,brief 落盘之后)——
    必须显式传 `decision_write="verify"`,不能悄悄退回默认的 write(P0-2)。"""
    from autoresearch.scan import post_run
    from autoresearch.scan.post_run import main

    scan = tmp_path / "2026-08-04"
    scan.mkdir()
    captured = {}

    def _fake_publish(_scan_dir, **kwargs):
        captured.update(kwargs)
        return {"status": "OK", "measurement_status": "OK", "maturity": {}}

    monkeypatch.setattr(post_run, "publish_run_observation", _fake_publish)

    assert main([str(scan), "observe"]) == 0
    assert captured.get("decision_write") == "verify"


# ── E2(task-2.2,2026-08-19):relative_buy 的 mode/exclude_pinned 从 scan_config 解析后
# 透传给两个写者 —— 装开关必须真的接得到配置,不能永远拿内建默认 ──────────────────────


def test_publish_run_observation_write_passes_relative_buy_config(tmp_path, monkeypatch):
    """writer-1(write 模式):config 里的 relative_buy.mode/exclude_pinned 必须原样传给
    `safe_write_decision`。"""
    scan = tmp_path / "2026-08-05"
    scan.mkdir()
    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "scan_config.jsonc").write_text(
        json.dumps({"relative_buy": {"mode": "active", "exclude_pinned": True}}),
        encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",
                        cfg_dir / "scan_config.jsonc")
    captured: dict = {}

    def _fake_safe_write(_scan_dir, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("autoresearch.scan.relative_buy.safe_write_decision", _fake_safe_write)

    publish_run_observation(scan, real_scan=False, decision_write="write")

    assert captured == {"mode": "active", "exclude_pinned": True,
                        "pool": "finalists"}   # v3.0 起 pool 也必须原样透传


def test_publish_run_observation_verify_passes_relative_buy_config(tmp_path, monkeypatch):
    """writer-2(verify 模式):同一份 config 必须同样传给 `safe_verify_decision`——两个
    写者用不同规则重算,「两次现算是否一致」这句话就没有意义。"""
    scan = tmp_path / "2026-08-06"
    scan.mkdir()
    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "scan_config.jsonc").write_text(
        json.dumps({"relative_buy": {"mode": "active", "exclude_pinned": True}}),
        encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",
                        cfg_dir / "scan_config.jsonc")
    captured: dict = {}

    def _fake_safe_verify(_scan_dir, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("autoresearch.scan.relative_buy.safe_verify_decision", _fake_safe_verify)

    publish_run_observation(scan, real_scan=False, decision_write="verify")

    assert captured == {"mode": "active", "exclude_pinned": True,
                        "pool": "finalists"}   # v3.0 起 pool 也必须原样透传


def test_publish_run_observation_defaults_relative_buy_to_shadow_without_config(
    tmp_path, monkeypatch,
):
    """没有 scan_config.jsonc(或没有 relative_buy 块)→ mode=shadow/exclude_pinned=False
    (parity,现行为不变)。"""
    scan = tmp_path / "2026-08-07"
    scan.mkdir()
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",
                        tmp_path / "nope.jsonc")
    captured: dict = {}

    def _fake_safe_write(_scan_dir, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("autoresearch.scan.relative_buy.safe_write_decision", _fake_safe_write)

    publish_run_observation(scan, real_scan=False, decision_write="write")

    assert captured == {"mode": "shadow", "exclude_pinned": False,
                        "pool": "finalists"}


# --- Task 16: CP7 冻结现场 ---------------------------------------------------


def test_finalize_step_is_skipped_honestly_without_an_active_run(monkeypatch):
    from autoresearch.scan import post_run as P

    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)

    result = P._finalize_forensic_run(None)

    assert result == {"finalized": False, "reason": "no active forensic run"}


def test_finalize_step_reports_its_own_failure_without_claiming_success(monkeypatch):
    from autoresearch.scan import post_run as P
    from autoresearch.trace import capsule as capsule_mod

    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    monkeypatch.setattr(
        capsule_mod,
        "finalize",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("spool is gone")),
    )

    result = P._finalize_forensic_run(None)

    assert result["finalized"] is False
    assert "spool is gone" in result["reason"]


def test_finalize_step_reports_the_three_verdicts_separately(codex_run, tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws
    from autoresearch.scan import post_run as P
    from autoresearch.trace.capsule import checkpoint

    handle, _ = codex_run
    report_dir = ws.reports_root() / "scan" / handle.run_id
    report_dir.mkdir(parents=True)
    (report_dir / "summary.md").write_text("# synthetic\n", encoding="utf-8")
    checkpoint(handle.run_id, "l3", "SUCCEEDED", [], {})
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)

    result = P._finalize_forensic_run(str(report_dir))

    assert result["finalized"] is True
    assert result["integrity_ok"] is True
    assert result["completeness_ok"] is False  # 这次没产出全部证据,如实报
    assert result["replayability"] == "NONE"
