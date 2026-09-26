"""L4 卡数唯一算法 `scan/l4/card_count.effective_caps`(2026-09-26 用户需求:一个键决定最终进 L4 卡的票数)。"""
from __future__ import annotations

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
