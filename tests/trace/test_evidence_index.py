"""统一外源审计索引(`autoresearch/trace/evidence_index.py`)。

spec: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §6.1 / §6.3 / §9 + §11 D-5。

锁的是「索引」这个身份本身:

* **只存引用,不复制 raw** —— 索引一旦复制正文 / snippet,就变成了第四本账,
  而三类证据的语义(接口可达 / 我们看见过 / 断言被原文支撑)会被合并成一个假结论。
* **可达 ≠ 已核** —— 搜索 snippet 只能证明「搜到过」,永远不得升格成 `VERIFIED`。
* **幂等 + 冻结零写入** —— 同输入重跑字节级同产物;冻结后内容要变就显式抛,
  不偷偷改历史。
* **四步定序**(§6.1)—— transcript → web_budget → evidence_index → expected /
  completeness / replay → MANIFEST。定序不是审美:后两步读前两步的产物,
  而 MANIFEST 必须能覆盖它们。

零网络:新闻目录用临时根,claim 走 CSV,工具行来自 capsule 夹具。
"""

from __future__ import annotations

import json

import pytest

from autoresearch.common import workspace as ws
from autoresearch.trace import evidence_index as ei, web_budget as wb
from autoresearch.trace.capsule import (
    BusinessStatus,
    bind_transcript,
    checkpoint,
    finalize,
    materialize_agent_index,
    read_manifest,
    record_agent_boundary,
    verify_manifest,
)

CUTOFF = "2026-08-27T15:00:00+08:00"


# ───────────────────────── canonicalize(§9)─────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "data:text/html,<script>x</script>",
        "javascript:alert(1)",
        "http://localhost:8080/admin",
        "http://127.0.0.1/x",
        "http://192.168.1.10/x",
        "http://[::1]/x",
        "http://box.internal/x",
        "https://exam\nple.com/x",
        "https:///nohost",
        "",
    ],
)
def test_malicious_or_local_urls_are_rejected_not_repaired(url):
    """拒绝比「尽力修好」重要:本机路径与注入面不是可复核的外部证据。"""
    with pytest.raises(ei.UrlRejected):
        ei.canonicalize_url(url)
    assert ei.safe_canonical_url(url) is None


def test_canonicalize_gives_one_identity_per_article():
    same = {
        ei.canonicalize_url("https://Example.COM:443/news?b=2&a=1&utm_source=x#sec"),
        ei.canonicalize_url("https://example.com/news?a=1&b=2"),
        ei.canonicalize_url("https://example.com/news?a=1&b=2&fbclid=zz"),
    }
    assert same == {"https://example.com/news?a=1&b=2"}


def test_redirect_is_only_followed_inside_the_allowed_domains():
    assert ei.follow_allowed_redirect(
        "https://news.google.com/x", "https://www.reuters.com/a", allowed_domains=["reuters.com"]
    ) == "https://www.reuters.com/a"
    # 源域自己恒允许(同域跳转不是越界)。
    assert ei.follow_allowed_redirect(
        "https://reuters.com/x", "https://reuters.com/y", allowed_domains=[]
    ) == "https://reuters.com/y"
    with pytest.raises(ei.UrlRejected):
        ei.follow_allowed_redirect(
            "https://news.google.com/x", "https://evil.example/a", allowed_domains=["reuters.com"]
        )


# ───────────────────────── 三类记录都进索引,且无 raw 重复 ─────────────────────────


SOURCE_READS = [
    {"blob_hash": "b" * 64, "endpoint": "tushare.daily", "stage": "L0", "subject": "market",
     "started_at": "2026-08-27T09:00:00+08:00", "status": "OK", "evidence_complete": True,
     "content": "这是原始正文,绝不能进索引"},
    {"correlation_id": "read-2", "endpoint": "fred.releases", "stage": "L0", "subject": "us",
     "started_at": "2026-08-27T09:01:00+08:00", "status": "FAILED", "evidence_complete": False},
]

OBSERVATIONS = [
    {"source_observation_id": "obs-1", "source": "gnews", "available_stage": "L1",
     "scan_run_id": "run-1", "first_seen_ts": "2026-08-27T10:00:00+08:00",
     "url": "https://example.com/a?utm_source=q", "content_hash": "c" * 16,
     "title": "标题正文不进索引"},
]

CLAIMS = [
    {"claim_id": "cl-1", "subject": "600000", "status": "VERIFIED", "stage": "l4",
     "effective_date": "2026-08-26", "raw_citation": "[公告|2026-08-26|https://example.com/ann]"},
    {"claim_id": "cl-2", "subject": "600000", "status": "UNVERIFIED", "stage": "l4",
     "effective_date": "2026-08-26", "raw_citation": "[新闻|2026-08-26|https://example.com/n]"},
]

