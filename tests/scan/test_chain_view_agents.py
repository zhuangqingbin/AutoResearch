"""tests/scan/test_chain_view_agents.py — Task 5(2026-09-12 场景重建设计稿 §5/§6/§8)。

覆盖 `chain_view.py` 的四件新事:① capsule↔ledger 按 invocation 合并(§6.2,V01/V02)；
② slim/deep 三处住址查找 + 观察到什么的如实渲染(§3.1,O01)；③ 摘要 80 行预算 + `--verbose`
详细模式(§8,V03)；④ E6 schema 2 渲染(selection/veto_accounting/conflicts/why 三种降级
态互不相同,Ruling 1)与结果段 P0 容忍 + 四类收益标签分离(Ruling 4,T01)。

判据同母文件 `test_chain_view.py`:缺证据必须明写「证据不足」而不是静默跳过或喊绝对结论
(spec §3.1 末段);capsule 文件存在**不得**让合并短路成整份索引(mutation probe a);
不支持/交错的证据**不得**渲染成确定的"未观察到"(mutation probe b)。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.scan import chain_view

CODE = "603317"
DATE = "2026-08-25"


# ───────────────────────── 通用 run 骨架(比 test_chain_view.py 的 `_run` 更精简) ─────────────────────────

def _base_run(tmp_path, *, code=CODE, date=DATE, run_id="20260825_2149"):
    run = tmp_path / ws.reports_root() / "scan" / run_id
    (run / "trace" / "staging").mkdir(parents=True)
    (run / "details").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": date, "run_id": run_id}),
                                       encoding="utf-8")
    return run


def _decision_doc(run, *, code=CODE, **overrides):
    doc = {"mode": "active", "rule_version": "e6.v2.0", "blocked": False,
           "buys": [{"code": code, "rank": 1, "basis": "relative"}],
           "candidates": [{"code": code, "eligible": True, "rank": 1,
                           "faces": {"target_align": 0.9}, "hard_gate": {"no_redflag": True},
                           "relative_decision_score": 0.475, "research_rating": "Overweight"}],
           "excluded": []}
    doc.update(overrides)
    (run / "trace" / "staging" / "_relative_buy_decision.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


# ───────────────────────── capsule / ledger 证据夹具 ─────────────────────────

def _capsule_index(run, invocations, *, coverage=None):
    idx = {"schema_version": 3, "run_id": run.name, "invocations": invocations,
          "coverage": coverage or {"expected": len(invocations),
                                    "present": sum(1 for r in invocations if r.get("status") == "PRESENT"),
                                    "missing": sum(1 for r in invocations if r.get("status") != "PRESENT")}}
    p = run / "capsule" / "agents" / "index.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    return idx


def _capsule_normalized(run, invocation_id, *, operations=None, items=None, snapshot_id="snap-1"):
    p = run / "capsule" / "agents" / "normalized" / f"{invocation_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": 2, "invocation_id": invocation_id, "status": "complete",
          "snapshot_id": snapshot_id, "items": items or [], "operations": operations or []}
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def _transcript_bytes(marker: str) -> bytes:
    """One realistic Claude harness JSONL prefix — the shape the real adapters
    parse, written the way the harness writes it (per-row `json.dumps`, *not*
    the canonical form the archive uses). Two different *marker*s give two
    genuinely different transcripts."""
    rows = [
        {"type": "assistant", "timestamp": "2026-08-25T21:49:00.000Z",
         "message": {"id": f"msg-{marker}", "model": "claude-opus-5",
                     "content": [{"type": "tool_use", "id": "tool-1", "name": "Write",
                                  "input": {"file_path": f"/x/{marker}.md",
                                            "content": f"card {marker}"}}]}},
        {"type": "user", "timestamp": "2026-08-25T21:49:01.000Z",
         "message": {"content": [{"type": "tool_result", "tool_use_id": "tool-1",
                                  "content": "ok", "is_error": False}]}},
    ]
    return ("\n".join(json.dumps(r) for r in rows) + "\n").encode("utf-8")


def _digests(marker: str) -> tuple[str, str]:
    """`(source_sha256, archive_sha256)` for one transcript, computed by the
    **real** snapshot code (`trace.transcripts.snapshot`) over real bytes —
    never a hand-typed `"a"*64`.

    2026-09-13 final-review F1: the whole point of deriving these is that the
    two producers' `source_sha256` measure different objects (capsule: the
    unredacted live prefix; ledger: whatever raw the offline source handed it,
    which for a `scan.salvage` blob is the redacted archive), while
    `archive_sha256` measures the same object on both sides. A fixture built
    from two identical literals cannot tell those two facts apart, which is
    exactly how the mismatch shipped. The end-to-end guard that drives both
    real producers lives in `tests/scan/test_transcript_binder.py`
    (`test_f1_one_real_transcript_through_both_real_producers_*`); these
    digests keep *this* file's merge fixtures honest about the same contract.
    """
    from autoresearch.trace.transcripts.snapshot import capture_snapshot
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"agent-{marker}.jsonl"
        p.write_bytes(_transcript_bytes(marker))
        snap = capture_snapshot(p, engine="claude")
        return snap.source_prefix.sha256, snap.archive.sha256


_SOURCE_A, _ARCHIVE_A = _digests("alpha")
_SOURCE_B, _ARCHIVE_B = _digests("beta")


def _row(invocation_id, *, role="l4-card", subject=CODE, status="PRESENT",
        normalized=None, source_sha256=_SOURCE_A, archive_sha256=_ARCHIVE_A,
        snapshot_id="snap-1", reason=None, unparsed_rows=0):
    return {"invocation_id": invocation_id, "role": role, "subject": subject,
           "status": status, "reason": reason,
           "normalized": normalized or f"agents/normalized/{invocation_id}.json",
           "snapshot_id": snapshot_id, "source_sha256": source_sha256,
           "archive_sha256": archive_sha256,
           "unparsed_rows": unparsed_rows}


def _ledger_index(run, report_run_id, invocations, *, revision_id="rev-1",
                  contract_run_id="x", engine="claude", computed_at="2026-08-26T01:00:00"):
    from autoresearch.scan.outcome import ledger_root
    doc = {"schema_version": 1, "report_run_id": report_run_id, "contract_run_id": contract_run_id,
          "engine": engine, "run_manifest_sha256": "m" * 64, "current_revision_id": revision_id,
          "parser_version": "transcripts.base@1", "computed_at": computed_at,
          "invocations": invocations,
          "coverage": {"expected": len(invocations),
                       "present": sum(1 for r in invocations if r.get("status") == "PRESENT"),
                       "missing": sum(1 for r in invocations if r.get("status") != "PRESENT")}}
    p = ledger_root(run.parent) / "agents_index" / f"{report_run_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def _ledger_normalized(run, report_run_id, revision_id, invocation_id, *, operations=None,
                       items=None, snapshot_id="snap-1"):
    from autoresearch.scan.outcome import ledger_root
    p = (ledger_root(run.parent) / "agents_index" / report_run_id / revision_id
        / "normalized" / f"{invocation_id}.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": 2, "invocation_id": invocation_id, "status": "complete",
          "snapshot_id": snapshot_id, "items": items or [], "operations": operations or []}
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def _read_op(call_id, path, *, kind="READ_SUCCEEDED", tool_name="Read"):
    return {"kind": kind, "call_id": call_id, "tool_name": tool_name, "path": path,
           "path_source": "tool_input", "item_index": 0,
           "response": {"sha256": "r" * 64, "byte_count": 100, "encoding": "utf8_text"},
           "artifact": None}


def _write_op(call_id, path, content: bytes, *, tool_name="Write"):
    return {"kind": "WRITE_SUCCEEDED", "call_id": call_id, "tool_name": tool_name, "path": path,
           "path_source": "tool_input", "item_index": 0,
           "response": {"sha256": "r" * 64, "byte_count": len(content), "encoding": "utf8_text"},
           "artifact": {"sha256": hashlib.sha256(content).hexdigest(), "byte_count": len(content)}}


# ═══════════════════════ brief bullet 1:四个先测的场景 ═══════════════════════

def test_v01_capsule_gone_ledger_present_surfaces_backfill_and_keeps_original(tmp_path, monkeypatch):
    """V01:capsule 索引存在但该 invocation GONE,ledger 补录 PRESENT —— 合并结果必须
    可见补录证据,且原始 GONE 状态仍然可追溯(不是被补录悄悄抹掉)。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    report_run_id = run.name
    _capsule_index(run, [_row("l4-card-603317", status="GONE",
                              reason="bound transcript is not a readable regular file",
                              normalized=None, source_sha256=None, snapshot_id=None)])
    _ledger_index(run, report_run_id,
                 [_row("l4-card-603317", normalized=f"{report_run_id}/rev-1/normalized/l4-card-603317.json")])
    _ledger_normalized(run, report_run_id, "rev-1", "l4-card-603317",
                       operations=[_read_op("c1", "/x/603317_2026-08-25_slim.md")])

    md = chain_view.render(run, CODE)
    # 2026-09-13 mutation probe (a) 逮到的真实缺陷:「补录」两个字与「GONE」在**标签本身**
    # 里恒出现(`补录状态=` 前缀、`原始状态=` 前缀都是无条件打印的),即便合并压根没读到
    # ledger 也一样通过——必须断言在具体的**值**上,而不是标签文字本身。
    assert "来源=ledger_backfill" in md          # 实际来源确实是补录,不是恰好没被用到
    assert "原始状态=GONE" in md                 # 原始 GONE 仍然精确可查(不是被抹掉)
    assert "补录状态=PRESENT" in md               # 补录状态确实是 PRESENT,不是标签空转
    assert "成功 1" in md                        # 补录的那条 READ_SUCCEEDED 真的被数进去了


