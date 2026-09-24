#!/usr/bin/env python3
"""不可买归因(2026-09-24 可买性对齐 §2.7;裁定④「把为什么不可买解掉」的可见面。
2026-09-25 controller 派工附录追加三处裁定 P22/P33/P35 + 当轮 no_redflag 拆分裁定一并落地,
见下方各处引用)。

零 LLM,`post_run.observe` 在 `relative_buy` 决策写完之后跑。四堵墙按序判定,第一堵撞上的就是
`wall`:

    menu           L2 落刀 > L0 + 6pp,或 L2 健康 < L0 健康 —— 菜单本身有病。
    cards_silent   菜单过关,且**没有一张解析成功的卡**写过机读入场行 —— 契约缺口,
                   agent 压根没被问过「允不允许入场」。
                   cards_refused  菜单过关,且**至少一张解析成功的卡**写过机读入场行、但不是
                   允许 —— 卡说了,答案是不/看条件,这是研究判断,不是接线问题。
    gates          有卡写了「允许入场」,但全部被硬门否决。
    none           出了一只 A 级 relative BUY。

P22(controller Ruling,派工附录):原方案把 cards_silent/cards_refused 合成一个 `cards` 值,
分不清"卡从没被问过"(Task 18 要修的契约缺口)与"卡问了、明确说不"(研究判断,任何接线
都改不动)。拆开后 Task 18 上线前后才能看出这条线到底有没有生效 —— 否则上线前后 `wall`
读数完全一样,谁都不知道修好了没有。

P33(entry_source 语义纠偏):`card_context.entry_source is None` 记的是"这次解析没做过任何
抽取尝试"(`l4/parsers._empty_card_context` 的三处早退路径之一 —— 仪表盘本身解不出来,连
有没有入场行都没来得及看),不是"卡没写入场行"。所以 silent/refused 的判定只看
`parse_status ∈ {"OK","PARTIAL"}` 的卡;否则会把解析器故障错记成"卡不肯说话",而
`cards_silent → 0` 这条验收信号(gate L1b)就会因为一个谁都看不见的原因永远不可达。
解析失败的卡数单独进 `cards.parse_failed`(挨着 `blind`),让"解析器坏了"读起来像坏了,
不像沉默。

P35(早停 vs 满卡的卡种混合):Task 18 之后早停卡也能写入场行(模板只许 `禁止|条件`,结构性
写不出「允许」——早停发生在决策完成之前,没资格说允许)。于是 `cards_refused` 会同时装下
"研究做完了、明确说不"与"研究还没做完、压根没资格说允许"两个不同世界。不新开第六个 wall
值(本分支已经拒绝过所有需要阈值的新墙,两个整数说得比一刀切阈值更准),只在 `cards` 里
挂 `earlystop`/`full` 两个计数,brief 一起印,读者自己看比例。

no_redflag 拆分(2026-09-25 controller 追加裁定,派工消息,非文件裁定):`hard_gate.
no_redflag` 六个否决因里,只有"卡面入场=禁止"(`entry_stance=PROHIBITED`)是本波新增的因
——其余五个(ST/退市、评级、卡面提案、早停红灯停因、流动性分位)都是本波之前就有的老因。
混在一个 `no_redflag` 整数里,本波自己的否决效果就看不见,所以在 `gates` 里加
`no_redflag_card_prohibited` 作为 `no_redflag` 的子集(与 `data_a_ticker` 拆 `data_a` 同一
手法:按 `excluded[].detail` 里的固定字面子串匹配 —— `relative_buy.py` 的
`fail("no_redflag", "卡面入场=禁止(entry_stance=PROHIBITED;v4.0 tiering)")` 是纯字面量,
不掺变量插值,子串稳定)。`no_redflag` 本身不变,仍是全量,下游读它的人不受影响。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

from autoresearch.common.scoring import falling_knife_mask, healthy_riser_mask

BUYABILITY_FILENAME = "_buyability.json"
WALLS = ("menu", "cards_silent", "cards_refused", "gates", "none")
MENU_KNIFE_TOLERANCE = 0.06          # 与 spec §3.1 A4 同一门:豁免两桶 ≤12 行 = 6pp
#: parse_status 的"已尝试解析"子集(P33)—— 只有落在这两态里的卡才有资格参与
#: cards_silent/cards_refused 判定。"ERROR" 从没走到过入场行探测那一步(`l4/parsers.
#: _empty_card_context` 三处早退路径恒 `entry_source=None`),把它算进"沉默"是错怪
#: 解析器故障,不是错怪 agent。
_PARSED_STATUSES = ("OK", "PARTIAL")


def _csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        return pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001 — 坏文件按缺席处理,不猜
        return None


def _json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return doc if isinstance(doc, dict) else None


def _share(mask: pd.Series | None) -> float | None:
    if mask is None or not len(mask):
        return None
    return round(float(mask.fillna(False).mean()), 4)


def _ctx(cand: dict) -> dict:
    ctx = cand.get("card_context")
    return ctx if isinstance(ctx, dict) else {}


def build_buyability(scan_dir: Path | str) -> dict:
    scan = Path(scan_dir)
    l0, l2 = _csv(scan / "L1_scored_full.csv"), _csv(scan / "L2_gbdt_top200.csv")
    fins = _csv(scan / "finalists.csv")
    decision = _json(scan / "_relative_buy_decision.json") or {}
    blind = _json(scan / "_blind_cards.json") or {}

    menu = {
        "l2_knife": _share(falling_knife_mask(l2)) if l2 is not None else None,
        "l0_knife": _share(falling_knife_mask(l0)) if l0 is not None else None,
        "l2_healthy": _share(healthy_riser_mask(l2)) if l2 is not None else None,
        "l0_healthy": _share(healthy_riser_mask(l0)) if l0 is not None else None,
        "sector_seats": int(l2["sector_seat"].fillna(False).astype(bool).sum())
        if l2 is not None and "sector_seat" in l2.columns else 0,
        "composite_seats": int((fins["guard"].astype(str) == "composite_seat").sum())
        if fins is not None and "guard" in fins.columns else 0,
    }

    cands = [c for c in (decision.get("candidates") or []) if isinstance(c, dict) and not c.get("pinned")]
    stance = [str(_ctx(c).get("entry_stance") or "UNKNOWN") for c in cands]
    parsed = [c for c in cands if str(_ctx(c).get("parse_status") or "") in _PARSED_STATUSES]
    cards = {
        "n": len(cands),
        "allowed": stance.count("ALLOWED"),
        "conditional": stance.count("CONDITIONAL"),
        "prohibited": stance.count("PROHIBITED"),
        "unknown": len(stance) - stance.count("ALLOWED") - stance.count("CONDITIONAL") - stance.count("PROHIBITED"),
        "blind": len(blind),
        # P33:解析失败的卡数,挨着 blind —— "没读到卡"与"读到了但解不出来"是两回事。
        "parse_failed": sum(1 for c in cands if str(_ctx(c).get("parse_status") or "") == "ERROR"),
        # P35:早停/满卡卡种计数,与入场立场正交,不进 wall 判定,只挨着立场计数一起印,
        # 让读者自己判断"没有允许"是不是因为大多数卡根本没资格说允许。
        "earlystop": sum(1 for c in cands if _ctx(c).get("card_kind") == "earlystop"),
        "full": sum(1 for c in cands if _ctx(c).get("card_kind") == "full"),
    }

    by_gate = ((decision.get("veto_accounting") or {}).get("by_gate") or {})
    excluded = decision.get("excluded") or []
    data_a_ticker = sum(1 for e in excluded if e.get("reason") == "hard_gate.data_a" and "票级" in str(e.get("detail", "")))
    # 2026-09-25 controller 追加裁定:no_redflag 六因里只有"卡面入场=禁止"是本波新增,
    # 其余五个是老因;子集匹配同 data_a_ticker 一样按 excluded[].detail 的固定字面子串。
    no_redflag_card_prohibited = sum(
        1 for e in excluded
        if e.get("reason") == "hard_gate.no_redflag" and "entry_stance=PROHIBITED" in str(e.get("detail", "")))
    gates = {"data_a_day": max(0, int(by_gate.get("data_a", 0)) - data_a_ticker), "data_a_ticker": data_a_ticker,
             "contract": int(by_gate.get("contract", 0)), "no_redflag": int(by_gate.get("no_redflag", 0)),
             "no_redflag_card_prohibited": no_redflag_card_prohibited}

    buys = decision.get("buys") or []
    buy = {"tier": (buys[0].get("tier") if buys else None), "code": (buys[0].get("code") if buys else None),
           "blocked": bool(decision.get("blocked"))}

    menu_bad = ((menu["l2_knife"] is not None and menu["l0_knife"] is not None
                 and menu["l2_knife"] > menu["l0_knife"] + MENU_KNIFE_TOLERANCE)
                or (menu["l2_healthy"] is not None and menu["l0_healthy"] is not None
                    and menu["l2_healthy"] < menu["l0_healthy"]))
    allowed_eligible = any(c.get("eligible") and str(_ctx(c).get("entry_stance")) == "ALLOWED"
                           for c in cands)
    if menu_bad:
        wall = "menu"
    elif cards["allowed"] == 0:
        # P22 + P33:allowed==0 只说"没有卡允许",分不清"没人被问"还是"问了都说不"。
        # 只看**解析成功**(OK/PARTIAL)的卡里有没有写过机读入场行。
        has_line = any(str(_ctx(c).get("entry_source") or "") == "line" for c in parsed)
        wall = "cards_refused" if has_line else "cards_silent"
    elif not allowed_eligible:
        wall = "gates"
    else:
        wall = "none"
    return {"schema_version": 1, "date": scan.name, "menu": menu, "cards": cards, "gates": gates,
            "buy": buy, "wall": wall}


def write_buyability(scan_dir: Path | str) -> Path:
    scan = Path(scan_dir)
    doc = build_buyability(scan)
    path = scan / BUYABILITY_FILENAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def safe_write_buyability(scan_dir: Path | str) -> Path | None:
    """观测腿:失败只打一行,不阻断发布。"""
    try:
        return write_buyability(scan_dir)
    except Exception as exc:  # noqa: BLE001 — 观测腿不得让 observe 失败,但必须留痕
        print(f"[buyability] 写入失败: {exc!r}", file=sys.stderr)
        return None
