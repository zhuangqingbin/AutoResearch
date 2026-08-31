"""网查预算的计量单位(`autoresearch/trace/web_budget.py`)。

spec: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §6.2 / §6.5 + §11 D-5。

本文件锁四件事,每一件都对应一条「量错了比不量更坏」的纪律:

1. **一行 tool call ≠ 一次 query**:batch 里有 N 条就记 N 条,`tool_calls` 与
   `search_queries` 必须能分开(相等只是巧合,不是契约)。
2. **UNMEASURED 不是 0、更不是「未超限」**:transcript 未绑定 / payload 不可解析 /
   batch 数不清 → 计数写 `null`、`cap_exceeded` 写 `null`。断言里逐条写明
   `is None` 且 `!= 0`,因为 0 正是最容易被下游读成「一次都没查」的那个值。
3. **自报只作诊断**:`self_report_delta` 不参与任何 cap 判定。
4. **两个引擎都只是 OBSERVED_ONLY**:靠解析 transcript 冒充强制是假的(§6.5)。

零网络:预算是纯函数 + capsule 夹具,取数不参与。
"""

from __future__ import annotations

import json

import pytest

from autoresearch.trace import web_budget as wb
from autoresearch.trace.capsule import (
    bind_transcript,
    materialize_agent_index,
    record_agent_boundary,
)

# ───────────────────────── 夹具:一份可逐条对账的工具行 ─────────────────────────
#
# 下面这些计数就是「与 fixture 一致」的真值,写在一处,断言全部引用它:
#
#   tool_calls      = 5(两次搜索 + 两次抓取 + 一次本地工具)
#   search_queries  = 4(batch 3 + 单条 1;其中 "a shares" 与 " A  Shares " 规范化后同一条)
#   duplicate_queries = 1
#   fetched_urls    = 3(其中两条 canonical 后同一篇 → 1 条重复,但**仍计预算**)
#   duplicate_urls  = 1
#   failed_units    = 2(那次 FAILED 的抓取带 2 个 url)

BUCKET = {"role": "l4-intel", "stage": "l4", "subject": "600000"}

FIXTURE_ROWS = [
    {
        **BUCKET,
        "tool_name": "WebSearch",
        "tool_call_id": "t1",
        "invocation_id": "i1",
        "request": json.dumps({"queries": ["a shares", "北向资金", " A  Shares "]}),
        "status": "COMPLETED",
        "requested_at": "2026-08-28T01:00:00+00:00",
        "completed_at": "2026-08-28T01:00:04+00:00",
    },
    {
        **BUCKET,
        "tool_name": "WebSearch",
        "tool_call_id": "t2",
        "invocation_id": "i1",
        "request": json.dumps({"query": "浦发银行 公告"}),
        "status": "COMPLETED",
        "requested_at": "2026-08-28T01:00:05+00:00",
        "completed_at": "2026-08-28T01:00:06+00:00",
    },
    {
        **BUCKET,
        "tool_name": "WebFetch",
        "tool_call_id": "t3",
        "invocation_id": "i1",
        "request": json.dumps({"url": "https://example.com/news?utm_source=x&id=7"}),
        "status": "COMPLETED",
        "requested_at": "2026-08-28T01:00:07+00:00",
        "completed_at": "2026-08-28T01:00:08+00:00",
    },
    {
        **BUCKET,
        "tool_name": "WebFetch",
        "tool_call_id": "t4",
        "invocation_id": "i1",
        # 同一篇原文的两个「不同」写法 + 另一篇 —— canonical 后 2 条里有 1 条重复。
        "request": json.dumps(
            {"urls": ["https://example.com/news?id=7#part2", "https://example.org/other"]}
        ),
        "status": "FAILED",
        "requested_at": "2026-08-28T01:00:09+00:00",
        "completed_at": "2026-08-28T01:00:12+00:00",
    },
    {
        **BUCKET,
        "tool_name": "Read",
        "tool_call_id": "t5",
        "invocation_id": "i1",
        "request": json.dumps({"file_path": "/tmp/x.md"}),
        "status": "COMPLETED",
        "requested_at": "2026-08-28T01:00:13+00:00",
        "completed_at": "2026-08-28T01:00:13+00:00",
    },
]

