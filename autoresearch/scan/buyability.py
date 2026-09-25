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

2026-09-25 复核轮二(整份文件仍是同一病的猎场 —— 一个值能在两个世界里同时为真,归因就
废了。本轮 controller 派工点名 I2/I4/I5,本文件闭合的三处):

I2:验收门 L1a 读『非盲卡里 entry_source=="line" 占比』,但产物只有一个内部布尔
`has_line`,没有任何字段把这个计数写出来 —— 门读的是一个没人生产的数。补
`cards.entry_line`:口径与 `has_line` 同源,只数**解析成功**(OK/PARTIAL)的卡,理由同
P33 —— ERROR 卡即便反常地带着 `entry_source=="line"` 也不算,不能把解析器故障错记成
「写过入场行」。

I4(a):`cards.earlystop`/`cards.full` 在 `card_kind` 概念不存在的老 schema 天(schema 1,
`card_context` 里压根没有这个键)读 0/0,与「看过、真是零」共用同一个假零 —— 这正是这两个
计数当初被加进来(P35)要防的病。改:全体(非📌)候选一张 `card_kind` 都没有 → 两者皆
`None`;只要有一张带这个键(说明当天 schema 够新,真的"看过"),按原逻辑计数,不因个别
候选缺键就整体退化成 None。

I4(b):`cards` 数非📌候选(`cands` 在 pinned 处过滤 —— A 级候选按设计恒排除 pinned,见
relative_buy.py 的 `eligible ∧ ¬pinned ∧ entry_stance=="ALLOWED"`),`gates` 的
by_gate/excluded 计数却是**全体**候选(`_hard_gate` 不看 pinned,pinned 票一样要过硬门、
一样记进 `excluded`/`by_gate`)。两个不同人口不能悄悄共用同一行展示 —— 2026-09-17 真实
产物 `cards.n=9` 旁边印着 `data_a_day=11`。选择:不强行拉平成同一人口(拉平会让 `cards`
掺进永远当不成 A 级的 pinned 票,污染卡面立场分布这个字段本来要回答的问题),改为
**标注** —— 新增 `gates.n`(=全体候选数,含 pinned),与既有 `cards.n` 并排,读者一眼就能
看出差几个(差额 = 当天 pinned 候选数)。

