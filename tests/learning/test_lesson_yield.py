"""lesson_yield(P0-5):逐日复用 retro.mtm_check_guards(apply=False)累计反事实 Δpp 曲线 +
support/refute 计数 + 裁决法(n≥20 且 cum_delta≤0 → 提名 retire)。合成 fixture,无网络。

spec: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4-P0-5
plan: docs/plans/2026-07-12-selflearning-p0-plan.md T5
"""
from __future__ import annotations

import pandas as pd
import pytest

import autoresearch.learning.feedback_store as fs
import autoresearch.learning.lesson_yield as ly

_GUARD_A = {"field": "winner_rate", "op": ">", "value": 50}
_GUARD_B = {"field": "pct_60d", "op": ">", "value": 50}


def _lesson(lid, guard=None, rule="r", scope=("global", "*")):
    return {"id": lid, "rule": rule, "scope": {"kind": scope[0], "value": scope[1]}, "guard": guard}


def _symmetric_day(field: str, sat_n: int, other_n: int, sat_fwd: float) -> pd.DataFrame:
    """构造市场均值恒为 0 的单日 attr:sat_n 行 field=60(命中 guard,fwd=sat_fwd),
    other_n 行 field=10(不命中,fwd=-sat_fwd*sat_n/other_n,使全天均值精确为 0)。"""
    other_fwd = -sat_fwd * sat_n / other_n
    rows = [{"code": f"{i:06d}", field: 60.0, "fwd_2_oc": sat_fwd} for i in range(sat_n)]
    rows += [{"code": f"{i + sat_n:06d}", field: 10.0, "fwd_2_oc": other_fwd} for i in range(other_n)]
    return pd.DataFrame(rows)


# ───────────────────────── compute_yield:核心累计数学 ─────────────────────────


def test_support_day_positive_delta_and_count():
    """命中组跑输市场(support)→ delta=-excess>0,support 计数+1。"""
    lsn = _lesson("ls_a", _GUARD_A)
    day = _symmetric_day("winner_rate", sat_n=10, other_n=10, sat_fwd=-0.05)   # 命中组 -5%,market=0
    df = ly.compute_yield([lsn], [("2026-07-01", day)])
    row = df.set_index("id").loc["ls_a"]
    assert row["support"] == 1 and row["refute"] == 0
    assert abs(row["cum_delta"] - 0.05) < 1e-9          # -excess = -(-0.05) = +0.05
    assert row["n_cum"] == 10 and row["n_days_hit"] == 1


def test_refute_day_negative_delta_and_count():
    """命中组跑赢市场(refute)→ delta=-excess<0,refute 计数+1。"""
    lsn = _lesson("ls_b", _GUARD_B)
    day = _symmetric_day("pct_60d", sat_n=10, other_n=10, sat_fwd=0.048)       # 命中组 +4.8%,market=0
    df = ly.compute_yield([lsn], [("2026-07-01", day)])
    row = df.set_index("id").loc["ls_b"]
    assert row["support"] == 0 and row["refute"] == 1
    assert abs(row["cum_delta"] - (-0.048)) < 1e-9


def test_cumulative_across_days_and_below_threshold_status():
    """单日 n_cum=10<20 → 无论 delta 符号,状态必须是"样本不足"(不提名)。"""
    lsn = _lesson("ls_c", _GUARD_A)
    day = _symmetric_day("winner_rate", sat_n=10, other_n=10, sat_fwd=-0.05)
    df = ly.compute_yield([lsn], [("2026-07-01", day)])
    row = df.set_index("id").loc["ls_c"]
    assert row["n_cum"] == 10
    assert row["status"] == "样本不足(n=10<20)"


