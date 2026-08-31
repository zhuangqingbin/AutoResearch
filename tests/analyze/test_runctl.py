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

import pytest

from autoresearch.analyze import runctl
from autoresearch.analyze.run_bootstrap import prepare_analyze_run, prompt_hashes
from autoresearch.common import workspace as ws
from autoresearch.contracts import stages as vocab
from autoresearch.trace import capsule as capsule_mod
from tests.forensic_fixtures import redirect_roots

DATE = "2026-08-27"
TICKER = "300857.SZ"


@pytest.fixture()
def tmp_ws(tmp_path, monkeypatch):
    redirect_roots(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _begin(monkeypatch, *, mode="LITE"):
    started = runctl.begin(TICKER, DATE, mode=mode, session_ref="sess-abc123")
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


def test_record_stage_swallows_an_evidence_failure(tmp_ws, monkeypatch, capsys):
    """取证坏了只准喊一声,不准把用户的研究带走。"""
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")

    assert runctl.record_stage("harvest", outputs=[]) is None
    assert "checkpoint 失败" in capsys.readouterr().err


def test_record_stage_refuses_to_write_into_a_scan_run(tmp_ws, monkeypatch, capsys):
    scan = capsule_mod.begin_run("scan-market", DATE, ws.ENGINE, {})
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", scan.run_id)

    assert runctl.record_stage("harvest", outputs=[]) is None
    assert "不往别人的现场里写" in capsys.readouterr().err


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
    assert runctl.main(["begin", TICKER, DATE, "--mode", "LITE"]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("RUN_ID=")
    run_id = first.split("=", 1)[1]
    assert ws.run_root("stock-research", run_id).is_dir()


def test_cli_bind_rejects_an_unknown_role(tmp_ws):
    with pytest.raises(SystemExit):
        runctl.main(["bind", "20260827T010203456789Z", "x.jsonl", "--role", "l3-rank"])