FIXTURE_TRUTH = {
    "tool_calls": 5,
    "search_queries": 4,
    "duplicate_queries": 1,
    "fetched_urls": 3,
    "duplicate_urls": 1,
    "failed_units": 2,
}


def _only_bucket(budget: dict) -> dict:
    assert len(budget["buckets"]) == 1, budget["buckets"]
    return budget["buckets"][0]


# ───────────────────────── 1. 一行 ≠ 一次查询 ─────────────────────────


def test_one_batch_call_counts_every_query_inside_it():
    """cap 8 的角色发一次 5 条 batch,不能被记成「1 条」。"""
    row = {**BUCKET, "tool_name": "WebSearch",
           "request": json.dumps({"queries": [f"q{i}" for i in range(5)]}),
           "status": "COMPLETED"}
    bucket = _only_bucket(wb.build_budget([row]))

    assert bucket["tool_calls"] == 1
    assert bucket["search_queries"] == 5


def test_tool_calls_and_search_queries_are_not_the_same_number():
    """两个计数必须能分开 —— 相等是巧合,不是契约(否则行数冒充查询数无从发现)。"""
    budget = wb.build_budget(FIXTURE_ROWS)
    bucket = _only_bucket(budget)

    assert bucket["tool_calls"] == FIXTURE_TRUTH["tool_calls"]
    assert bucket["search_queries"] == FIXTURE_TRUTH["search_queries"]
    assert bucket["tool_calls"] != bucket["search_queries"]


def test_nested_batch_requests_are_flattened_once_not_twice():
    """`searches` 既是 batch 容器名又是 query 字段名 —— 只能算一遍。

    重复计数会让预算凭空翻倍,并顺手伪造出同样多的「重复查询」诊断:
    两个数一起错、方向一致,看起来完全自洽。
    """
    row = {**BUCKET, "tool_name": "WebSearch",
           "request": json.dumps({"searches": ["a", "b"]}), "status": "COMPLETED"}
    bucket = _only_bucket(wb.build_budget([row]))

    assert bucket["search_queries"] == 2
    assert bucket["duplicate_queries"] == 0


def test_other_tools_cost_a_tool_call_but_no_budget():
    row = {**BUCKET, "tool_name": "Read", "request": "{}", "status": "COMPLETED"}
    bucket = _only_bucket(wb.build_budget([row]))

    assert bucket["tool_calls"] == 1
    assert bucket["search_queries"] == 0
    assert bucket["fetched_urls"] == 0
    assert bucket["measurement"] == wb.MEASURED


# ───────────────────────── 2. fetch / duplicate / failed 与 fixture 一致 ─────────────


def test_fetch_duplicate_and_failed_units_match_the_fixture():
    bucket = _only_bucket(wb.build_budget(FIXTURE_ROWS))

    for field, expected in FIXTURE_TRUTH.items():
        assert bucket[field] == expected, field
    assert bucket["measurement"] == wb.MEASURED


def test_duplicates_are_diagnostics_and_are_not_subtracted_from_the_budget():
    """重复调用仍计预算(§6.2)—— `duplicate_*` 只是诊断,不从真值里减。"""
    bucket = _only_bucket(wb.build_budget(FIXTURE_ROWS))

    assert bucket["duplicate_queries"] == 1
    assert bucket["duplicate_urls"] == 1
    # 4 条 query 里有 1 条是重复的,但真值仍是 4;3 条 url 里有 1 条重复,真值仍是 3。
    assert bucket["search_queries"] == 4
    assert bucket["fetched_urls"] == 3


def test_wall_seconds_span_the_whole_bucket():
    bucket = _only_bucket(wb.build_budget(FIXTURE_ROWS))
    assert bucket["wall_s"] == pytest.approx(13.0)


