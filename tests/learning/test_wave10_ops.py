"""Wave10 收尾批:A9 缓存/回执 · A12+C4 哨兵账本 · C2.1 对齐账本。合成,无网络。"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning import l3_l4_alignment as align, sentinel_audit as sa

# ══════════════════ A12 / C4:哨兵校准账本 ══════════════════

def _sentinel_day(root, date, *, mode="SENTINEL_PINNED", pinned=("300857",),
                  reason="材料枯竭"):
    from autoresearch.scan.run_mode import decide, write

    day = root / date
    (day / "retro").mkdir(parents=True, exist_ok=True)
    write(day, decide(sentinel_level="sentinel", force_full=(mode == "FORCED_FULL"),
                      pinned_codes=list(pinned), sentinel_reason=reason))
    return day


def _rejection(day, rows):
    pd.DataFrame(rows).to_csv(day / "retro" / "rejection_attribution.csv", index=False)


def _shadow(tmp_path, date, codes):
    path = tmp_path / "shadow.csv"
    pd.DataFrame([{"date": date, "code": c, "name": "", "conviction": 70,
                   "binding": "", "close": 1.0} for c in codes]).to_csv(path, index=False)
    return path


def test_non_sentinel_days_are_not_audited(tmp_path):
    from autoresearch.scan.run_mode import decide, write

    day = tmp_path / "2026-07-31"
    day.mkdir()
    write(day, decide(sentinel_level="full", force_full=False, pinned_codes=[]))
    assert sa.audit_day(day) is None


def test_false_negative_only_counts_the_preregistered_shadow_scope(tmp_path):
    """**这条是本账本的命门**:全市场口径在 5,000 只票里近乎恒真(abstention v1
    实测 1,299/5,528),会退化成「天天有漏」。只认 shadow scope。"""
    day = _sentinel_day(tmp_path, "2026-07-31")
    _rejection(day, [
        {"code": "300857", "buyable": True, "mature": True, "excess_2": 0.001},
        # scope 之外的大赢家 —— 不该被算成漏肉
        {"code": "999999", "buyable": True, "mature": True, "excess_2": 0.30},
    ])
    row = sa.audit_day(day, shadow_path=_shadow(tmp_path, "2026-07-31", ["300857"]))
    assert row["shadow_scope_n"] == 1
    assert row["false_negative_n"] == 0
    assert row["false_negative_status"] == "CLEAN"


def test_miss_is_flagged_when_a_scope_candidate_clears_two_pp(tmp_path):
    day = _sentinel_day(tmp_path, "2026-07-31")
    _rejection(day, [{"code": "300857", "buyable": True, "mature": True,
                      "excess_2": 0.05}])
    row = sa.audit_day(day, shadow_path=_shadow(tmp_path, "2026-07-31", ["300857"]))
    assert row["false_negative_n"] == 1 and row["false_negative_status"] == "MISS"
    assert row["false_negative_codes"] == "300857"


def test_untradable_or_immature_scope_is_unmeasured_not_clean(tmp_path):
    """不可测 ≠ 干净 —— 把「没数据」记成 CLEAN 会让 frontier 看起来免费。"""
    day = _sentinel_day(tmp_path, "2026-07-31")
    _rejection(day, [{"code": "300857", "buyable": False, "mature": True,
                      "excess_2": 0.05}])
    row = sa.audit_day(day, shadow_path=_shadow(tmp_path, "2026-07-31", ["300857"]))
    assert row["false_negative_status"].startswith(sa.UNMEASURED)
    assert row["false_negative_n"] == 0


def test_empty_scope_is_unmeasured(tmp_path):
    day = _sentinel_day(tmp_path, "2026-07-31")
    row = sa.audit_day(day, shadow_path=tmp_path / "nope.csv")
    assert row["false_negative_status"].startswith(sa.UNMEASURED)


def test_cost_saved_shrinks_with_pinned_count(tmp_path):
    """中间档仍跑持仓 L4 —— 省的是"全扫减去那几只",不是全额。"""
    few = sa.audit_day(_sentinel_day(tmp_path / "a", "2026-07-31", pinned=("300857",)),
                       shadow_path=tmp_path / "nope.csv")
    many = sa.audit_day(_sentinel_day(tmp_path / "b", "2026-07-31",
                                      pinned=tuple(f"00000{i}" for i in range(1, 9))),
                        shadow_path=tmp_path / "nope.csv")
    assert few["cost_saved_estimate_usd"] > many["cost_saved_estimate_usd"]


def test_frontier_keeps_the_unmeasured_denominator_on_screen(tmp_path):
    _sentinel_day(tmp_path, "2026-07-30")
    _sentinel_day(tmp_path, "2026-07-31")
    ledger = sa.roll(tmp_path, shadow_path=tmp_path / "nope.csv")
    stats = sa.frontier(ledger)
    assert stats["sentinel_days"] == 2 and stats["unmeasured_n"] == 2
    body = "\n".join(sa.render(ledger))
    assert "不可测 2/2 日" in body
    assert "不看全市场任一赢家" in body
    assert "不自动改 sentinel 阈值" in body


def test_empty_root_is_graceful(tmp_path):
    assert len(sa.roll(tmp_path)) == 0
    assert any("无哨兵日" in ln for ln in sa.render(sa.roll(tmp_path)))


# ══════════════════ C2.1:L3→L4 对齐账本 ══════════════════

def _align_day(root, date, judged):
    day = root / date
    (day / "retro").mkdir(parents=True, exist_ok=True)
    (day / "_l3_judged.json").write_text(json.dumps(judged, ensure_ascii=False),
                                         encoding="utf-8")
    return day


def test_alignment_row_joins_l3_judgement_with_gate_states(tmp_path):
    from autoresearch.scan.decision_record import DecisionRecord, write_decision_records

    day = _align_day(tmp_path, "2026-07-31", [
        {"code": "300857", "rank": 1, "finalist": True, "conviction": 71},
        {"code": "688766", "rank": 9, "finalist": False, "conviction": 40},
    ])
    pd.DataFrame([{"code": "300857", "name": "x", "cmf_20": 0.3, "obv_20": 0.2,
                   "main_net_ratio": -0.01}]).to_csv(
        day / "L2_gbdt_top200.csv", index=False)
    write_decision_records(day, [DecisionRecord.build(
        analysis_date="2026-07-31", contract_hash=None, code="300857",
        source_rating="Hold", rubric_rating="Hold",
        gate_states={"主力真在": "FAIL", "业绩真兑现": "PASS", "估值不透支": "PASS"},
        early_stop=None, ensemble_ratings=[], final_rating="Hold", proposal="HOLD",
        reason="t", evidence_refs=[], first_rejection_stage="L4_RUBRIC")])

    frame = align.build_day(day)
    row = frame.set_index("code").loc["300857"]
    assert bool(row["l3_finalist"]) is True and row["l3_rank"] == 1
    assert row["gate_main"] == "FAIL" and row["gate_earnings"] == "PASS"
    assert row["cmf20"] == 0.3 and row["main_net_1d"] == -0.01
    # bench 票也在账上(要对照,不能只记 finalist)
    assert "688766" in set(frame["code"])


def test_missing_judged_file_yields_nothing(tmp_path):
    day = tmp_path / "2026-07-31"
    day.mkdir()
    assert len(align.build_day(day)) == 0


def test_summary_refuses_to_conclude_before_the_maturity_gate():
    ledger = pd.DataFrame([
        {"date": "2026-07-31", "code": "a", "l3_finalist": True,
         "gate_main": "PASS", "excess_2": 0.01},
    ], columns=align._COLS)
    stats = align.summarize(ledger)
    assert stats["mature_days"] == 1 and stats["ready"] is False
    assert align.MATURE_DAYS_REQUIRED == 20
    body = "\n".join(align.render(ledger))
    assert "IMMATURE" in body and "不得据此下结论" in body


def test_summary_denominator_excludes_unknown_gate_states():
    """`UNMEASURED` 的门态不该进通过率分母 —— 否则「没判」会被算成「没通过」。"""
    ledger = pd.DataFrame([
        {"date": "d", "code": "a", "l3_finalist": True, "gate_main": "PASS",
         "excess_2": None},
        {"date": "d", "code": "b", "l3_finalist": True, "gate_main": "UNMEASURED",
         "excess_2": None},
    ], columns=align._COLS)
    s = align.summarize(ledger)["finalist"]
    assert s["n"] == 2 and s["gate_main_known"] == 1
    assert s["gate_main_pass_rate"] == 1.0


def test_render_states_the_objective_is_not_the_pass_rate():
    ledger = pd.DataFrame([{"date": "d", "code": "a", "l3_finalist": True,
                            "gate_main": "PASS", "excess_2": 0.01}],
                          columns=align._COLS)
    body = "\n".join(align.render(ledger))
    assert "目标函数不是过门率" in body
    assert "exp_20260729_l3_hard_constraint_f" in body     # 家族冲突提醒不能丢


def test_empty_root_is_graceful_alignment(tmp_path):
    assert len(align.roll(tmp_path)) == 0


# ══════════════════ A9:兜底缓存 + 建档回执 ══════════════════

def test_fetch_cache_is_opt_in(tmp_path, monkeypatch):
    """纯取数函数不该偷偷带全局缓存 —— 实测它让 5 条既有用例读到别人的条目。"""
    from autoresearch.data.sources import anns_fallback as af

    monkeypatch.setattr(af, "_CACHE_ROOT", tmp_path / "cache")
    calls = []

    def fake(code6, date):
        calls.append(code6)
        return pd.DataFrame([{"公告标题": "t", "公告时间": "2026-07-30"}])

    monkeypatch.setattr(af, "_raw_notices", fake)
    af.fetch_anns("000001", "2026-07-31")                    # 默认不缓存
    af.fetch_anns("000001", "2026-07-31")
    assert len(calls) == 2

    af.fetch_anns("000002", "2026-07-31", cache=True)        # 显式开 → 第二次命中
    af.fetch_anns("000002", "2026-07-31", cache=True)
    assert calls.count("000002") == 1


def test_negative_cache_expires_but_positive_does_not(tmp_path, monkeypatch):
    """把「这次没查到」永久缓存 = 把一次抖动固化成「这只票没有公告」。"""
    from autoresearch.data.sources import anns_fallback as af

    monkeypatch.setattr(af, "_CACHE_ROOT", tmp_path / "cache")
    af._cache_write("000001", "2026-07-31", [])              # 负缓存
    assert af._cache_read("000001", "2026-07-31") == []
    monkeypatch.setattr(af, "NEGATIVE_TTL_SECONDS", -1)
    assert af._cache_read("000001", "2026-07-31") is None    # 过期 → 重查

    af._cache_write("000002", "2026-07-31", [{"ann_date": "20260730", "title": "t",
                                              "source": af.SOURCE_TAG}])
    assert af._cache_read("000002", "2026-07-31")            # 正缓存不随 TTL 失效


def test_fetch_error_is_not_cached(tmp_path, monkeypatch):
    """异常是「没查成」,不是「没有公告」—— 记成负缓存会让抖动伪装 6 小时。"""
    from autoresearch.data.sources import anns_fallback as af

    monkeypatch.setattr(af, "_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(af, "_raw_notices",
                        lambda c, d: (_ for _ in ()).throw(RuntimeError("boom")))
    assert af.fetch_anns("000001", "2026-07-31", cache=True) == []
    assert af._cache_read("000001", "2026-07-31") is None


def test_batch_is_bounded_and_covers_every_code(tmp_path, monkeypatch):
    from autoresearch.data.sources import anns_fallback as af

    monkeypatch.setattr(af, "_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(af, "_raw_notices", lambda c, d: pd.DataFrame(
        [{"公告标题": f"t{c}", "公告时间": "2026-07-30"}]))
    out = af.fetch_anns_batch(["1", "000002", "300857"], "2026-07-31", workers=4)
    assert set(out) == {"000001", "000002", "300857"}
    assert all(len(v) == 1 for v in out.values())


def test_l3_news_uses_the_patchable_fallback_name():
    """并发放在调用侧、逐票仍走模块级名字 —— 直接 import 批量函数会绕过既有 monkeypatch,
    让「兜底真被调用 / 异常降级不抛」那几条用例静默失效。"""
    from pathlib import Path

    src = Path("autoresearch/scan/agents/l3_news.py").read_text(encoding="utf-8")
    assert "_fallback_fetch_anns(code, date)" in src
    assert "ThreadPoolExecutor" in src


def test_enqueue_receipt_separates_written_from_visible(tmp_path, monkeypatch):
    """Wave9 I-1 的病灶:写进去了 ≠ 消费者看得见。一个数说不清,所以要三个。"""
    from autoresearch.scan import post_run

    day = tmp_path / "2026-07-31"
    day.mkdir()
    (day / "_dossier_present.json").write_text("[]", encoding="utf-8")
    pd.DataFrame([{"code": "300857", "lane": ""}, {"code": "688766", "lane": "pinned"}]
                 ).to_csv(day / "finalists.csv", index=False)
    pool = tmp_path / "coverage_pool.json"
    pool.write_text(json.dumps({"stocks": {}, "pending_init": []}), encoding="utf-8")
    monkeypatch.setattr(post_run, "_pool_path", lambda: pool)
    monkeypatch.setattr("autoresearch.dossier.pool.POOL_PATH", pool)

    receipt = post_run.enqueue_receipt(day, "2026-07-31")
    assert receipt["requested"] == 1                 # pinned 不算入围
    assert receipt["inserted"] == 1
    assert "newly_visible" in receipt                # 三数齐,报告只用 newly_visible


def test_enqueue_receipt_does_not_change_the_list_contract(tmp_path, monkeypatch):
    """`enqueue_finalist_dossiers` 的 list[str] 返回被 20 个用例锁着,不得改。"""
    import inspect

    from autoresearch.scan import post_run

    sig = inspect.signature(post_run.enqueue_finalist_dossiers)
    assert str(sig.return_annotation).endswith("list[str]")