I4(c):`wall=="none"` 与 `gates.no_redflag_card_prohibited==0` 在 `tiering` 开/关下各自是
两个不同世界 —— 开:入场门真的跑过、没撞;关:入场门压根没跑,不存在"撞没撞"这回事。
产物新增顶层 `tiering`,读决策文档自己的同名顶层键(v4.0 起恒是 bool;更早的 schema 没有
这个键,读不到就是 `None`,不得默认猜成 `False` —— "没有这个概念"与"关"对"入场门有没有
跑过"这件事含义完全不同),不重读 config,这样产物描述的是它归因的那一次真实运行,不是
"现在 config 长什么样"。

I5:`menu.sector_seats` 原先只读 L2 CSV 的 `sector_seat` 列 —— 列缺席即 `None`(fix round 1
finding ②)。但 `_inject_sector_seats_l1`(universe.py)对空 `seats` 列表走"原样返回"分支
(presence-gated parity 的代价):特性**开着**但当日 `pick_sector_seats` 挑不出一个合格
行业时,列同样不出现,和"关着"变成同一个 `None`,两个世界分不清。universe.py 在"开着"
分支内**无条件**落 `_sector_seats.json`(不管 `pick_sector_seats` 挑没挑中),它的在场/
缺席才是真正只在"开着"那天才有的信号:列在场时直接信(在场必然来自非空 `seats` ——
`_inject_sector_seats_l1` 只在非空时才加列);列缺席时改问这份哨兵文件,在场则读
`len(seats)`(此时必为 0,否则列早该出现了),缺席才是 `None`。
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
#: cards_silent/cards_refused 判定(及 I2 的 `entry_line` 计数)。"ERROR" 从没走到过入场行
#: 探测那一步(`l4/parsers._empty_card_context` 三处早退路径恒 `entry_source=None`),把它
#: 算进"沉默"是错怪解析器故障,不是错怪 agent。
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


def _sector_seats_count(l2: pd.DataFrame | None, sector_seats_doc: dict | None) -> int | None:
    """I5:见文件头段落。列在场 → 直接信(在场必然来自非空 `seats`)。列缺席 →
    改问 `_sector_seats.json`(universe.py 只在特性"开着"那天无条件落这份哨兵,不管
    `pick_sector_seats` 挑没挑中):哨兵在场 → 特性开着、当日到货数 = `len(seats)`
    (缺列时必为 0);哨兵也缺席 → 特性真的关着,`None`。
    """
    if l2 is not None and "sector_seat" in l2.columns:
        return int(l2["sector_seat"].fillna(False).astype(bool).sum())
    if sector_seats_doc is not None:
        return int(len(sector_seats_doc.get("seats") or []))
    return None


def build_buyability(scan_dir: Path | str) -> dict:
    scan = Path(scan_dir)
    l0, l2 = _csv(scan / "L1_scored_full.csv"), _csv(scan / "L2_gbdt_top200.csv")
    fins = _csv(scan / "finalists.csv")
    decision = _json(scan / "_relative_buy_decision.json")
    blind = _json(scan / "_blind_cards.json") or {}
    sector_seats_doc = _json(scan / "_sector_seats.json")

    menu = {
        "l2_knife": _share(falling_knife_mask(l2)) if l2 is not None else None,
        "l0_knife": _share(falling_knife_mask(l0)) if l0 is not None else None,
        "l2_healthy": _share(healthy_riser_mask(l2)) if l2 is not None else None,
        "l0_healthy": _share(healthy_riser_mask(l0)) if l0 is not None else None,
        # fix round 1 finding ②:缺列/缺文件是「没看过」,不是「看过、真是零」——两者绝不能
        # 共用同一个 0(那正是下游会读错的「确定性假零」)。`l2_knife`/`l0_knife` 等字段
        # 早就这么干了(`_share` 缺列即 None),这两个字段之前漏做,现在补齐同一纪律。
        # I5(本轮复核):列缺席本身还分两个世界(关着 / 开着但当日零席位),`_sector_seats_
        # count` 借 `_sector_seats.json` 哨兵文件把两者分开,见该函数与文件头 I5 段落。
        "sector_seats": _sector_seats_count(l2, sector_seats_doc),
        "composite_seats": int((fins["guard"].astype(str) == "composite_seat").sum())
        if fins is not None and "guard" in fins.columns else None,
    }

    if not decision:
        # fix round 1 finding ①:决策文档缺席/不可读(含"读到了但是个空壳"),意味着没有
        # 候选/买入数据可归因——**不得**假装算出一个"看起来合理"的 wall,哪怕 L0/L2 菜单
        # 数据本身健在。`not decision` 与 `relative_facts()` 判 present=False 的
        # `not isinstance(decision, dict)` 同一份文件、同一个"没读到就是没读到"的语义
        # (外加空字典 `{}` 这个生产从不会写出的退化态,保守地也算"没有")——brief 自己的
        # `if not ba.get("wall")` 天然把这个 None 当"缺席"处理,不必在 brief.py 再加一层
        # 特判。menu 仍照算:它只读 L0/L2 CSV,与决策文档是否存在无关,不是从空处编出来的。
        # I4(c):`tiering` 同理——没有决策文档就没有"那一次运行",不得编成 False。
        return {"schema_version": 1, "date": scan.name, "menu": menu,
                "cards": None, "gates": None, "buy": None, "wall": None, "tiering": None}

    all_cands = [c for c in (decision.get("candidates") or []) if isinstance(c, dict)]
    # I4(b):`cands` 是 `all_cands` 剔掉 pinned 后的子集——A 级候选按设计恒排除 pinned,
    # `cards` 描述的是"卡面立场分布"这件事本来就该把 pinned(永远当不成 A 级)排除在外。
    # `gates` 的人口不做同样的过滤(见下),两者的分母因此不同,`gates.n` 把这个差异读出来。
    cands = [c for c in all_cands if not c.get("pinned")]
    stance = [str(_ctx(c).get("entry_stance") or "UNKNOWN") for c in cands]
    parsed = [c for c in cands if str(_ctx(c).get("parse_status") or "") in _PARSED_STATUSES]
    # I4(a):`card_kind` 只有 schema 2 才有——全体候选一个都没带这个键,说明当天的决策文档
    # 压根没有这个概念(不是"看过、真是零"),`earlystop`/`full` 必须整体读 None。
    has_card_kind = any(_ctx(c).get("card_kind") is not None for c in cands)
    cards = {
        "n": len(cands),
        "allowed": stance.count("ALLOWED"),
        "conditional": stance.count("CONDITIONAL"),
        "prohibited": stance.count("PROHIBITED"),
        "unknown": len(stance) - stance.count("ALLOWED") - stance.count("CONDITIONAL") - stance.count("PROHIBITED"),
        # I2:gate L1a 读『非盲卡里 entry_source=="line" 占比』——只数**解析成功**
        # (parsed)的卡,口径与下面 `has_line`/`wall` 判定同源(P33),ERROR 卡不算。
        "entry_line": sum(1 for c in parsed if str(_ctx(c).get("entry_source") or "") == "line"),
        "blind": len(blind),
        # P33:解析失败的卡数,挨着 blind —— "没读到卡"与"读到了但解不出来"是两回事。
        "parse_failed": sum(1 for c in cands if str(_ctx(c).get("parse_status") or "") == "ERROR"),
        # P35:早停/满卡卡种计数,与入场立场正交,不进 wall 判定,只挨着立场计数一起印,
        # 让读者自己判断"没有允许"是不是因为大多数卡根本没资格说允许。
        # I4(a):老 schema(没有 card_kind 概念)整体读 None,不是假 0。
        "earlystop": (sum(1 for c in cands if _ctx(c).get("card_kind") == "earlystop")
                     if has_card_kind else None),
        "full": (sum(1 for c in cands if _ctx(c).get("card_kind") == "full")
                if has_card_kind else None),
    }

    by_gate = ((decision.get("veto_accounting") or {}).get("by_gate") or {})
    excluded = decision.get("excluded") or []
    data_a_ticker = sum(1 for e in excluded if e.get("reason") == "hard_gate.data_a" and "票级" in str(e.get("detail", "")))
    # 2026-09-25 controller 追加裁定:no_redflag 六因里只有"卡面入场=禁止"是本波新增,
    # 其余五个是老因;子集匹配同 data_a_ticker 一样按 excluded[].detail 的固定字面子串。
    no_redflag_card_prohibited = sum(
        1 for e in excluded
        if e.get("reason") == "hard_gate.no_redflag" and "entry_stance=PROHIBITED" in str(e.get("detail", "")))
    gates = {
        # I4(b):gates 的人口是**全体**候选(含 pinned)—— `_hard_gate` 对每个 entry 都跑,
        # 不看 pinned。与 `cards.n` 并排能看出差几个(差额 = 当天 pinned 候选数)。
        "n": len(all_cands),
        "data_a_day": max(0, int(by_gate.get("data_a", 0)) - data_a_ticker), "data_a_ticker": data_a_ticker,
        "contract": int(by_gate.get("contract", 0)), "no_redflag": int(by_gate.get("no_redflag", 0)),
        "no_redflag_card_prohibited": no_redflag_card_prohibited,
        # task-12 附带修复 A:第五门(`relative_buy.rebalance_gate` 开时的 `rebalance_close`)
        # 命中数——`_veto_accounting_block`(relative_buy.py)的 `by_gate` 只遍历**当次真实
        # 生效**的硬门元组,旋钮关/老 schema 的文档里这个键压根不存在,不是存在且为 0
        # (同 I4(c) `tiering` 的纪律:「没有这个概念」与「有、但零命中」是两个不同世界,
        # 不得共用一个假 0)。既有键(`n`/`contract`/…)全部保持不变——这里只加新键,
        # 不改老键的取值方式,老文档读出的既有字段逐字不变。
        "rebalance_close": (int(by_gate["rebalance_close"]) if "rebalance_close" in by_gate else None),
    }

    buys = decision.get("buys") or []
    buy = {"tier": (buys[0].get("tier") if buys else None), "code": (buys[0].get("code") if buys else None),
           "blocked": bool(decision.get("blocked"))}

    menu_bad = ((menu["l2_knife"] is not None and menu["l0_knife"] is not None
                 and menu["l2_knife"] > menu["l0_knife"] + MENU_KNIFE_TOLERANCE)
                or (menu["l2_healthy"] is not None and menu["l0_healthy"] is not None
                    and menu["l2_healthy"] < menu["l0_healthy"]))
    # fix round 1 finding ③:`eligible` 只问当次运行实际生效的硬门(旋钮
    # `relative_buy.rebalance_gate` 关时四道:tradable/data_a/contract/no_redflag;开时多
    # 第五道 rebalance_close——`eligible` 是决策文档自己算好的字段,这里只读不重算,门数
    # 随文档当天的旋钮状态走,不是本文件里硬编码的常数),不问候选池——composite 池下一张
    # ALLOWED 且过硬门的卡仍可能因为不在证据席被
    # `relative_buy.py` 判 `in_pool=False`（该票同时会被记进 `excluded[reason=not_in_pool]`，
    # 但**留在** `candidates` 里，"不进池 ≠ 不进候选表"，relative_buy.py 原话)，从未真正
    # 有机会当 BUY。`in_pool` 由 relative_buy.py 按当天生效的 pool 逐票算好
    # (`finalists` 池下恒 True,`composite` 池下=是否证据席),这里只读、不重算——
    # 缺字段(旧 schema)默认 True，不倒过去惩罚没有这个概念的历史文档。
    allowed_eligible = any(c.get("eligible") and c.get("in_pool", True)
                           and str(_ctx(c).get("entry_stance")) == "ALLOWED"
                           for c in cands)
    if menu_bad:
        wall = "menu"
    elif cards["allowed"] == 0:
        # P22 + P33:allowed==0 只说"没有卡允许",分不清"没人被问"还是"问了都说不"。
        # 只看**解析成功**(OK/PARTIAL)的卡里有没有写过机读入场行 —— I2 的 `entry_line`
        # 就是这个判据的计数版本,直接复用,不重复遍历。
        has_line = cards["entry_line"] > 0
        wall = "cards_refused" if has_line else "cards_silent"
    elif not allowed_eligible:
        wall = "gates"
    else:
        wall = "none"
    return {"schema_version": 1, "date": scan.name, "menu": menu, "cards": cards, "gates": gates,
            "buy": buy, "wall": wall,
            # I4(c):顶层 `tiering`,原样透传决策文档自己的同名顶层键(v4.0 起恒 bool;
            # 更早 schema 没有这个键 → None,不得默认猜成 False)。`wall=="none"` 与
            # `gates.no_redflag_card_prohibited==0` 在 tiering 关着时都读不出"入场门有没有
            # 跑过",这一位就是唯一能回答这个问题的字段。
            "tiering": decision.get("tiering")}


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