# ───────────────────────── 3. UNMEASURED 不是 0,也不是「未超限」 ─────────────────


def test_unbound_transcript_is_unmeasured_and_never_reports_zero():
    """`unbound` 的格必须出现且恒 UNMEASURED —— 「没有行」不等于「零查询」。"""
    budget = wb.build_budget(
        [], unbound=[{**BUCKET, "status": "GONE", "reason": "reached dispatch has no transcript"}]
    )
    bucket = _only_bucket(budget)

    assert bucket["measurement"] == wb.UNMEASURED
    for field in ("tool_calls", "search_queries", "fetched_urls",
                  "failed_units", "duplicate_urls", "duplicate_queries"):
        assert bucket[field] is None, field
        assert bucket[field] != 0, field
    # 不得判「未超限」:cap_exceeded 只有 True/False 两种结论时才算下过结论。
    assert bucket["cap_exceeded"] is None
    assert bucket["cap_enforcement"] == wb.UNMEASURED
    assert budget["totals"]["cap_exceeded_buckets"] is None
    assert any("transcript" in r for r in bucket["unmeasured_reasons"])


def test_unparsable_payload_is_unmeasured_not_zero():
    row = {**BUCKET, "tool_name": "WebSearch", "request": "<not json>", "status": "COMPLETED"}
    bucket = _only_bucket(wb.build_budget([row]))

    assert bucket["measurement"] == wb.UNMEASURED
    assert bucket["search_queries"] is None
    assert bucket["cap_exceeded"] is None
    assert bucket["tool_calls"] == 1        # 调用发生过是事实,只是数不清里面几条


def test_search_call_without_a_recognizable_query_field_is_unmeasured():
    row = {**BUCKET, "tool_name": "WebSearch",
           "request": json.dumps({"queries": []}), "status": "COMPLETED"}
    bucket = _only_bucket(wb.build_budget([row]))

    assert bucket["measurement"] == wb.UNMEASURED
    assert bucket["search_queries"] is None


def test_unmeasured_bucket_never_claims_compliance_even_under_a_tiny_cap():
    """cap=1 而真值不可知 → 既不判超限,也不判合规。"""
    row = {**BUCKET, "tool_name": "WebSearch", "request": "not-json", "status": "COMPLETED"}
    bucket = _only_bucket(wb.build_budget([row], caps={"l4-intel": 1}))

    assert bucket["cap"] == 1
    assert bucket["cap_exceeded"] is None


def test_harness_without_transcript_makes_the_whole_budget_unmeasured():
    budget = wb.build_budget(FIXTURE_ROWS, transcript_bound=False)

    assert budget["totals"]["measurement"] == wb.UNMEASURED
    assert budget["totals"]["search_queries"] is None
    assert budget["totals"]["cap_enforcement"] == wb.UNMEASURED
    assert _only_bucket(budget)["measurement"] == wb.UNMEASURED


def test_no_rows_at_all_is_unmeasured_not_a_proof_of_zero():
    budget = wb.build_budget([])

    assert budget["totals"]["measurement"] == wb.UNMEASURED
    assert budget["totals"]["search_queries"] is None
    assert any("零查询" in r for r in budget["totals"]["unmeasured_reasons"])


def test_budget_telemetry_maps_unmeasured_to_its_own_basis():
    bucket = _only_bucket(wb.build_budget([], unbound=[{**BUCKET, "reason": "GONE"}]))
    telemetry = wb.budget_telemetry(bucket)

    assert telemetry["basis"] == "unmeasured"
    assert "tool_search_calls" not in telemetry          # 缺计量 ≠ 0 次调用


# ───────────────────────── 4. 自报只作诊断 ─────────────────────────


def test_self_report_is_a_diagnostic_and_never_drives_the_cap_verdict():
    """稿件自报 20 条、真值 4 条、cap 8 → 结论必须来自真值(未超),自报只留 delta。"""
    budget = wb.build_budget(
        FIXTURE_ROWS, caps={"l4-intel": 8}, self_reports={"l4-intel|l4|600000": 20}
    )
    bucket = _only_bucket(budget)

    assert bucket["search_queries"] == 4
    assert bucket["self_reported_queries"] == 20
    assert bucket["self_report_delta"] == 16
    assert bucket["cap_exceeded"] is False
    assert budget["totals"]["cap_exceeded_buckets"] == []


