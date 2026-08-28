"""现场留存(2026-08-26 §4):staging 镜像 / run 外输入快照 / MANIFEST 校验。

判据全部对着审计里那几条**真实缺口**写:子目录被 `is_file()` 静默跳过、slim 住在 context
根、prompt 本体只有 git_sha 代表、发布目录可写却无从检出。每条都配一个变异探针
(把实现那一段删掉,对应用例必须变红)。
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import retention


def _staging(tmp_path, date="2026-08-25"):
    d = tmp_path / ws.scan_root() / date
    (d / "L3_evidence").mkdir(parents=True)
    (d / "ensemble").mkdir()
    (d / "_sem").mkdir()
    (d / "finalists.csv").write_text("code,name\n603317,天味食品\n", encoding="utf-8")
    (d / "market_view.md").write_text("# 市场研判\n", encoding="utf-8")
    (d / "_relative_buy_decision.json").write_text('{"buys": []}', encoding="utf-8")
    (d / "L3_evidence" / "603317.json").write_text("{}", encoding="utf-8")
    (d / "ensemble" / "603317.run2.md").write_text("复核稿", encoding="utf-8")
    (d / "_sem" / "slot.lock").write_text("", encoding="utf-8")
    (d / "_l4_tasks.json.lock").write_text("", encoding="utf-8")
    return d


def test_mirror_carries_subdirectories_not_just_files(tmp_path):
    """审计的核心缺口:`publisher._archive_reasoning` 的 `p.is_file()` 过滤把 staging 的
    **子目录**(L3_evidence/L3_news/ensemble)整段静默跳过。镜像必须带上它们。"""
    scan = _staging(tmp_path)
    run = tmp_path / ws.reports_root() / "scan" / "20260825_2149"
    n = retention.mirror_staging(scan, run)
    st = run / "trace" / "staging"
    assert (st / "L3_evidence" / "603317.json").is_file()
    assert (st / "ensemble" / "603317.run2.md").is_file()
    assert (st / "_relative_buy_decision.json").is_file()
    assert (st / "market_view.md").is_file()
    assert n == 5          # 5 个真文件;锁与 _sem/ 不算


def test_mirror_skips_locks_and_sem(tmp_path):
    """`_sem/` 与 `*.lock` 是锁不是现场 —— 抄过去只会让 MANIFEST 天天变。"""
    scan = _staging(tmp_path)
    run = tmp_path / "run"
    retention.mirror_staging(scan, run)
    st = run / "trace" / "staging"
    assert not (st / "_sem").exists()
    assert not (st / "_l4_tasks.json.lock").exists()


def test_mirror_is_idempotent(tmp_path):
    """发布时跑一次、CP7 observe 再跑一次 —— 两次必须等价(否则清单永远对不上)。"""
    scan = _staging(tmp_path)
    run = tmp_path / "run"
    first = retention.mirror_staging(scan, run)
    second = retention.mirror_staging(scan, run)
    assert first == second
    assert len(list((run / "trace" / "staging").rglob("*.json"))) == 2


def test_snapshot_inputs_copies_slim_from_context_root(tmp_path, monkeypatch):
    """slim 住在活动引擎的 context **根目录**(不在 scan/<date>/ 下),所以镜像带不走它。
    这是审计 Table 2 第 1 行:卡片每个数字的来源,原先只有 sha256 活在任务簿里。"""
    monkeypatch.chdir(tmp_path)
    scan = _staging(tmp_path)
    ctx = tmp_path / ws.context_root()
    (ctx / "603317.SS_2026-08-25_slim.md").write_text("x" * 9000, encoding="utf-8")
    (ctx / "603317.SS_2026-08-25_slim_deep.md").write_text("y" * 5000, encoding="utf-8")
    (scan / "_harvest_list.txt").write_text("603317.SS\n", encoding="utf-8")
    run = tmp_path / "run"
    out = retention.snapshot_inputs(scan, run)
    assert out["slim"] == 2
    assert (run / "trace" / "inputs" / "slim" / "603317.SS_2026-08-25_slim.md").is_file()
    assert (run / "trace" / "inputs" / "slim" / "603317.SS_2026-08-25_slim_deep.md").is_file()


def test_snapshot_inputs_falls_back_to_finalists_when_harvest_list_missing(tmp_path, monkeypatch):
    """`_harvest_list.txt` 缺席(老 run / 中断)→ 从 finalists 现算 ticker,不是直接放弃。"""
    monkeypatch.chdir(tmp_path)
    scan = _staging(tmp_path)
    (tmp_path / ws.context_root() / "603317.SS_2026-08-25_slim.md").write_text("z", encoding="utf-8")
    out = retention.snapshot_inputs(scan, tmp_path / "run")
    assert out["slim"] == 1


def test_snapshot_inputs_copies_prompt_bodies(tmp_path, monkeypatch):
    """agent def / playbook / config 才是 prompt 本体;run 原先只有一个 git_sha 代表它们。"""
    monkeypatch.chdir(tmp_path)
    scan = _staging(tmp_path)
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "l4-card.md").write_text("铁律", encoding="utf-8")
    out = retention.snapshot_inputs(scan, tmp_path / "run")
    assert out["prompts"] == 1
    # 扁平化命名:`.claude/agents/l4-card.md` → `agents__l4-card.md`(同名不同目录不打架)
    assert (tmp_path / "run" / "trace" / "inputs" / "prompts" / "agents__l4-card.md").is_file()


def test_prompt_hashes_only_lists_existing_files(tmp_path, monkeypatch):
    """缺文件不进表 —— 空表的意思是「没记」,不是「干净」。"""
    monkeypatch.chdir(tmp_path)
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "l3-rank.md").write_text("A", encoding="utf-8")
    hashes = retention.prompt_hashes()
    assert list(hashes) == [".claude/agents/l3-rank.md"]
    (agents / "l3-rank.md").write_text("B", encoding="utf-8")
    assert retention.prompt_hashes() != hashes      # 内容变 → 指纹必须变


def test_manifest_detects_mutation_and_extra_files(tmp_path):
    """发布目录是普通权限:实测被无关工具写进过 `.omc/state/`、被回放覆盖过 run_health。
    清单的全部意义就是让这两件事**可检出**。"""
    run = tmp_path / "run"
    (run / "trace").mkdir(parents=True)
    (run / "summary.md").write_text("汇总", encoding="utf-8")
    (run / "trace" / "run_health.json").write_text('{"counts": {"cards": 5}}', encoding="utf-8")
    retention.write_manifest(run)
    assert retention.verify_manifest(run)["ok"] is True

    (run / "trace" / "run_health.json").write_text('{"counts": {"cards": 0}}', encoding="utf-8")
    res = retention.verify_manifest(run)
    assert res["ok"] is False and res["changed"] == ["trace/run_health.json"]

    retention.write_manifest(run)
    (run / "trace" / "hook-junk.json").write_text("{}", encoding="utf-8")
    res = retention.verify_manifest(run)
    assert res["ok"] is False and res["extra"] == ["trace/hook-junk.json"]

    retention.write_manifest(run)
    (run / "summary.md").unlink()
    res = retention.verify_manifest(run)
    assert res["ok"] is False and res["missing"] == ["summary.md"]


def test_manifest_format_is_sha256sum_compatible(tmp_path):
    """标准 `sha256sum` 两空格格式 —— 人手也能 `shasum -a 256 -c` 校验,不必信我们的代码。"""
    run = tmp_path / "run"
    run.mkdir()
    (run / "a.md").write_text("hello", encoding="utf-8")
    body = retention.write_manifest(run).read_text(encoding="utf-8")
    sha, sep, rel = body.rstrip("\n").partition("  ")
    assert sep == "  " and rel == "a.md" and len(sha) == 64


def test_manifest_excludes_itself(tmp_path):
    """清单自己不入清单,否则每写一次就自相矛盾一次。"""
    run = tmp_path / "run"
    (run / "trace").mkdir(parents=True)
    (run / "x.md").write_text("1", encoding="utf-8")
    retention.write_manifest(run)
    assert f"trace/{retention.MANIFEST_NAME}" not in retention.read_manifest(run)
    assert retention.verify_manifest(run)["ok"] is True


def test_verify_reports_no_manifest_for_old_runs(tmp_path):
    """2026-08-26 之前的 run 天然没有清单 —— 那是状态不是故障,不能报成「被改过」。"""
    run = tmp_path / "run"
    run.mkdir()
    res = retention.verify_manifest(run)
    assert res["ok"] is False and res["reason"] == "no-manifest"
    assert res["changed"] == [] and res["extra"] == []


def test_retain_never_raises_and_records_errors(tmp_path, monkeypatch):
    """留存是加法:它不该有能力毁掉一次已跑完的扫描(同 brief.safe_publish 口径)。"""
    monkeypatch.setattr(retention, "mirror_staging",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    res = retention.retain(tmp_path / "nope", tmp_path / "run")
    assert res["mirrored"] == 0
    assert any("mirror_staging" in e for e in res["errors"])


def test_retain_full_cycle_verifies(tmp_path, monkeypatch):
    """镜像 + 快照 + 清单 一趟下来,`verify` 必须绿 —— 这是活体验收①的单测版。"""
    monkeypatch.chdir(tmp_path)
    scan = _staging(tmp_path)
    run = tmp_path / ws.reports_root() / "scan" / "20260825_2149"
    run.mkdir(parents=True)
    (run / "summary.md").write_text("汇总", encoding="utf-8")
    res = retention.retain(scan, run)
    assert res["errors"] == []
    assert res["mirrored"] == 5
    assert retention.verify_manifest(run)["ok"] is True


@pytest.mark.parametrize("cmd,expect", [("manifest", 0), ("verify", 0)])
def test_cli_roundtrip(tmp_path, capsys, cmd, expect):
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.md").write_text("x", encoding="utf-8")
    retention.main(["manifest", str(run)])
    capsys.readouterr()
    assert retention.main([cmd, str(run)]) == expect
    assert json.loads(capsys.readouterr().out.splitlines()[0])["ok"] is True


def test_cli_verify_exit_1_when_tampered(tmp_path, capsys):
    """CLI 退出码要能进 CI/巡检 —— 被改过必须非 0。"""
    run = tmp_path / "run"
    run.mkdir()
    (run / "summary.md").write_text("x", encoding="utf-8")
    retention.write_manifest(run)
    (run / "summary.md").write_text("y", encoding="utf-8")
    assert retention.main(["verify", str(run)]) == 1
    capsys.readouterr()


def test_resolve_recorded_path_remaps_pre_isolation_context_root(tmp_path, monkeypatch):
    """2026-08-11 引擎隔离前的任务簿记的是裸 `context/…`(实测 113 处)。那个根没了、
    文件还在 —— 照字面找会把在场的产物报成 MISSING。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ws.context_root()).mkdir()
    real = tmp_path / ws.context_root() / "601288.SS_2026-07-28_slim.md"
    real.write_text("slim", encoding="utf-8")
    got = retention.resolve_recorded_path("context/601288.SS_2026-07-28_slim.md")
    assert got is not None and got.read_text(encoding="utf-8") == "slim"


