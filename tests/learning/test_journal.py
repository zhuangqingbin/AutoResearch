"""扫描日记:每日一行(regime/菜单/漏斗/买/触发/fwd 回填)。合成,无网络。

spec: docs/specs/2026-07-02-scan-observability-design.md §2
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning.journal import render, roll


def _mk_day(root, date, regime="range", with_retro=False, trigger=False):
    d = root / date
    (d / "details").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")
    pd.DataFrame([{"code": "000001", "industry": "半导体", "pct_60d": -30.0,
                   "main_net_ratio": 0.01, "cmf_20": 0.02},
                  {"code": "000002", "industry": "电力", "pct_60d": 10.0,
                   "main_net_ratio": 0.02, "cmf_20": 0.05}]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details" / "000001.md").write_text("**Rating**: Overweight\n", encoding="utf-8")
    if trigger:
        pd.DataFrame([{"code": "000009", "name": "乙", "status": "触发",
                       "detail": "", "narrative": "", "born": date, "expiry": ""}]).to_csv(
            d / "watchlist_status.csv", index=False)
    if with_retro:
        (d / "retro").mkdir()
        pd.DataFrame([{"code": f"{i:06d}", "fwd_1_oo": 0.01, "fwd_5_oc": -0.02}
                      for i in range(5)]).to_csv(d / "retro" / "attribution.csv", index=False)
        (d / "retro" / "done.json").write_text("{}", encoding="utf-8")


def test_journal_two_days(tmp_path):
    _mk_day(tmp_path, "2026-07-01", regime="risk_off", with_retro=True)
    _mk_day(tmp_path, "2026-07-02", trigger=True)
    df = roll(tmp_path)
    assert list(df["date"]) == ["2026-07-01", "2026-07-02"]
    r1, r2 = df.iloc[0], df.iloc[1]
    assert r1["regime"] == "risk_off" and r1["retro_done"] and r1["mkt_fwd1"] == 0.01
    assert r1["knife"] == 0.5 and r1["healthy"] == 1                    # 2 只 L2:1 落刀 1 健康
    assert r2["buys"] == 1 and r2["triggers"] == 1 and not r2["retro_done"]
    md = "\n".join(render(df))
    assert "2026-07-01" in md and "risk_off" in md and "汇总" in md
    assert "50%" in md and "200%" not in md      # 落刀=占比;计数列取整,别把 2 渲成 200%


def test_journal_empty(tmp_path):
    df = roll(tmp_path)
    assert not len(df)
    assert "_无 scan 日_" in "\n".join(render(df))


# ───────────────────────── D5 口径统一(spec 2026-07-12 P0-1):买单计数改用 attribution `bought` ─────────────────────────


def _mk_finalist_day(root, date, cards: dict, attribution_rows=None):
    """轻量 fixture:只搭 finalists+details+可选 attribution(D5 场景专用,别与 _mk_day 混)。"""
    d = root / date
    (d / "details").mkdir(parents=True)
    pd.DataFrame([{"code": c, "name": f"N{c}", "sector": "半导体"} for c in cards]).to_csv(
        d / "finalists.csv", index=False)
    for code, text in cards.items():
        (d / "details" / f"{code}.md").write_text(text, encoding="utf-8")
    if attribution_rows is not None:
        (d / "retro").mkdir()
        pd.DataFrame(attribution_rows).to_csv(d / "retro" / "attribution.csv", index=False)
    return d


def test_journal_buys_prefers_attribution_bought(tmp_path):
    """attribution.csv 有 bought 列且与卡面一致 → 走新路径,读数不变。"""
    _mk_finalist_day(tmp_path, "2026-07-03",
                     cards={"000001": "**Rating**: Overweight\n", "000002": "**Rating**: Hold\n"},
                     attribution_rows=[{"code": "000001", "bought": True, "fwd_1_oo": 0.01},
                                       {"code": "000002", "bought": False, "fwd_1_oo": -0.01}])
    df = roll(tmp_path)
    assert df.iloc[0]["buys"] == 1


def test_journal_buys_attribution_overrides_card_face(tmp_path):
    """D5 核心验收:attribution.bought 与卡面评级不一致时(如折回未回写卡片文本),
    journal 现在以 attribution 为单一事实源,不再被卡面文本带偏——证明真的切换了口径,非空转。"""
    _mk_finalist_day(tmp_path, "2026-07-03",
                     cards={"000001": "**Rating**: Overweight\n"},   # 卡面仍是 OW
                     attribution_rows=[{"code": "000001", "bought": False, "fwd_1_oo": 0.01}])  # 账本说没买
    df = roll(tmp_path)
    assert df.iloc[0]["buys"] == 0     # 以 attribution 为准(旧口径 count_buys 会读成 1)


def test_journal_buys_falls_back_without_attribution(tmp_path):
    """无 retro/attribution.csv(如今日刚发布、T+2 未成熟)→ 回退卡面口径 count_buys,现行为不变。"""
    _mk_finalist_day(tmp_path, "2026-07-03", cards={"000001": "**Rating**: Overweight\n"})
    df = roll(tmp_path)
    assert df.iloc[0]["buys"] == 1


def test_journal_buys_falls_back_without_bought_column(tmp_path):
    """attribution.csv 存在但无 bought 列(旧格式/仅归因子集)→ presence-gated 回退卡面口径,不误判 0。"""
    _mk_finalist_day(tmp_path, "2026-07-03", cards={"000001": "**Rating**: Overweight\n"},
                     attribution_rows=[{"code": "000001", "fwd_1_oo": 0.01}])   # 无 bought 列
    df = roll(tmp_path)
    assert df.iloc[0]["buys"] == 1


def test_journal_and_zero_buy_agree_on_same_attribution(tmp_path):
    """D5 收口验收:同一 attribution.csv 喂两本账,journal.buys 与 zero_buy_ledger.n_bought 现在同口径。

    卡面故意都写 Hold(与 attribution 相反)——旧代码下 journal 会读出 0、zero_buy 读出 1(分叉复现);
    修复后两本账必须相等。
    """
    from autoresearch.learning.zero_buy_ledger import roll as zb_roll
    _mk_finalist_day(tmp_path, "2026-07-03",
                     cards={"000001": "**Rating**: Hold\n", "000002": "**Rating**: Hold\n"},
                     attribution_rows=[{"code": "000001", "bought": True, "fwd_1_oo": 0.01},
                                       {"code": "000002", "bought": False, "fwd_1_oo": -0.01}])
    j = roll(tmp_path)
    z = zb_roll(tmp_path)
    assert j.iloc[0]["buys"] == int(z.iloc[0]["n_bought"]) == 1
