"""`analyze/runctl` —— 单票研究的现场控制器(D6.3/6.4)。

task: `.superpowers/sdd/2026-08-31-stock-research-p0-p1/task-13-brief.md`。

锁四件事:
1. begin → checkpoint → finalize 一圈跑完,`capsule.verify` 的**三个结论**分开可读;
2. LITE 的 expected 里没有 `intel` / `write` 腿;
3. **不开 `AUTORESEARCH_RUN_ID` 时是真 no-op** —— 零写盘,与今天逐字相同;
4. 取证故障吞成 stderr,业务不受影响(「一个索引 bug 不该毙掉用户的研究」)。
"""

from __future__ import annotations

import json
import os

import pytest

from autoresearch.analyze import runctl
from autoresearch.analyze.run_bootstrap import prepare_analyze_run, prompt_hashes
from autoresearch.common import workspace as ws
from autoresearch.contracts import stages as vocab
from autoresearch.trace import capsule as capsule_mod
from tests.forensic_fixtures import redirect_roots

DATE = "2026-08-27"
TICKER = "300857.SZ"


@pytest.fixture(autouse=True)
def simulated_legacy_body_access(monkeypatch):
    # Historical runctl lifecycle only. C4 entry refusal has dedicated unmocked tests.
    from autoresearch.contracts import research_access
    monkeypatch.setattr(research_access, 'require_legacy_access',
                        lambda *args: {'status': 'SIMULATED_LEGACY_BODY'})


@pytest.fixture()
def tmp_ws(tmp_path, monkeypatch):
    redirect_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _begin(monkeypatch, *, mode="LITE"):
    started = runctl.begin(
        TICKER,
        DATE,
        mode=mode,
        session_ref="sess-abc123",
        legacy_reason="test legacy harness",
    )
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", started["run_id"])
    return started


# ---------------------------------------------------------------- bootstrap


def test_prepare_analyze_run_freezes_the_mode_and_ticker(tmp_ws):
    contract = prepare_analyze_run(
        DATE,
        config={"mode": "FULL", "ticker": TICKER, "peers": "300308.SZ,600519.SS"},
        run_id="20260827T010203456789Z",
        engine=ws.ENGINE,
        workspace_path=ws.run_root("stock-research", "20260827T010203456789Z"),
    )
    assert contract.run_kind == "stock-research"
    assert contract.user_config["mode"] == "FULL"
    assert contract.user_config["ticker"] == TICKER
    assert contract.user_config["peers"] == ["300308.SZ", "600519.SS"]
    assert contract.schema_version == 3


@pytest.mark.parametrize(
    "config",
    [
        {"ticker": TICKER},                              # 缺档
        {"mode": "DEEP", "ticker": TICKER},              # 档不在词汇表
        {"mode": "LITE"},                                # 缺标的
        {"mode": "LITE", "ticker": TICKER, "l3": {}},    # 未知键
    ],
)
def test_prepare_analyze_run_rejects_a_config_it_cannot_account_for(tmp_ws, config):
    with pytest.raises(ValueError):
        prepare_analyze_run(
            DATE,
            config=config,
            run_id="20260827T010203456789Z",
            engine=ws.ENGINE,
            workspace_path=ws.run_root("stock-research", "20260827T010203456789Z"),
        )


def test_prompt_hashes_cover_the_skill_and_its_agents(tmp_ws, monkeypatch):
    monkeypatch.chdir(tmp_ws)
    skill = tmp_ws / ".claude/skills/stock-research"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("playbook", encoding="utf-8")
    agents = tmp_ws / ".claude/agents"
    agents.mkdir(parents=True)
    (agents / "l4-card.md").write_text("card writer", encoding="utf-8")
    (agents / "l3-rank.md").write_text("not a stock-research leg", encoding="utf-8")

    hashes = prompt_hashes()

    assert set(hashes) == {
        ".claude/skills/stock-research/SKILL.md",
        ".claude/agents/l4-card.md",
    }, "prompt 快照多收/漏收了文件"
    assert all(len(digest) == 64 for digest in hashes.values())