TOOLS = [
    {"invocation_id": "i1", "tool_call_id": "t1", "role": "l4-intel", "stage": "l4",
     "subject": "600000", "requested_at": "2026-08-27T11:00:00+08:00", "status": "COMPLETED",
     "tool_name": "WebSearch", "url": "https://example.com/serp?q=x",
     "result_hash": "d" * 64, "request": json.dumps({"query": "x"})},
    {"invocation_id": "i1", "tool_call_id": "t2", "role": "l4-intel", "stage": "l4",
     "subject": "600000", "requested_at": "2026-08-27T11:00:05+08:00", "status": "FAILED",
     "tool_name": "WebFetch", "url": "https://example.com/page", "result_hash": None},
]


def _full_index(**over) -> dict:
    kwargs = {"run_id": "run-1", "decision_cutoff": CUTOFF, "source_reads": SOURCE_READS,
              "observations": OBSERVATIONS, "claims": CLAIMS, "external_tools": TOOLS}
    kwargs.update(over)
    return ei.build_index(**kwargs)


def test_index_references_all_three_families_of_record():
    index = _full_index()

    assert index["counts"] == {"source_read": 2, "news_observation": 1,
                               "claim": 2, "external_tool": 2, "total": 7}
    kinds = {e["evidence_kind"] for e in index["entries"]}
    assert kinds == set(ei.EVIDENCE_KINDS)
    refs = {e["ref"] for e in index["entries"]}
    assert any(r.startswith("lineage/reads.jsonl#") for r in refs)
    assert any(r.startswith("news_catalog/source_observation#") for r in refs)
    assert any(r.startswith("_claim_ledger.csv#") for r in refs)
    assert any(r.startswith(f"lineage/{ei.TOOLS_NAME}#") for r in refs)


def test_index_copies_no_raw_content_only_references():
    index = _full_index()

    ei.assert_reference_only(index)                      # 显式契约门
    for entry in index["entries"]:
        assert not (ei.FORBIDDEN_ENTRY_KEYS & set(entry))
    blob = json.dumps(index, ensure_ascii=False)
    assert "这是原始正文" not in blob
    assert "标题正文不进索引" not in blob
    # 内容寻址引用可以留(blob hash 不是 raw)。
    assert any(e["blob_hash"] == "b" * 64 for e in index["entries"])


def test_reference_only_gate_rejects_a_copied_snippet():
    index = _full_index()
    index["entries"][0]["snippet"] = "搜索结果摘要"

    with pytest.raises(ei.EvidenceIndexError):
        ei.assert_reference_only(index)


def test_every_entry_carries_the_spec_columns():
    for entry in _full_index()["entries"]:
        assert set(entry) >= {"evidence_kind", "row_id_or_hash", "role", "stage", "subject",
                              "requested_at", "canonical_url", "decision_cutoff",
                              "verification_status"}
        assert entry["decision_cutoff"] == CUTOFF
        assert entry["verification_status"] in ei.VERIFICATION_STATUSES


def test_index_is_deterministically_sorted():
    first = _full_index()
    shuffled = _full_index(source_reads=list(reversed(SOURCE_READS)),
                           claims=list(reversed(CLAIMS)),
                           external_tools=list(reversed(TOOLS)))
    assert first == shuffled


# ───────────────────────── 可达 ≠ 已核 ─────────────────────────


def test_a_completed_search_never_becomes_verified():
    """搜索 snippet 只证明「搜到过」。让它升 VERIFIED = 把可达当成已核。"""
    index = _full_index()
    tools = [e for e in index["entries"] if e["evidence_kind"] == ei.KIND_EXTERNAL_TOOL]

    assert {e["verification_status"] for e in tools} == {ei.STATUS_TOOL_COMPLETED,
                                                         ei.STATUS_TOOL_FAILED}
    assert all(e["verification_status"] not in ei.CLAIM_STATUSES for e in tools)


def test_observations_are_observed_not_verified():
    news = [e for e in _full_index()["entries"] if e["evidence_kind"] == ei.KIND_NEWS_OBSERVATION]
    assert {e["verification_status"] for e in news} == {ei.STATUS_OBSERVED}


def test_deterministic_reads_have_their_own_vocabulary_and_no_url():
    reads = [e for e in _full_index()["entries"] if e["evidence_kind"] == ei.KIND_SOURCE_READ]

    assert {e["verification_status"] for e in reads} == {ei.STATUS_DETERMINISTIC_OK,
                                                         ei.STATUS_DETERMINISTIC_FAILED}
    # endpoint + params 才是确定层的身份,URL 语义在这里是编的。
    assert all(e["canonical_url"] is None for e in reads)