def test_v02_two_valid_sources_conflict_is_shown_not_silently_picked(tmp_path, monkeypatch):
    """V02(其一):capsule 与 ledger 两边都是有效 PRESENT,但内容(`archive_sha256`)不同
    —— 必须显式报冲突,不能悄悄选一边当答案。

    2026-09-13 final-review F1:两边的摘要都由**真实字节经真实快照代码**算出
    (`_digests`),不再是 `"a"*64`/`"b"*64` 两个手打字面量。差别是实质性的:两个手打
    字面量无论生产者怎么改都恒不相等,这条用例因此对「两边量的根本不是同一件东西」
    完全没有鉴别力——正是它一路绿着放过了 F1。现在 A/B 来自两份真不同的 transcript,
    冲突是真冲突;而**同一份** transcript 经两条真实生产路径的那条对照,在
    `tests/scan/test_transcript_binder.py::test_f1_one_real_transcript_through_both_real_producers_*`。
    """
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    report_run_id = run.name
    assert _ARCHIVE_A != _ARCHIVE_B      # 两份真 transcript,摘要真不同
    _capsule_index(run, [_row("l4-card-603317",
                              source_sha256=_SOURCE_A, archive_sha256=_ARCHIVE_A)])
    _capsule_normalized(run, "l4-card-603317", operations=[_read_op("c1", "/x/slim.md")])
    _ledger_index(run, report_run_id,
                 [_row("l4-card-603317", source_sha256=_SOURCE_B, archive_sha256=_ARCHIVE_B,
                       normalized=f"{report_run_id}/rev-1/normalized/l4-card-603317.json")])
    _ledger_normalized(run, report_run_id, "rev-1", "l4-card-603317",
                       operations=[_read_op("c1", "/x/slim.md")])

    md = chain_view.render(run, CODE)
    assert "冲突" in md
    # 两边的摘要都要现出来,不能只打个"冲突"旗子却藏起真正不同的地方是什么。
    assert _ARCHIVE_A[:12] in md and _ARCHIVE_B[:12] in md


def test_v02_same_transcript_through_both_producers_is_not_a_conflict(tmp_path, monkeypatch):
    """V02(其二,2026-09-13 final-review F1):两边 PRESENT、内容其实**相同**时必须
    合并成功、证据可见——绝不能因为两个生产者对 `source_sha256` 量的不是同一件东西
    就判成冲突把证据整体压掉。

    这正是 F1 的失败面:active binding 成功(本分支新默认)+ 离线索引跑过之后,每条
    invocation 都会被判 `conflict=True`/`effective=None`,§⑪ 一条操作都不渲染——合并
    视图比只看 capsule 还差,重现 spec §6.2 要根治的「索引存在、证据不可见」。
    """
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    report_run_id = run.name
    # 同一份 transcript 经两条真实生产路径:capsule 量未脱敏源文件前缀,ledger 量
    # 离线源交给它的 raw(salvage blob = 脱敏归档字节本身)。`source_sha256` 因此不同,
    # `archive_sha256` 相同——这才是可比的那一个。
    from autoresearch.trace.transcripts.snapshot import snapshot_from_archive_bytes
    import tempfile
    from autoresearch.trace.transcripts.snapshot import capture_snapshot
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "agent-alpha.jsonl"
        p.write_bytes(_transcript_bytes("alpha"))
        cap_snap = capture_snapshot(p, engine="claude")
        led_snap = snapshot_from_archive_bytes(cap_snap.archive_bytes, engine="claude", path=p)
    assert cap_snap.source_prefix.sha256 != led_snap.source_prefix.sha256
    assert cap_snap.archive.sha256 == led_snap.archive.sha256

    _capsule_index(run, [_row("l4-card-603317",
                              source_sha256=cap_snap.source_prefix.sha256,
                              archive_sha256=cap_snap.archive.sha256)])
    _capsule_normalized(run, "l4-card-603317", operations=[_read_op("c1", "/x/slim.md")])
    _ledger_index(run, report_run_id,
                 [_row("l4-card-603317", source_sha256=led_snap.source_prefix.sha256,
                       archive_sha256=led_snap.archive.sha256,
                       normalized=f"{report_run_id}/rev-1/normalized/l4-card-603317.json")])
    _ledger_normalized(run, report_run_id, "rev-1", "l4-card-603317",
                       operations=[_read_op("c1", "/x/slim.md")])

    md = chain_view.render(run, CODE)
    assert "⚠️冲突" not in md
    assert "内容核对=match" in md
    assert "成功 1" in md          # 证据真的渲染出来了,不是被冲突压掉的空白