def test_real_value_over_cap_is_reported_even_when_the_draft_under_reports():
    budget = wb.build_budget(FIXTURE_ROWS, caps={"l4-intel": 2}, self_reports={"l4-intel": 1})
    bucket = _only_bucket(budget)

    assert bucket["cap_exceeded"] is True
    assert bucket["self_report_delta"] == 1 - 4
    assert budget["totals"]["cap_exceeded_buckets"] == ["l4-intel|l4|600000"]


def test_fetch_cap_is_listed_separately_and_never_borrows_the_query_cap():
    budget = wb.build_budget(FIXTURE_ROWS, caps={"l4-intel": {"search": 99, "fetch": 2}})
    bucket = _only_bucket(budget)

    assert bucket["cap_unit"] == wb.CAP_UNIT_SEARCH
    assert (bucket["cap"], bucket["fetch_cap"]) == (99, 2)
    assert bucket["cap_exceeded"] is True        # 3 个 url > fetch cap 2


# ───────────────────────── 5. 限频的引擎边界(§6.5)─────────────────────────


@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_cap_enforcement_is_observed_only_on_both_engines(engine):
    """本波两个引擎都只是**观测**。宣称硬限频等价 = 用解析 transcript 冒充强制。"""
    budget = wb.build_budget(FIXTURE_ROWS, engine=engine)

    assert budget["totals"]["cap_enforcement"] == wb.OBSERVED_ONLY
    assert _only_bucket(budget)["cap_enforcement"] == wb.OBSERVED_ONLY
    assert wb.HARD_CLAUDE not in json.dumps(budget, ensure_ascii=False)
    assert "OBSERVED_ONLY" in budget["policy"]


def test_default_enforcement_is_observed_only():
    import inspect

    signature = inspect.signature(wb.build_budget)
    assert signature.parameters["enforcement"].default == wb.OBSERVED_ONLY
    assert inspect.signature(wb.materialize_web_budget).parameters["enforcement"].default == (
        wb.OBSERVED_ONLY
    )


def test_unknown_enforcement_is_rejected_at_build_time():
    with pytest.raises(wb.WebBudgetError):
        wb.build_budget(FIXTURE_ROWS, enforcement="HARD_SHARED")


# ───────────────────────── 6. §6.2 的 11 键 schema ─────────────────────────

#: `scan/self_review.WEB_BUDGET_KEYS` 逐字同源 —— 消费者按**顶层键**读这份文件。
SPEC_KEYS = ("measurement", "tool_calls", "search_queries", "fetched_urls", "failed_units",
             "duplicate_urls", "wall_s", "cap_unit", "cap", "cap_enforcement",
             "self_report_delta")


def test_every_bucket_carries_the_eleven_spec_keys():
    for bucket in wb.build_budget(FIXTURE_ROWS)["buckets"]:
        assert set(SPEC_KEYS) <= set(bucket)


def test_document_top_level_is_readable_by_the_self_review_consumer():
    """生产者 → 消费者接线锁(FN-1 家训:消费者读没人生产的键 = 永远 UNMEASURED)。

    `self_review.read_web_budget` 严格按 11 个**顶层**键读 `web_budget.json`;
    缺任一键它就返回「不可用」,于是真值路一辈子退化成自报路 —— 而两侧的单测各自
    用自己的合成夹具,全绿。
    """
    from autoresearch.scan.self_review import WEB_BUDGET_KEYS

    assert set(WEB_BUDGET_KEYS) == set(SPEC_KEYS)
    budget = wb.build_budget(FIXTURE_ROWS, caps={"l4-intel": 8})
    assert set(WEB_BUDGET_KEYS) <= set(budget), sorted(set(WEB_BUDGET_KEYS) - set(budget))
    assert budget["measurement"] == wb.MEASURED
    assert budget["search_queries"] == FIXTURE_TRUTH["search_queries"]
    assert budget["cap_unit"] == wb.CAP_UNIT_SEARCH
    assert budget["cap"] == 8


