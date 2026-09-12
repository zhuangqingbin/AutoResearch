"""可审阅回填与恢复(Task C3,2026-09-12 §6):把「受旧日期口径影响的账本」变成一件可
审阅、可回滚的迁移,而不是一次直接改写。

design: `.superpowers/sdd/2026-09-12-outcome-trading-calendar-integrity/task-C3-brief.md` §6

覆盖验收矩阵 C12(dry-run 无数据写、`--run-id` 精确范围、迁移中断可从前镜像恢复)与
C13(相同输入两次应用不重复、关键内容一致)。全部只写 `tmp_path`,不发网络、不读真实
`lake/`、不碰任何冻结 run 之外的字段——`compute_outcome` 本身在 Task C1/C2 已被逐条锁定
(见 `test_outcome_calendar.py`/`test_outcome.py`),这里直接把它换成受控 fake,只验证
Task C3 新增的迁移机器本身:范围选择、前/后镜像、diff 结构、原子写入点、中断可恢复、
幂等。

本文件不依赖 `test_outcome_calendar.py`/`test_outcome.py`(各自独立可跑),只复用与它们
同款的最小 run 夹具写法。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import outcome

# ───────────────────────── 夹具:发布目录(供 published_runs/run_facts 定位) ─────────────────────────

def _run(tmp_path: Path, run_id: str, date_str: str, codes: tuple[str, ...] = ("000001",)) -> Path:
    """`published_runs()`/`run_facts()` 需要的最小发布目录——本文件的 `compute_outcome`
    被整体 fake 掉,这里只需要让 run 能被**发现**、`run_facts` 能读到 codes 集合
    (用于 diff 的 code 并集),不需要真实的湖/日历。
    """
    run = tmp_path / ws.reports_root() / "scan" / run_id
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps(
        {"analysis_date": date_str, "run_id": f"{run_id}T000000000000Z",
         "generated_at": f"{date_str}T21:00:00"}), encoding="utf-8")
    base = run / "trace" / "staging"
    base.mkdir(parents=True)
    lines = "code,name,sector,lane,guard,conviction\n" + "".join(
        f"{c},测试{c},行业,,,50\n" for c in codes)
    (base / "finalists.csv").write_text(lines, encoding="utf-8")
    (base / "_final_ratings.json").write_text(json.dumps(dict.fromkeys(codes, "Hold")),
                                               encoding="utf-8")
    (base / "_relative_buy_decision.json").write_text(json.dumps(
        {"mode": "active", "rule_version": "e6.v2.0", "blocked": False,
         "buys": [{"code": c, "rank": 1, "basis": "relative"} for c in codes],
         "candidates": []}), encoding="utf-8")
    return run


# ───────────────────────── 受控 compute_outcome(不碰真实湖/日历) ─────────────────────────

def _old_doc(run_id: str, date_str: str, code: str, *, gap: float = 0.08,
            t1: str = "20260827", t2: str = "20260828") -> dict:
    """schema 1、`complete=True`、日期是"湖分区排序位置"算出来的错误日期——与
    `test_outcome.py::test_c10_...` 同款,代表本任务真正要修的那类历史行。"""
    return {
        "schema_version": 1, "run_id": run_id, "contract_run_id": f"{run_id}Tcap",
        "analysis_date": date_str, "ruler": outcome.MAIN, "t1": t1, "t2": t2,
        "decision_mode": "active", "rule_version": "e6.v2.0",
        "read_from_shared_staging": False, "complete": True,
        "n_rows": 1, "n_scored": 1,
        "exec_line": {"max_pct_1d": 3.0, "max_pos_in_range": 0.7}, "execution": None,
        "rows": {code: {"code": code, "name": f"测试{code}", "sector": "行业",
                        "role": "BUY", "e6_buy": True, outcome.MAIN: gap}},
    }


def _new_doc(run_id: str, date_str: str, code: str, *, status: str = outcome.MATURE,
            gap: float | None = 0.02, t1: str = "20260826", t2: str = "20260827",
            reason: str = "") -> dict:
    """Task C1/C2 修好之后,同一个 run 按可信日历重算出来的 schema 2 文档。"""
    rows = {code: {"code": code, "name": f"测试{code}", "sector": "行业",
                   "role": "BUY", "e6_buy": True, outcome.MAIN: gap}} if status == outcome.MATURE else {}
    return {
        "schema_version": 2, "run_id": run_id, "contract_run_id": f"{run_id}Tcap",
        "analysis_date": date_str, "ruler": outcome.MAIN,
        "outcome_status": status, "reason": reason,
        "calendar_quality": outcome.TRADE_CAL_QUALITY, "calendar_digest": "realcal01",
        "t1": t1, "t2": t2,
        "decision_mode": "active", "rule_version": "e6.v2.0",
        "read_from_shared_staging": False,
        "complete": status == outcome.MATURE, "n_rows": 1,
        "n_scored": 1 if status == outcome.MATURE else 0,
        "exec_line": {"max_pct_1d": 3.0, "max_pos_in_range": 0.7}, "execution": None,
        "rows": rows,
    }


def _fake_compute_outcome(monkeypatch, docs: dict[str, dict | None]) -> None:
    """把 `outcome.compute_outcome` 换成按 `run_dir.name` 分派的受控 fake——
    `plan_outcome_migration` 内部调用的是模块级裸名 `compute_outcome(...)`,
    monkeypatch 模块属性对它生效(与既有 `_fake_market` 同一手法)。
    """
    def fake(run_dir, **_kwargs):
        return docs.get(Path(run_dir).name)
    monkeypatch.setattr(outcome, "compute_outcome", fake)


def _seed_old_state(root: Path, run: Path, doc: dict) -> None:
    """把 `doc` 同时写进逐 run JSON 与 CSV——模拟"当年被 fill() 写过一次"的起点。"""
    outcome.write_outcome(doc, root)
    outcome.upsert_ledger(doc, root)


# ───────────────────────── C12:dry-run 无数据写 ─────────────────────────

def test_dry_run_writes_nothing_to_outcome_json_csv_or_frozen_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    old = _old_doc(run.name, "2026-08-25", "000001")
    _seed_old_state(root, run, old)
    before_outcome_bytes = outcome.outcome_path(run.name, root).read_bytes()
    before_csv_bytes = (outcome.ledger_root(root) / outcome.LEDGER_CSV).read_bytes()
    before_manifest_bytes = (run / "manifest.json").read_bytes()

    new = _new_doc(run.name, "2026-08-25", "000001", gap=0.015)
    _fake_compute_outcome(monkeypatch, {run.name: new})

    res = outcome.fill(reports_root=root, dry_run=True, rebuild=True)

    assert res["ok"] is True and res["dry_run"] is True
    # 三处保护目标逐字节不变:outcome JSON、CSV、冻结 run(manifest 代表)。
    assert outcome.outcome_path(run.name, root).read_bytes() == before_outcome_bytes
    assert (outcome.ledger_root(root) / outcome.LEDGER_CSV).read_bytes() == before_csv_bytes
    assert (run / "manifest.json").read_bytes() == before_manifest_bytes
    # 迁移目录本身**应该**被写出来(它是"可审阅的 diff",不是"什么都不留痕")。
    mdir = Path(res["migration_dir"])
    assert (mdir / outcome.MIGRATION_DIFF_FILE).is_file()
    assert (mdir / outcome.MIGRATION_STATE_FILE).is_file()
    state = json.loads((mdir / outcome.MIGRATION_STATE_FILE).read_text(encoding="utf-8"))
    assert state["status"] == "planned"
    assert all(not r["applied"] for r in state["runs"].values())


def test_dry_run_diff_has_the_required_columns_and_population(tmp_path, monkeypatch):
    """bullet 3/9:diff 必须逐行给出 run/code、旧新 t1/t2、旧新主收益、旧新成熟状态、
    失效原因,并单独给出受影响行数与人口——这是人在批准真实回填前读的东西。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    old = _old_doc(run.name, "2026-08-25", "000001", gap=0.08, t1="20260827", t2="20260828")
    _seed_old_state(root, run, old)
    new = _new_doc(run.name, "2026-08-25", "000001", gap=0.015, t1="20260826", t2="20260827")
    _fake_compute_outcome(monkeypatch, {run.name: new})

    res = outcome.fill(reports_root=root, dry_run=True, rebuild=True)
    diff = json.loads((Path(res["migration_dir"]) / outcome.MIGRATION_DIFF_FILE)
                      .read_text(encoding="utf-8"))

    assert diff["population"]["affected_runs"] == 1
    assert diff["population"]["affected_rows"] == 1
    row = next(r for r in diff["rows"] if r["code"] == "000001")
    assert row["run_id"] == run.name
    assert (row["old_t1"], row["new_t1"]) == ("20260827", "20260826")
    assert (row["old_t2"], row["new_t2"]) == ("20260828", "20260827")
    assert (row["old_main"], row["new_main"]) == (0.08, 0.015)
    assert (row["old_outcome_status"], row["new_outcome_status"]) == (None, outcome.MATURE)
    assert "reason" in row
    # source digests(bullet 1:"源文件摘要写入 diff")
    assert diff["source_ledger_digest"] and diff["source_ledger_digest"] != outcome.MIGRATION_ABSENT