def test_resolve_recorded_path_returns_none_when_really_gone(tmp_path, monkeypatch):
    """重映射只是**找同一件东西的新址**,不是把不存在的说成存在。"""
    monkeypatch.chdir(tmp_path)
    assert retention.resolve_recorded_path("context/nope_slim.md") is None
    assert retention.resolve_recorded_path((ws.context_root() / "nope_slim.md").as_posix()) is None


# ── transcript 归档(2026-08-26 §4.3 R4)──────────────────────────────────────

def _usage_ledger(scan, tmp_path, rows):
    import json as _json
    (scan / "_token_usage.json").write_text(
        _json.dumps({"schema_version": 1, "rows": rows}, ensure_ascii=False),
        encoding="utf-8")


def test_archive_transcripts_takes_only_judgement_agents(tmp_path):
    """卡片是结论,transcript 是**怎么得出结论的** —— 但 47 份命令壳与主会话不收
    (壳零判断;主会话是 session 级产物,可能混入与本次扫描无关的内容)。"""
    scan = _staging(tmp_path)
    tdir = tmp_path / "transcripts"
    tdir.mkdir()
    rows = []
    for agent in ("l4-card", "l3-rank", "general-purpose", "(主会话)"):
        p = tdir / f"agent-{agent}.jsonl"
        p.write_text('{"x": 1}\n' * 200, encoding="utf-8")
        rows.append({"agent": agent, "path": str(p)})
    _usage_ledger(scan, tmp_path, rows)
    run = tmp_path / "run"
    res = retention.archive_transcripts(scan, run)
    got = sorted(p.name for p in (run / "trace" / "transcripts").glob("*.jsonl.gz"))
    assert res["n"] == 2
    assert got == ["l3-rank-agent-l3-rank.jsonl.gz", "l4-card-agent-l4-card.jsonl.gz"]