def test_claim_status_carries_its_consumption_boundary():
    claims = {e["row_id_or_hash"]: e
              for e in _full_index()["entries"] if e["evidence_kind"] == ei.KIND_CLAIM}

    assert claims["cl-1"]["verification_status"] == "VERIFIED"
    assert claims["cl-1"]["consumption"] == "body_judgment"
    assert claims["cl-2"]["consumption"] == "appendix_only"      # 不得影响评级
    assert ei.consumption_for("REFUTED") == "excluded_diagnostic"


def test_unknown_claim_status_is_rejected_at_build_time():
    with pytest.raises(ei.EvidenceIndexError):
        ei.build_index(claims=[{"claim_id": "x", "status": "PROBABLY_FINE"}])


def test_unknown_context_is_rejected():
    with pytest.raises(ei.EvidenceIndexError):
        ei.build_index(context="standalone")


def test_evidence_incompleteness_is_diagnostic_not_a_business_gate():
    """§6.1:证据不完整写进 verification artifact,不得追加 gate_fires / 淘汰股票。"""
    policy = _full_index()["policy"]
    assert "不是业务门" in policy and "gate_fires" in policy


# ───────────────────────── PIT(§6.3)─────────────────────────


_LATE = dict(OBSERVATIONS[0], source_observation_id="obs-late",
             first_seen_ts="2026-08-27T23:00:00+08:00")


def _observation_ids(index: dict) -> set[str]:
    return {e["row_id_or_hash"] for e in index["entries"]
            if e["evidence_kind"] == ei.KIND_NEWS_OBSERVATION}


def test_observations_after_the_cutoff_do_not_enter_the_index(tmp_path):
    """PIT 过滤住在 materializer 里(`build_index` 是纯装配器,不自己判时点)。"""
    result = ei.materialize_report_trace(
        tmp_path / "run", context="macro_full", observations=[*OBSERVATIONS, _LATE],
        claims=[], decision_cutoff=CUTOFF, run_id="macro-1",
    )
    assert _observation_ids(result["index"]) == {"obs-1"}


def test_without_a_cutoff_nothing_is_filtered_out(tmp_path):
    result = ei.materialize_report_trace(
        tmp_path / "run", context="macro_full", observations=[*OBSERVATIONS, _LATE],
        claims=[], decision_cutoff=None, run_id="macro-1",
    )
    assert _observation_ids(result["index"]) == {"obs-1", "obs-late"}


def test_build_index_is_a_pure_assembler_and_does_not_filter():
    """留档:装配器**不**做 PIT —— 所以任何绕开 materializer 的新调用点都得自己过滤。"""
    index = _full_index(observations=[*OBSERVATIONS, _LATE])
    assert _observation_ids(index) == {"obs-1", "obs-late"}


# ───────────────────────── 幂等 / 冻结零写入 ─────────────────────────


def _seed_lineage(capsule):
    (capsule / "lineage").mkdir(parents=True, exist_ok=True)
    (capsule / "lineage/reads.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in SOURCE_READS), encoding="utf-8")
    (capsule / f"lineage/{ei.TOOLS_NAME}").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in TOOLS), encoding="utf-8")


def _seed_ledger(staging):
    import pandas as pd

    staging.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(CLAIMS).to_csv(staging / "_claim_ledger.csv", index=False)


def test_second_materialize_is_byte_identical(tmp_path):
    capsule = tmp_path / "capsule"
    staging = tmp_path / "staging"
    _seed_lineage(capsule)
    _seed_ledger(staging)

    ei.materialize_evidence_index(capsule, run_id="run-1", staging=staging,
                                  decision_cutoff=CUTOFF, catalog=_EmptyCatalog())
    path = capsule / "lineage" / ei.INDEX_NAME
    first = path.read_bytes()
    ei.materialize_evidence_index(capsule, run_id="run-1", staging=staging,
                                  decision_cutoff=CUTOFF, catalog=_EmptyCatalog())

    assert path.read_bytes() == first


def test_write_if_changed_writes_nothing_when_content_is_identical(tmp_path):
    path = tmp_path / "x.json"
    assert ei.write_json_if_changed(path, {"a": 1}) is True
    before = path.stat().st_mtime_ns
    assert ei.write_json_if_changed(path, {"a": 1}) is False
    assert path.stat().st_mtime_ns == before