def test_dry_run_reports_calendar_network_behavior_honestly(tmp_path, monkeypatch):
    """bullet 2:只读日历取数是否联网须如实说明——不能宣传成绝对零网络。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    _fake_compute_outcome(monkeypatch, {run.name: None})   # 无变化,只为了走通一次 dry-run
    res = outcome.fill(reports_root=root, dry_run=True, rebuild=True)
    assert isinstance(res.get("network"), str) and res["network"]


# ───────────────────────── C12:--run-id 精确范围 ─────────────────────────

def test_run_id_scopes_exactly_and_leaves_the_other_run_untouched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run_a = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    run_b = _run(tmp_path, "20260901-0902_2100", "2026-09-01")
    old_a = _old_doc(run_a.name, "2026-08-25", "000001")
    old_b = _old_doc(run_b.name, "2026-09-01", "000002")
    _seed_old_state(root, run_a, old_a)
    _seed_old_state(root, run_b, old_b)
    before_b_bytes = outcome.outcome_path(run_b.name, root).read_bytes()
    new_a = _new_doc(run_a.name, "2026-08-25", "000001", gap=0.01)
    new_b = _new_doc(run_b.name, "2026-09-01", "000002", gap=0.02)
    _fake_compute_outcome(monkeypatch, {run_a.name: new_a, run_b.name: new_b})

    res = outcome.fill(reports_root=root, run_id=run_a.name, rebuild=True)   # 真应用,精确范围

    assert res["ok"] is True and res["status"] == "applied"
    diff = json.loads((Path(res["migration_dir"]) / outcome.MIGRATION_DIFF_FILE)
                      .read_text(encoding="utf-8"))
    assert {r["run_id"] for r in diff["rows"]} == {run_a.name}
    # run_b 完全没被这次迁移看见:真实文件字节不变。
    assert outcome.outcome_path(run_b.name, root).read_bytes() == before_b_bytes
    csv_rows = outcome.load_ledger(root)
    assert {r["run_id"] for r in csv_rows if r["code"] == "000002"} == {run_b.name}
    b_row = next(r for r in csv_rows if r["run_id"] == run_b.name)
    assert b_row[outcome.MAIN] == "0.08"           # run_b 仍是旧值,未被这次迁移动过


def test_run_id_unknown_run_raises_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    with pytest.raises(ValueError, match="does-not-exist"):
        outcome.fill(reports_root=root, dry_run=True, run_id="does-not-exist")


# ─────────────────────── fix round 1 finding 1:裸 --rebuild 不能到达未审阅覆写 ───────────────────────

def test_bare_rebuild_refuses_instead_of_reaching_the_unaudited_overwrite(tmp_path, monkeypatch):
    """`fill(rebuild=True)`(既无 `dry_run` 也无 `run_id`)曾经会直通 `_fill_incremental`——
    对已核验通过的历史行做一次静默、不可审阅、不可恢复的就地覆写,恰恰是本任务存在的理由
    要防的那件事,而且是最容易被人不小心敲出来的命令形状。裸 `--rebuild` 必须拒绝执行,
    不是悄悄改道去做别的事——错误信息里要点名两条正确命令。
    """
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    old = _old_doc(run.name, "2026-08-25", "000001", gap=0.08)
    _seed_old_state(root, run, old)
    before_bytes = outcome.outcome_path(run.name, root).read_bytes()
    new = _new_doc(run.name, "2026-08-25", "000001", gap=0.01)
    _fake_compute_outcome(monkeypatch, {run.name: new})

    with pytest.raises(ValueError, match="--dry-run.*--run-id|--run-id.*--dry-run"):
        outcome.fill(reports_root=root, rebuild=True)

    # 拒绝必须真的什么都没做——不是"先做了一半才想起来拒绝"。
    assert outcome.outcome_path(run.name, root).read_bytes() == before_bytes
    assert not outcome.migrations_root(root).exists()


def test_bare_rebuild_cli_exits_nonzero_with_a_named_alternative(tmp_path, monkeypatch, capsys):
    """CLI 层同一条防线:`outcome fill --rebuild`(裸)必须以非零退出码结束,
    且打印的错误里点名 `--dry-run`/`--run-id` 两条审阅过的替代命令。"""
    monkeypatch.chdir(tmp_path)         # 同 test_cli_fill_and_line 既有手法
    rc = outcome.main(["fill", "--rebuild"])
    out = capsys.readouterr().out
    assert rc != 0
    payload = json.loads(out.splitlines()[0])
    assert payload["ok"] is False
    assert "--dry-run" in payload["error"] and "--run-id" in payload["error"]


def test_bare_fill_without_rebuild_is_unaffected(tmp_path, monkeypatch):
    """`rebuild=False`(缺省,平常夜间跑法)完全不受这次改动影响——只有 `rebuild=True`
    且既无 `dry_run` 也无 `run_id` 才拒绝。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    _fake_compute_outcome(monkeypatch, {})     # 不联网;facts 定位得到但 compute 无结果即可
    res = outcome.fill(reports_root=root)      # 既有夜间形状,不传 rebuild
    assert res["filled"] == 0 and "affected_runs" not in res     # 走的是朴素增量路径