def test_nominate_retire_at_n20_cum_delta_negative():
    """两个 refute 日累计 n=20、cum_delta<0 → 提名 retire。"""
    lsn = _lesson("ls_d", _GUARD_B)
    day = _symmetric_day("pct_60d", sat_n=10, other_n=10, sat_fwd=0.05)        # 每日 delta=-0.05
    df = ly.compute_yield([lsn], [("2026-07-01", day), ("2026-07-02", day)])
    row = df.set_index("id").loc["ls_d"]
    assert row["n_cum"] == 20
    assert abs(row["cum_delta"] - (-0.10)) < 1e-9
    assert row["status"] == "提名 retire(人批)"


def test_nominate_retire_boundary_delta_exactly_zero():
    """n_cum 恰好=20、cum_delta 恰好=0(一 support 一 refute 抵消)→ 仍提名(≤0 含等于)。"""
    lsn = _lesson("ls_e", _GUARD_A)
    d1 = _symmetric_day("winner_rate", sat_n=10, other_n=10, sat_fwd=-0.05)    # delta=+0.05
    d2 = _symmetric_day("winner_rate", sat_n=10, other_n=10, sat_fwd=0.05)     # delta=-0.05
    df = ly.compute_yield([lsn], [("2026-07-01", d1), ("2026-07-02", d2)])
    row = df.set_index("id").loc["ls_e"]
    assert row["n_cum"] == 20
    assert row["cum_delta"] == 0.0
    assert row["status"] == "提名 retire(人批)"


def test_positive_net_yield_not_nominated():
    """n_cum≥20 但 cum_delta>0(净贡献为正)→ 不提名,标"继续观察"。"""
    lsn = _lesson("ls_f", _GUARD_A)
    day = _symmetric_day("winner_rate", sat_n=10, other_n=10, sat_fwd=-0.05)   # 每日 delta=+0.05
    df = ly.compute_yield([lsn], [("2026-07-01", day), ("2026-07-02", day)])
    row = df.set_index("id").loc["ls_f"]
    assert row["n_cum"] == 20 and row["cum_delta"] > 0
    assert row["status"] == "净贡献为正,继续观察"


def test_day_missing_guard_field_skipped_not_crash():
    """当日 attr 缺该 lesson guard 的 field 列 → 该日对该 lesson 无贡献(不崩,不计入 n_cum)。"""
    lsn = _lesson("ls_g", _GUARD_A)
    day_no_field = pd.DataFrame({"code": ["000001", "000002"], "fwd_2_oc": [0.01, -0.01]})
    df = ly.compute_yield([lsn], [("2026-07-01", day_no_field)])
    row = df.set_index("id").loc["ls_g"]
    assert row["n_cum"] == 0 and row["n_days_hit"] == 0
    assert row["status"].startswith("样本不足")


def test_below_daily_min_n_skipped():
    """命中数 < day_min_n(默认 5)→ 该日判 skip,不计入曲线(与 retro.mtm_check_guards 口径一致)。"""
    lsn = _lesson("ls_h", _GUARD_A)
    day = _symmetric_day("winner_rate", sat_n=2, other_n=18, sat_fwd=-0.05)   # 命中仅 2 < 默认阈 5
    df = ly.compute_yield([lsn], [("2026-07-01", day)])
    row = df.set_index("id").loc["ls_h"]
    assert row["n_cum"] == 0 and row["n_days_hit"] == 0


def test_no_guard_lessons_returns_empty_frame():
    lsn_no_guard = _lesson("ls_i", guard=None)
    df = ly.compute_yield([lsn_no_guard], [("2026-07-01", _symmetric_day("winner_rate", 10, 10, -0.05))])
    assert df.empty


def test_empty_lessons_or_days_graceful():
    assert ly.compute_yield([], [("2026-07-01", pd.DataFrame())]).empty
    assert ly.compute_yield([_lesson("ls_j", _GUARD_A)], []).empty


