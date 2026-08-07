"""Wave11 A3:账本历史回填 gap 列 + ruler tag(隔夜尺 gap_c1_o2 换尺前的防混尺基建)。

背景:主尺正从 `fwd_2_oc` 换到 `gap_c1_o2`(T16 才 flip;本 task 结束 MAIN_RULER 仍是
`fwd_2_oc`)。多个账本的持久化列固定叫 `fwd_2_oc`(不随主尺漂移,这是对的),但取值来自
`MAIN_RULER` —— T16 flip 之后,同一列里新行装 gap 值、历史行装 oc 值,任何对该列求均值/
中位的地方都在算混合物,且不会报错、不会变红。本文件覆盖两件事:

1. `retro.refresh_attributions` 的 gap-only 轻量回填路径(只追加列、不碰旧值/行数/其余判据)。
2. `ruler` tag 的写侧(每行入账时打 MAIN_RULER 真值)+ 读侧兜底(`row.get("ruler",
   "fwd_2_oc")`)+ 聚合侧(gate_attribution.summarize / earlystop_ledger.render /
   l3_audit_ledger.roll 按 ruler 分段,不跨尺揉均值)。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning import (
    earlystop_ledger as el,
    ensemble_ledger as ens,
    gate_attribution as ga,
    l3_audit_ledger as lal,
    retro,
    t1_review as t1,
)

# ═══════════════════════ ① refresh_attributions:gap-only 轻量回填 ═══════════════════════
#
# premise-check(Step 1):`grep -n "realized_returns\|forward_returns" retro.py` 显示
# `realized_returns()` 内部调 `fl.forward_returns()`(T11 后该函数已产出 gap_c1_o2/
# buyable_c1/unsellable_o2),但 `realized_returns` 自己的落盘白名单 `cols` 此前把这三列
# 过滤掉了 —— "透传 forward_returns 全列" 这条premise 在 `forward_returns` 层为真,在
# `realized_returns` 出口处为假,是本 task 要修的第一个洞(见 retro.py `cols` 列表)。
#
# fixture 说明(源码为准,不让 fixture 撒谎):`refresh_attributions` 的既有判据要求
# `retro/done.json` 在场才会考虑这一天(源码 `p / "retro" / "done.json").exists()`)——
# 补上这个哨兵文件。同时 `fwd_10_oc`/`hi_10_oc`/`fwd_5_oc` 必须**已有真值**,否则会先
# 命中老的 `need_full` 判据、整段走全量 `attribute()`(需要 L1_scored_full.csv 等一整套
# 文件,本测试不提供,会静默失败跳过,永远走不到本测试真正要练的 gap-only 分支)。


def _mk_complete_day(root, date):
    """一天"只缺 gap 列"的 attribution.csv(其余判据全部满足,隔离出 need_gap 单一路径)。"""
    d = root / date / "retro"
    d.mkdir(parents=True)
    (d / "done.json").write_text("{}", encoding="utf-8")
    old = pd.DataFrame({
        "code": ["000001", "000002"],
        "fwd_2_oc": [0.02, -0.01],
        "fwd_5_oc": [0.03, -0.02],
        "fwd_10_oc": [0.04, -0.03],
        "hi_10_oc": [0.05, 0.01],
        "bucket": ["hit", "miss"],
    })
    old.to_csv(d / "attribution.csv", index=False)
    return d / "attribution.csv"


def test_refresh_adds_gap_cols_without_touching_old_and_is_idempotent(tmp_path, monkeypatch):
    path = _mk_complete_day(tmp_path, "2026-07-31")
    fake = pd.DataFrame({
        "code": ["000001", "000002"], "fwd_2_oc": [0.02, -0.01],
        "gap_c1_o2": [0.05, -0.015], "buyable_c1": [True, True],
        "unsellable_o2": [False, False],
    })
    monkeypatch.setattr(retro, "realized_returns", lambda *a, **k: fake)

    done = retro.refresh_attributions(scan_root=tmp_path)          # 触发判据:缺 gap_c1_o2 列
    assert done == ["2026-07-31"]

    got = pd.read_csv(path, dtype={"code": str})
    assert {"gap_c1_o2", "buyable_c1", "unsellable_o2", "ruler"} <= set(got.columns)
    # 旧列旧值原样不动
    assert got["fwd_2_oc"].tolist() == [0.02, -0.01]
    assert got["bucket"].tolist() == ["hit", "miss"]
    assert got["fwd_10_oc"].tolist() == [0.04, -0.03]
    # 不改行数
    assert len(got) == 2
    # 新列取到了真值,ruler 打了写入那一刻的 MAIN_RULER
    assert got["gap_c1_o2"].tolist() == [0.05, -0.015]
    assert (got["ruler"] == "fwd_2_oc").all()

    before = path.read_bytes()
    done2 = retro.refresh_attributions(scan_root=tmp_path)         # 幂等:第二跑零改动
    assert done2 == []
    assert path.read_bytes() == before


def test_refresh_gap_backfill_preserves_row_order_when_realized_has_extra_codes(tmp_path, monkeypatch):
    """realized 里多出/缺失的代码不得让 attr 的行数或顺序漂移(merge 必须是 left-on-attr)。"""
    d = tmp_path / "2026-07-28" / "retro"
    d.mkdir(parents=True)
    (d / "done.json").write_text("{}", encoding="utf-8")
    old = pd.DataFrame({
        "code": ["300100", "000002", "600001"],
        "fwd_2_oc": [0.01, 0.02, 0.03],
        "fwd_5_oc": [0.01, 0.02, 0.03], "fwd_10_oc": [0.01, 0.02, 0.03], "hi_10_oc": [0.02, 0.03, 0.04],
    })
    old.to_csv(d / "attribution.csv", index=False)
    # realized 顺序不同、且多一个 attr 没有的代码、少一个 attr 有的代码(000002 缺席 → NaN)
    fake = pd.DataFrame({
        "code": ["600001", "999999", "300100"],
        "gap_c1_o2": [0.09, 0.50, 0.07],
        "buyable_c1": [True, True, False],
        "unsellable_o2": [False, False, True],
    })
    monkeypatch.setattr(retro, "realized_returns", lambda *a, **k: fake)

    retro.refresh_attributions(scan_root=tmp_path)
    got = pd.read_csv(d / "attribution.csv", dtype={"code": str})
    assert got["code"].tolist() == ["300100", "000002", "600001"]   # 行序与旧文件一致,不随 realized 漂
    by_code = got.set_index("code")
    assert by_code.loc["300100", "gap_c1_o2"] == 0.07
    assert by_code.loc["600001", "gap_c1_o2"] == 0.09
    assert pd.isna(by_code.loc["000002", "gap_c1_o2"])               # realized 没有该码 → NaN,不是 0/丢行
    assert "999999" not in got["code"].tolist()                      # realized 多出的码不混进 attr


def test_refresh_gap_backfill_skips_when_realized_lacks_gap_column(tmp_path, monkeypatch):
    """数据源太老、realized_returns 连 gap_c1_o2 都没有 → 无从回填,诚实跳过(不写 ruler、不算已处理)。"""
    path = _mk_complete_day(tmp_path, "2026-06-01")
    before = path.read_bytes()
    fake_no_gap = pd.DataFrame({"code": ["000001", "000002"], "fwd_2_oc": [0.02, -0.01]})
    monkeypatch.setattr(retro, "realized_returns", lambda *a, **k: fake_no_gap)

    done = retro.refresh_attributions(scan_root=tmp_path)
    assert done == []
    assert path.read_bytes() == before                               # 文件真的没被碰


def test_refresh_full_path_unaffected_by_gap_condition(tmp_path, monkeypatch):
    """老判据(缺 fwd_10_oc/hi_10_oc)仍走全量 attribute(),不因新增 gap 判据而改行为。"""
    d = tmp_path / "2026-07-15" / "retro"
    d.mkdir(parents=True)
    (d / "done.json").write_text("{}", encoding="utf-8")
    pd.DataFrame({"code": ["000001"], "fwd_1_oo": [0.01], "fwd_5_oc": [0.05]}).to_csv(
        d / "attribution.csv", index=False)                          # 缺 fwd_10_oc/hi_10_oc → need_full
    called = []

    def fake_attribute(date, scan_root=None, report_root=None, abs_thresh=0.03):
        called.append(date)
        return pd.DataFrame()

    monkeypatch.setattr(retro, "attribute", fake_attribute)
    done = retro.refresh_attributions(scan_root=tmp_path)
    assert done == ["2026-07-15"] and called == ["2026-07-15"]


# ═══════════════════════ ② ruler tag:写侧(retro.attribute_frame / t1_review.append_ledger) ═══════════════════════


def test_attribute_frame_tags_every_row_with_current_ruler():
    realized = pd.DataFrame({
        "code": ["000001", "000002"], "fwd_1_oo": [0.01, -0.01], "fwd_2_oc": [0.02, -0.02],
        "fwd_5_oc": [float("nan")] * 2, "buyable": [True, True],
    })
    l1 = pd.DataFrame({"code": realized["code"], "composite": 0.5, "recalled": False})
    attr = retro.attribute_frame(l1, realized, buylist={})
    assert "ruler" in attr.columns
    assert (attr["ruler"] == "fwd_2_oc").all()          # 当前 MAIN_RULER 真值,本 task 结束仍是它


def test_build_retro_pairs_tags_ruler():
    attr = pd.DataFrame([
        {"code": "000001", "name": "买错", "industry": "半导体", "rating": "Overweight",
         "fwd_2_oc": -0.06, "winner": False, "bucket": "other"},
        {"code": "000002", "name": "漏赢", "industry": "半导体", "rating": "Underweight",
         "fwd_2_oc": 0.15, "winner": True, "bucket": "missed_l1"},
    ])
    pairs = retro.build_retro_pairs(attr)
    assert len(pairs) == 1
    assert pairs.iloc[0]["ruler"] == "fwd_2_oc"


def test_append_ledger_tags_new_rows_and_never_rewrites_legacy_rows(tmp_path):
    """历史行(本列上线前写的)没有 ruler 字段;新写的行才打标;旧行原样不回写。"""
    lp = tmp_path / "ledger.jsonl"
    legacy_row = {
        "t": "2026-07-10", "t1": "2026-07-13", "code": "600000", "name": "老票",
        "rating": "Overweight", "conviction": 70, "l4_conf": "", "cc1": 0.03, "oc1": 0.02,
        "excess": 0.02, "excess_ind": 0.02, "z": 1.0, "verdict": "准", "surprise": False,
        "sealed": False, "limit": "", "mechanism": None, "why": None, "stage": None,
        "diagnosed": False, "ts": "2026-07-10T18:00:00",
    }
    assert "ruler" not in legacy_row                    # 上线前的历史形状,没有这个键
    lp.write_text(json.dumps(legacy_row, ensure_ascii=False) + "\n", encoding="utf-8")

    sc = pd.DataFrame([{
        "code": "600001", "name": "新票", "rating": "Overweight", "conviction": 80.0,
        "l4_conf": "高", "cc1": 0.05, "oc1": 0.04, "excess": 0.03, "excess_ind": 0.03,
        "z": 1.5, "verdict": "准", "surprise": False, "sealed": False, "limit": "",
    }])
    res = {"t": "2026-07-16", "t1": "2026-07-17", "scorecard": sc}
    n = t1.append_ledger(res, path=lp)
    assert n == 1

    rows = [json.loads(ln) for ln in lp.read_text(encoding="utf-8").splitlines()]
    by_code = {r["code"]: r for r in rows}
    assert "ruler" not in by_code["600000"]              # 旧行没被"顺手"回填 tag
    assert by_code["600000"] == legacy_row               # 旧行字节级不变(round-trip 也没漂移其它字段)
    assert by_code["600001"]["ruler"] == "fwd_2_oc"       # 新行:写入那一刻的 MAIN_RULER 真值

    # 读侧兜底契约:旧行用 .get("ruler","fwd_2_oc") 读,诚实标旧尺(不是"未知")
    assert by_code["600000"].get("ruler", "fwd_2_oc") == "fwd_2_oc"


# ═══════════════════════ ③ 聚合侧:构造两种 ruler 混合的账本,断言不被揉进一个均值 ═══════════════════════


def test_gate_attribution_summarize_segments_by_ruler_not_blended():
    """同一 (cohort, gate) 下 fwd_2_oc 尺与 gap_c1_o2 尺的 excess_2 分布截然不同 ——
    若聚合层没分尺,mean_excess_2 会是两者的混合物(灵魂契约:分字段不分聚合=白做)。
    """
    rows = pd.DataFrame([
        # fwd_2_oc 尺:全部 CORRECT(excess 很负)
        {"cohort_version": ga.COHORT_V3, "date": "2026-07-01", "gate": "主力真在", "code": "000001",
         "tradable": True, "mature": True, "fwd_2_oc": -0.10, "market_fwd_2": 0.0,
         "market_baseline": "median_tradable_mature", "excess_2": -0.10,
         "outcome": "CORRECT", "outcome_reason": "underperformed_market", "source": "test",
         "ruler": "fwd_2_oc"},
        {"cohort_version": ga.COHORT_V3, "date": "2026-07-01", "gate": "主力真在", "code": "000002",
         "tradable": True, "mature": True, "fwd_2_oc": -0.08, "market_fwd_2": 0.0,
         "market_baseline": "median_tradable_mature", "excess_2": -0.08,
         "outcome": "CORRECT", "outcome_reason": "underperformed_market", "source": "test",
         "ruler": "fwd_2_oc"},
        # gap_c1_o2 尺(假设 T16 已 flip 后写的行):全部 FALSE_KILL(excess 很正)
        {"cohort_version": ga.COHORT_V3, "date": "2026-08-10", "gate": "主力真在", "code": "000003",
         "tradable": True, "mature": True, "fwd_2_oc": 0.20, "market_fwd_2": 0.0,
         "market_baseline": "median_tradable_mature", "excess_2": 0.20,
         "outcome": "FALSE_KILL", "outcome_reason": "excess2_ge_2pp", "source": "test",
         "ruler": "gap_c1_o2"},
        {"cohort_version": ga.COHORT_V3, "date": "2026-08-10", "gate": "主力真在", "code": "000004",
         "tradable": True, "mature": True, "fwd_2_oc": 0.22, "market_fwd_2": 0.0,
         "market_baseline": "median_tradable_mature", "excess_2": 0.22,
         "outcome": "FALSE_KILL", "outcome_reason": "excess2_ge_2pp", "source": "test",
         "ruler": "gap_c1_o2"},
    ])
    summary = ga.summarize(rows)
    assert len(summary) == 2                              # 分成两行,不是一行混合
    by_ruler = summary.set_index("ruler")
    assert abs(by_ruler.loc["fwd_2_oc", "mean_excess_2"] - (-0.09)) < 1e-9
    assert abs(by_ruler.loc["gap_c1_o2", "mean_excess_2"] - 0.21) < 1e-9
    # 混合会算出的假均值(=0.06)不应该出现在任何一行里
    assert not any(abs(v - 0.06) < 1e-9 for v in summary["mean_excess_2"])
    assert by_ruler.loc["fwd_2_oc", "false_kill_rate"] == 0.0
    assert by_ruler.loc["gap_c1_o2", "false_kill_rate"] == 1.0
    # 渲染层也要看得见分尺(不是只在内部算对、桌面上又归一显示)
    rendered = "\n".join(ga._summary_table(summary))
    assert "fwd_2_oc" in rendered and "gap_c1_o2" in rendered


def test_gate_attribution_summarize_defaults_missing_ruler_to_fwd_2_oc():
    """本列上线前落的行(无 ruler 列)读侧兜底 fwd_2_oc,不因缺列而从聚合里消失或报错。"""
    rows = pd.DataFrame([
        {"cohort_version": ga.COHORT_V3, "date": "2026-07-01", "gate": "业绩真兑现", "code": "000001",
         "tradable": True, "mature": True, "fwd_2_oc": 0.01, "market_fwd_2": 0.0,
         "market_baseline": "median_tradable_mature", "excess_2": 0.01,
         "outcome": "NEUTRAL", "outcome_reason": "inside_economic_band", "source": "test"},
    ])
    assert "ruler" not in rows.columns
    summary = ga.summarize(rows)
    assert summary.iloc[0]["ruler"] == "fwd_2_oc"


def test_earlystop_ledger_render_segments_by_ruler():
    """同一停因桶跨两种尺 → render() 拆行呈现,不把两尺的 fwd_2_oc 均值揉一起。"""
    ledger = pd.DataFrame([
        {"date": "2026-07-01", "code": "000001", "phase": "P3", "reason": "涨停追高",
         "fwd_2_oc": 0.10, "ruler": "fwd_2_oc"},
        {"date": "2026-07-02", "code": "000002", "phase": "P3", "reason": "涨停追高",
         "fwd_2_oc": 0.12, "ruler": "fwd_2_oc"},
        {"date": "2026-08-10", "code": "000003", "phase": "P3", "reason": "涨停追高",
         "fwd_2_oc": -0.20, "ruler": "gap_c1_o2"},
        {"date": "2026-08-11", "code": "000004", "phase": "P3", "reason": "涨停追高",
         "fwd_2_oc": -0.22, "ruler": "gap_c1_o2"},
    ])
    md = "\n".join(el.render(ledger))
    assert "跨 2 种尺" in md
    # 两行分尺呈现,各自均值,不出现混合均值(-0.05)
    assert "| 涨停追高 | fwd_2_oc | n=2 | +11.00% |" in md
    assert "| 涨停追高 | gap_c1_o2 | n=2 | -21.00% |" in md
    assert "-5.00%" not in md and "+5.00%" not in md      # 混合会算出的假均值不该出现


# ═══════════════════════ ④ l3_audit_ledger:读侧对旧文件(无 ruler 键)兜底 ═══════════════════════


def test_l3_audit_ledger_roll_defaults_legacy_payload_ruler(tmp_path):
    """一个"本列上线前"写的旧 JSON(无顶层 ruler、无逐行 ruler)与一个新 JSON 并存,
    roll() 必须把旧文件兜底成 fwd_2_oc,而不是让它在跨日拼接里变成 NaN 或报错。"""
    old_day = tmp_path / "2026-06-01" / "retro"
    old_day.mkdir(parents=True)
    legacy_payload = {
        "schema_version": 1, "date": "2026-06-01", "cohort": "L3_BENCH_SHADOW_AUDIT",
        "production_effect": "NONE", "minimum_forward_scan_days": 20, "forward_scan_days": 1,
        "sample_status": "IMMATURE",
        "summary": {"candidate_n": 1, "mature_n": 1, "opportunity_n": 0,
                    "mean_excess_2": -0.01, "main_finalist_mature_n": 0,
                    "main_finalist_mean_excess_2": None},
        "candidates": [{"code": "000001", "conviction": 80.0, "fragility": 20.0, "lane": "trend",
                        "mature": True, "fwd_2_oc": -0.01, "market_fwd_2": 0.0,
                        "excess_2": -0.01, "opportunity": False}],
    }
    assert "ruler" not in legacy_payload and "ruler" not in legacy_payload["candidates"][0]
    (old_day / "l3_audit_ledger.json").write_text(
        json.dumps(legacy_payload, ensure_ascii=False), encoding="utf-8")

    new_day = tmp_path / "2026-08-10" / "retro"
    new_day.mkdir(parents=True)
    new_payload = {**legacy_payload, "date": "2026-08-10", "ruler": "gap_c1_o2",
                   "candidates": [{**legacy_payload["candidates"][0], "ruler": "gap_c1_o2"}]}
    (new_day / "l3_audit_ledger.json").write_text(
        json.dumps(new_payload, ensure_ascii=False), encoding="utf-8")

    frame, summary = lal.roll(scan_root=tmp_path)
    assert set(frame["ruler"]) == {"fwd_2_oc", "gap_c1_o2"}
    assert frame.loc[frame["date"] == "2026-06-01", "ruler"].iloc[0] == "fwd_2_oc"
    assert summary["ruler_breakdown"]["fwd_2_oc"]["candidate_n"] == 1
    assert summary["ruler_breakdown"]["gap_c1_o2"]["candidate_n"] == 1


# ═══════════════════════ ⑤ review 补丁(task-13-review.md §3.2/§3.3):两处仍是跨尺盲聚合 ═══════════════════════
#
# §3.2:l3_audit_ledger.roll() 的顶层 summary["mature_n"]/["opportunity_n"] 此前是对
# summaries(逐日冻结快照列表)不分 ruler 的盲 sum —— ruler_breakdown 子字段分尺是对的,
# 但顶层字段(render() 头条印的那两个数)原样未改,"新增字段不等于修好了原有聚合"。
# §3.3:ensemble_ledger.trigger_summary() 按 trigger 汇总 fold_right/fold_wrong,不分
# ruler —— 该表是人工"救对率 <50% → 降为 1 跑"裁决的直接输入,混跑期间人工手算救对率
# 会不自知地把两把尺的折对/折错次数混一个分母,且 render() 此前零跨尺信号。


def _l3_day_payload(date, ruler, candidates):
    return {
        "schema_version": 1, "date": date, "cohort": "L3_BENCH_SHADOW_AUDIT",
        "production_effect": "NONE", "minimum_forward_scan_days": 20, "forward_scan_days": 1,
        "sample_status": "IMMATURE", "ruler": ruler,
        "summary": {
            "candidate_n": len(candidates),
            "mature_n": sum(1 for c in candidates if c["mature"]),
            "opportunity_n": sum(1 for c in candidates if c["opportunity"]),
            "mean_excess_2": None, "main_finalist_mature_n": 0,
            "main_finalist_mean_excess_2": None,
        },
        "candidates": candidates,
    }


def _l3_candidate(code, excess, opportunity, ruler):
    return {"code": code, "conviction": 80.0, "fragility": 20.0, "lane": "trend",
            "mature": True, "fwd_2_oc": excess, "market_fwd_2": 0.0, "excess_2": excess,
            "opportunity": opportunity, "ruler": ruler}


def test_l3_audit_ledger_roll_flags_mixed_ruler_and_render_hides_blind_top_line(tmp_path):
    """两天分别是 fwd_2_oc 尺(0 机会)、gap_c1_o2 尺(2 机会)—— 顶层 opportunity_n 若被当
    干净数字印成头条,读者会看到"机会:2"却不知道这 2 个机会全部来自另一把尺、当天尺下
    实际是 0。这正是 review §3.2 点名的"部分白做"。
    """
    day1 = tmp_path / "2026-07-01" / "retro"
    day1.mkdir(parents=True)
    (day1 / "l3_audit_ledger.json").write_text(json.dumps(_l3_day_payload(
        "2026-07-01", "fwd_2_oc",
        [_l3_candidate("000001", -0.01, False, "fwd_2_oc"),
         _l3_candidate("000002", -0.02, False, "fwd_2_oc")]),
        ensure_ascii=False), encoding="utf-8")

    day2 = tmp_path / "2026-08-10" / "retro"
    day2.mkdir(parents=True)
    (day2 / "l3_audit_ledger.json").write_text(json.dumps(_l3_day_payload(
        "2026-08-10", "gap_c1_o2",
        [_l3_candidate("000003", 0.05, True, "gap_c1_o2"),
         _l3_candidate("000004", 0.06, True, "gap_c1_o2")]),
        ensure_ascii=False), encoding="utf-8")

    frame, summary = lal.roll(scan_root=tmp_path)
    assert summary["mixed_ruler"] is True
    # 顶层字典的值仍是结构性的盲 sum(程序可读的事实,不是"修没修"的判据本身)
    assert summary["mature_n"] == 4 and summary["opportunity_n"] == 2
    assert summary["ruler_breakdown"]["fwd_2_oc"]["opportunity_n"] == 0
    assert summary["ruler_breakdown"]["gap_c1_o2"]["opportunity_n"] == 2

    md = lal.render(frame, summary)
    assert "混合 2 种尺" in md and "顶层加总不可比" in md
    assert "候选:4；成熟:4" not in md            # 旧版会印的干净头条,不该再出现
    assert "机会:2" not in md                     # 旧版会把混合后的 2 印成"捕获 +2pp 机会:2"
    assert "fwd_2_oc`:候选 2；成熟 2；捕获 +2pp 机会 0" in md
    assert "gap_c1_o2`:候选 2；成熟 2；捕获 +2pp 机会 2" in md


def test_l3_audit_ledger_roll_single_ruler_top_line_unaffected(tmp_path):
    """单尺场景(现状)—— 顶层数照常当干净数字印,新判据不改变现行为。"""
    day = tmp_path / "2026-07-01" / "retro"
    day.mkdir(parents=True)
    (day / "l3_audit_ledger.json").write_text(json.dumps(_l3_day_payload(
        "2026-07-01", "fwd_2_oc", [_l3_candidate("000001", 0.05, True, "fwd_2_oc")]),
        ensure_ascii=False), encoding="utf-8")
    frame, summary = lal.roll(scan_root=tmp_path)
    assert summary["mixed_ruler"] is False
    md = lal.render(frame, summary)
    assert "候选:1；成熟:1；捕获 +2pp 机会:1" in md
    assert "混合" not in md


def test_ensemble_trigger_summary_segments_by_ruler_not_blended():
    """同一 trigger 下 fwd_2_oc 尺全 FOLD_RIGHT、gap_c1_o2 尺全 FOLD_WRONG —— 若聚合层
    没分尺,会把两组各 2 的 fold_right/fold_wrong 算进同一行(看着像 50% 救对率),实际是
    两把尺各自 100% 与 0%,方向完全相反的两个结论被平均成了一个假中间数。
    """
    rows = pd.DataFrame([
        {"trigger": "ow_review", "verdict": "FOLD_RIGHT", "ruler": "fwd_2_oc"},
        {"trigger": "ow_review", "verdict": "FOLD_RIGHT", "ruler": "fwd_2_oc"},
        {"trigger": "ow_review", "verdict": "FOLD_WRONG", "ruler": "gap_c1_o2"},
        {"trigger": "ow_review", "verdict": "FOLD_WRONG", "ruler": "gap_c1_o2"},
    ])
    summary = ens.trigger_summary(rows)
    assert len(summary) == 2                          # 拆成两行,不是混一行
    by_ruler = summary.set_index("ruler")
    assert by_ruler.loc["fwd_2_oc", "fold_right"] == 2 and by_ruler.loc["fwd_2_oc", "fold_wrong"] == 0
    assert by_ruler.loc["gap_c1_o2", "fold_wrong"] == 2 and by_ruler.loc["gap_c1_o2", "fold_right"] == 0
    # 混合会算出的假象:同一行 fold_right=2 且 fold_wrong=2(看着 50% 救对率)不该出现在任何一行
    assert not any(r.fold_right == 2 and r.fold_wrong == 2 for r in summary.itertuples())

    rendered = ens.render(rows)
    assert "fwd_2_oc" in rendered and "gap_c1_o2" in rendered
    assert "折对/折错行合并成一个分母" in rendered


def test_ensemble_trigger_summary_single_ruler_unaffected():
    """单尺场景(既有测试同款 fixture,无 ruler 列)—— 分组结果与之前完全一致,不因新判据改变。"""
    rows = pd.DataFrame(
        [{"trigger": "ow_review", "verdict": "FOLD_RIGHT"} for _ in range(9)]
        + [{"trigger": "sell_review", "verdict": "FOLD_WRONG"} for _ in range(10)]
    )
    assert "ruler" not in rows.columns
    summary = ens.trigger_summary(rows).set_index("trigger")
    assert summary.loc["ow_review", "status"] == "IMMATURE"
    assert summary.loc["sell_review", "status"] == "MATURE"
    assert (summary["ruler"] == "fwd_2_oc").all()      # 缺列兜底,不因此把行拆没了/报错