# ───────────────────────── C12:迁移中断可从前镜像恢复 ─────────────────────────

def _two_run_scope(tmp_path, monkeypatch, root):
    run_a = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    run_b = _run(tmp_path, "20260826-0827_2100", "2026-08-26")
    old_a = _old_doc(run_a.name, "2026-08-25", "000001", gap=0.08)
    old_b = _old_doc(run_b.name, "2026-08-26", "000002", gap=0.09)
    _seed_old_state(root, run_a, old_a)
    _seed_old_state(root, run_b, old_b)
    new_a = _new_doc(run_a.name, "2026-08-25", "000001", gap=0.01)
    new_b = _new_doc(run_b.name, "2026-08-26", "000002", gap=0.02)
    _fake_compute_outcome(monkeypatch, {run_a.name: new_a, run_b.name: new_b})
    return run_a, run_b, old_a, old_b


def test_interrupted_apply_checkpoints_partial_progress_and_is_not_marked_applied(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run_a, run_b, old_a, old_b = _two_run_scope(tmp_path, monkeypatch, root)

    real_write_bytes = outcome.atomic_write_bytes

    def flaky(path, payload):
        # 只打断 run_b 的**真实**目标文件替换,不打断迁移目录自己的前/后镜像写入。
        if path.name == f"{run_b.name}.json" and path.parent.name == "outcome":
            raise OSError("simulated crash mid-apply")
        return real_write_bytes(path, payload)

    monkeypatch.setattr(outcome, "atomic_write_bytes", flaky)
    with pytest.raises(OSError, match="simulated crash"):
        outcome.run_outcome_migration(reports_root=root, run_id=None, rebuild=True, dry_run=False)

    # migration_id 由输入(scope)派生,不靠内部保存的一次性对象——重新计算一次范围
    # 应该能定位到同一个迁移目录。
    monkeypatch.setattr(outcome, "atomic_write_bytes", real_write_bytes)
    mig_id = outcome._migration_id(run_id=None, rebuild=True)
    mdir = outcome._migration_dir(mig_id, root)
    state = json.loads((mdir / outcome.MIGRATION_STATE_FILE).read_text(encoding="utf-8"))
    assert state["status"] != "applied"
    assert state["runs"][run_a.name]["applied"] is True
    assert state["runs"][run_b.name]["applied"] is False
    # run_a 已经换成新值;run_b 仍是旧值(中途失败不能记完成,也不能半写)。
    doc_a = json.loads(outcome.outcome_path(run_a.name, root).read_text(encoding="utf-8"))
    doc_b = json.loads(outcome.outcome_path(run_b.name, root).read_text(encoding="utf-8"))
    assert doc_a["rows"]["000001"][outcome.MAIN] == 0.01
    assert doc_b["rows"]["000002"][outcome.MAIN] == 0.09        # 未变,仍是旧的
    # CSV 还没到"一次性重建"那一步(全部 run 都 applied 之后才做)。
    csv_rows = {r["run_id"]: r for r in outcome.load_ledger(root)}
    assert csv_rows[run_b.name][outcome.MAIN] == "0.09"


def test_restore_after_interruption_recovers_via_content_hash_not_row_count(tmp_path, monkeypatch):
    """C12 的核心:恢复用的是本次 diff 指定的精确 before 文件,验证用内容 hash——
    不是"文件数对了就算数"。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run_a, run_b, old_a, old_b = _two_run_scope(tmp_path, monkeypatch, root)
    real_write_bytes = outcome.atomic_write_bytes

    def flaky(path, payload):
        if path.name == f"{run_b.name}.json" and path.parent.name == "outcome":
            raise OSError("simulated crash mid-apply")
        return real_write_bytes(path, payload)

    monkeypatch.setattr(outcome, "atomic_write_bytes", flaky)
    with pytest.raises(OSError):
        outcome.run_outcome_migration(reports_root=root, run_id=None, rebuild=True, dry_run=False)
    monkeypatch.setattr(outcome, "atomic_write_bytes", real_write_bytes)

    mig_id = outcome._migration_id(run_id=None, rebuild=True)
    result = outcome.restore_outcome_migration(mig_id, reports_root=root)

    assert result["ok"] is True and result["status"] == "restored"
    # 内容 hash 校验字段真实存在且为真——不是"数了几个文件"。
    assert result["verified"][run_a.name]["content_hash_ok"] is True
    assert result["verified"][run_b.name]["content_hash_ok"] is True
    assert "actual_hash" in result["verified"][run_a.name]
    # run_a(曾被写过新值)必须恢复回原始字节,不是"文件存在就算恢复"。
    restored_a = json.loads(outcome.outcome_path(run_a.name, root).read_text(encoding="utf-8"))
    assert restored_a["rows"]["000001"][outcome.MAIN] == 0.08     # 回到迁移前的旧值
    csv_rows = {r["run_id"]: r for r in outcome.load_ledger(root)}
    assert csv_rows[run_a.name][outcome.MAIN] == "0.08"
    assert csv_rows[run_b.name][outcome.MAIN] == "0.09"
    assert len({(r["run_id"], r["code"]) for r in outcome.load_ledger(root)}) == len(outcome.load_ledger(root))


def test_resume_with_the_same_inputs_completes_an_interrupted_migration(tmp_path, monkeypatch):
    """bullet 5 的另一半:不恢复,而是用同一份输入继续——已经写完的 run 不需要重来,
    没写完的接着写完,最终状态与"从未中断过"一致。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run_a, run_b, old_a, old_b = _two_run_scope(tmp_path, monkeypatch, root)
    real_write_bytes = outcome.atomic_write_bytes
    calls = {"n": 0}

    def flaky(path, payload):
        if path.name == f"{run_b.name}.json" and path.parent.name == "outcome" and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("simulated crash mid-apply")
        return real_write_bytes(path, payload)

    monkeypatch.setattr(outcome, "atomic_write_bytes", flaky)
    with pytest.raises(OSError):
        outcome.run_outcome_migration(reports_root=root, run_id=None, rebuild=True, dry_run=False)

    # 同一份输入(同样的 scope 参数)重新调用——不删任何东西,不传 migration_id。
    res = outcome.run_outcome_migration(reports_root=root, run_id=None, rebuild=True, dry_run=False)

    assert res["ok"] is True and res["status"] == "applied"
    doc_a = json.loads(outcome.outcome_path(run_a.name, root).read_text(encoding="utf-8"))
    doc_b = json.loads(outcome.outcome_path(run_b.name, root).read_text(encoding="utf-8"))
    assert doc_a["rows"]["000001"][outcome.MAIN] == 0.01
    assert doc_b["rows"]["000002"][outcome.MAIN] == 0.02
    csv_rows = {r["run_id"]: r for r in outcome.load_ledger(root)}
    assert csv_rows[run_a.name][outcome.MAIN] == "0.01"
    assert csv_rows[run_b.name][outcome.MAIN] == "0.02"
    assert len({(r["run_id"], r["code"]) for r in outcome.load_ledger(root)}) == len(outcome.load_ledger(root))


# ───────────────────────── C13:相同输入两次不产生重复 ─────────────────────────

def test_same_inputs_applied_twice_produce_no_duplicate_rows_and_identical_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    old = _old_doc(run.name, "2026-08-25", "000001", gap=0.08)
    _seed_old_state(root, run, old)
    new = _new_doc(run.name, "2026-08-25", "000001", gap=0.015)
    _fake_compute_outcome(monkeypatch, {run.name: new})

    first = outcome.fill(reports_root=root, run_id=run.name, rebuild=True)
    second = outcome.fill(reports_root=root, run_id=run.name, rebuild=True)

    assert first["migration_id"] == second["migration_id"]      # 同一份输入 → 同一次迁移
    assert first["status"] == "applied"
    assert second["status"] == "already_applied"
    rows = outcome.load_ledger(root)
    keys = [(r["run_id"], r["code"]) for r in rows]
    assert len(keys) == len(set(keys))                          # 无重复
    assert len(rows) == 1
    assert rows[0][outcome.MAIN] == "0.015"                      # 内容与第一次一致


def test_migration_id_is_derived_from_scope_not_the_clock(tmp_path, monkeypatch):
    """bullet 8:同一份输入两次规划得到同一个 migration_id;不同 --run-id 得到不同 id。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    old = _old_doc(run.name, "2026-08-25", "000001")
    _seed_old_state(root, run, old)
    new = _new_doc(run.name, "2026-08-25", "000001", gap=0.01)
    _fake_compute_outcome(monkeypatch, {run.name: new})

    id_1 = outcome._migration_id(run_id=run.name, rebuild=True)
    id_2 = outcome._migration_id(run_id=run.name, rebuild=True)
    id_3 = outcome._migration_id(run_id=None, rebuild=True)
    assert id_1 == id_2
    assert id_1 != id_3


def test_frozen_run_directory_is_never_written_during_a_real_apply(tmp_path, monkeypatch):
    """原始 frozen run 与 lake 不参与替换——发布目录里除了我们放进去的夹具文件,
    没有任何新文件出现,已有文件字节不变。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    old = _old_doc(run.name, "2026-08-25", "000001")
    _seed_old_state(root, run, old)
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    new = _new_doc(run.name, "2026-08-25", "000001", gap=0.01)
    _fake_compute_outcome(monkeypatch, {run.name: new})

    outcome.fill(reports_root=root, run_id=run.name, rebuild=True)

    after = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    assert after == before


def test_source_ledger_hash_precheck_refuses_a_stale_apply(tmp_path, monkeypatch):
    """bullet 4:应用前校验源账本 hash 未变化——规划之后、应用之前,如果账本被
    并发写入,拒绝盲目应用而不是覆盖别人的写入。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    old = _old_doc(run.name, "2026-08-25", "000001")
    _seed_old_state(root, run, old)
    new = _new_doc(run.name, "2026-08-25", "000001", gap=0.01)
    _fake_compute_outcome(monkeypatch, {run.name: new})

    plan_res = outcome.fill(reports_root=root, dry_run=True, run_id=run.name, rebuild=True)
    # 规划之后,模拟另一个进程往账本追加了一行(源账本 hash 因此改变)。
    csv_path = outcome.ledger_root(root) / outcome.LEDGER_CSV
    with csv_path.open("a", encoding="utf-8") as fh:
        fh.write("\n")

    with pytest.raises(RuntimeError, match="源账本"):
        outcome.apply_outcome_migration(plan_res["migration_id"], reports_root=root)


# ─────────────── fix round 1 finding 4:中断点挪到「一次性重建 CSV」那个循环内部 ───────────────
#
# 此前只测过"JSON 替换阶段"中断——所有 run 的真实文件都已经换成新值之后,「一次性重建
# CSV」那个循环本身还在跑(见 finding 2:这个循环现在是 N 次各自原子的 upsert_ledger
# 调用,不是一次事务),复核指出这条路径完全没有中断测试过。这里补上,并顺带验证一个
# 复核发现的真实缺陷:旧代码的账本 hash 前置校验会在这个中断点之后的续跑里误报
# "源账本被并发写入"(把这次迁移自己刚写下的合法进度当成了别人的破坏)。

def test_resume_completes_an_interruption_inside_the_csv_rebuild_loop(tmp_path, monkeypatch):
    """中断点在 JSON 阶段**之后**、CSV 全部重建**之前**:两个 run 的真实文件都已经是
    新值,`upsert_ledger` 只成功了一次。续跑必须(a)不误报账本 hash 不符,(b)补完
    CSV 重建,(c)不产生重复 `(run_id, code)` 行,(d)不重复应用已经 applied 的 JSON。
    """
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run_a, run_b, old_a, old_b = _two_run_scope(tmp_path, monkeypatch, root)
    real_upsert = outcome.upsert_ledger
    calls = {"n": 0}

    def flaky_upsert(doc, reports_root=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated crash mid csv-rebuild")
        return real_upsert(doc, reports_root)

    monkeypatch.setattr(outcome, "upsert_ledger", flaky_upsert)
    with pytest.raises(OSError, match="simulated crash mid csv-rebuild"):
        outcome.run_outcome_migration(reports_root=root, run_id=None, rebuild=True, dry_run=False)

    mig_id = outcome._migration_id(run_id=None, rebuild=True)
    mdir = outcome._migration_dir(mig_id, root)
    state = json.loads((mdir / outcome.MIGRATION_STATE_FILE).read_text(encoding="utf-8"))
    assert state["status"] == "applying"          # 不是 planned(已经推进过门),不是 applied
    assert state.get("csv_rebuilt") is not True
    assert all(r["applied"] for r in state["runs"].values())     # JSON 阶段确实已经全部完成
    doc_a = json.loads(outcome.outcome_path(run_a.name, root).read_text(encoding="utf-8"))
    doc_b = json.loads(outcome.outcome_path(run_b.name, root).read_text(encoding="utf-8"))
    assert doc_a["rows"]["000001"][outcome.MAIN] == 0.01
    assert doc_b["rows"]["000002"][outcome.MAIN] == 0.02

    monkeypatch.setattr(outcome, "upsert_ledger", real_upsert)
    # 续跑——这一步此前会被(现已修复的)账本 hash 误报挡住:CSV 已经被第一次
    # upsert_ledger 调用合法地改过,续跑不能再拿规划时那份基线去比对它。
    res = outcome.run_outcome_migration(reports_root=root, run_id=None, rebuild=True, dry_run=False)
    assert res["ok"] is True and res["status"] == "applied"
    csv_rows = outcome.load_ledger(root)
    by_run = {r["run_id"]: r for r in csv_rows}
    assert by_run[run_a.name][outcome.MAIN] == "0.01"
    assert by_run[run_b.name][outcome.MAIN] == "0.02"
    assert len({(r["run_id"], r["code"]) for r in csv_rows}) == len(csv_rows)   # 无重复行


def test_restore_recovers_after_an_interruption_inside_the_csv_rebuild_loop(tmp_path, monkeypatch):
    """同一个中断点,走恢复而不是续跑——两个 run 的真实 JSON 都要回到迁移前的旧值,
    CSV 也要回到迁移前的旧值,而且校验方式是内容 hash,不是行数或存在性
    (Task C3 原始验收 C12 的字面要求,这里对准的是 finding 4 新增的中断点)。
    """
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run_a, run_b, old_a, old_b = _two_run_scope(tmp_path, monkeypatch, root)
    real_upsert = outcome.upsert_ledger
    calls = {"n": 0}

    def flaky_upsert(doc, reports_root=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated crash mid csv-rebuild")
        return real_upsert(doc, reports_root)

    monkeypatch.setattr(outcome, "upsert_ledger", flaky_upsert)
    with pytest.raises(OSError, match="simulated crash mid csv-rebuild"):
        outcome.run_outcome_migration(reports_root=root, run_id=None, rebuild=True, dry_run=False)
    monkeypatch.setattr(outcome, "upsert_ledger", real_upsert)

    mig_id = outcome._migration_id(run_id=None, rebuild=True)
    result = outcome.restore_outcome_migration(mig_id, reports_root=root)

    assert result["ok"] is True and result["status"] == "restored"
    assert result["verified"][run_a.name]["content_hash_ok"] is True
    assert result["verified"][run_b.name]["content_hash_ok"] is True
    assert "actual_hash" in result["verified"][run_a.name]
    restored_a = json.loads(outcome.outcome_path(run_a.name, root).read_text(encoding="utf-8"))
    restored_b = json.loads(outcome.outcome_path(run_b.name, root).read_text(encoding="utf-8"))
    assert restored_a["rows"]["000001"][outcome.MAIN] == 0.08     # 迁移前的旧值,不是新值
    assert restored_b["rows"]["000002"][outcome.MAIN] == 0.09
    csv_rows = outcome.load_ledger(root)
    by_run = {r["run_id"]: r for r in csv_rows}
    assert by_run[run_a.name][outcome.MAIN] == "0.08"
    assert by_run[run_b.name][outcome.MAIN] == "0.09"
    assert len({(r["run_id"], r["code"]) for r in csv_rows}) == len(csv_rows)


# ══════════ fix round 2 ══════════

# ── finding 1(重要):apply → restore → re-apply 不能把撤回后的旧值悄悄覆写回 after ──

def test_apply_restore_reapply_ends_up_matching_before_images_not_stale_after_values(tmp_path, monkeypatch):
    """`restore_outcome_migration` 从不重置 `applied`/`csv_rebuilt`——旧代码里对同一个
    `migration_id` 再调一次 `apply_outcome_migration` 会把"已经 applied"的 run 整个跳过
    (marker 说已经做过),直接跑到"一次性重建 CSV"那一步,用规划时那份**已经过期**的
    `after/` 候选文档重建 `recommendations.csv`——这时真实的逐 run JSON 早已被
    restore 换回了迁移前的旧值,CSV 却被悄悄推回新值:两者从此互相矛盾,而这条链路
    (应用→恢复→再应用)正是「迁移中途出问题之后」操作员会走的那条路,必须是最安全的
    那条。

    修法(应用侧按内容 hash 核验,而非重置恢复侧的 marker——见 round-2 报告附录选择
    理由):`apply_outcome_migration` 不再盲信 `applied=True`,而是拿**当前磁盘内容**
    去对 `after_sha256`——真的还是 after 内容才信;如果现在是 `before_sha256`(最常见
    原因就是被 `restore_outcome_migration` 撤回过),就如实把 marker 改正成
    `applied=False` 并且**这一遍什么都不覆写**——不会把 restore 刚放回去的旧值悄悄
    换掉。`recommendations.csv` 因此维持 restore 留下的样子,不会又被拉回新值。
    """
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    old = _old_doc(run.name, "2026-08-25", "000001", gap=0.08)
    _seed_old_state(root, run, old)
    before_json_bytes = outcome.outcome_path(run.name, root).read_bytes()
    before_csv_bytes = (outcome.ledger_root(root) / outcome.LEDGER_CSV).read_bytes()

    new = _new_doc(run.name, "2026-08-25", "000001", gap=0.01)
    _fake_compute_outcome(monkeypatch, {run.name: new})

    # apply
    apply_res = outcome.fill(reports_root=root, run_id=run.name, rebuild=True)
    assert apply_res["status"] == "applied"
    mig_id = apply_res["migration_id"]
    assert outcome.outcome_path(run.name, root).read_bytes() != before_json_bytes   # 夹具健全性

    # restore
    restore_res = outcome.restore_outcome_migration(mig_id, reports_root=root)
    assert restore_res["verified"][run.name]["content_hash_ok"] is True
    assert outcome.sha256_bytes(outcome.outcome_path(run.name, root).read_bytes()) == \
        outcome.sha256_bytes(before_json_bytes)

    # re-apply(同一个 migration_id,不重新规划)——不能把 CSV/JSON 悄悄推回 after。
    reapply_res = outcome.apply_outcome_migration(mig_id, reports_root=root)
    assert reapply_res["ok"] is True

    real_json_bytes = outcome.outcome_path(run.name, root).read_bytes()
    real_csv_bytes = (outcome.ledger_root(root) / outcome.LEDGER_CSV).read_bytes()
    # 按内容 hash 比较,不是数行数/看文件存不存在(同 Task C3 全篇的既定纪律)。
    assert outcome.sha256_bytes(real_json_bytes) == outcome.sha256_bytes(before_json_bytes)
    assert outcome.sha256_bytes(real_csv_bytes) == outcome.sha256_bytes(before_csv_bytes)

    # marker 必须被如实改正,不是悄悄留着一个跟现实不符的 True。
    mdir = outcome._migration_dir(mig_id, root)
    state = json.loads((mdir / outcome.MIGRATION_STATE_FILE).read_text(encoding="utf-8"))
    assert state["runs"][run.name]["applied"] is False

    # "genuinely re-does the work"的另一半:marker 改正之后,一次**新的**、独立的
    # apply 调用必须真的能把它重新推到 after——不是从此永久卡死在 before。
    second_reapply = outcome.apply_outcome_migration(mig_id, reports_root=root)
    assert second_reapply["status"] == "applied"
    final_json = json.loads(outcome.outcome_path(run.name, root).read_text(encoding="utf-8"))
    assert final_json["rows"]["000001"][outcome.MAIN] == 0.01
    final_csv = {r["run_id"]: r for r in outcome.load_ledger(root)}
    assert final_csv[run.name][outcome.MAIN] == "0.01"


def test_reapply_after_restore_refuses_when_target_matches_neither_before_nor_after(tmp_path, monkeypatch):
    """hash 核验的第三分支:如果 marker 说 applied,但磁盘现状既不是 before 也不是
    after(第三方直接改写了文件),必须拒绝而不是猜——这正是"按内容 hash 核验,
    不只是重置 marker"更强的地方:它连"被恢复"之外的过期原因也接得住。"""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    old = _old_doc(run.name, "2026-08-25", "000001", gap=0.08)
    _seed_old_state(root, run, old)
    new = _new_doc(run.name, "2026-08-25", "000001", gap=0.01)
    _fake_compute_outcome(monkeypatch, {run.name: new})

    apply_res = outcome.fill(reports_root=root, run_id=run.name, rebuild=True)
    mig_id = apply_res["migration_id"]
    outcome.restore_outcome_migration(mig_id, reports_root=root)
    # 第三方(既不是这次迁移也不是它的 restore)直接改写了目标文件。
    outcome.outcome_path(run.name, root).write_text('{"mystery": true}', encoding="utf-8")

    with pytest.raises(RuntimeError, match=run.name):
        outcome.apply_outcome_migration(mig_id, reports_root=root)


# ── finding 2(复核发现的覆盖缺口):迁移侧的撤回快照此前没有任何断言锁住 ──

def test_plan_outcome_migration_records_a_withdrawal_snapshot_via_the_shared_helper(tmp_path, monkeypatch):
    """`plan_outcome_migration` 调用共用的 `_maybe_withdraw`(见 fix round 1 finding 3),
    但复核发现 `tests/scan/test_outcome_migration.py` 里连"previous"/"withdraw"字样都
    搜不到一次——删掉 `plan_outcome_migration` 里那一行调用不会让任何测试变红。这里补上
    直接断言:MATURE→非 MATURE 的过渡,候选文档必须带 `previous` 快照,内容与旧文档一致。
    """
    monkeypatch.chdir(tmp_path)
    root = tmp_path / ws.reports_root() / "scan"
    run = _run(tmp_path, "20260825-0826_2100", "2026-08-25")
    old = _old_doc(run.name, "2026-08-25", "000001", gap=0.08, t1="20260826", t2="20260827")
    _seed_old_state(root, run, old)
    new = _new_doc(run.name, "2026-08-25", "000001", status=outcome.MISSING_MARKET_DATA,
                   reason="行情缺失(湖无分区)")
    _fake_compute_outcome(monkeypatch, {run.name: new})

    plan = outcome.plan_outcome_migration(reports_root=root, run_id=run.name, rebuild=True)
    entry = plan["runs"][run.name]
    after_doc = json.loads(entry["after_bytes"].decode("utf-8"))

    assert after_doc["outcome_status"] == outcome.MISSING_MARKET_DATA
    assert after_doc["rows"] == {}                              # 非 MATURE,没有可汇总的行
    assert after_doc["previous"]["complete"] is True
    assert after_doc["previous"]["rows"]["000001"][outcome.MAIN] == 0.08
    assert after_doc["previous"]["t1"] == "20260826"
