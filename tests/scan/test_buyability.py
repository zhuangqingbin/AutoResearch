"""不可买归因(2026-09-24 §2.7 + 2026-09-25 controller 派工附录 P22/P33/P35 + 当轮
no_redflag 拆分裁定 + 2026-09-25 复核轮二 I2/I4/I5):四堵墙 menu / cards_silent /
cards_refused / gates,第一堵撞上的就是 wall。

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

复核轮二(2026-09-25)追加 fixture 注记:`_scan` 新增三个专供 I4(b)/I4(c)/I5 用的参数——
`pinned_data_a_failures`(追加 N 只📌持仓候选,各自因票级 data_a 被 `_hard_gate` 否决)、
`tiering`(写/不写决策文档顶层同名键)、`sector_seats`(非 None 时落一份 `_sector_seats.json`
哨兵文件)。默认值全部不改变旧用例的行为(见各参数自己的 docstring)。
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
         extra_excluded: tuple[dict, ...] = (), write_decision: bool = True,
         pinned_data_a_failures: int = 0, tiering: bool | None = None,
         sector_seats: list[dict] | None = None, rebalance_close: int | None = None) -> Path:
    """`cards`:每个候选一份 `card_context`(用 `_card()` 造)。`in_pool`:每个候选一个
    bool,与 `cards` 等长,缺省全 True(`relative_buy.py` 的 `finalists` 池下恒 True;
    `composite` 池下=是否证据席 —— fix round 1 finding ③ 测试用,与 `cards` 分开传避免把
    两个维度耦合进 `_card()`)。`gates_ok=False` 时给每张卡按 Task 1 的文案造一条票级
    `hard_gate.data_a` 否决(与原 brief 语义一致)。`extra_excluded` 用来叠加与
    `cards`/`gates_ok` 无关的其它硬门否决行(no_redflag 拆分测试用)。
    `write_decision=False`:不落 `_relative_buy_decision.json`(fix round 1 finding ①
    「决策文档缺席」测试专用,其余参数在这条路径下不生效)。

    `pinned_data_a_failures`(I4(b)专用,默认 0=不变旧行为):追加 N 只📌持仓候选,各自
    因票级 data_a 被 `_hard_gate` 否决——镜像真实产物「pinned 票一样要过硬门」的事实,
    让 `gates.n`(全体候选)与 `cards.n`(非📌候选)读出不同的数。
    `tiering`(I4(c)专用,默认 `None`):非 None 时把值原样写进决策文档顶层同名键;
    保持默认 `None` 时**不写这个键**,模拟没有这个概念的老 schema(读出来应为 `None`,
    不是 `False`)。
    `sector_seats`(I5 专用,默认 `None`=不落该文件):非 None 时落一份 `_sector_seats.json`
    (`{"seats": sector_seats}`),模拟行业席位特性"开着"那天 universe.py 无条件写下的
    哨兵文件。
    `rebalance_close`(task-12 附带修复 A 专用,默认 `None`):非 None 时把值写进
    `veto_accounting.by_gate.rebalance_close`,镜像生产在 `relative_buy.py` 里的真实行为——
    `by_gate` 字典推导式只遍历**当次运行实际生效**的硬门元组(`_hard_gates(rebalance_gate)`),
    旋钮关/老 schema 时这个键根本不存在,不是存在且为 0。保持默认 `None` 时**不写这个键**,
    模拟旋钮关或没有第五门概念的文档(读出来应为 `None`,不是 `0`)。
    """
    scan = tmp_path / "2026-09-17"
    scan.mkdir()
    _frame(1000, l0_knife, l0_healthy).to_csv(scan / "L1_scored_full.csv", index=False)
    l2 = _frame(200, l2_knife, l2_healthy)
    l2["sector_seat"] = False
    l2.to_csv(scan / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame({"code": ["600001"], "guard": ["composite_seat"]}).to_csv(scan / "finalists.csv", index=False)
    if sector_seats is not None:
        (scan / "_sector_seats.json").write_text(
            json.dumps({"schema_version": 1, "date": "2026-09-17", "seats": sector_seats}),
            encoding="utf-8")
    if not write_decision:
        return scan
    pool_flags = in_pool if in_pool else tuple(True for _ in cards)
    cands = [{"code": f"6000{i:02d}", "eligible": gates_ok, "pinned": False, "in_pool": p,
              "hard_gate": {"tradable": True, "data_a": gates_ok, "contract": True, "no_redflag": True},
              "card_context": ctx} for i, (ctx, p) in enumerate(zip(cards, pool_flags, strict=True))]
    pinned_cands = [{"code": f"9000{i:02d}", "eligible": False, "pinned": True, "in_pool": True,
                     "hard_gate": {"tradable": True, "data_a": False, "contract": True, "no_redflag": True},
                     "card_context": _card("UNKNOWN")} for i in range(pinned_data_a_failures)]
    excluded = list(extra_excluded)
    no_redflag_n = sum(1 for e in excluded if e.get("reason") == "hard_gate.no_redflag")
    if not gates_ok:
        excluded = excluded + [{"code": c["code"], "reason": "hard_gate.data_a",
                                "detail": f"stage l4_{c['code']} 失败(票级 data_a)"} for c in cands]
    excluded = excluded + [{"code": c["code"], "reason": "hard_gate.data_a",
                            "detail": f"stage l4_{c['code']} 失败(票级 data_a)"} for c in pinned_cands]
    data_a_n = (len(cands) if not gates_ok else 0) + len(pinned_cands)
    by_gate = {"data_a": data_a_n, "contract": 0, "no_redflag": no_redflag_n, "tradable": 0}
    if rebalance_close is not None:
        by_gate["rebalance_close"] = rebalance_close
    doc = {"blocked": not gates_ok, "buys": ([{"code": "600000", "tier": tier}] if gates_ok and tier else []),
           "candidates": cands + pinned_cands, "excluded": excluded,
           "veto_accounting": {"by_gate": by_gate}}
    if tiering is not None:
        doc["tiering"] = tiering
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
                            "entry_line": 0, "blind": 0, "parse_failed": 0, "earlystop": 2, "full": 1}


def test_wall_cards_refused_when_a_parsed_card_carries_the_entry_line(tmp_path):
    """至少一张**解析成功**的卡写过机读入场行、但不是允许(P35:早停卡的模板只许
    `禁止|条件`)—— 研究判断,不是接线缺口。"""
    cards = (_card("PROHIBITED", source="line", status="OK", kind="earlystop"),
             _card("UNKNOWN", source="prose", status="OK", kind="full"))
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards))
    assert doc["wall"] == "cards_refused"
    assert doc["cards"] == {"n": 2, "allowed": 0, "conditional": 0, "prohibited": 1, "unknown": 1,
                            "entry_line": 1, "blind": 0, "parse_failed": 0, "earlystop": 1, "full": 1}


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


# ───────────────── entry_line 计数(I2,2026-09-25 复核轮二):gate L1a 的分母 ─────────────────

def test_cards_entry_line_counts_only_parsed_cards_carrying_the_machine_readable_line(tmp_path):
    """I2:gate L1a 读『非盲卡里 entry_source=="line" 占比』,但产物此前只有一个内部布尔
    `has_line`,没有任何字段把这个计数写出来 —— 门读的是一个没人生产的数。`cards.entry_line`
    补上这个计数,口径与 `has_line`/`wall` 判定同源(P33):只数**解析成功**(OK/PARTIAL)
    的卡,ERROR 卡即便(反常地)带着 `entry_source=="line"` 也不算——parser 故障不能被
    错记成『写过入场行』。"""
    cards = (_card("ALLOWED", source="line", status="OK", kind="full"),
             _card("CONDITIONAL", source="line", status="PARTIAL", kind="full"),
             _card("UNKNOWN", source="line", status="ERROR", kind="unknown"),
             _card("PROHIBITED", source="prose", status="OK", kind="full"))
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards))
    assert doc["cards"]["n"] == 4
    assert doc["cards"]["entry_line"] == 2


# ───────────── cards.earlystop / cards.full 的 None vs 0(I4(a),2026-09-25 复核轮二) ─────────────

def test_cards_earlystop_and_full_are_none_when_no_candidate_carries_card_kind(tmp_path):
    """I4(a):`card_kind` 只有 schema 2 才有(`chain_view._card_kind_and_early_stop`
    docstring:「schema 2 才有 card_context.card_kind;schema 1 ... 一律 None」)——schema 1
    的 `card_context` 里压根没有这个键。老日子 `earlystop`/`full` 若仍读 0/0,就与
    『看过、真是零』共用同一个假零,正是这两个计数(P35)当初要防的病。整份(非📌)候选
    没有一张带 `card_kind` → 两者皆 `None`,不是 0。"""
    cards = ({"entry_stance": "CONDITIONAL", "entry_source": "prose", "parse_status": "OK"},
             {"entry_stance": "PROHIBITED", "entry_source": "prose", "parse_status": "OK"})
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards))
    assert doc["cards"]["earlystop"] is None
    assert doc["cards"]["full"] is None


def test_cards_earlystop_and_full_count_normally_when_schema_carries_card_kind(tmp_path):
    """对照组:哪怕只有一张卡带 `card_kind`,当天的 schema 就够新、是真的"看过"了,其余
    (理论上不该发生,防御式覆盖)缺键的候选各自算作既不早停也不满卡,不整体退化成
    None——判据只要求"有一张带这个键",不要求"全部都带"。"""
    cards = (_card("ALLOWED", kind="full"),
             {"entry_stance": "UNKNOWN", "entry_source": None, "parse_status": "ERROR"})
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, cards=cards))
    assert doc["cards"]["earlystop"] == 0
    assert doc["cards"]["full"] == 1


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


def test_gates_n_counts_all_candidates_including_pinned_while_cards_excludes_them(tmp_path):
    """I4(b),2026-09-25 复核轮二:`cards` 数非📌候选(`relative_buy.py` 的 A 级候选按设计
    恒排除 pinned),`gates` 的 by_gate/excluded 计数却是**全体**候选(pinned 票一样要过
    `_hard_gate`,失败一样记进 `excluded`)。2026-09-17 真实产物 `cards.n=9` 旁边印着
    `data_a_day=11`,两个不同人口摆成一行——不强行拉平(拉平会让 cards 掺进永远当不成
    A 级的 pinned 票),改为标注:`gates.n` 显式记 gates 自己数出来的分母,与 `cards.n`
    并排就能看出差几个(这里差 2,恰是两只📌持仓各自票级 data_a 否决)。"""
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, pinned_data_a_failures=2))
    assert doc["cards"]["n"] == 1
    assert doc["gates"]["n"] == 3
    assert doc["gates"]["data_a_ticker"] == 2


# ───────────────── tiering 顶层旗(I4(c),2026-09-25 复核轮二) ─────────────────

def test_tiering_flag_is_read_from_the_decision_document_when_true(tmp_path):
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, tiering=True))
    assert doc["tiering"] is True


def test_tiering_flag_is_read_from_the_decision_document_when_false(tmp_path):
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, tiering=False))
    assert doc["tiering"] is False


def test_tiering_flag_is_none_for_legacy_documents_that_predate_the_key(tmp_path):
    """I4(c):`wall=="none"` 与 `gates.no_redflag_card_prohibited==0` 在 tiering 开/关下是
    两个不同世界(开:入场门真的跑过、没撞;关:入场门压根没跑)——决策文档没有这个键
    (schema 1 / 更早的 schema 2)时,不得默认猜成 `False`(那会把"没有这个概念"读成"关",
    两者对"入场门有没有跑过"含义完全不同)。"""
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25))   # 默认不传 tiering → 文档没有这个键
    assert doc["tiering"] is None


# ───────────────── gates.rebalance_close(task-12 附带修复 A) ─────────────────


def test_gates_rebalance_close_reads_the_fifth_gate_tally_when_present(tmp_path):
    """第五门(`relative_buy.rebalance_gate` 开)命中数要能读到这份不可买归因产物里——
    此前 `gates{}` 只选读 `contract`/`no_redflag` 两个键,第五门的计数进了决策文件的
    `veto_accounting.by_gate` 却从没被 `buyability.py` 转抄出来,`⑤` 门否决的日子在这份
    产物里读起来和其它三门否决完全一样看不出「是哪一道门」。"""
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25, rebalance_close=2))
    assert doc["gates"]["rebalance_close"] == 2


def test_gates_rebalance_close_is_none_not_zero_when_the_fifth_gate_never_ran(tmp_path):
    """同 `tiering` 的 I4(c) 纪律:旋钮关/老 schema 时 `by_gate` 字典里压根没有
    `rebalance_close` 这个键(`_veto_accounting_block` 只遍历当次真实生效的硬门元组)——
    这与"门跑过、命中数是 0"是两个不同世界,不得共用同一个假 0。"""
    doc = build_buyability(_scan(tmp_path, l2_knife=0.25))    # 默认不传 → by_gate 没有这个键
    assert doc["gates"]["rebalance_close"] is None


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
    合理」的 cards_silent。`cards`/`gates`/`buy` 同理不得编成零值结构。`tiering`(I4(c),
    2026-09-25 复核轮二)同理:没有决策文档就没有"那一次运行",不得编成 `False`。"""
    scan = _scan(tmp_path, l2_knife=0.25, write_decision=False)
    doc = build_buyability(scan)
    assert doc["wall"] is None
    assert doc["cards"] is None and doc["gates"] is None and doc["buy"] is None
    assert doc["tiering"] is None
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
    """2026-09-25 复核轮二(I5)仍然成立:列缺席 + 没有 `_sector_seats.json` 哨兵文件
    (`_scan()` 默认不落它)—— 两个信号都缺席,真的是 None。"""
    scan = _scan(tmp_path, l2_knife=0.25)
    l2 = pd.read_csv(scan / "L2_gbdt_top200.csv", dtype={"code": str})
    l2 = l2.drop(columns=["sector_seat"])
    l2.to_csv(scan / "L2_gbdt_top200.csv", index=False)
    doc = build_buyability(scan)
    assert doc["menu"]["sector_seats"] is None


