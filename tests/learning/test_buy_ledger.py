"""买单 ledger:抽账/目标命中/基率/空表。合成,无网络。

spec: docs/specs/2026-07-02-scan-portfolio-memory-design.md §2
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.buy_ledger import rating_base_rates, render, roll

CARD = ("# 决策卡\n\n| 评级 | 目标(EV) | R:R |\n|---|---|---|\n"
        "| Overweight | 120(EV) | 2:1 |\n\n**Rating**: Overweight\n")


def _mk_day(root, date, with_attr=True, hi=None, fwd10=0.25, fwd2=0.05):
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details" / "000001.md").write_text(CARD, encoding="utf-8")
    pd.DataFrame([{"code": "000001", "close": 100.0}]).to_csv(
        d / "L1_scored_full.csv", index=False)
    if with_attr:
        (d / "retro").mkdir()
        # gap_c1_o2:当前 MAIN_RULER(T16 flip)—— buy_ledger.roll 按 MAIN_RULER 动态读源列。
        row = {"code": "000001", "fwd_1_oo": 0.01, "gap_c1_o2": fwd2, "fwd_5_oc": 0.08,
               "fwd_10_oc": fwd10, "gap_d1": 0.02}
        if hi is not None:
            row["hi_10_oc"] = hi
        pd.DataFrame([row]).to_csv(d / "retro" / "attribution.csv", index=False)
    return d


def test_roll_and_target_hit_immature_without_hi(tmp_path):
    """无 hi_10(未成熟)→ target_hit 诚实标 None,不再回退收盘口径(旧分支已删除)。"""
    _mk_day(tmp_path, "2026-07-01")                           # 无 hi_10 → 未成熟
    df = roll(tmp_path)
    assert len(df) == 1
    r = df.iloc[0]
    assert r["rating"] == "Overweight" and r["fwd_5"] == 0.08 and r["fwd_10"] == 0.25
    assert r["fwd_2"] == 0.05
    assert r["gap_open"] == 0.02
    assert abs(r["target_ret"] - 0.20) < 1e-9                # 120/100−1
    assert r["target_hit"] is None or pd.isna(r["target_hit"])   # 缺 hi → 未成熟,不回退收盘
    md = "\n".join(render(df))
    assert "Overweight" in md and "⚠样本少" in md and "fwd_2" in md


def test_target_hit_schema_switch():
    """switch 日前的卡按 hi_10 判(10日语义),switch 日起按 hi_2(超短语义)。"""
    from autoresearch.learning import buy_ledger as bl

    row = pd.Series({"hi_2_oc": 0.02, "hi_10_oc": 0.08, "gap_d1": 0.0})
    tr = 0.05                                      # 目标 +5%
    assert bl.target_hit_for("2026-07-06", tr, row) is True    # 旧卡:hi_10=8% ≥ 5%
    assert bl.target_hit_for("2026-07-13", tr, row) is False   # 新卡:hi_2=2% < 5%
    assert bl.target_hit_for("2026-07-10", None, row) is None  # tr=None → 未成熟
    assert bl.target_hit_for("2026-07-10", tr, pd.Series({"gap_d1": 0.0})) is None  # 缺 hi 列 → None


def test_rebase_col_for_v4_uses_fwd_1_cc_others_use_gap_d1():
    """C2 修复(final-review 2026-08-08):rebase 基的单点选择——v4(ruler.TOUCH_COL,c1 基)
    用 fwd_1_cc;v3/更早(o1 基)用 gap_d1。"""
    from autoresearch.common import ruler
    from autoresearch.learning.buy_ledger import _rebase_col_for

    assert _rebase_col_for(ruler.TOUCH_COL) == "fwd_1_cc"
    assert _rebase_col_for("hi_2_oc") == "gap_d1"
    assert _rebase_col_for("hi_10_oc") == "gap_d1"


def test_target_hit_v4_rebases_with_fwd_1_cc_not_gap_d1():
    """C2 修复(final-review 2026-08-08)回归锁:v4(col=ruler.TOUCH_COL)分支必须用 `fwd_1_cc`
    (c1 基)rebase 目标幅,不是 `gap_d1`(o1 基)——两列故意给不同值,用错基连命中方向都会翻。

    tr=0.20(目标);fwd_1_cc=0.10 → t_entry=(1.20/1.10)-1≈0.0909,hi=0.10≥0.0909 → 命中;
    若误用 gap_d1=-0.05 → t_entry=(1.20/0.95)-1≈0.2632,hi=0.10<0.2632 → 不命中(方向反了)。
    """
    from autoresearch.common import ruler
    from autoresearch.learning import buy_ledger as bl

    row = pd.Series({ruler.TOUCH_COL: 0.10, "fwd_1_cc": 0.10, "gap_d1": -0.05})
    assert bl.target_hit_for(ruler.SCHEMA_SWITCH_V4, 0.20, row) is True


def test_target_hit_v3_still_rebases_with_gap_d1_not_fwd_1_cc():
    """对照:v3(hi_2_oc,o1 基)分支不受 C2 修复影响,继续用 gap_d1——即便行里混进
    一个不同值的 fwd_1_cc 也不该被拿来 rebase(选基跟 col 走,不是"随便挑一个能用的列")。
    """
    from autoresearch.learning import buy_ledger as bl

    row = pd.Series({"hi_2_oc": 0.10, "fwd_1_cc": -0.05, "gap_d1": 0.10})
    # gap_d1=0.10(正确基)→ t_entry=(1.20/1.10)-1≈0.0909,hi=0.10≥ → 命中
    assert bl.target_hit_for("2026-07-15", 0.20, row) is True


def test_target_hit_by_touch(tmp_path):
    """触价口径:收盘没到目标(fwd_10 0.10 < 0.20)但 10 日内最高摸到过 → 命中。"""
    _mk_day(tmp_path, "2026-07-01", hi=0.25, fwd10=0.10)
    r = roll(tmp_path).iloc[0]
    # 目标幅 0.20(close 基)→ o1 基 = 1.20/1.02−1 ≈ 0.1765;hi 0.25 ≥ → 触价命中
    assert bool(r["target_hit"]) and r["hi_10"] == 0.25 and r["fwd_10"] == 0.10
    _mk_day(tmp_path / "b", "2026-07-01", hi=0.05, fwd10=0.10)
    assert not bool(roll(tmp_path / "b").iloc[0]["target_hit"])   # 没摸到也没收到 → ✗


def test_unrealized_fwd_degrades(tmp_path):
    _mk_day(tmp_path, "2026-07-01", with_attr=False)
    df = roll(tmp_path)
    r = df.iloc[0]
    assert r["fwd_2"] is None or pd.isna(r["fwd_2"])
    assert r["fwd_5"] is None or pd.isna(r["fwd_5"])
    assert r["target_hit"] is None or pd.isna(r["target_hit"])


def test_base_rates_and_empty(tmp_path):
    assert rating_base_rates(pd.DataFrame()) == []
    assert "尚无 ≥OW 买单" in "\n".join(render(roll(tmp_path)))
    _mk_day(tmp_path, "2026-07-01")
    br = rating_base_rates(roll(tmp_path), min_n=10)
    assert br[0]["rating"] == "Overweight" and br[0]["n"] == 1 and br[0]["thin"]
    assert br[0]["win5"] == 1.0
    assert br[0]["win2"] == 1.0 and abs(br[0]["mean2"] - 0.05) < 1e-9   # 按 fwd_2 计,主尺


def test_base_rates_n_realized_follows_fwd2_not_fwd5(tmp_path):
    """f2 已实现样本 > f5(近期买单 T+2 已成熟、T+5 未成熟,retro 一次性落账不会自动重跑)→
    n_realized/thin 按主尺 f2 走,不被更慢成熟的 f5 拖累标 thin。"""
    _mk_day(tmp_path, "2026-07-01", fwd2=0.03)                    # 甲:f2/f5 都已实现
    d2 = tmp_path / "2026-07-02"
    (d2 / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "000002", "name": "乙", "sector": "半导体"}]).to_csv(
        d2 / "finalists.csv", index=False)
    (d2 / "details" / "000002.md").write_text(CARD, encoding="utf-8")
    pd.DataFrame([{"code": "000002", "close": 100.0}]).to_csv(
        d2 / "L1_scored_full.csv", index=False)
    (d2 / "retro").mkdir()
    pd.DataFrame([{"code": "000002", "fwd_1_oo": 0.01, "gap_c1_o2": 0.04,
                  "fwd_10_oc": 0.25, "gap_d1": 0.02}]).to_csv(     # 无 fwd_5_oc 列 → 该单 f5 未实现
        d2 / "retro" / "attribution.csv", index=False)
    ledger = roll(tmp_path)
    br = rating_base_rates(ledger, min_n=2)
    row = br[0]
    assert row["n"] == 2
    assert row["n_realized"] == 2       # 两单 fwd_2 都已实现(若仍按 f5 只有 1 单)
    assert not row["thin"]              # 按旧口径(f5=1<2)会被误标 thin


# ---------------- 全卡目标校准(spec 2026-07-05 §6) ----------------

def _mk_rated_day(root, date, cards, with_attr=True):
    """cards = [(code, rating, hi_10 或 None)];close=100、目标=120(幅 0.20)、gap=0.02。"""
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": c, "name": f"n{c}", "sector": "半导体"} for c, _, _ in cards]
                 ).to_csv(d / "finalists.csv", index=False)
    pd.DataFrame([{"code": c, "close": 100.0} for c, _, _ in cards]
                 ).to_csv(d / "L1_scored_full.csv", index=False)
    attr_rows = []
    for code, rating, hi in cards:
        (d / "details" / f"{code}.md").write_text(CARD.replace("Overweight", rating),
                                                  encoding="utf-8")
        row = {"code": code, "fwd_1_oo": 0.01, "fwd_5_oc": 0.08, "fwd_10_oc": 0.10,
               "gap_d1": 0.02}
        if hi is not None:
            row["hi_10_oc"] = hi
        attr_rows.append(row)
    if with_attr:
        (d / "retro").mkdir()
        pd.DataFrame(attr_rows).to_csv(d / "retro" / "attribution.csv", index=False)
    return d


def test_target_calibration_counts_all_ratings(tmp_path):
    """全评级入账(非只 ≥OW):Hold 卡也进统计 —— 0 买连败下样本不再永久 thin。"""
    from autoresearch.learning.buy_ledger import target_calibration
    # 目标幅 0.20(close 基)→ o1 基 t_entry = 1.20/1.02−1 ≈ 0.1765
    _mk_rated_day(tmp_path, "2026-07-01", [("000001", "Hold", 0.25)])        # 触达
    _mk_rated_day(tmp_path, "2026-07-02", [("000002", "Underweight", 0.05)])  # 未触达
    st = target_calibration(tmp_path, min_n=1)
    assert st["n"] == 2 and st["n_mature"] == 2
    assert abs(st["hit_rate"] - 0.5) < 1e-9
    assert abs(st["med_target"] - 0.20) < 1e-9
    assert abs(st["med_mfe"] - 0.15) < 1e-9          # median(0.25, 0.05)
    assert not st["thin"]


def test_target_calibration_excludes_downside_targets(tmp_path):
    """UW 向下目标(tr≤0)不入统计——负目标幅任何上涨都'触达',会稀释过乐观读数。"""
    from autoresearch.learning.buy_ledger import target_calibration
    _mk_rated_day(tmp_path, "2026-07-01", [("000001", "Hold", 0.25)])
    d = tmp_path / "2026-07-01"
    (d / "details" / "000002.md").write_text(
        CARD.replace("Overweight", "Underweight").replace("120(EV)", "80(EV)"),
        encoding="utf-8")
    fin = pd.read_csv(d / "finalists.csv", dtype={"code": str})
    pd.concat([fin, pd.DataFrame([{"code": "000002", "name": "n2", "sector": "半导体"}])]
              ).to_csv(d / "finalists.csv", index=False)
    l1 = pd.read_csv(d / "L1_scored_full.csv", dtype={"code": str})
    pd.concat([l1, pd.DataFrame([{"code": "000002", "close": 100.0}])]
              ).to_csv(d / "L1_scored_full.csv", index=False)
    st = target_calibration(tmp_path, min_n=1)
    assert st["n"] == 1                      # 80/100−1 = −0.2 ≤ 0 → 剔除


def test_target_calibration_window_and_immature(tmp_path):
    from autoresearch.learning.buy_ledger import target_calibration
    _mk_rated_day(tmp_path, "2026-07-01", [("000001", "Hold", 0.25)])
    _mk_rated_day(tmp_path, "2026-07-02", [("000002", "Hold", None)])   # 无 hi → 未成熟
    st = target_calibration(tmp_path, min_n=1)
    assert st["n"] == 2 and st["n_mature"] == 1      # 未成熟计 n 不计 mature
    st1 = target_calibration(tmp_path, window=1, min_n=1)
    assert st1["n"] == 1 and st1["n_mature"] == 0    # 窗口只留最近 1 个 scan 日
    assert target_calibration(tmp_path / "nx") is None    # 无现场 → None


def test_calibration_line_thin_gates_injection(tmp_path):
    from autoresearch.learning.buy_ledger import calibration_line, target_calibration
    _mk_rated_day(tmp_path, "2026-07-01", [("000001", "Hold", 0.25)])
    thin_line = calibration_line(target_calibration(tmp_path))          # 默认 min_n=10
    assert "样本少" in thin_line and "禁注" in thin_line
    line = calibration_line(target_calibration(tmp_path, min_n=1))
    assert line.startswith("📐") and "触达率" in line and "100%" in line
    assert calibration_line(None) is None


def test_render_calibration_section_presence_gated(tmp_path):
    from autoresearch.learning.buy_ledger import target_calibration
    _mk_day(tmp_path, "2026-07-01")
    ledger = roll(tmp_path)
    assert "全卡目标校准" not in "\n".join(render(ledger))               # 不传 → 无节(parity)
    md = "\n".join(render(ledger, calib=target_calibration(tmp_path, min_n=1)))
    assert "全卡目标校准" in md and "触达率" in md


# ── legacy 冻结(E6 转正,task-2.4):旧 OW 买单账停止长大,历史照旧 ────────────


def test_roll_is_unfrozen_without_an_activate_date(tmp_path, monkeypatch):
    from autoresearch.learning import legacy_freeze
    monkeypatch.setattr(legacy_freeze, "cutoff", lambda: None)
    _mk_day(tmp_path, "2026-08-18")
    _mk_day(tmp_path, "2026-08-20")

    assert list(roll(tmp_path)["date"]) == ["2026-08-18", "2026-08-20"]


def test_roll_stops_at_the_activate_date_inclusive(tmp_path, monkeypatch):
    """冻结后「≥OW 张数」不再是买单数 —— 继续记新行就是把两种定义接成一条趋势线。"""
    from autoresearch.learning import legacy_freeze
    monkeypatch.setattr(legacy_freeze, "cutoff", lambda: "2026-08-20")
    _mk_day(tmp_path, "2026-08-18")
    _mk_day(tmp_path, "2026-08-20")
    _mk_day(tmp_path, "2026-08-21")

    assert list(roll(tmp_path)["date"]) == ["2026-08-18"]


def test_render_carries_a_legacy_banner_when_frozen(tmp_path, monkeypatch):
    from autoresearch.learning import legacy_freeze
    monkeypatch.setattr(legacy_freeze, "cutoff", lambda: "2026-08-20")
    _mk_day(tmp_path, "2026-08-18")

    md = "\n".join(render(roll(tmp_path)))

    assert "legacy 冻结" in md and "旧绝对门 ≥Overweight 买单账" in md
    monkeypatch.setattr(legacy_freeze, "cutoff", lambda: None)
    assert "legacy 冻结" not in "\n".join(render(roll(tmp_path)))