def test_archive_transcripts_is_byte_idempotent(tmp_path):
    """gzip 头默认带时间戳 → 同输入两次产出不同字节 → MANIFEST 天天变、`verify` 天天红。
    变异探针:去掉 `mtime=0` 这条用例必红。"""
    scan = _staging(tmp_path)
    p = tmp_path / "agent-l4-card.jsonl"
    p.write_text('{"x": 1}\n' * 50, encoding="utf-8")
    _usage_ledger(scan, tmp_path, [{"agent": "l4-card", "path": str(p)}])
    run = tmp_path / "run"
    retention.archive_transcripts(scan, run)
    first = (run / "trace" / "transcripts" / "l4-card-agent-l4-card.jsonl.gz").read_bytes()
    retention.archive_transcripts(scan, run)
    second = (run / "trace" / "transcripts" / "l4-card-agent-l4-card.jsonl.gz").read_bytes()
    assert first == second
    # ⚠️ 上面那条**单独不够**:同一秒内两次 compress 的 mtime 本来就相同,去掉 `mtime=0`
    #    它照样绿(实测,变异探针 M7)。真正的判据是 gzip 头第 4–8 字节(MTIME 字段)恒零 ——
    #    跨秒/跨天重跑才不会让 MANIFEST 天天变。
    assert first[4:8] == b"\x00\x00\x00\x00"