def test_menu_sector_seats_is_zero_not_none_when_the_sentinel_says_enabled_but_empty(tmp_path):
    """I5,2026-09-25 复核轮二:特性真的开着,当日 `pick_sector_seats` 挑不出一个合格行业
    ——`_inject_sector_seats_l1` 对空 `seats` 走"原样返回"分支,L2 CSV 同样不会有
    `sector_seat` 列(和"关着"读起来完全一样)。但 universe.py 在"开着"分支内无条件落了
    `_sector_seats.json`(`seats: []`),这是『看过、真是零』,不能读成 None —— 这正是
    I5 要修的那个二义性。"""
    scan = _scan(tmp_path, l2_knife=0.25)
    l2 = pd.read_csv(scan / "L2_gbdt_top200.csv", dtype={"code": str})
    l2 = l2.drop(columns=["sector_seat"])
    l2.to_csv(scan / "L2_gbdt_top200.csv", index=False)
    (scan / "_sector_seats.json").write_text(
        json.dumps({"schema_version": 1, "date": "2026-09-17", "seats": []}), encoding="utf-8")
    doc = build_buyability(scan)
    assert doc["menu"]["sector_seats"] == 0


def test_menu_sector_seats_reads_the_sentinel_seats_length_when_the_column_is_absent(tmp_path):
    """哨兵在场且非空:即便(防御式覆盖)L2 列因故缺席,也读 `len(seats)`,不是 0/None。"""
    scan = _scan(tmp_path, l2_knife=0.25)
    l2 = pd.read_csv(scan / "L2_gbdt_top200.csv", dtype={"code": str})
    l2 = l2.drop(columns=["sector_seat"])
    l2.to_csv(scan / "L2_gbdt_top200.csv", index=False)
    (scan / "_sector_seats.json").write_text(
        json.dumps({"schema_version": 1, "date": "2026-09-17",
                    "seats": [{"code": "600001"}, {"code": "600002"}]}), encoding="utf-8")
    doc = build_buyability(scan)
    assert doc["menu"]["sector_seats"] == 2


def test_menu_sector_seats_prefers_the_l2_column_when_present(tmp_path):
    """列在场时直接信列(在场必然来自非空 seats,真实产物里两者恒相等);不必要求调用方
    额外落一份哨兵文件才能读出真值——这是最常见的"开着、真的挑中了"的日子,不能因为加了
    哨兵回退就退化。"""
    scan = _scan(tmp_path, l2_knife=0.25)
    l2 = pd.read_csv(scan / "L2_gbdt_top200.csv", dtype={"code": str})
    l2.loc[0, "sector_seat"] = True
    l2.loc[1, "sector_seat"] = True
    l2.to_csv(scan / "L2_gbdt_top200.csv", index=False)
    doc = build_buyability(scan)
    assert doc["menu"]["sector_seats"] == 2


def test_menu_composite_seats_is_none_not_zero_when_finalists_lacks_guard_column(tmp_path):
    scan = _scan(tmp_path, l2_knife=0.25)
    pd.DataFrame({"code": ["600001"]}).to_csv(scan / "finalists.csv", index=False)
    doc = build_buyability(scan)
    assert doc["menu"]["composite_seats"] is None
