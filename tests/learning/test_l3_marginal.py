"""L3 边际价值单测 —— 锁 §4.4 的四条纪律。

1. pinned / conviction_guard 这类强制补入行**两侧同时剔除**(否则拿保送票的收益证明排序有用);
2. baseline 必须**同 K 同层分布**(否则把 lane 暴露的收益记到排序头上);
3. Jaccard 只量暴露差异,**不进判据**;
4. 未显著但区间宽 = UNKNOWN,绝不是「排序无价值」;tier-2 恰恰在 UNKNOWN 时触发(补功效)。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.learning import l3_marginal as lm


def _write_day(root, date, *, kept_rows, finalists, attr_rows, pinned_notes=None):
    d = root / date
    (d / "retro").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(kept_rows).to_csv(d / "_l3_pass1_kept.csv", index=False)
    fin = pd.DataFrame({"code": finalists})
    if pinned_notes:
        fin["pinned_note"] = [pinned_notes.get(c, "") for c in finalists]
    fin.to_csv(d / "finalists.csv", index=False)
    pd.DataFrame(attr_rows).to_csv(d / "retro" / "attribution.csv", index=False)
    return d


def _kept(code, *, reason="lane", detail="momentum", score=50.0, industry="电子"):
    return {"code": code, "selection_reason": reason, "selection_detail": detail,
            "gbdt_score": score, "composite": score, "industry": industry}


def _attr(code, fwd, *, winner=False, buyable=True):
    return {"code": code, "fwd_2_oc": fwd, "buyable": buyable, "tradable": True,
            "winner": winner}


# ── day_frame:presence-gated + 基准口径 ───────────────────────────


def test_day_frame_needs_kept_finalists_and_attribution(tmp_path):
    d = tmp_path / "2026-08-01"
    (d / "retro").mkdir(parents=True)
    assert lm.day_frame(d) is None


def test_day_frame_rejects_legacy_kept_without_selection_reason(tmp_path):
    """本波之前的日期没有 provenance —— 不能硬算,只能跳过。"""
    d = tmp_path / "2026-08-01"
    (d / "retro").mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "gbdt_score": 1.0}]).to_csv(
        d / "_l3_pass1_kept.csv", index=False)
    pd.DataFrame({"code": ["000001"]}).to_csv(d / "finalists.csv", index=False)
    pd.DataFrame([_attr("000001", 0.01)]).to_csv(d / "retro" / "attribution.csv", index=False)
    assert lm.day_frame(d) is None


def test_excess_uses_tradable_mature_median_baseline(tmp_path):
    d = _write_day(
        tmp_path, "2026-08-01",
        kept_rows=[_kept(f"00000{i}") for i in range(1, 6)],
        finalists=["000001"],
        attr_rows=[_attr(f"00000{i}", 0.01 * i) for i in range(1, 6)])
    frame = lm.day_frame(d)
    # 中位 = 0.03 → excess 依次 −0.02…+0.02
    assert frame.set_index("code").loc["000001", "excess_2"] == pytest.approx(-0.02)
    assert set(frame["market_baseline"]) == {"median_tradable_mature"}


def test_untradable_rows_excluded_from_market_baseline(tmp_path):
    d = _write_day(
        tmp_path, "2026-08-01",
        kept_rows=[_kept(f"00000{i}") for i in range(1, 5)],
        finalists=["000001"],
        attr_rows=[_attr("000001", 0.01), _attr("000002", 0.03),
                   _attr("000003", 0.05), _attr("000004", 9.99, buyable=False)])
    frame = lm.day_frame(d)
    assert frame.set_index("code").loc["000002", "excess_2"] == pytest.approx(0.0)


# ── 纪律 1:强制补入行两侧同时剔除 ────────────────────────────────


def test_forced_rows_are_excluded_from_both_sides(tmp_path):
    """保送票哪怕涨到天上,也不能进 actual 或 baseline —— 07-16 判例。"""
    d = _write_day(
        tmp_path, "2026-08-01",
        kept_rows=[_kept("000001", reason="pinned", detail="", score=1.0),
                   _kept("000002", reason="conviction_guard", detail="n_channels=3", score=2.0),
                   _kept("000003", score=90.0), _kept("000004", score=80.0),
                   _kept("000005", score=70.0)],
        finalists=["000001", "000003"],
        attr_rows=[_attr("000001", 5.00), _attr("000002", 5.00), _attr("000003", 0.02),
                   _attr("000004", 0.01), _attr("000005", 0.00)])
    frame = lm.day_frame(d)
    est = lm.day_estimate(frame)
    assert est["forced_excluded"] == 2
    assert est["k"] == 1                              # 000001 是 pinned → 不算 L3 真选
    assert "000001" not in lm.matched_baseline(frame)
    assert "000002" not in lm.matched_baseline(frame)


def test_pinned_note_in_finalists_also_drops_the_pick(tmp_path):
    """finalists.csv 的 `pinned_note` 是识别保送行的单一信号 —— 也要剔。"""
    d = _write_day(
        tmp_path, "2026-08-01",
        kept_rows=[_kept(f"00000{i}", score=100.0 - i) for i in range(1, 6)],
        finalists=["000001", "000002"],
        pinned_notes={"000001": "📌 持仓保送"},
        attr_rows=[_attr(f"00000{i}", 0.01 * i) for i in range(1, 6)])
    est = lm.day_estimate(lm.day_frame(d))
    assert est["k"] == 1


def test_all_forced_day_yields_no_estimate(tmp_path):
    d = _write_day(
        tmp_path, "2026-08-01",
        kept_rows=[_kept("000001", reason="pinned", detail="")],
        finalists=["000001"],
        attr_rows=[_attr("000001", 0.01), _attr("000002", 0.02)])
    assert lm.day_estimate(lm.day_frame(d)) is None


# ── 纪律 2:同 K 同层分布 ────────────────────────────────────────


def test_baseline_matches_k_and_stratum_distribution(tmp_path):
    """actual 全取 value lane → baseline 也必须全在 value lane(不能跑去拿涨得好的 momentum)。"""
    kept = ([_kept(f"v{i:05d}", detail="value", score=float(i)) for i in range(5)]
            + [_kept(f"m{i:05d}", detail="momentum", score=100.0 + i) for i in range(5)])
    d = _write_day(
        tmp_path, "2026-08-01", kept_rows=kept,
        finalists=["v00000", "v00001"],
        attr_rows=[_attr(r["code"], 0.01) for r in kept])
    frame = lm.day_frame(d)
    base = lm.matched_baseline(frame, by="lane")
    assert len(base) == 2
    assert all(c.startswith("v") for c in base)
    # 层内按确定性分取头部 → v00004 / v00003
    assert base == {"v00004", "v00003"}


def test_baseline_can_stratify_by_industry(tmp_path):
    kept = ([_kept(f"a{i:05d}", industry="电子", score=float(i)) for i in range(4)]
            + [_kept(f"b{i:05d}", industry="白酒", score=50.0 + i) for i in range(4)])
    d = _write_day(tmp_path, "2026-08-01", kept_rows=kept, finalists=["a00000"],
                   attr_rows=[_attr(r["code"], 0.01) for r in kept])
    base = lm.matched_baseline(lm.day_frame(d), by="industry")
    assert base == {"a00003"}


def test_reference_draws_are_deterministic_and_same_size(tmp_path):
    kept = [_kept(f"{i:06d}", score=float(i)) for i in range(10)]
    d = _write_day(tmp_path, "2026-08-01", kept_rows=kept,
                   finalists=["000000", "000001", "000002"],
                   attr_rows=[_attr(r["code"], 0.001 * i) for i, r in enumerate(kept)])
    frame = lm.day_frame(d)
    a = lm.reference_draws(frame, draws=50)
    b = lm.reference_draws(frame, draws=50)
    assert a == b and len(a) == 50


# ── 纪律 3+4:Jaccard 不进判据;宽区间 = UNKNOWN ──────────────────


def _many_days(root, n_days, *, spread):
    """造 n 天;`spread` 控制 actual 相对 baseline 的日间差异幅度。"""
    for i in range(n_days):
        date = f"2026-06-{i + 1:02d}"
        kept = [_kept(f"{j:06d}", score=float(10 - j)) for j in range(6)]
        # actual 取分低的两只、baseline 会取分高的两只 → 两侧确有分歧
        fwd = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06]
        if i % 2:
            fwd = [f + spread for f in fwd[::-1]]
        _write_day(root, date, kept_rows=kept, finalists=["000004", "000005"],
                   attr_rows=[_attr(f"{j:06d}", fwd[j]) for j in range(6)])


def test_thin_history_is_immature_not_a_conclusion(tmp_path):
    _many_days(tmp_path, 4, spread=0.5)
    result = lm.tier1(tmp_path)
    assert result["n_days"] == 4
    assert result["verdict"]["verdict"] == "IMMATURE"
    assert result["verdict"]["recommendation"] == "DO_NOT_PROMOTE"


def test_noisy_history_is_unknown_not_no_value(tmp_path):
    """区间宽 → UNKNOWN。读成「排序无价值」正是 §4.4 点名要禁的动作。"""
    _many_days(tmp_path, 24, spread=0.8)
    result = lm.tier1(tmp_path)
    assert result["verdict"]["verdict"] == "UNKNOWN"
    assert "只有 EQUIVALENT" in result["verdict"]["readback_guard"]


def test_jaccard_is_reported_but_not_in_the_verdict(tmp_path):
    _many_days(tmp_path, 24, spread=0.8)
    result = lm.tier1(tmp_path)
    assert "mean_jaccard" in result
    assert "jaccard" not in json.dumps(result["verdict"], ensure_ascii=False)


def test_estimand_says_counterfactual_not_causal(tmp_path):
    _many_days(tmp_path, 3, spread=0.1)
    result = lm.tier1(tmp_path)
    assert "非因果实验" in result["estimand"]


# ── tier-2:UNKNOWN 时触发(补功效),不是显著后才验证 ────────────


def test_tier2_triggers_on_unknown_with_divergence(tmp_path):
    _many_days(tmp_path, 24, spread=0.8)
    result = lm.tier1(tmp_path)
    t2 = result["tier2"]
    assert t2["trigger"] is True and t2["codes"]
    assert "功效" in t2["reason"]


def test_identical_sides_are_equivalent_by_identity_not_by_discovery(tmp_path):
    """L3 恰好选中确定性头部 → 两侧同一批 → delta 恒 0。裁决可以是 EQUIVALENT,
    但 tier-2 绝不该触发:对同一批票补卡毫无信息。"""
    for i in range(24):
        kept = [_kept(f"{j:06d}", score=float(10 - j)) for j in range(6)]
        _write_day(tmp_path, f"2026-06-{i + 1:02d}", kept_rows=kept,
                   finalists=["000000", "000001"],
                   attr_rows=[_attr(f"{j:06d}", 0.01 * j) for j in range(6)])
    result = lm.tier1(tmp_path)
    assert result["mean_jaccard"] == 1.0
    assert result["verdict"]["verdict"] == "EQUIVALENT"
    assert result["tier2"]["trigger"] is False


def test_tier2_blocked_by_low_divergence_even_when_unknown():
    """功效不足 + 两侧几乎重合 → 仍不触发(divergence 门单独把关)。"""
    daily = pd.DataFrame([{"date": f"2026-06-{i:02d}", "jaccard": 0.95,
                           "divergent_codes": ["000009"]} for i in range(1, 25)])
    plan = lm.tier2_plan(daily, {"verdict": "UNKNOWN"})
    assert plan["trigger"] is False and "divergence" in plan["reason"]


def test_tier2_off_when_tier1_already_decided(tmp_path):
    daily = pd.DataFrame([{"date": "2026-06-01", "jaccard": 0.0, "divergent_codes": ["x"]}])
    plan = lm.tier2_plan(daily, {"verdict": "PASS"})
    assert plan["trigger"] is False and "PASS" in plan["reason"]


# ── 空/边界 + 产物 ──────────────────────────────────────────────


def test_empty_root_is_immature_with_zero_days(tmp_path):
    result = lm.tier1(tmp_path / "nope")
    assert result["n_days"] == 0 and result["verdict"]["verdict"] == "IMMATURE"
    assert result["tier2"]["trigger"] is False


def test_template_is_preregistered_and_valid():
    from autoresearch.learning import experiment_template as et

    assert et.validate(lm.template()) == []
    assert lm.template().equivalence_margin == lm.EQUIVALENCE_MARGIN


def test_cli_writes_both_artifacts(tmp_path):
    _many_days(tmp_path, 3, spread=0.1)
    out_json, out_md = tmp_path / "o.json", tmp_path / "o.md"
    assert lm.main(["--scan-root", str(tmp_path), "--json-out", str(out_json),
                    "--md-out", str(out_md)]) == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == lm.SCHEMA_VERSION
    md = out_md.read_text(encoding="utf-8")
    assert "反事实估计" in md and "Jaccard 只量" in md