def test_archive_transcripts_records_gone_without_lying(tmp_path):
    """transcript 住在 harness 管的目录里、受其保留策略约束 —— 已经被清掉的要记 `GONE`,
    不能悄悄少一份。"""
    scan = _staging(tmp_path)
    _usage_ledger(scan, tmp_path, [{"agent": "l4-card", "path": str(tmp_path / "gone.jsonl")}])
    run = tmp_path / "run"
    res = retention.archive_transcripts(scan, run)
    assert res["n"] == 0 and res["gone"] == 1
    idx = json.loads((run / "trace" / "transcripts" / "_index.json").read_text(encoding="utf-8"))
    assert idx["transcripts"][0]["status"] == "GONE"


def test_archive_transcripts_presence_gated(tmp_path):
    """计量还没跑(发布那一刻的常态)→ 静默 0,不报错、不留空目录。"""
    scan = _staging(tmp_path)
    res = retention.archive_transcripts(scan, tmp_path / "run")
    assert res == {"n": 0, "bytes": 0, "reason": "no-usage-ledger"}
    assert not (tmp_path / "run" / "trace" / "transcripts").exists()


def test_archive_transcripts_survives_broken_ledger(tmp_path):
    scan = _staging(tmp_path)
    (scan / "_token_usage.json").write_text("{坏", encoding="utf-8")
    assert retention.archive_transcripts(scan, tmp_path / "run")["reason"] == "bad-usage-ledger"