# ---------------------------------------------------------------- 不开 run 时零留痕


def test_record_stage_is_a_true_noop_without_a_run(tmp_ws, monkeypatch, capsys):
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    before = sorted(p.name for p in tmp_ws.iterdir())

    assert runctl.record_stage("harvest", outputs=[tmp_ws / "x.md"]) is None

    assert sorted(p.name for p in tmp_ws.iterdir()) == before
    assert capsys.readouterr().err == ""


def test_record_stage_propagates_a_missing_run_identity(tmp_ws, monkeypatch):
    """显式绑定的假 run_id 不是普通外源降级，必须在业务写入前失败。"""
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")

    with pytest.raises(RuntimeError, match="RUN_NOT_FOUND"):
        runctl.record_stage("harvest", outputs=[])


def test_record_stage_propagates_a_cross_workflow_identity(tmp_ws, monkeypatch):
    scan = capsule_mod.begin_run("scan-market", DATE, ws.ENGINE, {})
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", scan.run_id)

    with pytest.raises(RuntimeError, match="RUN_OPERATION_NOT_OWNED"):
        runctl.record_stage("harvest", outputs=[])


# ---------------------------------------------------------------- 一圈跑完


def test_runctl_begin_finalize_roundtrip(tmp_ws, monkeypatch):
    started = _begin(monkeypatch)
    assert "analyze_runs" in started["workspace"]

    slim = tmp_ws / f"{TICKER}_{DATE}_slim.md"
    slim.write_text("# slim\n\n## Verified market snapshot\n", encoding="utf-8")
    deep = tmp_ws / f"{TICKER}_{DATE}_slim_deep.md"
    deep.write_text("# deep\n", encoding="utf-8")

    recorded = runctl.record_stage(
        "harvest", outputs=[slim, deep], metrics={"tier": "slim", "blocks": 17}
    )
    assert recorded is not None
    assert recorded["stage"] == "harvest" and recorded["status"] == "SUCCEEDED"

    handle = capsule_mod.load_run(started["run_id"])
    # 产物被快照进 run staging —— finalize 会把整个 staging 冻进 capsule/products。
    assert (handle.staging / slim.name).read_text(encoding="utf-8") == slim.read_text(
        encoding="utf-8"
    )
    assert (handle.staging / deep.name).is_file()

    runctl.record_stage("card", outputs=[], metrics={"rating": "Hold"})
    result = runctl.finalize(
        started["run_id"], status="INTERRUPTED", reason="用例只跑到卡"
    )

    assert result["business_status"] == "INTERRUPTED"
    facts = capsule_mod.verify(started["run_id"])
    # 三个结论**分开**读 —— MANIFEST 通过 ≠ 现场完整。
    assert facts["integrity_ok"] is True
    assert facts["replayability"] == "NONE"
    assert facts["event_chain_ok"] is True
    # 完整性**不**断言 True:这个夹具自己把 `snapshot_identity` 桩掉了(identity 三件
    # 因此不在场),而合成 run 一次湖也没读(`lineage/reads.jsonl` 因此不在场)——
    # 那是夹具的事实,不是接线的洞。真跑的读数见 §LIVE。
    # 断言改成**恰好这四条**:checkpoint 这一层该产的(阶段 result / 日志 / 产物 /
    # 事件链 / capsule.json / failure.json)一条都不许缺,否则接线就是坏的。
    assert set(facts["missing_required"]) == {
        "identity/dependencies.txt",
        "identity/environment.json",
        "identity/source_manifest.json",
        "lineage/reads.jsonl",
    }, facts["missing_required"]

    frozen = capsule_mod._find_final_path(
        started["run_id"], kind="stock-research"
    )
    assert (frozen / "capsule/products/staging" / slim.name).is_file()


