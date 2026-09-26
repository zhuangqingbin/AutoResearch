"""L4 卡数的唯一算法(2026-09-26 用户需求:一个键决定最终进 L4 卡的票数,且真实生效)。

四处曾各管一段:menu.l4_budget(五面旗)/ scan-market.js 写死的 min(10, budget)/
l3.finalist_max / composite_seat.m(不占名额)。现在只有这里算,GATE1 回显,消费方只读。
"""
from __future__ import annotations

DEFAULT_MAX_CARDS = 13      # = 退役前 finalist_max 10 + composite m 3(逐字 parity)


def effective_caps(cfg: dict | None, l4_budget: int) -> dict:
    """返回 {max_cards, budget_flags, seat_m, finalist_cap, l3cap}。

    - `max_cards`:非 📌 卡上限(含 composite 席位);📌 持仓恒出卡、不占额。
    - `finalist_cap` = max(1, max_cards − seat_m):留给 L3 finalist tier 的名额。
    - `l3cap`:传给 l3-rank / `write_finalists` 的 finalist tier 上限;`budget_flags=true` 时再与
      `menu.l4_budget`(五面旗只降不升)取小,`false` 时忽略旗。永不为 0。
    """
    from autoresearch.scan.l3.merge import composite_seat_cfg

    l4 = (cfg or {}).get("l4") or {}
    max_cards = int(l4.get("max_cards", DEFAULT_MAX_CARDS))
    budget_flags = bool(l4.get("budget_flags", True))
    seat_on, seat_m = composite_seat_cfg(cfg)
    seat_m = int(seat_m) if seat_on else 0
    finalist_cap = max(1, max_cards - seat_m)
    l3cap = min(finalist_cap, int(l4_budget)) if budget_flags else finalist_cap
    return {"max_cards": max_cards, "budget_flags": budget_flags, "seat_m": seat_m,
            "finalist_cap": finalist_cap, "l3cap": max(1, int(l3cap))}