# ── 湖清单(2026-08-26 §4.2 #13 / §5 P3)────────────────────────────────────

def _lake(tmp_path, days, extras=()):
    root = tmp_path / "lake"
    (root / "daily").mkdir(parents=True)
    for d in days:
        (root / "daily" / f"{d}.parquet").write_bytes(b"D" + d.encode())
    (root / "stock_basic").mkdir()
    (root / "stock_basic" / "static.parquet").write_bytes(b"basic-v1")
    for ep, stem, body in extras:
        (root / ep).mkdir(exist_ok=True)
        (root / ep / f"{stem}.parquet").write_bytes(body)
    return root


def test_lake_manifest_covers_window_and_static(tmp_path):
    root = _lake(tmp_path, ["20260820", "20260821", "20260824", "20260825"],
                 extras=[("stock_news_em", "000001@20260825", b"news")])
    doc = retention.lake_manifest("2026-08-25", lake_root=root, window_days=2)
    keys = set(doc["files"])
    assert "stock_basic/static" in keys                 # 「刷新即覆盖」的那一类,必须在
    assert "daily/20260825" in keys and "daily/20260824" in keys
    assert "daily/20260820" not in keys                 # 窗口之外
    assert "stock_news_em/000001@20260825" in keys      # as_of 键按 @ 后的日期归窗


def test_lake_manifest_detects_static_overwrite(tmp_path):
    """`stock_basic` 刷新即覆盖 —— 行业分类/上市日/ST 标记会在过去的 run 底下悄悄变。
    这正是本清单最该盯的一类。"""
    root = _lake(tmp_path, ["20260825"])
    before = retention.lake_manifest("2026-08-25", lake_root=root)["files"]
    (root / "stock_basic" / "static.parquet").write_bytes(b"basic-v2-restated")
    after = retention.lake_manifest("2026-08-25", lake_root=root)["files"]
    assert before["stock_basic/static"] != after["stock_basic/static"]


def test_lake_manifest_note_does_not_overclaim(tmp_path):
    """它证明「文件是不是同一批」,**不**证明「当天读过它们」。措辞里必须写着这句话 ——
    过度声称的留痕比没有留痕更危险(会让人以为 lineage 已经有了)。"""
    doc = retention.lake_manifest("2026-08-25", lake_root=_lake(tmp_path, ["20260825"]))
    assert "不等于" in doc["note"] and "lineage" in doc["note"]