def test_corrupted_normalized_document_is_insufficient_not_absolute(tmp_path, monkeypatch):
    """corrupted normalized:索引行说 PRESENT,但指向的 normalized JSON 是坏文件 ——
    必须显式「证据不足」,绝不能因为坏文件解析失败就退化成静默空白或绝对"没读到"。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    _capsule_index(run, [_row("l4-card-603317")])
    p = run / "capsule" / "agents" / "normalized" / "l4-card-603317.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{坏 json", encoding="utf-8")

    md = chain_view.render(run, CODE)
    assert "证据不足" in md


def test_shared_input_without_attribution_is_reference_only(tmp_path, monkeypatch):
    """shared 无归属:slim 只在共享 context 根找到,没有任何 run 归属证据 —— 只能标参考,
    不得进入⑪证据计数/E6 BUY 叙事/收益数字。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    shared = tmp_path / ws.scan_root() / DATE / "_external_inputs"
    shared.mkdir(parents=True)
    (shared / f"{CODE}.SS_{DATE}_slim.md").write_text("z" * 500, encoding="utf-8")

    md = chain_view.render(run, CODE)
    assert "参考" in md
    assert "本视图有片段读自共享 staging" in md


# ═══════════════════════ O01:观察到什么(Read 失败/Glob/Grep/截断) ═══════════════════════

def _slim_fixture(run):
    slim_dir = run / "trace" / "inputs" / "slim"
    slim_dir.mkdir(parents=True, exist_ok=True)
    slim = slim_dir / f"{CODE}.SS_{DATE}_slim.md"
    slim.write_text("s" * 100, encoding="utf-8")
    return slim


def test_o01_read_failed_is_not_counted_as_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    slim = _slim_fixture(run)
    _capsule_index(run, [_row("l4-card-603317")])
    _capsule_normalized(run, "l4-card-603317",
                        operations=[_read_op("c1", str(slim), kind="READ_FAILED")])
    md = chain_view.render(run, CODE)
    assert "读取失败" in md
    assert "读取成功" not in md


def test_o01_read_partial_from_grep_is_not_a_full_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    slim = _slim_fixture(run)
    _capsule_index(run, [_row("l4-card-603317")])
    _capsule_normalized(run, "l4-card-603317",
                        operations=[_read_op("c1", str(slim), kind="READ_PARTIAL", tool_name="Grep")])
    md = chain_view.render(run, CODE)
    assert "部分读取" in md
    assert "读取成功" not in md


def test_o01_discovered_glob_never_claims_content_was_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    slim = _slim_fixture(run)
    _capsule_index(run, [_row("l4-card-603317")])
    _capsule_normalized(run, "l4-card-603317",
                        operations=[_read_op("c1", str(slim), kind="DISCOVERED", tool_name="Glob")])
    md = chain_view.render(run, CODE)
    # DISCOVERED 不属于 `_READ_KINDS`(spec §3.1:Glob 只能说"发现",不能说"读到")——
    # 因此匹配不到任何 READ 家族操作,落到"覆盖完整但没找到"那句强断言。
    assert "在该调用记录中未观察到成功读取" in md
    assert "读取成功" not in md


def test_o01_unsupported_segment_is_insufficient_not_a_definite_claim(tmp_path, monkeypatch):
    """mutation probe (b) 的靶子:capsule 行 status=UNSUPPORTED(工具形状不认识)时
    必须渲染"证据不足",绝不能渲染成确定的"未观察到成功读取"。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    _slim_fixture(run)
    _capsule_index(run, [_row("l4-card-603317", status="UNSUPPORTED",
                              reason="unknown tool shape", normalized=None,
                              source_sha256=None, snapshot_id=None)])
    md = chain_view.render(run, CODE)
    assert "证据不足" in md
    assert "在该调用记录中未观察到成功读取" not in md


# ═══════════════════════ O02:写入 hash 与发布版本核验(同产物核验) ═══════════════════════

def test_o02_write_then_edit_reports_subsequent_edit_not_a_clean_match(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    card_path = run / "details" / f"{CODE}.md"
    card_path.write_text(f"# 决策卡 — {CODE} 天味食品\n正文", encoding="utf-8")
    write_op = _write_op("c1", str(card_path), b"# early draft")
    edit_op = {"kind": "WRITE_SUCCEEDED", "call_id": "c2", "tool_name": "Edit",
              "path": str(card_path), "path_source": "tool_input", "item_index": 1,
              "response": {"sha256": "r" * 64, "byte_count": 10, "encoding": "utf8_text"},
              "artifact": None}
    _capsule_index(run, [_row("l4-card-603317")])
    _capsule_normalized(run, "l4-card-603317", operations=[write_op, edit_op])
    md = chain_view.render(run, CODE)
    assert "SUBSEQUENT_EDIT" in md


def test_o02_patch_diff_only_has_no_full_postimage_to_compare(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    card_path = run / "details" / f"{CODE}.md"
    card_path.write_text(f"# 决策卡 — {CODE} 天味食品\n正文", encoding="utf-8")
    patch_op = {"kind": "WRITE_SUCCEEDED", "call_id": "c1", "tool_name": "apply_patch",
               "path": str(card_path), "path_source": "tool_input", "item_index": 0,
               "response": {"sha256": "r" * 64, "byte_count": 10, "encoding": "utf8_text"},
               "artifact": None}
    _capsule_index(run, [_row("l4-card-603317")])
    _capsule_normalized(run, "l4-card-603317", operations=[patch_op])
    md = chain_view.render(run, CODE)
    assert "NO_FULL_POSTIMAGE" in md


def test_o02_intel_write_hash_matches_current_published_version(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    intel_path = run / "trace" / "staging" / f"_l4_intel_{CODE}.md"
    content = b"# intel body, unchanged since capture"
    intel_path.write_bytes(content)
    _capsule_index(run, [_row("l4-intel-603317", role="l4-intel")])
    _capsule_normalized(run, "l4-intel-603317",
                        operations=[_write_op("c1", str(intel_path), content)])
    md = chain_view.render(run, CODE)
    assert "写入核验:MATCH" in md


def test_o02_intel_write_hash_differs_from_current_published_version(tmp_path, monkeypatch):
    """intel 对 intel、不跟卡比——这条也顺带证明"同产物"边界没有被跨过(卡的写入
    hash 永远不会被拿来核对 intel 文件)。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    intel_path = run / "trace" / "staging" / f"_l4_intel_{CODE}.md"
    intel_path.write_bytes(b"# intel body v2 (changed after capture)")
    _capsule_index(run, [_row("l4-intel-603317", role="l4-intel")])
    _capsule_normalized(run, "l4-intel-603317",
                        operations=[_write_op("c1", str(intel_path), b"# intel body v1")])
    md = chain_view.render(run, CODE)
    assert "写入核验:DIFFERS" in md


# ═══════════════════════ R01:成功 0 BUY / 失败 run / 两类 sentinel ═══════════════════════

def test_r01_successful_zero_buy_day_does_not_fabricate_a_buy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    _decision_doc(run, blocked=True, buys=[],
                 candidates=[{"code": CODE, "eligible": False, "rank": None,
                             "faces": {}, "hard_gate": {"no_redflag": False},
                             "relative_decision_score": 0.1, "research_rating": "Sell"}])
    md = chain_view.render(run, CODE)
    assert "blocked=True" in md
    assert "本票就是当日 BUY" not in md


