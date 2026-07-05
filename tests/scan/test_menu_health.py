"""L2 菜单体检块:健康上涨/落刀/行业集中度/估值,缺列缺文件降级。合成 fixture。

spec: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.2
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.menu import menu_health


def _mk(scan_dir, l2_rows, l1_rows):
    scan_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(l2_rows).to_csv(scan_dir / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame(l1_rows).to_csv(scan_dir / "L1_scored_full.csv", index=False)


def _row(code, ind="半导体", pct=10.0, mnr=0.01, cmf=0.05, pe=30.0, reserved=0):
    return {"code": code, "industry": ind, "pct_60d": pct, "main_net_ratio": mnr,
            "cmf_20": cmf, "pe": pe, "l2_lane_reserved": reserved}


def test_menu_health_core(tmp_path):
    l2 = [_row("000001", "半导体", 10, 0.01, 0.05),        # 健康上涨
          _row("000002", "半导体", -30, -0.01, -0.05),     # 落刀
          _row("000003", "白酒Ⅱ", -25, 0.01, -0.02),       # 落刀
          _row("000004", "半导体", 60, 0.01, 0.05, pe=80), # 过热(pct>40 不算健康)
          _row("000005", "电力", 5, -0.02, 0.01, reserved=1)]
    l1 = l2 + [_row(f"00000{i}", "电力", 3, 0.01, 0.01) for i in range(6, 10)]  # 全市场再加 4 只健康
    _mk(tmp_path, l2, l1)
    s = menu_health(tmp_path)
    assert "🍱 L2 菜单体检" in s
    assert "健康上涨" in s and "1/5" in s and "5/9" in s     # L2 1只 vs 全市场 5只
    assert "落刀" in s and "40%" in s                        # L2 2/5
    assert "半导体" in s and "60%" in s                      # 行业 top 3/5
    assert "floor 救回" in s and "1" in s


def test_missing_files_empty(tmp_path):
    assert menu_health(tmp_path) == ""


def test_missing_cols_degrade(tmp_path):
    l2 = [{"code": "000001", "industry": "半导体", "pct_60d": -30.0}]
    _mk(tmp_path, l2, l2)
    s = menu_health(tmp_path)
    assert "🍱 L2 菜单体检" in s and "落刀" in s
    assert "健康上涨" not in s        # 缺 main/cmf 列 → 该行降级消失,不抛


def test_l4_budget_sick_menu(tmp_path):
    """三旗(落刀70%+健康0+risk_off)→ 预算砍半;spec l4-economy §2。"""
    import json

    from autoresearch.scan.menu import l4_budget
    l2 = [_row(f"{i:06d}", pct=-30, mnr=-0.01, cmf=-0.05) for i in range(7)] + \
         [_row(f"{i:06d}", pct=60, mnr=-0.01, cmf=-0.05) for i in range(7, 10)]
    _mk(tmp_path, l2, l2)
    (tmp_path / "meta.json").write_text(json.dumps({"regime": "risk_off"}), encoding="utf-8")
    n, why = l4_budget(tmp_path)
    assert n == 15 and "落刀" in why and "risk_off" in why


def test_l4_budget_one_flag_and_clean(tmp_path):
    from autoresearch.scan.menu import l4_budget
    l2 = [_row(f"{i:06d}", pct=10, mnr=0.01, cmf=0.05) for i in range(9)] + \
         [_row("000099", pct=-30, mnr=-0.01, cmf=-0.05)]          # 健康9只、落刀10% → 0旗
    _mk(tmp_path, l2, l2)
    n, why = l4_budget(tmp_path)
    assert n == 30 and "健康" in why
    l2b = [_row(f"{i:06d}", pct=60, mnr=0.01, cmf=0.05) for i in range(9)] + \
          [_row("000098", pct=10, mnr=0.01, cmf=0.05)]            # 健康仅1 → 1旗
    _mk(tmp_path, l2b, l2b)
    n2, why2 = l4_budget(tmp_path)
    assert n2 == 22 and "健康涨仅1" in why2


def test_l4_budget_missing_is_parity(tmp_path):
    from autoresearch.scan.menu import l4_budget
    n, why = l4_budget(tmp_path)
    assert n == 30 and "parity" in why


# ───────────────────────── 预算加旗:相对落刀 + 0买连败(2026-07-04) ─────────────────────────


def _mk_day(root, date, rating_text):
    """造一个已出卡的 scan 日(finalists + details 卡),供 0买连败判定。"""
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": "000001", "name": "甲"}]).to_csv(d / "finalists.csv", index=False)
    (d / "details" / "000001.md").write_text(rating_text, encoding="utf-8")
    return d


_HOLD_CARD = "# 决策卡\n**Rating**: Hold\n"
_OW_CARD = "# 决策卡\n**Rating**: Overweight\n"


def _healthy_menu(scan_dir, n=10):
    """0 旗基准菜单(健康上涨 n 只,无落刀)。"""
    rows = [_row(f"{i:06d}", pct=10, mnr=0.01, cmf=0.05) for i in range(n)]
    _mk(scan_dir, rows, rows)


def test_l4_budget_relative_knife(tmp_path):
    """07-03 病灶:L2 落刀 45% vs 全市场 15%(>2× 且 >40%)绝对门 60% 抓不住 → 应计 1 旗。"""
    from autoresearch.scan.menu import l4_budget
    l2 = [_row(f"{i:06d}", pct=-30, mnr=-0.01, cmf=-0.05) for i in range(9)] + \
         [_row(f"{100 + i:06d}", pct=10, mnr=0.01, cmf=0.05) for i in range(11)]   # 落刀 45%,健康 11
    l1 = [_row(f"{i:06d}", pct=-30, mnr=-0.01, cmf=-0.05) for i in range(6)] + \
         [_row(f"{200 + i:06d}", pct=10, mnr=0.01, cmf=0.05) for i in range(34)]   # 全市场落刀 15%
    _mk(tmp_path, l2, l1)
    n, why = l4_budget(tmp_path)
    assert n == 22 and "落刀45%" in why and "全市场" in why


def test_l4_budget_zero_buy_streak_flags(tmp_path):
    """0买连败≥3 计 1 旗(→3/4 档);≥5 计重旗(→1/2 档)。"""
    from autoresearch.scan.menu import l4_budget, zero_buy_streak
    root = tmp_path / "a"
    for dt in ("2026-07-01", "2026-07-02", "2026-07-03"):
        _mk_day(root, dt, _HOLD_CARD)
    today = root / "2026-07-04"
    _healthy_menu(today)
    assert zero_buy_streak(today) == 3
    n, why = l4_budget(today)
    assert n == 22 and "0买连败3日" in why

    root2 = tmp_path / "b"
    for dt in ("2026-06-27", "2026-06-28", "2026-07-01", "2026-07-02", "2026-07-03"):
        _mk_day(root2, dt, _HOLD_CARD)
    today2 = root2 / "2026-07-04"
    _healthy_menu(today2)
    assert zero_buy_streak(today2) == 5
    n2, why2 = l4_budget(today2)
    assert n2 == 15 and "0买连败5日" in why2


def test_l4_budget_streak_broken_by_buy_day(tmp_path):
    """最近一个有买日打断连败 → 不计旗,预算回基准。"""
    from autoresearch.scan.menu import l4_budget, zero_buy_streak
    root = tmp_path / "c"
    _mk_day(root, "2026-07-01", _HOLD_CARD)
    _mk_day(root, "2026-07-02", _HOLD_CARD)
    _mk_day(root, "2026-07-03", _OW_CARD)        # 昨日有买 → 连败清零
    today = root / "2026-07-04"
    _healthy_menu(today)
    assert zero_buy_streak(today) == 0
    n, why = l4_budget(today)
    assert n == 30 and "菜单健康" in why