def test_diff_lake_manifest(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for run, body in ((a, b"v1"), (b, b"v2")):
        (run / "trace").mkdir(parents=True)
        (run / "trace" / "lake_manifest.json").write_text(
            json.dumps({"files": {"stock_basic/static": body.decode(),
                                  "daily/20260825": "same"}}), encoding="utf-8")
    d = retention.diff_lake_manifest(a, b)
    assert d["changed"] == ["stock_basic/static"] and d["only_a"] == []
    assert retention.diff_lake_manifest(a, tmp_path / "nope")["reason"] == "no-manifest"


def test_lake_manifest_missing_lake_is_not_an_error(tmp_path):
    doc = retention.lake_manifest("2026-08-25", lake_root=tmp_path / "nope")
    assert doc["files"] == {} and "不存在" in doc["note"]


def test_write_lake_manifest_derives_exact_reads_from_active_capsule(tmp_path):
    workspace = tmp_path / "scan_runs" / "20260827T010203456789Z"
    scan = workspace / "staging" / "2026-08-27"
    capsule = workspace / "capsule"
    reads = capsule / "lineage" / "reads.jsonl"
    scan.mkdir(parents=True)
    reads.parent.mkdir(parents=True)
    frame = pd.DataFrame({"close": [10.5]})
    raw = retention._dataframe_bytes(frame)
    digest = hashlib.sha256(raw).hexdigest()
    blob = capsule / "blobs/sha256" / digest[:2] / digest
    blob.parent.mkdir(parents=True)
    blob.write_bytes(raw)
    rows = [
        {
            "schema_version": 1,
            "endpoint": "daily",
            "path": "lake/daily/20260825.parquet",
            "blob_hash": digest,
            "bytes": len(raw),
            "rows": 1,
            "columns_hash": retention._frame_columns_hash(frame),
            "status": "SUCCEEDED",
            "evidence_complete": True,
        },
        {
            "schema_version": 1,
            "endpoint": "daily_basic",
            "path": None,
            "blob_hash": None,
            "bytes": None,
            "status": "FAILED",
            "evidence_complete": False,
        },
    ]
    reads.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    run = tmp_path / "report"

    assert retention.write_lake_manifest(scan, run) == 1
    doc = json.loads((run / "trace/lake_manifest.json").read_text(encoding="utf-8"))
    assert doc["schema"] == "exact_reads"
    assert doc["source_mode"] == "exact_reads"
    assert doc["files"] == {"daily/20260825": f"{digest}:{len(raw)}"}
    assert doc["n_failed_reads"] == 1


def test_readable_all_failed_lineage_is_still_exact_not_window_guess(tmp_path, monkeypatch):
    workspace = tmp_path / "scan_runs" / "20260827T010203456789Z"
    scan = workspace / "staging" / "2026-08-27"
    reads = workspace / "capsule" / "lineage" / "reads.jsonl"
    scan.mkdir(parents=True)
    reads.parent.mkdir(parents=True)
    reads.write_text(
        json.dumps(
            {
                "status": "FAILED",
                "endpoint": "daily",
                "blob_hash": None,
                "evidence_complete": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        retention,
        "lake_manifest",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not guess")),
    )

    run = tmp_path / "report"
    assert retention.write_lake_manifest(scan, run) == 0
    doc = json.loads((run / "trace/lake_manifest.json").read_text(encoding="utf-8"))
    assert doc["source_mode"] == "exact_reads"
    assert doc["files"] == {}
    assert doc["n_total_reads"] == 1
    assert doc["n_successful_reads"] == 0
    assert doc["n_failed_reads"] == 1
    assert doc["n_incomplete_reads"] == 0
    assert doc["n_no_blob_reads"] == 0


def test_readable_success_without_blob_is_exact_and_counted_incomplete(tmp_path, monkeypatch):
    workspace = tmp_path / "scan_runs" / "20260827T010203456789Z"
    scan = workspace / "staging" / "2026-08-27"
    reads = workspace / "capsule" / "lineage" / "reads.jsonl"
    scan.mkdir(parents=True)
    reads.parent.mkdir(parents=True)
    reads.write_text(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "endpoint": "daily",
                "blob_hash": None,
                "evidence_complete": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        retention,
        "lake_manifest",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not guess")),
    )

    run = tmp_path / "report"
    retention.write_lake_manifest(scan, run)
    doc = json.loads((run / "trace/lake_manifest.json").read_text(encoding="utf-8"))
    assert doc["source_mode"] == "exact_reads"
    assert doc["files"] == {}
    assert doc["n_total_reads"] == 1
    assert doc["n_successful_reads"] == 1
    assert doc["n_failed_reads"] == 0
    assert doc["n_incomplete_reads"] == 1
    assert doc["n_no_blob_reads"] == 1


def test_empty_readable_lineage_is_exact_with_zero_counts(tmp_path, monkeypatch):
    workspace = tmp_path / "scan_runs" / "20260827T010203456789Z"
    scan = workspace / "staging" / "2026-08-27"
    reads = workspace / "capsule" / "lineage" / "reads.jsonl"
    scan.mkdir(parents=True)
    reads.parent.mkdir(parents=True)
    reads.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        retention,
        "lake_manifest",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not guess")),
    )

    run = tmp_path / "report"
    retention.write_lake_manifest(scan, run)
    doc = json.loads((run / "trace/lake_manifest.json").read_text(encoding="utf-8"))
    assert doc["source_mode"] == "exact_reads"
    assert doc["files"] == {}
    assert doc["n_total_reads"] == 0


def test_unreadable_lineage_falls_back_with_reason(tmp_path, monkeypatch):
    workspace = tmp_path / "scan_runs" / "20260827T010203456789Z"
    scan = workspace / "staging" / "2026-08-27"
    reads = workspace / "capsule" / "lineage" / "reads.jsonl"
    scan.mkdir(parents=True)
    reads.parent.mkdir(parents=True)
    reads.write_text("{broken\n", encoding="utf-8")
    monkeypatch.setattr(
        retention,
        "lake_manifest",
        lambda date: {
            "schema_version": 1,
            "date": date,
            "n_files": 0,
            "files": {},
            "note": "guess",
        },
    )

    run = tmp_path / "report"
    retention.write_lake_manifest(scan, run)
    doc = json.loads((run / "trace/lake_manifest.json").read_text(encoding="utf-8"))
    assert doc["source_mode"] == "window_guess"
    assert doc["source_reason"] == "lineage_unreadable"


def test_exact_manifest_requires_matching_source_event_and_no_gap(tmp_path, monkeypatch):
    workspace = tmp_path / "scan_runs" / "20260827T010203456789Z"
    scan = workspace / "staging" / "2026-08-27"
    lineage = workspace / "capsule" / "lineage"
    events = workspace / "capsule" / "events"
    scan.mkdir(parents=True)
    lineage.mkdir(parents=True)
    events.mkdir(parents=True)
    row = {
        "status": "SUCCEEDED",
        "endpoint": "daily",
        "path": None,
        "blob_hash": None,
        "bytes": None,
        "rows": 1,
        "columns_hash": "a" * 64,
        "evidence_complete": True,
        "correlation_id": "source-correlation",
    }
    (lineage / "reads.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (events / "events.jsonl").write_text("", encoding="utf-8")
    (lineage / "evidence_gaps.jsonl").write_text(
        json.dumps({"correlation_id": "source-correlation"}) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        retention,
        "lake_manifest",
        lambda date: {"schema_version": 1, "date": date, "files": {}, "note": "guess"},
    )

    run = tmp_path / "report"
    retention.write_lake_manifest(scan, run)
    doc = json.loads((run / "trace/lake_manifest.json").read_text(encoding="utf-8"))
    assert doc["source_mode"] == "exact_reads"
    assert doc["n_incomplete_reads"] == 1
    assert doc["n_missing_source_events"] == 1
    assert doc["n_evidence_gaps"] == 1


@pytest.mark.parametrize("malformation", ["digest", "size", "content", "parquet_rows"])
def test_malformed_blob_semantics_fall_back_without_aborting(
    tmp_path, monkeypatch, malformation
):
    workspace = tmp_path / "scan_runs" / "20260827T010203456789Z"
    scan = workspace / "staging" / "2026-08-27"
    capsule = workspace / "capsule"
    reads = capsule / "lineage/reads.jsonl"
    scan.mkdir(parents=True)
    reads.parent.mkdir(parents=True)
    frame = pd.DataFrame({"x": [1]})
    raw = retention._dataframe_bytes(frame)
    digest = hashlib.sha256(raw).hexdigest()
    blob = capsule / "blobs/sha256" / digest[:2] / digest
    blob.parent.mkdir(parents=True)
    blob.write_bytes(raw)
    row = {
        "status": "SUCCEEDED",
        "endpoint": "daily",
        "path": None,
        "blob_hash": digest,
        "bytes": len(raw),
        "rows": 1,
        "columns_hash": retention._frame_columns_hash(frame),
        "evidence_complete": False,
    }
    if malformation == "digest":
        row["blob_hash"] = "not-a-digest"
    elif malformation == "size":
        row["bytes"] += 1
    elif malformation == "content":
        blob.write_bytes(raw + b"corrupt")
    else:
        row["rows"] = 2
    reads.write_text(json.dumps(row) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        retention,
        "lake_manifest",
        lambda date: {"schema_version": 1, "date": date, "files": {}, "note": "guess"},
    )

    run = tmp_path / "report"
    assert retention.write_lake_manifest(scan, run) == 0
    doc = json.loads((run / "trace/lake_manifest.json").read_text(encoding="utf-8"))
    assert doc["source_mode"] == "window_guess"
    assert doc["source_reason"] == "lineage_unreadable"


def test_exact_manifest_deduplicates_repeated_path_content_versions(tmp_path):
    workspace = tmp_path / "scan_runs" / "20260827T010203456789Z"
    scan = workspace / "staging" / "2026-08-27"
    capsule = workspace / "capsule"
    reads = capsule / "lineage/reads.jsonl"
    scan.mkdir(parents=True)
    reads.parent.mkdir(parents=True)
    versions = []
    for value in (1, 2):
        frame = pd.DataFrame({"x": [value]})
        raw = retention._dataframe_bytes(frame)
        digest = hashlib.sha256(raw).hexdigest()
        blob = capsule / "blobs/sha256" / digest[:2] / digest
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(raw)
        versions.append((digest, len(raw), retention._frame_columns_hash(frame)))
    rows = []
    for digest, size, columns_hash in (
        versions[0], versions[1], versions[1], versions[0]
    ):
        rows.append(
            {
                "status": "SUCCEEDED",
                "endpoint": "daily",
                "path": "lake/daily/20260825.parquet",
                "blob_hash": digest,
                "bytes": size,
                "rows": 1,
                "columns_hash": columns_hash,
                "evidence_complete": False,
            }
        )
    reads.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    doc, reason = retention._read_exact_manifest(scan)
    assert reason == ""
    assert doc["n_files"] == 2
    assert len(doc["files"]) == 2


def test_write_lake_manifest_falls_back_to_labeled_window_guess(tmp_path, monkeypatch):
    scan = tmp_path / "scan" / "2026-08-25"
    scan.mkdir(parents=True)
    monkeypatch.setattr(retention, "lake_manifest", lambda date: {
        "schema_version": 1,
        "date": date,
        "n_files": 0,
        "files": {},
        "note": "guess",
    })
    run = tmp_path / "run"
    retention.write_lake_manifest(scan, run)
    doc = json.loads((run / "trace/lake_manifest.json").read_text(encoding="utf-8"))
    assert doc["schema"] == "window_guess"
    assert doc["source_mode"] == "window_guess"


def test_write_lake_manifest_does_not_rewrite_historical_manifest(tmp_path, monkeypatch):
    run = tmp_path / "run"
    target = run / "trace/lake_manifest.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"n_files":7,"historical":true}', encoding="utf-8")
    monkeypatch.setattr(retention, "lake_manifest", lambda *_: (_ for _ in ()).throw(AssertionError()))
    assert retention.write_lake_manifest(tmp_path / "scan" / "2026-08-25", run) == 7
    assert target.read_text(encoding="utf-8") == '{"n_files":7,"historical":true}'


def test_retain_calls_lake_manifest_before_writing_manifest(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(retention, "mirror_staging", lambda *args: 0)
    monkeypatch.setattr(retention, "snapshot_inputs", lambda *args: {})
    monkeypatch.setattr(retention, "archive_transcripts", lambda *args: {})
    monkeypatch.setattr(
        retention,
        "write_lake_manifest",
        lambda *args: calls.append("lake") or 3,
    )
    monkeypatch.setattr(
        retention,
        "write_manifest",
        lambda *args: calls.append("manifest") or (tmp_path / "manifest"),
    )

    result = retention.retain(tmp_path / "scan", tmp_path / "run")
    assert calls == ["lake", "manifest"]
    assert result["lake_files"] == 3