def test_top_level_rollup_is_unmeasured_when_any_bucket_is(tmp_path):
    from autoresearch.scan.self_review import read_web_budget

    budget = wb.build_budget([{**BUCKET, "tool_name": "WebSearch",
                               "request": "junk", "status": "COMPLETED"}])
    path = tmp_path / wb.BUDGET_NAME
    wb.write_budget(path, budget)

    obj, why = read_web_budget(path)
    assert why == "" and obj is not None            # schema 完整,能读
    assert obj["measurement"] == wb.UNMEASURED      # 但结论是「量不到」
    assert obj["search_queries"] is None


# ───────────────────────── 7. 落盘:幂等 + 冻结零写入 ─────────────────────────


def test_write_budget_is_idempotent_to_the_byte(tmp_path):
    budget = wb.build_budget(FIXTURE_ROWS)
    path = tmp_path / wb.BUDGET_NAME

    assert wb.write_budget(path, budget) is True
    first = path.read_bytes()
    assert wb.write_budget(path, budget) is False   # 内容没变 → 一个字节都不写
    assert path.read_bytes() == first


def test_frozen_budget_refuses_a_silent_rewrite(tmp_path):
    from autoresearch.trace.evidence_index import FrozenEvidenceError

    path = tmp_path / wb.BUDGET_NAME
    wb.write_budget(path, wb.build_budget(FIXTURE_ROWS))
    path.chmod(0o444)

    assert wb.write_budget(path, wb.build_budget(FIXTURE_ROWS)) is False  # 同内容 → 零写入
    with pytest.raises(FrozenEvidenceError):
        wb.write_budget(path, wb.build_budget(FIXTURE_ROWS[:2]))


# ───────────────────────── 8. capsule 端到端(D-5 scan adapter)─────────────────────