def test_frozen_artifact_refuses_a_changed_rewrite(tmp_path):
    path = tmp_path / "x.json"
    ei.write_json_if_changed(path, {"a": 1})
    path.chmod(0o444)

    assert ei.write_json_if_changed(path, {"a": 1}) is False    # 同内容 → 零写入
    with pytest.raises(ei.FrozenEvidenceError):
        ei.write_json_if_changed(path, {"a": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}   # 历史一个字节没动


class _EmptyCatalog:
    """新闻目录桩:回一张空表(测试禁止碰真目录根)。"""

    def observations(self):
        import pandas as pd

        return pd.DataFrame(columns=["scan_run_id", "first_seen_ts"])


class _OneRowCatalog:
    def observations(self):
        import pandas as pd

        return pd.DataFrame([
            {**OBSERVATIONS[0], "scan_run_id": "run-1"},
            {**OBSERVATIONS[0], "source_observation_id": "obs-other", "scan_run_id": "run-2"},
        ])


def test_materialize_only_indexes_this_runs_observations(tmp_path):
    capsule = tmp_path / "capsule"
    _seed_lineage(capsule)

    index = ei.materialize_evidence_index(capsule, run_id="run-1", decision_cutoff=CUTOFF,
                                          catalog=_OneRowCatalog())

    ids = {e["row_id_or_hash"] for e in index["entries"]
           if e["evidence_kind"] == ei.KIND_NEWS_OBSERVATION}
    assert ids == {"obs-1"}


# ───────────────────────── 独立技能 adapter(§6.1 第二段)─────────────────────────


def test_report_trace_writes_the_three_artifacts_without_a_news_stage(tmp_path):
    result = ei.materialize_report_trace(
        tmp_path / "run", context="macro_full", external_tool_rows=TOOLS,
        claims=CLAIMS, decision_cutoff=CUTOFF, run_id="macro-1",
    )
    trace = result["trace_dir"]

    assert (trace / ei.TOOLS_NAME).is_file()
    assert (trace / wb.BUDGET_NAME).is_file()
    assert (trace / ei.INDEX_NAME).is_file()
    assert result["index"]["context"] == "macro_full"
    # 独立技能不发明 stage;时点信息落在 context / decision_cutoff 上。
    assert result["index"]["decision_cutoff"] == CUTOFF
    assert {e["stage"] for e in result["index"]["entries"]} <= {"l4", None}


def test_report_trace_without_a_transcript_is_unmeasured_but_still_indexes_claims(tmp_path):
    result = ei.materialize_report_trace(
        tmp_path / "run", context="stock_full", external_tool_rows=[],
        source_reads=SOURCE_READS, claims=CLAIMS, run_id="stock-1",
    )

    assert result["web_budget"]["totals"]["measurement"] == wb.UNMEASURED
    assert result["web_budget"]["totals"]["search_queries"] is None
    assert result["index"]["counts"]["claim"] == 2
    assert result["index"]["counts"]["source_read"] == 2


def test_report_trace_rejects_the_scan_context(tmp_path):
    with pytest.raises(ei.EvidenceIndexError):
        ei.materialize_report_trace(tmp_path / "run", context="scan")


def test_report_trace_is_byte_identical_on_a_second_pass(tmp_path):
    kwargs = {"context": "sector_full", "external_tool_rows": TOOLS, "claims": CLAIMS,
              "decision_cutoff": CUTOFF, "run_id": "sector-1"}
    ei.materialize_report_trace(tmp_path / "run", **kwargs)
    before = {p.name: p.read_bytes() for p in (tmp_path / "run/trace").iterdir()}
    ei.materialize_report_trace(tmp_path / "run", **kwargs)

    assert {p.name: p.read_bytes() for p in (tmp_path / "run/trace").iterdir()} == before


# ───────────────────────── §6.1 四步定序 ─────────────────────────


_TRANSCRIPT = [
    {"type": "assistant", "attributionAgent": "l4-intel", "timestamp": "2026-08-27T01:02:03Z",
     "message": {"id": "m1", "model": "claude-sonnet-4",
                 "content": [{"type": "tool_use", "id": "c1", "name": "WebSearch",
                              "input": {"queries": ["浦发银行 中报", "银行 资金"]}}],
                 "usage": {"input_tokens": 1, "output_tokens": 1}}},
    {"type": "user", "timestamp": "2026-08-27T01:02:04Z",
     "message": {"content": [{"type": "tool_result", "tool_use_id": "c1",
                              "content": "snippet 只证明搜到过"}]}},
    {"type": "assistant", "attributionAgent": "l4-intel", "timestamp": "2026-08-27T01:02:05Z",
     "message": {"id": "m2", "model": "claude-sonnet-4", "stop_reason": "end_turn",
                 "content": [{"type": "text", "text": "done"}],
                 "usage": {"input_tokens": 1, "output_tokens": 1}}},
]


@pytest.fixture
def bound_run(codex_run, tmp_path):
    handle, _ = codex_run
    source = tmp_path / "harness" / "agent-l4-intel.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in _TRANSCRIPT),
                      encoding="utf-8")
    record_agent_boundary(handle.run_id, "AGENT_DISPATCHED", role="l4-intel", subject="600000",
                          invocation_id="l4-intel-600000-1", attempt=1)
    bind_transcript(handle.run_id, source, role="l4-intel", subject="600000",
                    invocation_id="l4-intel-600000-1", engine="claude")
    return handle