def test_sorted_by_cum_delta_ascending():
    """worst(最负 cum_delta)排最前,便于报表优先看最该退休的条目。

    两个 lesson 各自的 guard field 只出现在各自专属的日子里(day_good 无 pct_60d 列、
    day_bad 无 winner_rate 列)→ 每日彼此的 guard 谓词天然 field-not-in-columns 跳过,
    互不干扰(不能把两天 concat 成一天,否则重名 fwd_2_oc 列会踩坏 mtm_check_guards)。
    """
    good = _lesson("ls_good", _GUARD_A)
    bad = _lesson("ls_bad", _GUARD_B)
    day_good = _symmetric_day("winner_rate", 10, 10, -0.05)     # delta=+0.05
    day_bad = _symmetric_day("pct_60d", 10, 10, 0.05)           # delta=-0.05
    df = ly.compute_yield([good, bad], [("2026-07-01", day_good), ("2026-07-02", day_bad)])
    assert list(df["id"]) == ["ls_bad", "ls_good"]              # bad(负)排前


# ───────────────────────── _walk_attribution ─────────────────────────


def test_walk_attribution_sorted_and_skips_bad(tmp_path):
    for d, fwd in [("2026-07-03", 0.02), ("2026-07-01", 0.01), ("2026-07-02", -0.01)]:
        p = tmp_path / d / "retro"
        p.mkdir(parents=True)
        pd.DataFrame({"code": ["000001"], "fwd_2_oc": [fwd]}).to_csv(p / "attribution.csv", index=False)
    # 损坏文件:非 CSV 内容
    bad = tmp_path / "2026-07-04" / "retro"
    bad.mkdir(parents=True)
    (bad / "attribution.csv").write_bytes(b"\x00\x01not,a,csv\xffbroken")
    # 缺 fwd_2_oc 列
    nocol = tmp_path / "2026-07-05" / "retro"
    nocol.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"]}).to_csv(nocol / "attribution.csv", index=False)
    # 无 retro 子目录(应被 glob 忽略)
    (tmp_path / "2026-07-06").mkdir()

    out = ly._walk_attribution(tmp_path)
    assert [d for d, _ in out] == ["2026-07-01", "2026-07-02", "2026-07-03"]


def test_walk_attribution_missing_root(tmp_path):
    assert ly._walk_attribution(tmp_path / "nope") == []


# ───────────────────────── active_guard_lessons:feedback_store 接线 ─────────────────────────


@pytest.fixture
def _tmp_know(tmp_path):
    old = fs.KNOW
    fs.set_root(tmp_path / "know")
    yield
    fs.set_root(old)


def test_active_guard_lessons_filters_global_active_guard(_tmp_know):
    fs.upsert_lesson("has_guard", ("global", "*"), rule="r1", evidence=[], guard=_GUARD_A)
    fs.upsert_lesson("no_guard", ("global", "*"), rule="r2", evidence=[])
    fs.upsert_lesson("industry_scoped", ("industry", "电子"), rule="r3", evidence=[], guard=_GUARD_B)
    fs.upsert_lesson("to_retire", ("global", "*"), rule="r4", evidence=[], guard=_GUARD_A)
    fs.retire_lesson("to_retire")

    ids = {r["id"] for r in ly.active_guard_lessons()}
    assert ids == {"ls_has_guard"}


def test_active_guard_lessons_empty_store(_tmp_know):
    assert ly.active_guard_lessons() == []


# ───────────────────────── roll:端到端接线(显式 lessons,绕过 feedback_store) ─────────────────────────


def test_roll_end_to_end_explicit_lessons(tmp_path):
    lsn = _lesson("ls_roll", _GUARD_A)
    day = _symmetric_day("winner_rate", sat_n=10, other_n=10, sat_fwd=-0.05)
    p = tmp_path / "2026-07-01" / "retro"
    p.mkdir(parents=True)
    day.to_csv(p / "attribution.csv", index=False)

    df = ly.roll(scan_root=tmp_path, lessons=[lsn])
    assert len(df) == 1 and df.iloc[0]["id"] == "ls_roll" and df.iloc[0]["n_cum"] == 10


