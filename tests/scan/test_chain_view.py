"""推荐链路视图(2026-08-26 §4.3 R7)。

判据两条:① 十段都在场时把片段**接对**(接错比缺席更危险);② 缺片段时**明写缺席**,
绝不静默跳过或伪造 —— 老 run 天然缺一半,复盘的人必须知道自己在看什么。
"""
from __future__ import annotations

import json

from autoresearch.common import workspace as ws
from autoresearch.scan import chain_view


def _run(tmp_path, *, with_mirror=True):
    run = tmp_path / ws.reports_root() / "scan" / "20260825_2149"
    (run / "details").mkdir(parents=True)
    (run / "trace").mkdir(exist_ok=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-08-25"}), encoding="utf-8")
    (run / "brief.md").write_text("- ✅ relative BUY:天味食品 603317 · 卡面 Underweight\n",
                                  encoding="utf-8")
    (run / "details" / "天味食品.md").write_text("# 决策卡 — 603317 天味食品\n", encoding="utf-8")
    if not with_mirror:
        return run
    st = run / "trace" / "staging"
    st.mkdir(parents=True)
    (st / "L1_scored_full.csv").write_text(
        "code,name,industry,composite,pct_1d,pct_60d\n603317,天味食品,调味发酵品Ⅱ,60.7,0.07,-10.1\n",
        encoding="utf-8")
    (st / "L1_recall_top1000.csv").write_text(
        "code,recall_channels,n_channels,best_rank\n603317,growth,1,26\n", encoding="utf-8")
    (st / "L1_channels.csv").write_text(
        "channel,code,channel_rank,channel_score\ngrowth,603317,26,91.5\n", encoding="utf-8")
    (st / "L2_gbdt_top200.csv").write_text(
        "code,l2_rank,gbdt_score,selection_reason,selection_detail,l2_lane_reserved\n"
        "603317,143,60.7,lane,成长,True\n", encoding="utf-8")
    (st / "_l3_pass1_kept.csv").write_text(
        "code,selection_reason,selection_detail\n603317,lane,growth\n", encoding="utf-8")
    (st / "_l3_pass1_meta.json").write_text(
        json.dumps({"n_in": 201, "n_kept": 40, "target": 40, "forced_in": 6}), encoding="utf-8")
    (st / "L3_judged_full.csv").write_text(
        "code,conviction,lane,triage_lean,finalist,thesis,mechanism,risk,catalyst\n"
        "603317,55,reversion,Hold,True,深超卖,说明会估值修复,低基旗,业绩说明会\n", encoding="utf-8")
    (st / "finalists.csv").write_text("code,name,lane,guard\n603317,天味食品,reversion,\n",
                                      encoding="utf-8")
    (st / "_early_stop.json").write_text(
        json.dumps({"603317": {"phase": "P3", "reason": "资金流出"}}), encoding="utf-8")
    (st / "_final_ratings.json").write_text(json.dumps({"603317": "Underweight"}), encoding="utf-8")
    (st / "decision_records.json").write_text(json.dumps({"records": [{
        "code": "603317", "source_rating": "Underweight", "rubric_rating": "Underweight",
        "final_rating": "Underweight", "proposal": "SELL",
        "gate_states": {"主力真在": "UNKNOWN"}, "reason": "early_stop:P3:资金流出"}]}),
        encoding="utf-8")
    (st / "_relative_buy_decision.json").write_text(json.dumps({
        "mode": "active", "rule_version": "e6.v2.0", "blocked": False,
        "buys": [{"code": "603317", "rank": 1, "basis": "relative"}],
        "candidates": [{"code": "603317", "eligible": True, "rank": 2,
                        "faces": {"target_align": 0.9}, "hard_gate": {"no_redflag": True},
                        "relative_decision_score": 0.475, "research_rating": "Underweight"}],
        "excluded": []}), encoding="utf-8")
    (st / "_candidate_passport.json").write_text(json.dumps({
        "candidates": {"603317": {"code": "603317", "l3": {"conviction": 55}}}}), encoding="utf-8")
    (st / "_l4_prompt_603317.md").write_text("# L4 派发", encoding="utf-8")
    (st / "_l4_intel_603317.md").write_text("# 活体情报", encoding="utf-8")
    inputs = run / "trace" / "inputs" / "slim"
    inputs.mkdir(parents=True)
    (inputs / "603317.SS_2026-08-25_slim.md").write_text("x" * 9000, encoding="utf-8")
    (inputs / "603317.SS_2026-08-25_slim_deep.md").write_text("y" * 5000, encoding="utf-8")
    return run


# ───────────────────── ⑩ 结果段:schema 2 / 日历状态 / 反事实标签(2026-09-12 Task C2) ─────────────────────
#
# 与上面 `_run` 的夹具不同,这些测试真的写一份 `outcome.py` 的逐 run JSON(`outcome.write_outcome`),
# 因为 `_sec_outcome` 只在那份文档存在时才走到"结果"段本体——其它既有测试(`test_full_chain_links_
# every_stage` 等)从不创建它,因此对它们零影响(已用 `-k` 复核)。

def _outcome_doc(run, **overrides) -> dict:
    from autoresearch.scan import outcome as oc

    base = {
        "schema_version": oc.OUTCOME_SCHEMA_VERSION, "run_id": run.name,
        "contract_run_id": "x", "analysis_date": "2026-08-25", "ruler": oc.MAIN,
        "outcome_status": oc.MATURE, "reason": "",
        "calendar_quality": oc.TRADE_CAL_QUALITY, "calendar_digest": "d0",
        "t1": "20260826", "t2": "20260827",
        "decision_mode": "active", "rule_version": "e6.v2.0", "read_from_shared_staging": False,
        "complete": True, "n_rows": 1, "n_scored": 1,
        "exec_line": {"max_pct_1d": 3.0, "max_pos_in_range": 0.7},
        "execution": {"first_available_session": "2026-08-26", "exec_lag": 0,
                      "actionability_status": "ACTIONABLE"},
        "rows": {"603317": {"code": "603317", "name": "天味食品", "role": "BUY",
                             "t1_close": 10.6, "t1_pct_chg": 6.0, "t1_pos_in_range": 0.8,
                             "exec_ok": False, "t2_open": 10.5, oc.MAIN: 0.08,
                             "rel_gap_market": 0.02, "rel_gap_sector": 0.01,
                             "fwd_5_oc": None, "fwd_10_oc": None,
                             "exec_gap_c1_o2": None, "exec_outcome_status": None}},
    }
    base.update(overrides)
    return base


def test_outcome_section_shows_dates_and_calendar_quality_for_a_mature_run(tmp_path, monkeypatch):
    """bullet 6/9:MATURE 结果的口径行必须带 T+1/T+2 日期与日历 quality,主尺标签必须
    显式标"毛"(C14:不能读成实际成交)。"""
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan import outcome as oc

    run = _run(tmp_path)
    oc.write_outcome(_outcome_doc(run), run.parent)

    md = chain_view.render(run, "603317")
    assert "T+1 20260826" in md and "T+2 20260827" in md
    assert "quality=trade_cal" in md
    assert "推荐毛收益" in md and "非实际成交" in md
    assert "0.08" in md


def test_outcome_section_shows_the_not_yet_mature_reason_instead_of_stale_numbers(tmp_path, monkeypatch):
    """bullet 7:消费者的**输出**必须随 `outcome_status` 真的变化——非 MATURE 时不能展示
    任何行级数字(没有可汇总的主尺值可展示),必须显式给出状态与原因。"""
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan import outcome as oc

    run = _run(tmp_path)
    oc.write_outcome(_outcome_doc(run, outcome_status=oc.UNVERIFIED_CALENDAR,
                                  reason="日历质量不可信:lake_partitions",
                                  calendar_quality="lake_partitions",
                                  complete=False, rows={}), run.parent)

    md = chain_view.render(run, "603317")
    assert "未成熟/未核验" in md
    assert "UNVERIFIED_CALENDAR" in md
    assert "日历质量不可信" in md
    assert "0.08" not in md               # 旧数字不得残留在非成熟展示旁边
    assert "推荐毛收益" not in md


def test_outcome_section_separates_the_execution_counterfactual_from_the_main_ruler(tmp_path, monkeypatch):
    """bullet 9:反事实(迟到锚)必须与主尺视觉上分开、明确标注,且**只在适用时才出现**——
    `exec_outcome_status is None`(不适用)时这一行不应该渲染出来。"""
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan import outcome as oc

    run = _run(tmp_path)
    # 不适用(正常 run,没有迟到锚)—— 不应该出现反事实行。
    oc.write_outcome(_outcome_doc(run), run.parent)
    md_normal = chain_view.render(run, "603317")
    assert "反事实" not in md_normal

    # 适用且算出了值 —— 必须单独一行,明确标"反事实"与"非实际成交",且与主尺分开。
    doc = _outcome_doc(run)
    doc["rows"]["603317"]["exec_gap_c1_o2"] = 0.077
    doc["rows"]["603317"]["exec_outcome_status"] = oc.MATURE
    oc.write_outcome(doc, run.parent)
    md_late = chain_view.render(run, "603317")
    assert "反事实" in md_late and "非实际成交" in md_late
    assert "0.077" in md_late
    # 2026-09-12 Task 5(controller ruling #4):spec §8 末段要求"推荐毛收益、事后执行
    # 条件测算、迟到报告反事实收益、实际成交"四个标签必须分开命名——"未接 broker,
    # 实际成交未知"必须是独立一行的断言,不能只靠"非实际成交"四个字的否定形态代替
    # (那句是给前三者的免责说明,不是"实际成交"这件事本身的答案)。旧断言(唯一出现
    # 只在"非实际成交"子串里)因此改向:两处"非实际成交"标注仍在,外加一条独立的
    # "实际成交:未知"事实行。
    assert md_late.count("非实际成交") == 2   # 推荐毛收益 + 执行反事实估计,各自标注
    assert "实际成交:未知" in md_late


def test_full_chain_links_every_stage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    md = chain_view.render(_run(tmp_path), "603317")
    for marker in ("① run 身份", "② 候选护照", "③ L1 召回", "④ L2 菜单", "⑤ L3 pass1 分诊",
                   "⑥ L3 精排判断", "⑦ L4 研究", "⑧ E6 相对 BUY 决策", "⑨ 报告口径", "⑩ 结果"):
        assert marker in md
    # 每段都接对了它自己的那条事实(接错比缺席更危险)
    assert "growth #26(91.5)" in md            # L1 逐路名次
    assert "143 / 60.7" in md                  # L2 rank/score
    assert "lane` · growth" in md or "`lane` · growth" in md
    assert "201 → 40" in md                    # pass1 分诊规模
    assert "说明会估值修复" in md               # L3 兑现机制
    assert "停于 P3 · 停因「资金流出」" in md    # L4 早停
    assert "proposal=SELL" in md               # 决策记录
    assert "本票就是当日 BUY" in md             # E6
    assert "details/天味食品.md" in md          # 发布卡(相对路径)


def test_absent_pieces_are_named_not_skipped(tmp_path, monkeypatch):
    """老 run(无 trace/staging、无 inputs)—— 每一段都要明写缺席,不能静默跳过。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ws.scan_root()).mkdir(parents=True)
    md = chain_view.render(_run(tmp_path, with_mirror=False), "603317")
    assert md.count(chain_view.ABSENT) >= 5
    # capsule 之前的 run:完好性只能由旧 MANIFEST 回答,完整性必须明说「未知」,
    # 绝不能因为没有 expected 清单就默认通过。
    assert "早于 forensic capsule" in md
    assert "**完整性**:未知" in md
    assert "结果账本尚未回填" in md


def test_shared_staging_fallback_is_flagged(tmp_path, monkeypatch):
    """兜底读共享 staging 时必须打警示 —— 同数据日重跑会覆盖它,那可能已不是本 run 那份
    (实测 64 个已发布 run 只剩 49 个 staging)。"""
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path, with_mirror=False)
    shared = tmp_path / ws.scan_root() / "2026-08-25"
    shared.mkdir(parents=True)
    (shared / "L1_scored_full.csv").write_text("code,name,composite\n603317,天味食品,60.7\n",
                                               encoding="utf-8")
    md = chain_view.render(run, "603317")
    assert "本视图有片段读自共享 staging" in md


def test_code_is_zero_padded(tmp_path, monkeypatch):
    """A 股前导零坑(同族:finalists ticker 丢前导零 → assemble 误判卡片缺失)。"""
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    st = run / "trace" / "staging"
    (st / "L1_scored_full.csv").write_text("code,name,composite\n000651,格力电器,70\n",
                                           encoding="utf-8")
    assert "格力电器" in chain_view.render(run, "651")


def test_render_survives_a_corrupt_section(tmp_path, monkeypatch):
    """一段读坏不该让整张视图消失(坏 JSON 是真实存在的现场)。"""
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    (run / "trace" / "staging" / "_relative_buy_decision.json").write_text("{坏", encoding="utf-8")
    md = chain_view.render(run, "603317")
    assert "⑩ 结果" in md and "⑦ L4 研究" in md


def test_round_floats_only_touches_long_decimals():
    assert chain_view._round_floats("-10.100334448160531%") == "-10.1003%"
    assert chain_view._round_floats("60.7") == "60.7"          # 短的不动
    assert chain_view._round_floats("2026-08-25") == "2026-08-25"


def test_cli_writes_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    out = tmp_path / "chain.md"
    assert chain_view.main([str(run), "603317", "--out", str(out)]) == 0
    assert "天味食品" in out.read_text(encoding="utf-8")
    capsys.readouterr()


def test_cli_missing_run_returns_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert chain_view.main(["nope_run", "603317"]) == 2
    capsys.readouterr()


# --- Task 16: the collapsed ✓ is gone -------------------------------------


def _capsule_run(run_dir, *, completeness_ok, replay="FULL", business="SUCCEEDED"):
    import json

    capsule = run_dir / "capsule"
    (capsule / "verification").mkdir(parents=True, exist_ok=True)
    (capsule / "capsule.json").write_text(
        json.dumps(
            {
                "capsule_schema_version": 1,
                "business_status": business,
                "evidence_status": "COMPLETE" if completeness_ok else "EVIDENCE_INCOMPLETE",
                "replayability": replay,
            }
        ),
        encoding="utf-8",
    )
    (capsule / "verification/completeness.json").write_text(
        json.dumps(
            {
                "completeness_ok": completeness_ok,
                "missing_required": [] if completeness_ok else ["agents/l4-card/*"],
                "coverage": {
                    "agents": {"expected": 2, "present": 2 if completeness_ok else 1},
                    "sources": {"reads": 10, "covered": 10},
                },
            }
        ),
        encoding="utf-8",
    )
    from autoresearch.trace.atomic import sha256_bytes
    from autoresearch.trace.capsule import write_manifest

    manifest = write_manifest(run_dir)
    (capsule / "verification/ROOT.json").write_text(
        json.dumps(
            {
                "root_hash": sha256_bytes(manifest.read_bytes()),
                "durability": "LOCAL_ONLY",
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_chain_view_never_calls_manifest_integrity_scene_completeness(tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws

    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    (tmp_path / ws.scan_root()).mkdir(parents=True)
    run = _run(tmp_path, with_mirror=True)
    _capsule_run(run, completeness_ok=False)

    rendered = chain_view.render(run, "603317")

    assert "**完好性**:✓" in rendered
    assert "**完整性**:✗" in rendered
    assert "现场完整性" not in rendered
    assert "agents/l4-card/*" in rendered
    assert "**现场可复盘**:✗" in rendered


def test_chain_view_green_requires_every_fact(tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws

    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    (tmp_path / ws.scan_root()).mkdir(parents=True)
    run = _run(tmp_path, with_mirror=True)
    _capsule_run(run, completeness_ok=True)

    assert "**现场可复盘**:✓" in chain_view.render(run, "603317")

    _capsule_run(run, completeness_ok=True, replay="NONE")
    assert "**现场可复盘**:✗" in chain_view.render(run, "603317")