def test_lite_mode_expected_stages(tmp_ws, monkeypatch):
    started = _begin(monkeypatch, mode="LITE")
    runctl.record_stage("harvest", outputs=[])
    runctl.finalize(started["run_id"], status="INTERRUPTED", reason="用例")

    handle_capsule = ws.run_root("stock-research", started["run_id"]) / "capsule"
    expected = json.loads(
        (handle_capsule / "verification/expected.json").read_text(encoding="utf-8")
    )
    keys = {item["key"] for item in expected["items"]}
    assert "stage:intel" not in keys
    assert "stage:write" not in keys
    assert keys >= {f"stage:{stage}" for stage in vocab.ANALYZE_LITE_STAGES}


def test_full_mode_owes_the_intel_leg(tmp_ws, monkeypatch):
    started = _begin(monkeypatch, mode="FULL")
    runctl.record_stage("harvest", outputs=[])
    runctl.finalize(started["run_id"], status="INTERRUPTED", reason="用例")

    handle_capsule = ws.run_root("stock-research", started["run_id"]) / "capsule"
    expected = json.loads(
        (handle_capsule / "verification/expected.json").read_text(encoding="utf-8")
    )
    keys = {item["key"] for item in expected["items"]}
    assert "stage:intel" in keys and "stage:write" in keys
    assert "agent:company-intel" in keys and "agent:us-intel" in keys


# ---------------------------------------------------------------- manifest 回填


def test_finalize_backfills_the_manifest_run_id_before_freezing(tmp_ws, monkeypatch):
    started = _begin(monkeypatch)
    runctl.record_stage("harvest", outputs=[])

    report_dir = ws.run_reports_root("stock-research") / "20260827_0102"
    report_dir.mkdir(parents=True)
    (report_dir / "manifest.json").write_text(
        json.dumps({"schema_version": 2, "ticker": TICKER, "run_id": None}),
        encoding="utf-8",
    )
    (report_dir / "报告.md").write_text("# 报告\n", encoding="utf-8")

    result = runctl.finalize(started["run_id"], report_dir=str(report_dir))

    payload = json.loads((report_dir / "manifest.json").read_text(encoding="utf-8"))
    assert payload["run_id"] == started["run_id"]
    assert result["business_status"] == "SUCCEEDED"
    # 回填发生在冻结**之前**,所以 MANIFEST 覆盖的正是回填后的字节。
    assert capsule_mod.verify(started["run_id"])["integrity_ok"] is True


def test_backfill_is_idempotent_and_skips_a_missing_manifest(tmp_ws):
    assert runctl.backfill_manifest_run_id(tmp_ws, "20260827T010203456789Z") is None
    (tmp_ws / "manifest.json").write_text(json.dumps({"run_id": None}), encoding="utf-8")
    first = runctl.backfill_manifest_run_id(tmp_ws, "20260827T010203456789Z")
    second = runctl.backfill_manifest_run_id(tmp_ws, "20260827T010203456789Z")
    assert first == second
    assert json.loads(first.read_text(encoding="utf-8"))["run_id"] == (
        "20260827T010203456789Z"
    )


# ---------------------------------------------------------------- CLI


