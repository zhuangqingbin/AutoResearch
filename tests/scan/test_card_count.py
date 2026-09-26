"""L4 卡数唯一算法 `scan/l4/card_count.effective_caps`(2026-09-26 用户需求:一个键决定最终进 L4 卡的票数)。"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.scan.l4 import card_count
from autoresearch.scan.l4.card_count import DEFAULT_MAX_CARDS, effective_caps


def test_default_is_parity_with_today():
    """默认 13 = finalist_max 10 + composite m 3;l3cap 与改动前 Math.min(10, budget) 逐字同值。"""
    assert DEFAULT_MAX_CARDS == 13
    cfg = {"l3": {"composite_seat": {"enabled": True, "m": 3}}}
    for budget, want in ((30, 10), (22, 10), (15, 10), (8, 8)):
        assert effective_caps(cfg, budget)["l3cap"] == want


def test_max_cards_binds_below_budget():
    cfg = {"l4": {"max_cards": 5}, "l3": {"composite_seat": {"enabled": True, "m": 3}}}
    caps = effective_caps(cfg, 30)
    assert caps == {"max_cards": 5, "budget_flags": True, "seat_m": 3,
                    "finalist_cap": 2, "l3cap": 2}


def test_max_cards_smaller_than_seats_keeps_positive_cap():
    cfg = {"l4": {"max_cards": 2}, "l3": {"composite_seat": {"enabled": True, "m": 3}}}
    caps = effective_caps(cfg, 30)
    assert caps["finalist_cap"] == 1 and caps["l3cap"] == 1    # 永不为 0/负


def test_budget_flags_false_ignores_menu_budget():
    cfg = {"l4": {"max_cards": 20, "budget_flags": False},
           "l3": {"composite_seat": {"enabled": True, "m": 3}}}
    assert effective_caps(cfg, 15)["l3cap"] == 17               # 旗压到 15 也不理


def test_seats_disabled_do_not_reserve():
    cfg = {"l4": {"max_cards": 8}, "l3": {"composite_seat": {"enabled": False, "m": 3}}}
    assert effective_caps(cfg, 30) == {"max_cards": 8, "budget_flags": True, "seat_m": 0,
                                       "finalist_cap": 8, "l3cap": 8}


# ───────── replay CLI:拷贝真 staging 到 scratch,按给定 max_cards 重跑 write_finalists ─────────

def _judged_n(n: int, conv0: int = 90):
    return [{"code": f"{600000 + i:06d}", "name": f"N{i}", "sector": "S", "lenses": "a",
             "conviction": conv0 - i, "fragility": "f", "thesis": "t 1", "mechanism": "m",
             "risk": "r", "catalyst": "c", "triage_lean": "Hold", "lane": "trend",
             "pct_60d": 1.0, "sentiment": "中性", "finalist": True} for i in range(n)]


def _src(tmp_path, n=12):
    src = tmp_path / "src" / "2026-09-17"
    src.mkdir(parents=True)
    (src / "_l3_judged.json").write_text(json.dumps(_judged_n(n)), encoding="utf-8")
    pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(n)], "gbdt_score": 0.1, "pct_1d": 0.0}
                 ).to_csv(src / "L2_gbdt_top200.csv", index=False)
    return src


def test_replay_cli_copies_staging_and_reports_counts(tmp_path, monkeypatch, capsys):
    src = _src(tmp_path)
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l3": {"composite_seat": {"enabled": False, "m": 3}}})
    rc = card_count.main(["replay", str(src), "--max-cards", "5", "--out", str(tmp_path / "out")])
    assert rc == 0
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert got["finalists_non_pinned"] == 5 and got["out"].startswith(str(tmp_path / "out"))
    assert not (src / "finalists.csv").exists()        # 只写 scratch,不碰源 staging


def test_replay_reuses_the_days_pinned_rows_not_todays_pinned_file(tmp_path, monkeypatch, capsys):
    """回放要忠实:📌 取源 finalists.csv 的 lane=pinned 行(码 + 当日 pinned_note),不读今天的 pinned.jsonc。"""
    src = _src(tmp_path)
    pd.DataFrame([{"code": "688981", "lane": "pinned", "pinned_note": "中芯国际 持仓"}]
                 ).to_csv(src / "finalists.csv", index=False)
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l3": {"composite_seat": {"enabled": False, "m": 3}}})
    card_count.main(["replay", str(src), "--max-cards", "3", "--out", str(tmp_path / "out")])
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert got["finalists_non_pinned"] == 3 and got["finalists_pinned"] == 1
    fin = pd.read_csv(tmp_path / "out" / "2026-09-17" / "finalists.csv", dtype={"code": str})
    assert fin.loc[fin["lane"] == "pinned", "pinned_note"].tolist() == ["中芯国际 持仓"]


def test_replay_refuses_to_write_inside_the_source_staging(tmp_path):
    src = _src(tmp_path)
    with pytest.raises(SystemExit):
        card_count.main(["replay", str(src), "--max-cards", "5", "--out", str(src / "scratch")])
