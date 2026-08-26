"""推荐链路视图(2026-08-26 §4.3 R7)。

判据两条:① 十段都在场时把片段**接对**(接错比缺席更危险);② 缺片段时**明写缺席**,
绝不静默跳过或伪造 —— 老 run 天然缺一半,复盘的人必须知道自己在看什么。
"""
from __future__ import annotations

import json

from autoresearch.scan import chain_view


def _run(tmp_path, *, with_mirror=True):
    run = tmp_path / "reports_claude" / "scan" / "20260825_2149"
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
    (tmp_path / "context_claude" / "scan").mkdir(parents=True)
    md = chain_view.render(_run(tmp_path, with_mirror=False), "603317")
    assert md.count(chain_view.ABSENT) >= 5
    assert "MANIFEST 缺席" in md
    assert "结果账本尚未回填" in md


def test_shared_staging_fallback_is_flagged(tmp_path, monkeypatch):
    """兜底读共享 staging 时必须打警示 —— 同数据日重跑会覆盖它,那可能已不是本 run 那份
    (实测 64 个已发布 run 只剩 49 个 staging)。"""
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path, with_mirror=False)
    shared = tmp_path / "context_claude" / "scan" / "2026-08-25"
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