def test_roll_defaults_to_active_guard_lessons(tmp_path, _tmp_know):
    fs.upsert_lesson("wired", ("global", "*"), rule="r", evidence=[], guard=_GUARD_A)
    day = _symmetric_day("winner_rate", sat_n=10, other_n=10, sat_fwd=-0.05)
    p = tmp_path / "2026-07-01" / "retro"
    p.mkdir(parents=True)
    day.to_csv(p / "attribution.csv", index=False)

    df = ly.roll(scan_root=tmp_path)
    assert list(df["id"]) == ["ls_wired"]


# ───────────────────────── render:报表格式(直构 DataFrame,与 compute_yield 数学解耦) ─────────────────────────


def _row(id_, rule="规则", n_days_hit=2, n_cum=20, support=1, refute=1, cum_delta=0.0,
        status="提名 retire(人批)", curve=None):
    return {"id": id_, "rule": rule, "guard": _GUARD_A, "n_days_hit": n_days_hit, "n_cum": n_cum,
           "support": support, "refute": refute, "cum_delta": cum_delta, "status": status,
           "curve": curve or []}


def test_render_no_guard_lessons_message():
    lines = ly.render(pd.DataFrame(columns=ly._COLS), n_lessons_total=5)
    text = "\n".join(lines)
    assert "带 guard 的 0 条" in text and "guard 覆盖率 0" in text


def test_render_table_and_nomination_section():
    df = pd.DataFrame([_row("ls_x", status="提名 retire(人批)", cum_delta=-0.12)])
    text = "\n".join(ly.render(df, n_lessons_total=1))
    assert "`ls_x`" in text
    assert "-12.00pp" in text
    assert "## 提名 retire" in text and "只提名不动作" in text


def test_render_no_nomination_section_when_none_qualify():
    df = pd.DataFrame([_row("ls_y", status="净贡献为正,继续观察", cum_delta=0.08)])
    text = "\n".join(ly.render(df, n_lessons_total=1))
    assert "## 提名 retire" not in text


def test_render_aggregate_flat_triggers_p2_2_hint():
    # 两条各 n_cum=10(合计20=达阈),Δ 分别 +0.010 / -0.008 → 合计 +0.002(0.2pp)<0.5pp 触发提示。
    df = pd.DataFrame([
        _row("ls_p", n_cum=10, cum_delta=0.010, status="样本不足(n=10<20)"),
        _row("ls_q", n_cum=10, cum_delta=-0.008, status="样本不足(n=10<20)"),
    ])
    text = "\n".join(ly.render(df, n_lessons_total=2))
    assert "全体合计边际" in text and "触发 P2-2" in text


def test_render_aggregate_not_flat_no_hint():
    df = pd.DataFrame([
        _row("ls_p", n_cum=15, cum_delta=0.20, status="净贡献为正,继续观察"),
        _row("ls_q", n_cum=15, cum_delta=-0.01, status="样本不足(n=15<20)"),
    ])
    text = "\n".join(ly.render(df, n_lessons_total=2))
    assert "触发 P2-2" not in text


def test_render_aggregate_skipped_when_below_combined_threshold():
    """合计 n_cum 都不足 20(如 2×5)→ 不出全体合计边际节(防零星噪声触发)。"""
    df = pd.DataFrame([
        _row("ls_p", n_cum=5, cum_delta=0.001, status="样本不足(n=5<20)"),
        _row("ls_q", n_cum=5, cum_delta=0.0005, status="样本不足(n=5<20)"),
    ])
    text = "\n".join(ly.render(df, n_lessons_total=2))
    assert "全体合计边际" not in text


def test_render_curve_section_presence_gated():
    curve = [{"date": "2026-07-01", "n": 10, "excess": -0.05, "delta": 0.05, "cum_delta": 0.05}]
    df_with_curve = pd.DataFrame([_row("ls_r", curve=curve)])
    text_with = "\n".join(ly.render(df_with_curve, n_lessons_total=1))
    assert "2026-07-01" in text_with and "逐条命中曲线" in text_with

    df_no_curve = pd.DataFrame([_row("ls_s", curve=[])])
    text_without = "\n".join(ly.render(df_no_curve, n_lessons_total=1))
    assert "逐条命中曲线" not in text_without