def test_cli_begin_prints_an_exportable_run_id(tmp_ws, capsys):
    assert runctl.main([
        "begin",
        TICKER,
        DATE,
        "--mode",
        "LITE",
        "--legacy-reason",
        "test legacy CLI",
    ]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("RUN_ID=")
    run_id = first.split("=", 1)[1]
    assert ws.run_root("stock-research", run_id).is_dir()


def test_cli_bind_rejects_an_unknown_role(tmp_ws):
    with pytest.raises(SystemExit):
        runctl.main(["bind", "20260827T010203456789Z", "x.jsonl", "--role", "l3-rank"])


# ---------------------------------------------------------------- D6.5: codex escape hatch


def test_begin_calls_the_codex_escape_hatch_only_on_codex_engine(tmp_ws, monkeypatch):
    """`tmp_ws` 走 `redirect_roots` → `ws.ENGINE == "codex"`,`begin()` 必须接线调用。"""
    calls = []
    monkeypatch.setattr(
        runctl, "_record_codex_escape_hatch", lambda handle: calls.append(handle.run_id)
    )

    started = _begin(monkeypatch)

    assert calls == [started["run_id"]]


def test_begin_skips_the_codex_escape_hatch_on_claude_engine(tmp_ws, monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "claude")
    calls = []
    monkeypatch.setattr(
        runctl, "_record_codex_escape_hatch", lambda handle: calls.append(handle.run_id)
    )

    runctl.begin(
        TICKER,
        DATE,
        mode="LITE",
        session_ref="sess-claude-branch",
        legacy_reason="test legacy harness",
    )

    assert calls == []


def test_record_codex_escape_hatch_writes_the_detected_mode(tmp_ws, monkeypatch):
    """逃逸口只读 `~/.codex/config.toml`,把探测值追加进 `environment.json`(D6.5)。"""
    monkeypatch.setattr(
        "autoresearch.trace.identity.detect_codex_web_search_mode",
        lambda *args, **kwargs: "cached",
    )
    started = _begin(monkeypatch)
    handle = capsule_mod.load_run(started["run_id"])
    env_path = handle.capsule / "identity" / "environment.json"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(json.dumps({"schema_version": 1, "engine": "codex"}), encoding="utf-8")

    runctl._record_codex_escape_hatch(handle)

    payload = json.loads(env_path.read_text(encoding="utf-8"))
    assert payload["codex_web_search_mode"] == "cached"
    assert payload["engine"] == "codex"  # 原有键原样保留,只是追加


def test_record_codex_escape_hatch_is_a_silent_noop_without_environment_json(
    tmp_ws, monkeypatch, capsys
):
    """身份快照缺失是既有的独立容错路径(`EVIDENCE_MISSING` 事件已经记过一遍)——
    这个附加键不该为同一件事再吵一次。"""
    started = _begin(monkeypatch)
    handle = capsule_mod.load_run(started["run_id"])
    assert not (handle.capsule / "identity" / "environment.json").is_file()

    # Startup can emit its separate daily-rollout completeness warning.
    # This assertion covers only the optional metadata writer below.
    capsys.readouterr()
    runctl._record_codex_escape_hatch(handle)

    assert capsys.readouterr().err == ""
    assert not (handle.capsule / "identity" / "environment.json").is_file()


def test_record_codex_escape_hatch_warns_on_unreadable_json(tmp_ws, monkeypatch, capsys):
    started = _begin(monkeypatch)
    handle = capsule_mod.load_run(started["run_id"])
    env_path = handle.capsule / "identity" / "environment.json"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("{ not json", encoding="utf-8")

    runctl._record_codex_escape_hatch(handle)

    assert "codex_web_search_mode 记录失败" in capsys.readouterr().err


def test_begin_warns_if_codex_rollout_missing_is_called(tmp_ws, monkeypatch):
    calls = []
    monkeypatch.setattr(
        runctl, "_warn_if_codex_rollout_missing", lambda: calls.append(True)
    )

    _begin(monkeypatch)

    assert calls == [True]


def test_warn_if_codex_rollout_missing_fires_when_directory_is_empty(
    tmp_path, monkeypatch, capsys
):
    from datetime import date as calendar_date

    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    sessions_root = tmp_path / "sessions"

    runctl._warn_if_codex_rollout_missing(
        calendar_date(2026, 8, 27), sessions_root=sessions_root
    )

    err = capsys.readouterr().err
    assert "rollout 目录空/缺席" in err
    assert str(sessions_root / "2026" / "08" / "27") in err


def test_warn_if_codex_rollout_missing_silent_when_a_file_is_present(
    tmp_path, monkeypatch, capsys
):
    from datetime import date as calendar_date

    monkeypatch.setenv("CODEX_SANDBOX_NETWORK_DISABLED", "1")
    day_dir = tmp_path / "sessions" / "2026" / "08" / "27"
    day_dir.mkdir(parents=True)
    (day_dir / "rollout-fixture.jsonl").write_text("{}\n", encoding="utf-8")

    runctl._warn_if_codex_rollout_missing(
        calendar_date(2026, 8, 27), sessions_root=tmp_path / "sessions"
    )

    assert capsys.readouterr().err == ""


def test_warn_if_codex_rollout_missing_silent_without_codex_env(
    tmp_path, monkeypatch, capsys
):
    from datetime import date as calendar_date

    for key in list(os.environ):
        if key.startswith("CODEX_"):
            monkeypatch.delenv(key, raising=False)

    runctl._warn_if_codex_rollout_missing(
        calendar_date(2026, 8, 27), sessions_root=tmp_path / "sessions"
    )

    assert capsys.readouterr().err == ""


def test_session_validate_checkpoints_the_card_stage_like_legacy(tmp_ws, monkeypatch):
    """session_v1 LITE owes `stages/card` exactly as the legacy runctl step did."""
    from autoresearch.session_agent import domain_ops
    from autoresearch.trace.capsule import load_run
    from autoresearch.trace.replay import REPLAY_ENV

    started = _begin(monkeypatch, mode="LITE")
    handle = load_run(started["run_id"])
    card = handle.staging / "card.md"
    card.write_text("**Rating**: Hold\n", encoding="utf-8")
    value = {"rating": "Hold", "proposal": "HOLD", "card_sha256": "a" * 64}

    domain_ops._record_card_stage(handle, card, value)

    stage = handle.capsule / "stages/card/attempt-1/result.json"
    result = json.loads(stage.read_text(encoding="utf-8"))
    assert result["status"] == "SUCCEEDED"
    assert result["metrics"]["rating"] == "Hold"
    outputs = json.loads((stage.parent / "outputs.json").read_text(encoding="utf-8"))
    assert [row["status"] for row in outputs["artifacts"]] == ["PRESENT"]
    # Offline replay re-runs validate in a sandbox: it never writes live run stages.
    monkeypatch.setenv(REPLAY_ENV, str(tmp_ws))
    domain_ops._record_card_stage(handle, card, value)
    assert not (handle.capsule / "stages/card/attempt-2").exists()


# ------------------------------------------------ session_v1 FULL 的写身份(2026-10-02)
#
# session_v1 的单股 FULL 计划登记的装配操作叫 `stock.full.assemble`,而装配器自己的 checkpoint
# 一直以 `stock.assemble` 的身份过写守卫。带 session 计划的 run 只认计划里登记的操作,
# 于是 FULL 跑完 15 个推理任务后会在最后一步被 `RUN_OPERATION_NOT_OWNED` 拦下。

def test_session_full_assemble_checkpoint_is_owned_by_its_registered_operation(tmp_ws, monkeypatch):
    from pathlib import Path

    from autoresearch.session_agent.workflows.stock import build_stock_plan
    from tests.session_agent.test_stock_full import _context, _full_request

    started = _begin(monkeypatch, mode="FULL")
    handle = capsule_mod.load_run(started["run_id"])
    monkeypatch.chdir(Path(__file__).resolve().parents[2])     # 角色登记表按仓库根读 agent 定义
    plan = build_stock_plan(_full_request(subject=TICKER), _context(tmp_ws / "plan-only"))
    monkeypatch.chdir(tmp_ws)
    session = handle.workspace / "session"
    session.mkdir(parents=True, exist_ok=True)
    (session / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    operation = next(task["operation"] for task in plan["tasks"] if task["task_id"] == "stock.assemble")
    assert operation == "stock.full.assemble"

    with pytest.raises(RuntimeError, match="RUN_OPERATION_NOT_OWNED"):
        runctl.record_stage("assemble", outputs=[])                    # 旧身份不在这份计划里
    recorded = runctl.record_stage("assemble", outputs=[], operation=operation)
    assert recorded is not None and recorded["stage"] == "assemble"