def test_r01_failed_run_renders_business_status_without_crashing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    man = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    man["business_status"] = "FAILED"
    (run / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    md = chain_view.render(run, CODE)
    assert "FAILED" in md
    assert "Traceback" not in md


def test_r01_two_sentinel_types_render_their_mode_honestly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_empty = _base_run(tmp_path, run_id="20260825_0011")
    (run_empty / "trace" / "staging" / "run_mode.json").write_text(
        json.dumps({"mode": "SENTINEL_EMPTY"}), encoding="utf-8")
    md_empty = chain_view.render(run_empty, CODE)
    assert "SENTINEL_EMPTY" in md_empty
    assert "本票就是当日 BUY" not in md_empty

    run_pinned = _base_run(tmp_path, run_id="20260825_0012")
    (run_pinned / "trace" / "staging" / "run_mode.json").write_text(
        json.dumps({"mode": "SENTINEL_PINNED"}), encoding="utf-8")
    _decision_doc(run_pinned)
    md_pinned = chain_view.render(run_pinned, CODE)
    assert "SENTINEL_PINNED" in md_pinned
    assert "本票就是当日 BUY" in md_pinned   # pinned 场景下确实可以有 BUY,不是被禁止


# ═══════════════════════ T01:收益标签四分 + 时间锚 ═══════════════════════

def test_t01_four_return_labels_are_distinct_and_actual_fill_is_unknown(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan import outcome as oc

    run = _base_run(tmp_path)
    doc = {
        "schema_version": oc.OUTCOME_SCHEMA_VERSION, "run_id": run.name, "contract_run_id": "x",
        "analysis_date": DATE, "ruler": oc.MAIN, "outcome_status": oc.MATURE, "reason": "",
        "calendar_quality": oc.TRADE_CAL_QUALITY, "calendar_digest": "d0",
        "t1": "20260826", "t2": "20260827", "decision_mode": "active", "rule_version": "e6.v2.0",
        "read_from_shared_staging": False, "complete": True, "n_rows": 1, "n_scored": 1,
        "exec_line": {"max_pct_1d": 3.0, "max_pos_in_range": 0.7},
        "execution": {"first_available_session": "2026-08-27", "exec_lag": 1,
                     "actionability_status": "LATE_REVALIDATION_REQUIRED"},
        "rows": {CODE: {"code": CODE, "name": "天味食品", "role": "BUY",
                        "t1_close": 10.6, "t1_pct_chg": 6.0, "t1_pos_in_range": 0.8,
                        "exec_ok": False, "t2_open": 10.5, oc.MAIN: 0.08,
                        "rel_gap_market": 0.02, "rel_gap_sector": 0.01,
                        "fwd_5_oc": None, "fwd_10_oc": None,
                        "exec_gap_c1_o2": 0.05, "exec_outcome_status": oc.MATURE}},
    }
    oc.write_outcome(doc, run.parent)
    md = chain_view.render(run, CODE)
    assert "推荐毛收益" in md                                  # ① 推荐毛收益
    assert "执行条件" in md and "事后按收盘价测算,非盘中核验" in md   # ② 事后执行条件测算
    assert "迟到报告反事实收益" in md                             # ③ 迟到报告反事实收益
    assert "实际成交:未知" in md                                 # ④ 实际成交未知
    assert "净收益" not in md.replace("不是净收益", "")           # 不得把毛收益标成净收益


def test_t01_old_schema1_outcome_row_renders_unverified_never_verified(tmp_path, monkeypatch):
    """P0 容忍(controller ruling #4):schema 1 的旧账本没有 `outcome_status`/日历质量
    字段——哪怕它当年判定 `complete=True` 且带着真实数字,也绝不能被渲染成"已核验"。"""
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.outcome import write_outcome

    run = _base_run(tmp_path)
    legacy_doc = {
        "schema_version": 1, "run_id": run.name, "contract_run_id": "x",
        "analysis_date": DATE, "ruler": "gap_c1_o2", "t1": "20260826", "t2": "20260827",
        "decision_mode": "active", "rule_version": "e6.v2.0", "read_from_shared_staging": False,
        "complete": True, "n_rows": 1, "n_scored": 1,
        "rows": {CODE: {"code": CODE, "role": "BUY", "gap_c1_o2": 0.08}},
    }
    write_outcome(legacy_doc, run.parent)
    md = chain_view.render(run, CODE)
    assert "未成熟/未核验" in md
    assert "0.08" not in md   # 旧数字不能连着"已核验"的排版一起冒出来


# ═══════════════════════ controller ruling #1:E6 三种降级态互不相同 ═══════════════════════

def _schema2_doc(*, card_context, blocked=False, code=CODE, conflicts=None):
    return {
        "schema_version": 2, "mode": "active", "rule_version": "e6.v3.0", "blocked": blocked,
        "buys": [] if blocked else [{"code": code, "rank": 1, "basis": "relative"}],
        "candidates": [{"code": code, "eligible": True, "rank": 1, "observation_rank": 1,
                        "faces": {"target_align": 0.9}, "hard_gate": {"no_redflag": True},
                        "relative_decision_score": 0.6, "research_rating": "Overweight",
                        "card_context": card_context}],
        "excluded": [],
        "selection": {"pool": "finalists",
                     "population": {"candidates": 1, "passed_hard_gates": 1,
                                    "after_pinned_exclusion": 1, "final_pool": 1},
                     "codes": [code], "sort_keys": {"names": ["relative_decision_score"],
                                                     "directions": ["desc"], "values": {code: [0.6]}},
                     "exclusions": {"pinned_holding": [], "not_in_pool": []},
                     "winner": {"code": code, "pool_rank": 1}, "buys_count": 1,
                     "second_buy_reason": "v1 影子期无已验证阈值"},
        "veto_accounting": {"population": {"candidates": 1, "passed_hard_gates": 1,
                                           "after_pinned_exclusion": 1, "final_pool": 1},
                            "vetoed_stocks": 0, "vetoed_codes": [],
                            "by_gate": {"tradable": 0, "data_a": 0, "contract": 0, "no_redflag": 0}},
        "field_usage": {},
        "conflicts": conflicts or [],
        "why": f"{code} 是 finalists 池(共 1 只)第 1 名。",
    }


def _write_decision(run, doc):
    (run / "trace" / "staging" / "_relative_buy_decision.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _section(md: str, marker: str) -> str:
    start = md.index(marker)
    rest = md[start:]
    nxt = rest.find("\n## ", 1)
    return rest if nxt == -1 else rest[:nxt]


def test_e6_schema1_no_card_parse_failed_and_healthy_all_render_differently(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    run1 = _base_run(tmp_path, run_id="20260825_0001")
    _decision_doc(run1)                                          # schema 1(没有 card_context)
    md1 = chain_view.render(run1, CODE)

    run2 = _base_run(tmp_path, run_id="20260825_0002")            # schema 2 · 无卡
    no_card_ctx = {
        "card_kind": "unknown", "proposal": None, "ev_target": None, "rr": None,
        "position_raw": None, "trigger_raw": None, "entry_stance": "UNKNOWN",
        "no_new_position": None, "exec_lines": {}, "parse_status": "ERROR",
        "parse_errors": ["text: 卡片正文为空或缺失"],
        "source": {"relative_path": None, "card_sha256": None,
                  "snapshot_quality": "unarchived", "blob_digest": None, "post_hoc": False},
    }
    _write_decision(run2, _schema2_doc(
        card_context=no_card_ctx,
        conflicts=[{"code": CODE, "type": "card_missing_or_unparseable",
                   "detail": f"card_context.parse_errors={no_card_ctx['parse_errors']}"}]))
    md2 = chain_view.render(run2, CODE)

    run3 = _base_run(tmp_path, run_id="20260825_0003")            # schema 2 · 卡存在但解析失败
    parse_failed_ctx = {
        "card_kind": "unknown", "proposal": None, "ev_target": None, "rr": None,
        "position_raw": None, "trigger_raw": None, "entry_stance": "UNKNOWN",
        "no_new_position": None, "exec_lines": {}, "parse_status": "ERROR",
        "parse_errors": ["text: 卡片文件读取失败(details/603317.md): garbled bytes"],
        "source": {"relative_path": "details/603317.md", "card_sha256": "c" * 64,
                  "snapshot_quality": "archived", "blob_digest": "c" * 64, "post_hoc": False},
    }
    _write_decision(run3, _schema2_doc(
        card_context=parse_failed_ctx,
        conflicts=[{"code": CODE, "type": "card_missing_or_unparseable",
                   "detail": f"card_context.parse_errors={parse_failed_ctx['parse_errors']}"}]))
    md3 = chain_view.render(run3, CODE)

    run4 = _base_run(tmp_path, run_id="20260825_0004")            # schema 2 · 健康卡
    healthy_ctx = {
        "card_kind": "full", "proposal": "BUY", "ev_target": "12%", "rr": "1:2",
        "position_raw": "20%", "trigger_raw": "突破 15 日均线",
        "entry_stance": "ALLOWED", "no_new_position": False,
        "exec_lines": {"pct_chg": {"raw": None, "op": None, "threshold": None,
                                   "presence": False, "contract_match": "UNKNOWN",
                                   "contract_version": None},
                      "pos_in_range": {"raw": None, "op": None, "threshold": None,
                                       "presence": False, "contract_match": "UNKNOWN",
                                       "contract_version": None}},
        "parse_status": "OK", "parse_errors": [],
        "source": {"relative_path": "details/603317.md", "card_sha256": "d" * 64,
                  "snapshot_quality": "archived", "blob_digest": "d" * 64, "post_hoc": False},
    }
    _write_decision(run4, _schema2_doc(card_context=healthy_ctx))
    md4 = chain_view.render(run4, CODE)

    e8_1, e8_2, e8_3, e8_4 = (_section(m, "## ⑧") for m in (md1, md2, md3, md4))
    assert len({e8_1, e8_2, e8_3, e8_4}) == 4, "四种降级态必须互不相同"
    assert "card_context:" not in e8_1                              # schema 1:整行都不出现
    assert "card_context:" in e8_2 and f"source={chain_view.ABSENT}" in e8_2
    assert "card_context:" in e8_3 and "source=details/603317.md" in e8_3
    assert "entry_stance=ALLOWED" in e8_4
    assert "why:" in e8_4 and "finalists 池" in e8_4                  # why 原样展示,未被改写


# ═══════════════════════ V03:摘要/详细模式 + 80 行预算 + golden ═══════════════════════

def test_golden_same_evidence_version_renders_byte_identical_twice(tmp_path, monkeypatch):
    """bullet 9:同一冻结证据版本重复渲染,summary 与 verbose 都必须字节一致。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    _decision_doc(run)
    assert chain_view.render(run, CODE) == chain_view.render(run, CODE)
    assert (chain_view.render(run, CODE, verbose=True)
           == chain_view.render(run, CODE, verbose=True))


def test_v03_realistic_fixture_summary_stays_within_80_lines_verbose_expands(tmp_path, monkeypatch):
    """V03:证据丰富的现实向夹具(满编十段叙事 + capsule/ledger 补录 + MATURE 结果账本)
    —— 默认摘要必须 ≤80 行,冲突/缺口/`--verbose` 指向都要活着走出截断;详细模式确实
    更长、且带逐项 call_id。"""
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan import outcome as oc
    from tests.scan.test_chain_view import _outcome_doc as _full_outcome_doc, _run as _full_run

    run = _full_run(tmp_path)
    report_run_id = run.name
    _capsule_index(run, [
        _row("l4-card-603317", status="GONE", reason="not captured", normalized=None,
             source_sha256=None, snapshot_id=None),
        _row("l4-intel-603317", role="l4-intel"),
    ])
    _capsule_normalized(run, "l4-intel-603317",
                        operations=[_read_op("g1", "/x/some_other_file.md", kind="READ_PARTIAL",
                                             tool_name="Grep")])
    _ledger_index(run, report_run_id,
                 [_row("l4-card-603317",
                       normalized=f"{report_run_id}/rev-1/normalized/l4-card-603317.json")])
    _ledger_normalized(run, report_run_id, "rev-1", "l4-card-603317",
                       operations=[_read_op("c1", "/x/603317.SS_2026-08-25_slim.md")])
    oc.write_outcome(_full_outcome_doc(run), run.parent)

    summary = chain_view.render(run, "603317")
    n = len(summary.splitlines())
    assert n <= 80, f"摘要 {n} 行,超出 80 行预算"
    assert "`--verbose`" in summary
    assert "缺口" in summary
    assert "GONE" in summary            # 原始状态即便走了截断也留存(在受保护的 ⑦/⑪ 里)

    verbose = chain_view.render(run, "603317", verbose=True)
    assert len(verbose.splitlines()) > n
    assert "call_id=" in verbose


# ═══════════════════════ golden:固定文本,逐字节核对(bullet 9) ═══════════════════════
#
# 两份 golden 都是真实渲染输出的字面拷贝(先跑一次、人工核对内容正确,再贴进来当回归
# 锚点——不是手写的期望值)。第一份是 schema 1 兼容的最小夹具(没有 capsule/ledger/
# 结果账本);第二份带一条真实 capsule 证据,用来证明 verbose 确实比 summary 多东西。
# op 的 `path` 用固定假路径(不是 tmp_path 派生的真实路径),否则这份 golden 每次跑的
# tmp_path 不同就没法字面比较。

_GOLDEN_SCHEMA1_MINIMAL = (
    '# 推荐链路 — 603317 @ run `20260101_0000`(数据日 2026-08-25)\n\n_确定性生成(零 '
    'LLM);每段的「缺席」都是事实,不是渲染失败。仅供研究,非投资建议。_\n## ① run 身份\n'
    '- run_contract:_缺席_\n- **业务状态**:UNKNOWN\n- **完好性(MANIFEST)**:✗(只回答'
    '「已列文件有没有被改」)\n- **完整性**:未知 —— 本 run 早于 forensic capsule,没有 '
    'expected 清单可比\n- **可重放**:未知 · **归档**:未知\n- **批准时刻**:_缺席_'
    '(manifest_generated_at·estimated)\n- **第一个可执行尾盘**:_缺席_\n- **迟到 '
    'session**:—\n- **可执行状态**:UNKNOWN\n\n## ② 候选护照(全漏斗轨迹)\n- _缺席_'
    '(`_candidate_passport.json` 未随本 run 留存)\n\n## ③ L1 召回\n- L1 打分行 _缺席_'
    '(该票未过 L0 硬门,或产物未留存)\n\n## ④ L2 菜单\n- 未进 L2 菜单(被召回线或分层采样'
    '挡在外面)\n\n## ⑤ L3 pass1 分诊\n- _缺席_(pass1 产物未留存)\n\n## ⑥ L3 精排判断\n'
    '- judged 行 _缺席_(l3-rank 未判它 / 产物未留存)\n- **未入 finalist**\n\n## ⑦ L4 研究\n'
    '- 派发 prompt:_缺席_\n- slim(P1–P3 表面块):_缺席_(未留存)\n- deep(P4 深核):_缺席_'
    '(未留存)\n- 活体情报:_缺席_\n- 发布卡:_缺席_\n\n## ⑧ E6 相对 BUY 决策\n- 当日 '
    'mode=active · rule=e6.v2.0 · blocked=False · BUY=[\'603317\']\n- **eligible / rank**:'
    'True / 1\n- **四面**:{"target_align": 0.9}\n- **硬门**:{"no_redflag": true}\n'
    '- **决策分**:0.475\n- **卡面评级**:Overweight\n- ✅ **本票就是当日 BUY**\n\n'
    '## ⑨ 报告口径(brief)\n- _缺席_\n\n## ⑩ 结果(事后)\n- _缺席_(结果账本尚未回填 —— '
    '`python -m autoresearch.scan.outcome fill`)\n\n## ⑪ 证据现场(transcript 归属)\n'
    '- capsule/ledger 均无证据索引(该 run 早于/未启用 transcript 绑定;不代表研究没发生,'
    '只代表这层证据没有留痕)\n'
)


def test_golden_schema1_minimal_summary_and_verbose_are_byte_identical(tmp_path, monkeypatch):
    """golden #1:schema 1 兼容(决策文件没有 `schema_version` 键)+ 没有 capsule/ledger
    证据的最小夹具——summary 与 verbose 在这个夹具下逐字节相同(⑪ 在到达任何 `if verbose`
    分支之前就已经返回),这本身就是 schema 1 向后兼容的证据。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path, run_id="20260101_0000")
    _decision_doc(run)
    summary = chain_view.render(run, CODE)
    verbose = chain_view.render(run, CODE, verbose=True)
    assert summary == _GOLDEN_SCHEMA1_MINIMAL
    assert verbose == _GOLDEN_SCHEMA1_MINIMAL


_GOLDEN_WITH_EVIDENCE_SUMMARY = (
    '# 推荐链路 — 603317 @ run `20260101_0001`(数据日 2026-08-25)\n\n_确定性生成(零 '
    'LLM);每段的「缺席」都是事实,不是渲染失败。仅供研究,非投资建议。_\n## ① run 身份\n'
    '- run_contract:_缺席_\n- **业务状态**:UNKNOWN\n- **完好性(MANIFEST)**:✗(只回答'
    '「已列文件有没有被改」)\n- **完整性**:未知 —— 本 run 早于 forensic capsule,没有 '
    'expected 清单可比\n- **可重放**:未知 · **归档**:未知\n- **批准时刻**:_缺席_'
    '(manifest_generated_at·estimated)\n- **第一个可执行尾盘**:_缺席_\n- **迟到 '
    'session**:—\n- **可执行状态**:UNKNOWN\n\n## ② 候选护照(全漏斗轨迹)\n- _缺席_'
    '(`_candidate_passport.json` 未随本 run 留存)\n\n## ③ L1 召回\n- L1 打分行 _缺席_'
    '(该票未过 L0 硬门,或产物未留存)\n\n## ④ L2 菜单\n- 未进 L2 菜单(被召回线或分层采样'
    '挡在外面)\n\n## ⑤ L3 pass1 分诊\n- _缺席_(pass1 产物未留存)\n\n## ⑥ L3 精排判断\n'
    '- judged 行 _缺席_(l3-rank 未判它 / 产物未留存)\n- **未入 finalist**\n\n## ⑦ L4 研究\n'
    '- 派发 prompt:_缺席_\n- slim(P1–P3 表面块):trace/inputs/slim/603317.SS_2026-08-25'
    '_slim.md · 100B · 已归档 · 读取:读取成功\n- deep(P4 深核):_缺席_(未留存)\n- 活体情报:'
    '_缺席_\n- 发布卡:_缺席_\n\n## ⑧ E6 相对 BUY 决策\n- _缺席_(决策文件未留存 —— '
    '2026-08-26 前它不在 run 目录里)\n\n## ⑨ 报告口径(brief)\n- _缺席_\n\n'
    '## ⑩ 结果(事后)\n- _缺席_(结果账本尚未回填 —— `python -m autoresearch.scan.outcome '
    'fill`)\n\n## ⑪ 证据现场(transcript 归属)\n- l4-card(`l4-card-603317`):来源=capsule'
    ' · 原始状态=PRESENT · 补录状态=NOT_BACKFILLED\n- 观察计数:成功 1 · 部分 0 · 失败 0'
    ' · 仅请求 0 · 缺口(证据不足) 0\n'
)

_GOLDEN_WITH_EVIDENCE_VERBOSE = (
    _GOLDEN_WITH_EVIDENCE_SUMMARY
    + '- 逐项操作(call_id 级;`item_index` 是它在归一化记录里的位置,不是 transcript 行号'
      '——快照身份见 `snapshot` 与上方来源/invocation 行):\n'
      '  - call_id=call-1 · kind=READ_SUCCEEDED · tool=Read · path=/fixed/603317.SS_2026'
      '-08-25_slim.md · snapshot=snap-golden- · item_index=0(归一化记录内位置,非 '
      'transcript 行号) · response_sha256=rrrrrrrrrrrr · artifact_sha256=—\n'
      '- 时间锚:决策批准时刻=_缺席_(单项操作是否早于/晚于批准时刻,取决于 transcript 时间戳'
      '是否留存;缺失一律显示未知,不能因为文件被归档就推断当时已看过)\n'
)


def test_golden_with_one_capsule_invocation_summary_vs_verbose(tmp_path, monkeypatch):
    """golden #2:一条真实 capsule invocation(READ_SUCCEEDED 命中 slim)——verbose 严格
    比 summary 多(逐项操作 + 时间锚),summary 前缀与 verbose 前缀逐字节相同。op 的 path
    用固定假路径,不含 tmp_path,才能贴成字面 golden。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path, run_id="20260101_0001")
    slim = _slim_fixture(run)
    _capsule_index(run, [_row("l4-card-603317", snapshot_id="snap-golden-1")])
    op = _read_op("call-1", "/fixed/" + slim.name)
    _capsule_normalized(run, "l4-card-603317", operations=[op], snapshot_id="snap-golden-1")

    summary = chain_view.render(run, CODE)
    verbose = chain_view.render(run, CODE, verbose=True)
    assert summary == _GOLDEN_WITH_EVIDENCE_SUMMARY
    assert verbose == _GOLDEN_WITH_EVIDENCE_VERBOSE
    assert verbose.startswith(summary)   # verbose 是 summary 逐字节前缀,再加详细内容


# ═══════════════════════ fix round 1:复核四项 ═══════════════════════

def test_capsule_present_but_normalized_unreadable_falls_back_to_ledger(tmp_path, monkeypatch):
    """finding 1(fix round 1):spec §6.2 三个回落触发词——缺失/GONE/**不可归一化**——
    只实现了前两个。capsule 行自称 PRESENT,但它的 normalized 产物是坏 JSON,必须与
    "缺失"/"GONE"同等对待,回落到有效的 ledger 补录;不能让 ledger 的真实读取被吞掉、
    只剩"证据不足"(复核直接复现的缺陷:旧代码在这个组合下把 ledger 的成功读取吞了)。

    与已有的 `test_corrupted_normalized_document_is_insufficient_not_absolute` 的区别:
    那条测试**没有 ledger 行**,从没练到"capsule 坏 + ledger 好"这个组合(复核原话)。
    """
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    report_run_id = run.name
    _capsule_index(run, [_row("l4-card-603317", status="PRESENT")])
    bad = run / "capsule" / "agents" / "normalized" / "l4-card-603317.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{坏 json", encoding="utf-8")
    _ledger_index(run, report_run_id,
                 [_row("l4-card-603317",
                       normalized=f"{report_run_id}/rev-1/normalized/l4-card-603317.json")])
    _ledger_normalized(run, report_run_id, "rev-1", "l4-card-603317",
                       operations=[_read_op("c1", "/x/603317_2026-08-25_slim.md")])

    md = chain_view.render(run, CODE)
    assert "来源=ledger_backfill" in md
    assert "原始状态=PRESENT_UNNORMALIZABLE" in md   # 原始 PRESENT 这个事实没被抹掉,
                                                      # 但也没被当成"真的可用"
    assert "补录状态=PRESENT" in md
    assert "成功 1" in md          # ledger 的真实读取没有被吞掉
    assert "⚠️冲突" not in md      # 这不是冲突(capsule 那边压根不算数),是干净的回落


def test_visible_text_blocks_cap_states_the_omitted_count(tmp_path, monkeypatch):
    """finding 2(fix round 1):spec §8「长文本…至多 6 块，明确省略量」——六块上限之外
    的内容不能悄悄丢弃,省略量必须现出来。8 段可见文本 → 显示 6 块 + 明确"另省略 2 块"。

    `kind="message"` 是 Task 2 两个适配器共用的真实可见文本形状(`claude.py`/`codex.py`
    的 `add("message", {"message_id":..., "role":..., "text":...}, timestamp)`)——
    fix round 2 复核指出上一版夹具用的 `"assistant_text"` 是编的,不是任何适配器真的
    产出的 kind,这里改用真实形状。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    items = [{"index": i, "kind": "message",
             "payload": {"message_id": f"m{i}", "role": "assistant",
                        "text": f"第{i}段可见分析文本"},
             "timestamp": None} for i in range(8)]
    _capsule_index(run, [_row("l4-card-603317")])
    _capsule_normalized(run, "l4-card-603317", items=items, operations=[])

    md = chain_view.render(run, CODE, verbose=True)
    assert "可见分析文本(6 块" in md
    assert "另省略 2 块" in md
    assert "第7段可见分析文本" not in md   # 第 8 块(index 7)确实没显示——不是凑巧对上


def test_hash_compare_detail_is_rendered_not_discarded(tmp_path, monkeypatch):
    """finding 3(fix round 1):`_hash_compare` 算出来的 `detail` 此前算了就扔——选择
    "接进 --verbose 或删掉"里的前者:接进渲染(不限 verbose,因为它不占新行,只是把已有
    "写入核验:"这一行变长,不影响 80 行预算)。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    intel_path = run / "trace" / "staging" / f"_l4_intel_{CODE}.md"
    content = b"# intel body"
    intel_path.write_bytes(content)
    _capsule_index(run, [_row("l4-intel-603317", role="l4-intel")])
    _capsule_normalized(run, "l4-intel-603317",
                        operations=[_write_op("c1", str(intel_path), content)])
    md = chain_view.render(run, CODE)
    assert "写入核验:MATCH(与当前发布版本字节一致)" in md


def test_find_invocation_subject_substring_does_not_match_a_different_neighbour(tmp_path, monkeypatch):
    """finding 4(fix round 1,cheap coverage):真实生产者(`.claude/workflows/l4-stock.js:166`)
    对 l4-card/l4-intel 只发裸六位代码当 subject,子串匹配分支今天完全不会被真实数据触发
    ——但从没有夹具练过它。构造一个"看起来像"、数字其实不同的邻居 invocation(平安银行
    000001,不含目标 603317 的子串),证明:①找到的是目标本身(天味食品 603317 的
    display-name 风格 subject,走的正是子串匹配那条分支)②邻居不会被误当成目标。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    _capsule_index(run, [
        _row("l4-card-a", subject="603317 天味食品"),
        _row("l4-card-b", subject="000001 平安银行"),
    ])
    src = chain_view.Sources(run)
    merged = chain_view._merged_invocations(src, run.name)

    found = chain_view._find_invocation(merged, "l4-card", "603317")
    assert found is not None
    iid, m = found
    assert iid == "l4-card-a"
    assert (m["capsule"] or {}).get("subject") == "603317 天味食品"

    # 反向核验:搜平安银行的代码不会被"天味食品"那行误配。
    found_other = chain_view._find_invocation(merged, "l4-card", "000001")
    assert found_other is not None
    assert found_other[0] == "l4-card-b"


# ═══════════════════════ fix round 2:复核两项 ═══════════════════════

def test_render_reads_each_evidence_file_at_most_once(tmp_path, monkeypatch):
    """finding 1(fix round 2):`_sec_l4` 与 `_sec_scene` 曾经各自独立重建一遍合并表,
    每条 PRESENT 记录的"能不能归一化"判断(fix round 1)与真正消费又是各读一次——一条
    记录一次 render() 里最多被读 4 次。用计数包装器包住真正的读取原语
    (`chain_view._read_json_or_none`,所有磁盘 JSON 读取的唯一入口),断言一次渲染里
    每个文件路径都只被读了一次。verbose 模式两段都会消费 normalized 文档,最容易暴露
    重复读。"""
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    _capsule_index(run, [_row("l4-card-603317"), _row("l4-intel-603317", role="l4-intel")])
    _capsule_normalized(run, "l4-card-603317", operations=[_read_op("c1", "/x/slim.md")])
    _capsule_normalized(run, "l4-intel-603317", operations=[_read_op("i1", "/x/intel.md")])

    counts: dict[str, int] = {}
    real_read = chain_view._read_json_or_none

    def counting(path):
        counts[str(path)] = counts.get(str(path), 0) + 1
        return real_read(path)

    monkeypatch.setattr(chain_view, "_read_json_or_none", counting)
    md = chain_view.render(run, CODE, verbose=True)

    over_read = {p: n for p, n in counts.items() if n > 1}
    assert not over_read, f"这些文件在一次 render() 里被读了不止一次:{over_read}"
    # 不是空跑一场没读到任何东西——真的读到了 capsule 索引与两份 normalized 产物。
    assert any(p.endswith("capsule/agents/index.json") for p in counts)
    assert sum(1 for p in counts if p.endswith("l4-card-603317.json")) == 1
    assert sum(1 for p in counts if p.endswith("l4-intel-603317.json")) == 1
    assert "成功 2" in md   # 两条 invocation 的读取都被数进去了,不是被重复读悄悄弄丢


def test_visible_text_classifies_message_agent_message_and_tool_result_correctly(
        tmp_path, monkeypatch):
    """finding 2(fix round 2):`_visible_text_blocks` 的分类逻辑此前只在空/合成
    `items` 上跑过。用 Task 2 两个适配器**真实**产出的 item 形状(`claude.py`/
    `codex.py` 的 `add()` 调用点逐字核对过):

    - `kind="message"`、`role="assistant"`——Claude 侧普通 assistant 可见文本。
    - `kind="message"`、`role="agent"`——Codex 侧 `agent_message` 的真实 role 字面量
      (`("agent" if kind == "agent_message" else "assistant")`,codex.py 逐字如此)、
      文本内容是一段"推理小结"——这是 Codex 把它对用户说的话明文暴露出来的真实形状,
      不是编的;真正的原始 reasoning/encrypted_reasoning 事件在 `_SKIPPED_RESPONSE_
      ITEMS` 那一步就被整个丢弃,从不会变成 `NormalizedItem`,所以它不是这里的反例。
    - `kind="tool_result"`(Claude 真实形状,payload 里恰好也带一个 `content` 键)——
      **必须不被当成可见文本**。这是比"没有 text/content 字段"更硬的反例:它的
      payload 里确实有 `content`,如果分类只看字段有没有、不看 `kind`,这条会被
      误当成可见文本泄出来——证明 kind 检查是真正在起作用,不是摆设。
    """
    monkeypatch.chdir(tmp_path)
    run = _base_run(tmp_path)
    items = [
        {"index": 0, "kind": "message",
         "payload": {"message_id": "m1", "role": "assistant", "text": "助手可见分析文本"},
         "timestamp": None},
        {"index": 1, "kind": "message",
         "payload": {"message_id": "m2", "role": "agent", "text": "推理小结:综合三项指标后判断"},
         "timestamp": None},
        {"index": 2, "kind": "tool_result",
         "payload": {"tool_use_id": "t1", "content": "这是工具返回,不该被当成可见分析文本",
                    "is_error": False},
         "timestamp": None},
    ]
    _capsule_index(run, [_row("l4-card-603317")])
    _capsule_normalized(run, "l4-card-603317", items=items, operations=[])

    md = chain_view.render(run, CODE, verbose=True)
    assert "助手可见分析文本" in md
    assert "推理小结:综合三项指标后判断" in md
    assert "这是工具返回,不该被当成可见分析文本" not in md


# ═══════════════════════ Task 8 (2026-09-12): the real producer, not the
# synthetic ledger fixture ═══════════════════════
#
# `_ledger_index`/`_ledger_normalized` above are Task 5's own synthetic
# contract, built before `transcript_binder.offline_index` (Task 8) existed
# (controller ruling #2: "Task 5's own review flagged that its shape was a
# synthetic contract pending your producer; your review will diff the two").
# This test replaces that synthetic ledger with `offline_index`'s *real*
# output against a real, hand-built frozen run (no `chain_view.py` edits --
# this only proves the already-committed reader correctly consumes the new
# producer's actual bytes).


def _frozen_run_for_chain_view(tmp_path, *, code=CODE, date=DATE, report_run_id="20260827-0827_1930"):
    """A minimal, *real* frozen run `transcript_binder.offline_index` can
    process end to end: a real `capsule/events/events.jsonl` dispatch pair,
    a real `run_contract.json` (with a `workspace_path` this run's own
    archived transcript write path is built to agree with), and one
    retention-archived transcript -- everything `offline_index` actually
    reads, none of `chain_view`'s own synthetic ledger helpers."""
    import gzip

    from autoresearch.trace.events import append_event

    run = _base_run(tmp_path, code=code, date=date, run_id=report_run_id)
    contract_run_id = "20260827T193000000000Z"
    workspace_path = str(tmp_path / "original-workspace")
    contract = {
        "schema_version": 3, "run_id": contract_run_id, "engine": "claude",
        "run_kind": "scan-market", "analysis_date": date, "session_ref": None,
        "workspace_path": workspace_path, "user_config": {},
    }
    (run / "trace" / "run_contract.json").write_text(json.dumps(contract), encoding="utf-8")
    (run / "manifest.json").write_text(
        json.dumps({"analysis_date": date, "run_id": contract_run_id,
                    "generated_at": "2026-08-27T20:00:00"}),
        encoding="utf-8",
    )
    (run / "trace" / "staging" / "run_mode.json").write_text(
        json.dumps({"schema_version": 1, "mode": "FULL"}), encoding="utf-8"
    )
    events_path = run / "capsule" / "events" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    inv_id = f"l4-card-{code}-1"
    # `append_event` stamps `ts` itself (the real wall clock -- it takes no
    # `now=` override); this single-member family binds via a tier-1 product
    # hit with no purpose-built invocation_id, so the exact dispatch/complete
    # timestamps never enter the attribution decision (see `assign()`'s
    # single-member-family fast path) -- the archived rows' own `timestamp`
    # fields below are independent, fixed values used only for readability.
    append_event(
        events_path, run_id=contract_run_id, engine="claude", stage="l4",
        invocation_id=inv_id, attempt=1, subject=code, event_type="AGENT_DISPATCHED",
        payload={"role": "l4-card"},
    )
    append_event(
        events_path, run_id=contract_run_id, engine="claude", stage="l4",
        invocation_id=inv_id, attempt=1, subject=code, event_type="AGENT_COMPLETED",
        payload={"role": "l4-card", "result": {}},
    )
    dispatched_ts = "2026-08-27T19:00:00.000000Z"
    completed_ts = "2026-08-27T19:05:00.000000Z"

    card_abs_path = f"{workspace_path}/staging/{date}/details/{code}.md"
    rows = [
        {
            "type": "assistant", "timestamp": dispatched_ts,
            "message": {"id": "msg-1", "model": "claude-opus-5", "content": [
                {"type": "tool_use", "id": "tool-1", "name": "Write",
                 "input": {"file_path": card_abs_path, "content": "# 决策卡\n"}},
            ]},
        },
        {
            "type": "user", "timestamp": completed_ts,
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "content": "ok", "is_error": False},
            ]},
        },
    ]
    raw = ("\n".join(json.dumps(r) for r in rows) + "\n").encode("utf-8")
    archive_dir = run / "trace" / "transcripts"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / "l4-card-agent-h01chainview.jsonl.gz").write_bytes(
        gzip.compress(raw, compresslevel=6, mtime=0)
    )
    (archive_dir / "_index.json").write_text(
        json.dumps({
            "schema_version": 1, "agents": ["l4-card"],
            "transcripts": [{
                "agent": "l4-card", "file": "agent-h01chainview.jsonl", "status": "PRESENT",
                "raw_bytes": len(raw), "gz_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            }],
        }),
        encoding="utf-8",
    )
    return run, inv_id


def test_h01_real_offline_index_output_renders_through_chain_view(tmp_path, monkeypatch):
    """`chain_view.py` is never edited for this task -- this proves the
    reader already committed in Task 5 correctly consumes
    `transcript_binder.offline_index`'s *actual* bytes (not a hand-built
    stand-in), for a capsule whose own `agents/index.json` never even
    mentions this invocation (the realistic "no capsule row at all" case,
    distinct from V01's "capsule row present but GONE")."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ws, "ENGINE", "claude")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_claude")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_claude")
    from autoresearch.scan import transcript_binder as tb

    run, inv_id = _frozen_run_for_chain_view(tmp_path)
    # No capsule/agents/index.json at all -- exactly what a real published
    # run predating Task 4's active-wiring looks like (never even attempted
    # to bind at publish time -- distinct from V01's "attempted and GONE").

    result = tb.offline_index(run, sessions_root=tmp_path / "no-sessions")
    row = next(r for r in result["invocations"] if r["invocation_id"] == inv_id)
    assert row["status"] == "PRESENT"
    assert row["binding_status"] == "BOUND"

    md = chain_view.render(run, CODE)
    assert "来源=ledger_backfill" in md
    assert "原始状态=NO_CAPSULE_INDEX" in md
    assert "补录状态=PRESENT" in md
    assert "成功 1" in md  # the WRITE_SUCCEEDED offline_index actually recorded

    # Verbose mode resolves the ledger-relative `normalized` path exactly as
    # chain_view.py:_EvidenceCache.normalized_doc computes it -- proving the
    # producer's relative-path convention (report_run_id/revision_id/
    # normalized/<invocation_id>.json) is what the already-committed reader
    # expects, not a shape this test had to special-case.
    verbose_md = chain_view.render(run, CODE, verbose=True)
    assert f"snapshot={row['snapshot_id'][:12]}" in verbose_md