def test_steps_two_and_three_really_depend_on_step_one(bound_run):
    """定序不是审美:步骤 2/3 读的正是步骤 1 才生成的 `external_tools.jsonl`。

    倒着跑 —— 在归档 transcript **之前**建预算与索引 —— 会安静地产出一份「什么都没查」
    的现场:预算 UNMEASURED、索引零工具行。这正是必须固定顺序的原因。
    """
    handle = bound_run
    assert not (handle.capsule / f"lineage/{ei.TOOLS_NAME}").exists()

    early_budget = wb.materialize_web_budget(handle.capsule, run_id=handle.run_id)
    early_index = ei.materialize_evidence_index(handle.capsule, run_id=handle.run_id,
                                                catalog=_EmptyCatalog())
    assert early_budget["totals"]["measurement"] == wb.UNMEASURED
    assert early_index["counts"]["external_tool"] == 0

    materialize_agent_index(handle.run_id)                              # 步骤 1
    budget = wb.materialize_web_budget(handle.capsule, run_id=handle.run_id)   # 步骤 2
    index = ei.materialize_evidence_index(handle.capsule, run_id=handle.run_id,
                                          catalog=_EmptyCatalog())              # 步骤 3

    assert budget["totals"]["measurement"] == wb.MEASURED
    assert budget["totals"]["search_queries"] == 2
    assert index["counts"]["external_tool"] == 1


def test_the_four_steps_in_order_end_with_a_manifest_that_covers_them(bound_run, tmp_path):
    """步骤 2/3 在 MANIFEST 之前 —— 可观测的判据就是「清单里有它俩」。

    冻结之后再跑一遍(同输入)必须是**零写入**;这两条合起来就是
    「MANIFEST / publish 后零写入」的验收(§11 D-5)。
    """
    handle = bound_run
    report_dir = ws.reports_root() / "scan" / handle.run_id
    report_dir.mkdir(parents=True)
    (report_dir / "summary.md").write_text("# synthetic\n", encoding="utf-8")
    checkpoint(handle.run_id, "l3", "SUCCEEDED", [], {"finalists": 1})

    materialize_agent_index(handle.run_id)                                     # 1
    wb.materialize_web_budget(handle.capsule, run_id=handle.run_id)            # 2
    ei.materialize_evidence_index(handle.capsule, run_id=handle.run_id,
                                  catalog=_EmptyCatalog())                     # 3
    result = finalize(handle.run_id, BusinessStatus.SUCCEEDED, report_dir)     # 4

    listed = read_manifest(result.final_path)
    assert f"capsule/lineage/{wb.BUDGET_NAME}" in listed
    assert f"capsule/lineage/{ei.INDEX_NAME}" in listed
    assert verify_manifest(result.final_path)["ok"] is True

    frozen = result.final_path / "capsule"
    stamps = {p: p.stat().st_mtime_ns
              for p in (frozen / "lineage" / wb.BUDGET_NAME, frozen / "lineage" / ei.INDEX_NAME)}
    assert wb.materialize_web_budget(frozen, run_id=handle.run_id) is not None
    ei.materialize_evidence_index(frozen, run_id=handle.run_id, catalog=_EmptyCatalog())

    # 「零写入」= 连 mtime 都没动;只比 hash 的话,一次原样重写也会通过。
    assert {p: p.stat().st_mtime_ns for p in stamps} == stamps
    assert verify_manifest(result.final_path)["ok"] is True


def test_rebuilding_a_frozen_index_from_different_inputs_raises(bound_run):
    handle = bound_run
    materialize_agent_index(handle.run_id)
    ei.materialize_evidence_index(handle.capsule, run_id=handle.run_id, catalog=_EmptyCatalog())
    (handle.capsule / "lineage" / ei.INDEX_NAME).chmod(0o444)

    with pytest.raises(ei.FrozenEvidenceError):
        ei.materialize_evidence_index(handle.capsule, run_id=handle.run_id,
                                      decision_cutoff=CUTOFF, catalog=_OneRowCatalog())
