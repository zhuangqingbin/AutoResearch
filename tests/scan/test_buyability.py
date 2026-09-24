"""不可买归因(2026-09-24 §2.7 + 2026-09-25 controller 派工附录 P22/P33/P35 + 当轮
no_redflag 拆分裁定):四堵墙 menu / cards_silent / cards_refused / gates,第一堵撞上的
就是 wall。

fixture 注记(与 task-21-brief 给定的 `_frame`/`_scan`的一处出入,必须说明):brief 原始
Step 1 代码里 `_scan` 把 L0 的 `healthy_share` 恒写死 `0.11`、L2 恒写死 `0.08`,与
`l2_knife`/`l0_knife` 完全脱钩 —— 于是 `l2_healthy(0.08) < l0_healthy(0.11)` 对**任何**
`l2_knife`/`l0_knife` 取值都成立,`menu_bad` 的「健康」支路对 `test_wall_cards_*`/
`test_wall_gates_*`/`test_wall_none_*` 三条用例全部误触发,三条用例都会读到 `wall==
"menu"` 而不是各自要测的墙(实测验证过,见 task-21-report.md)。`menu_bad` 本身的语义
经 addendum 与「批次2」实测(L2 健康份额全程 ≥ L0)反向印证是对的,所以这里改的是**夹具**
不是产物逻辑:`_scan` 把 `l2_healthy`/`l0_healthy` 开放成独立参数,默认两者相等(健康支路
不触发),`test_wall_menu_when_l2_healthy_share_undercuts_l0` 单独用不等值验证健康支路本身
没有被误删。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from autoresearch.scan.buyability import (
    BUYABILITY_FILENAME,
    WALLS,
    build_buyability,
    write_buyability,
)


def _frame(n: int, knife_share: float, healthy_share: float) -> pd.DataFrame:
    k = int(n * knife_share)
    h = int(n * healthy_share)
    return pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(n)], "industry": "电力",
                         "pct_60d": [-30.0] * k + [10.0] * (n - k),
                         "main_net_ratio": [-0.01] * (n - h) + [0.02] * h,
                         "cmf_20": [-0.1] * (n - h) + [0.1] * h})


def _card(stance: str = "UNKNOWN", *, source: str | None = "prose", status: str = "OK",
         kind: str = "full") -> dict:
    """一份候选的 `card_context`(缩到本模块用得到的四个字段)。"""
    return {"entry_stance": stance, "entry_source": source, "parse_status": status, "card_kind": kind}


def _scan(tmp_path: Path, *, l2_knife: float = 0.25, l0_knife: float = 0.23,
         l2_healthy: float = 0.11, l0_healthy: float = 0.11,
         cards: tuple[dict, ...] = (_card("ALLOWED"),), in_pool: tuple[bool, ...] = (),
         gates_ok: bool = True, tier: str | None = None, blind: int = 0,
         extra_excluded: tuple[dict, ...] = (), write_decision: bool = True) -> Path:
    """`cards`:每个候选一份 `card_context`(用 `_card()` 造)。`in_pool`:每个候选一个
    bool,与 `cards` 等长,缺省全 True(`relative_buy.py` 的 `finalists` 池下恒 True;
    `composite` 池下=是否证据席 —— fix round 1 finding ③ 测试用,与 `cards` 分开传避免把
    两个维度耦合进 `_card()`)。`gates_ok=False` 时给每张卡按 Task 1 的文案造一条票级
    `hard_gate.data_a` 否决(与原 brief 语义一致)。`extra_excluded` 用来叠加与
    `cards`/`gates_ok` 无关的其它硬门否决行(no_redflag 拆分测试用)。
    `write_decision=False`:不落 `_relative_buy_decision.json`(fix round 1 finding ①
    「决策文档缺席」测试专用,其余参数在这条路径下不生效)。"""
    scan = tmp_path / "2026-09-17"
    scan.mkdir()
    _frame(1000, l0_knife, l0_healthy).to_csv(scan / "L1_scored_full.csv", index=False)
    l2 = _frame(200, l2_knife, l2_healthy)
    l2["sector_seat"] = False
    l2.to_csv(scan / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame({"code": ["600001"], "guard": ["composite_seat"]}).to_csv(scan / "finalists.csv", index=False)
    if not write_decision:
        return scan
    pool_flags = in_pool if in_pool else tuple(True for _ in cards)
    cands = [{"code": f"6000{i:02d}", "eligible": gates_ok, "pinned": False, "in_pool": p,
              "hard_gate": {"tradable": True, "data_a": gates_ok, "contract": True, "no_redflag": True},
              "card_context": ctx} for i, (ctx, p) in enumerate(zip(cards, pool_flags, strict=True))]
    excluded = list(extra_excluded)
    no_redflag_n = sum(1 for e in excluded if e.get("reason") == "hard_gate.no_redflag")
    if not gates_ok:
        excluded = excluded + [{"code": c["code"], "reason": "hard_gate.data_a",
                                "detail": f"stage l4_{c['code']} 失败(票级 data_a)"} for c in cands]
    doc = {"blocked": not gates_ok, "buys": ([{"code": "600000", "tier": tier}] if gates_ok and tier else []),
           "candidates": cands, "excluded": excluded,
           "veto_accounting": {"by_gate": {"data_a": 0 if gates_ok else len(cands), "contract": 0,
                                           "no_redflag": no_redflag_n, "tradable": 0}}}
    (scan / "_relative_buy_decision.json").write_text(json.dumps(doc), encoding="utf-8")
    if blind:
        (scan / "_blind_cards.json").write_text(json.dumps({f"7000{i:02d}": {} for i in range(blind)}),
                                                encoding="utf-8")
    return scan


# ───────────────────────────── wall 词表 ─────────────────────────────

def test_walls_tuple_is_the_five_value_vocabulary():
    """P22 把 `cards` 拆成两个值 —— 词表必须跟着变,否则「声明的词表」与「实际吐出的值」
    漂移,正是这次拆分要防的缺陷再高一层重演一次(addendum 原话)。"""
    assert WALLS == ("menu", "cards_silent", "cards_refused", "gates", "none")


# ───────────────────────────── menu 墙(两支 OR 条件各自覆盖) ─────────────────────────────

def test_wall_menu_when_l2_knife_exceeds_l0_plus_tolerance(tmp_path):
    doc = build_buyability(_scan(tmp_path, l2_knife=0.40, l0_knife=0.23))
    assert doc["wall"] == "menu" and doc["menu"]["l2_knife"] == 0.40 and doc["menu"]["composite_seats"] == 1


def test_wall_menu_when_l2_healthy_share_undercuts_l0(tmp_path):
    """`menu_bad` 的第二支(健康份额)独立覆盖 —— 只改 `l2_healthy`,knife 两侧留在容差内。"""
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, l0_knife=0.23, l2_healthy=0.05, l0_healthy=0.11))
    assert doc["wall"] == "menu"
    assert doc["menu"]["l2_healthy"] == 0.05 and doc["menu"]["l0_healthy"] == 0.11


# ───────────────────────────── cards_silent / cards_refused(P22 + P33) ─────────────────────────────

def test_wall_cards_silent_when_no_parsed_card_carries_the_entry_line(tmp_path):
    """菜单过关,三张卡都没有允许,且**没有一张解析成功的卡写过机读入场行**
    (全是散文推断)—— 契约缺口,agent 压根没被问过。"""
    cards = (_card("CONDITIONAL", source="prose", status="OK", kind="full"),
             _card("UNKNOWN", source="prose", status="PARTIAL", kind="earlystop"),
             _card("PROHIBITED", source="prose", status="OK", kind="earlystop"))
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards))
    assert doc["wall"] == "cards_silent"
    assert doc["cards"] == {"n": 3, "allowed": 0, "conditional": 1, "prohibited": 1, "unknown": 1,
                            "blind": 0, "parse_failed": 0, "earlystop": 2, "full": 1}


def test_wall_cards_refused_when_a_parsed_card_carries_the_entry_line(tmp_path):
    """至少一张**解析成功**的卡写过机读入场行、但不是允许(P35:早停卡的模板只许
    `禁止|条件`)—— 研究判断,不是接线缺口。"""
    cards = (_card("PROHIBITED", source="line", status="OK", kind="earlystop"),
             _card("UNKNOWN", source="prose", status="OK", kind="full"))
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards))
    assert doc["wall"] == "cards_refused"
    assert doc["cards"] == {"n": 2, "allowed": 0, "conditional": 0, "prohibited": 1, "unknown": 1,
                            "blind": 0, "parse_failed": 0, "earlystop": 1, "full": 1}


def test_cards_refused_ignores_entry_line_on_a_parse_failed_card(tmp_path):
    """P33 的核心断言:`parse_status=="ERROR"` 的卡即便(反常地)带着 `entry_source=="line"`,
    也不能被算进「有卡写过入场行」—— 真实解析器在 ERROR 路径上恒 `entry_source=None`
    (`l4/parsers._empty_card_context`),这里用一张违反该不变量的合成卡直接压强判据本身:
    实现如果只检查全体候选的 `entry_source=="line"`(不过滤 `parse_status`),这条会红。"""
    cards = (_card("UNKNOWN", source="line", status="ERROR", kind="unknown"),
             _card("CONDITIONAL", source="prose", status="OK", kind="full"))
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards))
    assert doc["wall"] == "cards_silent"
    assert doc["cards"]["parse_failed"] == 1


def test_parser_broken_reads_as_broken_not_silently_ok(tmp_path):
    """P33:解析器整体故障(全体候选 `parse_status=="ERROR"`)时,`wall` 仍走 vacuous 的
    `cards_silent`(不新开第五个值),但 `cards.parse_failed` 必须等于总数 —— 这是「读起来
    像坏了,不像沉默」的落点:读 JSON 的人一眼能分清是没人写、还是没人能读。"""
    cards = tuple(_card("UNKNOWN", source=None, status="ERROR", kind="unknown") for _ in range(4))
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards))
    assert doc["wall"] == "cards_silent"
    assert doc["cards"]["parse_failed"] == 4 == doc["cards"]["n"]


# ───────────────────────────── gates 墙 ─────────────────────────────

def test_wall_gates_when_allowed_cards_all_vetoed(tmp_path):
    cards = (_card("ALLOWED", source="line", status="OK", kind="full"),)
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards, gates_ok=False))
    assert doc["wall"] == "gates" and doc["gates"]["data_a_ticker"] == 1 and doc["buy"]["blocked"] is True


def test_wall_gates_when_the_only_allowed_eligible_candidate_is_not_in_pool(tmp_path):
    """fix round 1 finding ③:`eligible` 只问硬门,composite 池下过硬门的 ALLOWED 候选仍可能
    因为不是证据席被 `relative_buy.py` 判 `in_pool=False`、从未真正有机会当 BUY —— `wall`
    不能把这种情形读成 `none`(等于宣称出过一只 BUY)。用两张卡(in_pool 不同)锁住这个
    组合,不是只造一张。"""
    cards = (_card("ALLOWED", source="line", status="OK", kind="full"),
             _card("CONDITIONAL", source="prose", status="OK", kind="full"))
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards, in_pool=(False, True)))
    assert doc["wall"] == "gates"
    assert doc["cards"]["allowed"] == 1, "计数不看 in_pool——仍诚实数「写了允许的卡」有几张"


def test_wall_none_when_the_allowed_eligible_candidate_is_in_pool(tmp_path):
    """对照组:同一张 ALLOWED 卡,`in_pool=True` 时(第二张卡陪衬,证明多候选下判定仍对)
    才该读 `none`——防止 finding ③ 的修法矫枉过正,把 in_pool=True 也误判成 gates。"""
    cards = (_card("ALLOWED", source="line", status="OK", kind="full"),
             _card("CONDITIONAL", source="prose", status="OK", kind="full"))
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards, in_pool=(True, True), tier="A"))
    assert doc["wall"] == "none"


def test_gates_no_redflag_splits_card_prohibited_from_other_causes(tmp_path):
    """2026-09-25 controller 追加裁定(派工消息,不在 addendum 文件里):`hard_gate.
    no_redflag` 六个否决因里只有「卡面入场=禁止」(`entry_stance=PROHIBITED`)是本波新增,
    其余五个是老因,混在一个整数里本波自己的效果就看不见。子集按 `excluded[].detail` 的固定
    字面子串匹配(与 `data_a_ticker` 拆 `data_a` 同一手法),`no_redflag` 本身仍是全量。"""
    extra = (
        {"code": "600010", "reason": "hard_gate.no_redflag",
         "detail": "卡面入场=禁止(entry_stance=PROHIBITED;v4.0 tiering)"},
        {"code": "600011", "reason": "hard_gate.no_redflag",
         "detail": "research_rating=Underweight(v3.0 起 UW/Sell 一律否决)"},
        {"code": "600012", "reason": "hard_gate.no_redflag", "detail": "ST/退市标记:'ST某某'"},
    )
    doc = build_buyability(_scan(tmp_path, extra_excluded=extra))
    assert doc["gates"]["no_redflag"] == 3
    assert doc["gates"]["no_redflag_card_prohibited"] == 1


# ───────────────────────────── none(出了 A 级) ─────────────────────────────

def test_wall_none_when_a_tier_buy_exists(tmp_path):
    cards = (_card("ALLOWED", source="line", status="OK", kind="full"),)
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards, tier="A"))
    assert doc["wall"] == "none" and doc["buy"] == {"tier": "A", "code": "600000", "blocked": False}


# ───────────────────────────── 落盘 ─────────────────────────────

def test_write_buyability_lands_the_file(tmp_path):
    scan = _scan(tmp_path, l2_knife=0.25)
    p = write_buyability(scan)
    assert p == scan / BUYABILITY_FILENAME
    assert json.loads(p.read_text(encoding="utf-8"))["schema_version"] == 1


# ───────────── fix round 1 finding ①:没有决策文档就没有 wall,不许编一个 ─────────────

def test_wall_is_none_when_decision_document_is_missing(tmp_path):
    """mutation-proof 靶子:菜单健康(若走旧逻辑 `cards["allowed"]==0` 会直接算成
    `cards_silent`)+ 决策文档整份不存在 —— 必须读到 `wall is None`,不是一个「看起来
    合理」的 cards_silent。`cards`/`gates`/`buy` 同理不得编成零值结构。"""
    scan = _scan(tmp_path, l2_knife=0.25, write_decision=False)
    doc = build_buyability(scan)
    assert doc["wall"] is None
    assert doc["cards"] is None and doc["gates"] is None and doc["buy"] is None
    assert doc["menu"]["l2_knife"] == 0.25, "menu 是独立于决策文档的真实测量,不该被一并清空"


def test_wall_is_none_when_decision_document_is_unreadable(tmp_path):
    """「不可读」与「缺席」同一处置(`_json` 对坏 JSON 恒返回 `None`)。"""
    scan = _scan(tmp_path, l2_knife=0.25, write_decision=False)
    (scan / "_relative_buy_decision.json").write_text("{not valid json", encoding="utf-8")
    doc = build_buyability(scan)
    assert doc["wall"] is None


def test_wall_is_none_when_decision_document_is_an_empty_object(tmp_path):
    """退化态(生产从不会写出,但保守地也不算「有」):`{}` 是 falsy,和缺席同一处置。"""
    scan = _scan(tmp_path, l2_knife=0.25, write_decision=False)
    (scan / "_relative_buy_decision.json").write_text("{}", encoding="utf-8")
    doc = build_buyability(scan)
    assert doc["wall"] is None


# ───────── fix round 1 finding ②:菜单席位缺源是 None,不是假 0 ─────────

def test_menu_sector_seats_is_none_not_zero_when_l2_lacks_the_column(tmp_path):
    scan = _scan(tmp_path, l2_knife=0.25)
    l2 = pd.read_csv(scan / "L2_gbdt_top200.csv")
    l2 = l2.drop(columns=["sector_seat"])
    l2.to_csv(scan / "L2_gbdt_top200.csv", index=False)
    doc = build_buyability(scan)
    assert doc["menu"]["sector_seats"] is None


def test_menu_composite_seats_is_none_not_zero_when_finalists_lacks_guard_column(tmp_path):
    scan = _scan(tmp_path, l2_knife=0.25)
    pd.DataFrame({"code": ["600001"]}).to_csv(scan / "finalists.csv", index=False)
    doc = build_buyability(scan)
    assert doc["menu"]["composite_seats"] is None