_TRANSCRIPT = [
    {
        "type": "assistant",
        "attributionAgent": "l4-intel",
        "timestamp": "2026-08-27T01:02:03Z",
        "message": {
            "id": "msg-1",
            "model": "claude-sonnet-4",
            "content": [
                {"type": "tool_use", "id": "call-1", "name": "WebSearch",
                 "input": {"queries": ["浦发银行 中报", "银行板块 资金", "浦发银行 中报"]}},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    },
    {
        "type": "user",
        "timestamp": "2026-08-27T01:02:05Z",
        "message": {"content": [{"type": "tool_result", "tool_use_id": "call-1",
                                 "content": "synthetic search result"}]},
    },
    {
        "type": "assistant",
        "attributionAgent": "l4-intel",
        "timestamp": "2026-08-27T01:02:06Z",
        "message": {
            "id": "msg-2",
            "model": "claude-sonnet-4",
            "stop_reason": "end_turn",
            "content": [
                {"type": "tool_use", "id": "call-2", "name": "WebFetch",
                 "input": {"url": "https://example.com/a?utm_source=z"}},
                {"type": "text", "text": "done"},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    },
]


def _write_transcript(tmp_path):
    path = tmp_path / "harness" / "agent-l4-intel.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in _TRANSCRIPT),
        encoding="utf-8",
    )
    return path


def test_materialize_web_budget_counts_batch_queries_from_a_real_capsule(codex_run, tmp_path):
    """scan adapter:transcript → `external_tools.jsonl` → `web_budget.json`。

    一次 3-query 的 batch + 一次抓取 = **2** 个 tool call、**3** 条 query
    (其中 1 条重复,仍计预算)、**1** 个 url。
    """
    handle, _ = codex_run
    source = _write_transcript(tmp_path)
    record_agent_boundary(handle.run_id, "AGENT_DISPATCHED", role="l4-intel",
                          subject="600000", invocation_id="l4-intel-600000-1", attempt=1)
    bind_transcript(handle.run_id, source, role="l4-intel", subject="600000",
                    invocation_id="l4-intel-600000-1", engine="claude")
    materialize_agent_index(handle.run_id)

    budget = wb.materialize_web_budget(handle.capsule, run_id=handle.run_id, engine="claude",
                                       caps={"l4-intel": 2})
    bucket = _only_bucket(budget)

    assert bucket["role"] == "l4-intel" and bucket["subject"] == "600000"
    assert bucket["tool_calls"] == 2
    assert bucket["search_queries"] == 3
    assert bucket["duplicate_queries"] == 1
    assert bucket["fetched_urls"] == 1
    assert bucket["measurement"] == wb.MEASURED
    assert bucket["cap_exceeded"] is True                  # 3 > cap 2,靠真值不是行数
    assert (handle.capsule / "lineage" / wb.BUDGET_NAME).is_file()


def test_materialize_web_budget_is_byte_identical_on_a_second_pass(codex_run, tmp_path):
    handle, _ = codex_run
    source = _write_transcript(tmp_path)
    record_agent_boundary(handle.run_id, "AGENT_DISPATCHED", role="l4-intel",
                          subject="600000", invocation_id="l4-intel-600000-1", attempt=1)
    bind_transcript(handle.run_id, source, role="l4-intel", subject="600000",
                    invocation_id="l4-intel-600000-1", engine="claude")
    materialize_agent_index(handle.run_id)

    wb.materialize_web_budget(handle.capsule, run_id=handle.run_id)
    path = handle.capsule / "lineage" / wb.BUDGET_NAME
    first = path.read_bytes()
    wb.materialize_web_budget(handle.capsule, run_id=handle.run_id)

    assert path.read_bytes() == first


def test_dispatch_without_a_transcript_stays_unmeasured_in_the_capsule(codex_run):
    """现场缺 transcript 的角色必须**出现在预算里**且恒 UNMEASURED(GONE ≠ 零查询)。"""
    handle, _ = codex_run
    record_agent_boundary(handle.run_id, "AGENT_DISPATCHED", role="l4-intel",
                          subject="600000", invocation_id="l4-intel-600000-1", attempt=1)
    materialize_agent_index(handle.run_id)

    budget = wb.materialize_web_budget(handle.capsule, run_id=handle.run_id)
    bucket = _only_bucket(budget)

    assert bucket["role"] == "l4-intel"
    assert bucket["measurement"] == wb.UNMEASURED
    assert bucket["search_queries"] is None
    assert bucket["cap_exceeded"] is None


def test_unbound_from_agent_index_skips_roles_that_never_have_transcripts():
    index = {
        "invocations": [
            {"role": "l4-intel", "subject": "600000", "stage": "l4",
             "expected": True, "status": "GONE", "reason": "no transcript"},
            {"role": "l4-card", "subject": "600000", "stage": "l4",
             "expected": True, "status": "PRESENT"},
            {"role": "trace-control", "subject": "600000", "stage": "l4",
             "expected": False, "status": "NOT_EXPECTED"},
        ]
    }

    rows = wb.unbound_from_agent_index(index)

    assert [r["role"] for r in rows] == ["l4-intel"]


# ───────────────────────── 9. 分类与规范化 ─────────────────────────


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("WebSearch", wb.KIND_SEARCH),
        ("mcp__brave__brave_search", wb.KIND_SEARCH),
        ("WebFetch", wb.KIND_FETCH),
        ("mcp__x__read_url", wb.KIND_FETCH),
        ("Read", wb.KIND_OTHER),
        ("", wb.KIND_OTHER),
    ],
)
def test_tool_classification(name, kind):
    assert wb.classify_tool(name) == kind


def test_query_normalization_folds_whitespace_and_case():
    assert wb.normalize_query("  A   Shares  ") == "a shares"
    assert wb.query_hash("A  Shares") == wb.query_hash("a shares")
    assert wb.query_hash("a shares") != wb.query_hash("b shares")
