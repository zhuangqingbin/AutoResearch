"""Critical ArtifactIndex: status, content hashes, collections, and persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from autoresearch.scan.artifacts import build_artifact_index, write_artifact_index

NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def _by_name(index: dict) -> dict[str, dict]:
    return {row["name"]: row for row in index["artifacts"]}


def test_index_hashes_scan_and_report_artifacts(tmp_path):
    scan = tmp_path / "context" / "scan" / "2026-07-28"
    report = tmp_path / "reports" / "scan" / "20260728_1400"
    (scan / "details").mkdir(parents=True)
    report.mkdir(parents=True)
    (scan / "market_pack.json").write_text('{"breadth":{}}', encoding="utf-8")
    (scan / "details" / "000001.md").write_text("# card", encoding="utf-8")
    (scan / "details" / "000002.md").write_text("# card 2", encoding="utf-8")
    (scan / "stage_results").mkdir()
    (scan / "stage_results" / "gate1.json").write_text(
        '{"stage":"gate1"}',
        encoding="utf-8",
    )
    (scan / "decision_records.json").write_text(
        '{"records":[]}',
        encoding="utf-8",
    )
    (scan / "_l4_tasks.json").write_text(
        '{"schema_version":1,"tasks":{}}',
        encoding="utf-8",
    )
    (scan / "_budget_observation.json").write_text(
        '{"schema_version":1,"status":"SUCCEEDED"}',
        encoding="utf-8",
    )
    (scan / "outbox").mkdir()
    (scan / "outbox" / "events.json").write_text(
        '{"events":[]}',
        encoding="utf-8",
    )
    (scan / "outbox" / "consumer_state.json").write_text(
        '{"receipts":[]}',
        encoding="utf-8",
    )
    (report / "summary.md").write_text("# summary", encoding="utf-8")
    (report / "manifest.json").write_text(
        '{"analysis_date":"2026-07-28"}',
        encoding="utf-8",
    )

    index = build_artifact_index(scan, report_dir=report, now=NOW)
    rows = _by_name(index)

    assert index["schema_version"] == 1
    assert index["analysis_date"] == "2026-07-28"
    assert rows["market_pack"]["status"] == "PRESENT"
    assert len(rows["market_pack"]["content_hash"]) == 64
    assert rows["l4_cards"]["status"] == "PRESENT"
    assert len(rows["l4_cards"]["content_hash"]) == 64
    assert rows["summary"]["status"] == "PRESENT"
    assert rows["stage_results"]["status"] == "PRESENT"
    assert len(rows["stage_results"]["content_hash"]) == 64
    assert rows["decision_records"]["status"] == "PRESENT"
    assert rows["l4_task_book"]["status"] == "PRESENT"
    assert rows["budget_observation"]["status"] == "PRESENT"
    assert len(rows["decision_records"]["content_hash"]) == 64
    assert rows["outbox_events"]["status"] == "PRESENT"
    assert rows["consumer_state"]["status"] == "PRESENT"
    # Wave10 B3:earlystop_shadow_queue / earlystop_shadow_cards 两条随整族退役 28→26;
    # 2026-08-21 learning 层退役再删 5 条(retro_attribution / rejection_attribution /
    # abstention_verdict / l3_audit_candidates / l3_audit_ledger)26→21。
    assert len(rows) == 21
    assert rows["finalists"]["status"] == "MISSING"
    assert rows["finalists"]["content_hash"] is None
    assert rows["market_pack"]["input_hash"] is None


def test_index_distinguishes_empty_from_missing(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    (scan / "finalists.csv").write_bytes(b"")
    rows = _by_name(build_artifact_index(scan, now=NOW))
    assert rows["finalists"]["status"] == "EMPTY"
    assert rows["l3_judged"]["status"] == "MISSING"


def test_write_index_is_atomic_and_carries_contract_identity(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    (scan / "run_contract.json").write_text(
        json.dumps({"run_id": "run-1", "contract_hash": "a" * 64}),
        encoding="utf-8",
    )
    path = write_artifact_index(scan, now=NOW)
    index = json.loads(path.read_text(encoding="utf-8"))
    assert path == scan / "artifact_index.json"
    assert index["run_id"] == "run-1"
    assert index["contract_hash"] == "a" * 64
    assert not (scan / "artifact_index.json.tmp").exists()


# ── Wave10 B1(2026-08-01):从 `tests/scan/test_l4_reuse.py` **迁移**过来的活契约 ──
#
# `read_finalists` 的前导零契约此前**唯一**的锁在 test_l4_reuse.py 里(那个文件自己在
# 第 111 行注明了这件事)。L4 卡 TTL 复用已于 2026-07-29 按用户裁定退役,整族要删 ——
# 但 `read_finalists` 住在 live 的 artifacts.py。直接删测试文件 = 静默孤立这个契约,
# 正是 [[deadcode-cleanup-wave-20260719]] 的教训。故先迁移,再删原文件。

def test_read_finalists_preserves_ticker_leading_zeros(tmp_path):
    """磁盘上代码列已丢前导零(002156→2156,如 int64 往返回写)→ 读口必须补回 6 位。"""
    from autoresearch.scan.artifacts import read_finalists

    fp = tmp_path / "finalists.csv"
    fp.write_text("ticker,code,name\n2156,2156,x\n300476,300476,y\n", encoding="utf-8")
    fin = read_finalists(fp)
    assert set(fin["ticker"]) == {"002156", "300476"}
    assert set(fin["code"]) == {"002156", "300476"}


def test_read_finalists_keeps_codes_as_strings(tmp_path):
    """补零只有在列本身是字符串时才成立 —— dtype 掉回 int64 会让 zfill 变成无声的 no-op。"""
    from autoresearch.scan.artifacts import read_finalists

    fp = tmp_path / "finalists.csv"
    fp.write_text("code,name\n2156,x\n", encoding="utf-8")
    assert read_finalists(fp)["code"].dtype == object


# ── 现场附录(2026-08-28 §6.3 / §6.4)────────────────────────────────────────────
#
# appendix 是**契约门控**产物:清单里加一项不能让所有历史 run 变红。判据只有一条 ——
# 这一次 run 自己的 `run_contract.artifact_schema_versions` 里有没有记这个名字。


def _contract(scan, versions: dict) -> None:
    scan.mkdir(parents=True, exist_ok=True)
    (scan / "run_contract.json").write_text(
        json.dumps({"run_id": "run-1", "contract_hash": "b" * 64,
                    "artifact_schema_versions": versions}),
        encoding="utf-8",
    )


def test_new_contract_makes_the_appendix_a_real_obligation(tmp_path):
    """契约登记了 appendix → 缺席必红(MISSING),不能靠「新文件而已」蒙混过去。"""
    scan, report = tmp_path / "2026-08-28", tmp_path / "run"
    report.mkdir()
    _contract(scan, {"summary": 1, "appendix": 1})
    rows = _by_name(build_artifact_index(scan, report_dir=report, now=NOW))
    assert rows["appendix"]["status"] == "MISSING"
    assert rows["appendix"]["root"] == "report" and rows["appendix"]["path"] == "appendix.md"


def test_present_appendix_is_hashed_like_any_other_artifact(tmp_path):
    scan, report = tmp_path / "2026-08-28", tmp_path / "run"
    report.mkdir()
    _contract(scan, {"summary": 1, "appendix": 1})
    (report / "appendix.md").write_text("# 扫描附录\n", encoding="utf-8")
    rows = _by_name(build_artifact_index(scan, report_dir=report, now=NOW))
    assert rows["appendix"]["status"] == "PRESENT"
    assert len(rows["appendix"]["content_hash"]) == 64


def test_legacy_contract_never_grows_an_appendix_obligation(tmp_path):
    """空 map 的 legacy run:**整行不生成** —— 既不 MISSING、也不进 coverage 分母。
    「NOT_EXPECTED」与「该有却没有」在读者眼里必须长得不一样。"""
    scan, report = tmp_path / "2026-07-28", tmp_path / "run"
    report.mkdir()
    _contract(scan, {})
    index = build_artifact_index(scan, report_dir=report, now=NOW)
    rows = _by_name(index)
    assert "appendix" not in rows
    assert index["coverage"]["registered"] == len(index["artifacts"])
    # 其余产物照旧被追责 —— 门控只对新加的那一项生效,不是给整张表开后门
    assert rows["summary"]["status"] == "MISSING"


def test_run_without_any_contract_is_treated_as_legacy(tmp_path):
    """契约文件都没有(更老的现场)→ 同样不追责 appendix。"""
    scan, report = tmp_path / "2026-07-01", tmp_path / "run"
    scan.mkdir()
    report.mkdir()
    assert "appendix" not in _by_name(build_artifact_index(scan, report_dir=report, now=NOW))


def test_todays_contracts_do_register_the_appendix(tmp_path):
    """接线检查:新 run 的契约必须真的记上 appendix,否则上面那条「必红」永远触发不到
    (「生产者没接线」FN-1 家族)。"""
    from autoresearch.scan.artifacts import artifact_schema_versions

    assert artifact_schema_versions()["appendix"] == 1
