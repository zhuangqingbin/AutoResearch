"""event 召回路契约(Wave4 Task 3):事件门 + 非涨幅排序 + 缺列降级 + 默认不启用。

Review Round 1(2026-07-25)Fix 5:排序列从 `ev_pos` 改成 `ev_hard`(真实公司行为)——
`_frame()` 与排序断言同步更新,见 `.superpowers/sdd/w4-task-2-report.md` Fix Round 1。
"""
import pandas as pd

from autoresearch.scan.recall import CHANNEL_DEFAULTS, build, registered_channels


def _frame(n=6):
    return pd.DataFrame({
        "code": [f"00000{i}" for i in range(n)],
        "composite": [50.0 + i for i in range(n)],
        "pct_1d": [10.0, 9.5, 0.2, -1.0, 0.5, 0.1],     # 涨幅:不得成为排序依据
        "amount_yi": [5.0] * n,
        # 000002 = 双硬事件(回购实施+增持,ev_hard=2,surv_n=3 封顶贡献 1 → ev_pos=3)。
        # 000003 = 纯调研(0 硬事件,ev_hard=0,surv_n=1 封顶贡献 1 → ev_pos=1)。
        # 000004 = 单硬事件(增持,ev_hard=1,无调研 → ev_pos=1)。
        # 000003 与 000004 在 ev_pos 上打平(都是 1)但 ev_hard 不同,且 000003(纯调研)
        # 排在原始行序里 000004(真事件)**之前**——若排序退回按 ev_pos(打平时 stable
        # 排序保原序)会把 000003 排到 000004 前面;只有真按 ev_hard 排序才会把 000004
        # 排到 000003 前面,逮住"排序列被换回 ev_pos"这个变异。
        "ev_pos": [0.0, 0.0, 3.0, 1.0, 1.0, 0.0],
        "ev_hard": [0.0, 0.0, 2.0, 0.0, 1.0, 0.0],
        "ev_rep_impl": [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
        "ev_holder_in": [0.0, 0.0, 1.0, 0.0, 1.0, 0.0],
        "ev_surv_n": [0.0, 0.0, 3.0, 1.0, 0.0, 0.0],
        "ev_rep_plan": [0.0] * n, "ev_holder_de": [0.0] * n,
    })


def test_event_channel_registered_with_spec():
    assert "event" in registered_channels()
    spec = CHANNEL_DEFAULTS["event"]
    assert spec.quota == 80 and spec.floor == 20


def test_event_channel_gates_on_events_not_price():
    out = build("event")(_frame(), "2026-07-24", 10)
    got = list(out["code"])
    assert "000000" not in got and "000001" not in got, "涨幅最大但无事件 → 不得召回"
    assert got == ["000002", "000004", "000003"], (
        "排序须用 ev_hard(真实公司行为),不是 ev_pos——000003(纯调研,ev_hard=0)与"
        "000004(单一硬事件,ev_hard=1)在 ev_pos 上打平(都是 1),若排序退回按 ev_pos,"
        "stable 排序会保留原始行序,把先出现的纯调研票 000003 排到 000004 前面"
    )


def _tie_frame():
    """5 只**同为单一硬事件**(ev_hard=1)的票 —— 真湖 07-21 去重后门内分布是
    `{3:12, 2:26, 1:218}`,即绝大多数候选都落在这一层。故意让**行序与 composite 完全相反**
    (行序 000010→000014 是 composite 升序),这样"按行序切"和"按 composite 切"给出的答案
    正好相反,测试才有鉴别力。"""
    return pd.DataFrame({
        "code": [f"0000{10 + i}" for i in range(5)],
        "composite": [10.0, 20.0, 30.0, 40.0, 50.0],
        "ev_pos": [1.0] * 5, "ev_hard": [1.0] * 5,
        "ev_rep_impl": [0.0] * 5, "ev_rep_plan": [0.0] * 5,
        "ev_holder_in": [1.0] * 5, "ev_holder_de": [0.0] * 5, "ev_surv_n": [0.0] * 5,
    })


def test_event_channel_breaks_ties_by_composite():
    """R2-I3 / R1-I-1:`ev_hard` 是离散小整数,`gate_rank` 只有一个排序键且 `kind="stable"`
    ⇒ 并列层内的顺序 = **帧行序**;而进 `gate_rank` 的是 `build_market_frame` 的原始 ts_code
    序(`recall_select` 跑在 `scored.sort_values("composite")` 之前)= 事实上任意。

    真湖 07-21(按公告去重后)门内 `{3:12, 2:26, 1:218}`,quota=80 ⇒ 38 席按信号排、
    **42 席(52%)从 218 只并列票里靠代码序切**;换帧行序实测入选名单差 17 只,且 code 序
    系统性偏好 000/002 前缀。这条锁住"并列票按 composite 决胜,不按行序"。
    """
    out = build("event")(_tie_frame(), "2026-07-25", 3)
    assert list(out["code"]) == ["000014", "000013", "000012"], \
        "全部 ev_hard 并列时须按 composite 降序取,不得按帧行序(行序取到的是 10/11/12)"
    assert out["channel_score"].is_monotonic_decreasing
    assert out["channel_score"].nunique() == 3, "并列层必须被排开(同分=又回到行序抽签)"


def test_event_channel_tiebreak_cannot_overturn_hard_event_count():
    """决胜键必须**只在并列层内**起作用:合计 kicker ≤0.7 < 1,压不过一个整数级差。
    构造最极端的对抗——ev_hard=1 的票同时拿满"有调研"+全场最高 composite,仍须排在
    ev_hard=2、composite 全场最低、且行序更靠后的票之后。"""
    f = _tie_frame()
    f.loc[0, ["ev_hard", "ev_pos", "composite"]] = [2.0, 2.0, 1.0]      # 双硬事件 + 最低分
    f.loc[4, ["ev_pos", "ev_surv_n", "composite"]] = [2.0, 5.0, 99.0]   # 单硬事件 + 调研 + 最高分
    out = build("event")(f, "2026-07-25", 5)
    assert list(out["code"])[0] == "000010", "硬事件件数是主轴,kicker 不得翻盘"
    assert list(out["code"])[1] == "000014", "同为 1 件硬事件时,带调研的排在纯事件票之前"


def test_event_channel_missing_cols_degrades_to_empty():
    """事件列缺失(取数全失败)→ 空帧,与其余 10 路同款降级契约。"""
    f = _frame().drop(columns=["ev_pos"])
    out = build("event")(f, "2026-07-24", 10)
    assert len(out) == 0 and list(out.columns) == ["code", "channel_rank", "channel_score"]


def test_event_channel_missing_ev_hard_degrades_to_empty():
    """ev_pos 列在但 ev_hard 缺(部分升级/旧产物防御)→ 同样空帧降级,不得回退拿
    ev_pos 硬排(那正是 I-5 要治的病)。"""
    f = _frame().drop(columns=["ev_hard"])
    out = build("event")(f, "2026-07-24", 10)
    assert len(out) == 0


def test_event_channel_all_zero_degrades_to_empty():
    """事件列在但全 0(三腿失败后 attach 填 0)→ 空帧,不得召回一堆零事件票。

    **如实标注护栏强度(Review Round 1 m-2)**:本测试锁的是「行为」不是「那一行代码」——
    源码里 `if not bool(mask.any()): return empty_result()` 那条守卫**删掉本测试照绿**
    (reviewer 变异 M2a 实测):`ev_pos` 全 0 ⇒ mask 全 False ⇒ 后面第三条守卫(门内
    `ev_hard` 全 0)接管返回同样的空帧;即便三条守卫全删,`gate_rank` 里 `frame[mask]`
    也是空 ⇒ 还是空帧。那条守卫是**纵深防御 + 意图自述**,不是本测试的被守对象,别把它
    当护栏记账(把冗余当护栏 = 以为有守卫其实没有,本 repo 的老病)。真正被本测试锁死的
    是"全 0 输入不得产出非空召回"这个对外行为,无论它由哪条分支兑现。
    """
    f = _frame()
    f["ev_pos"] = 0.0
    out = build("event")(f, "2026-07-24", 10)
    assert len(out) == 0


def test_event_channel_all_survey_no_hard_events_degrades_to_empty():
    """I-5 修复:门内全是纯调研(ev_hard 全 0,ev_pos 仅靠 min(surv_n,1) 撑到正)→ 空帧,
    不召回一整池只有调研、没有回购/增持的票——正是 07-21 真湖"裸 ev_pos top10 全是
    调研"的病灶,归一化 + 这道门双重防线堵住它。"""
    f = _frame()
    f["ev_hard"] = 0.0                    # 全池硬事件清零,但 ev_pos 仍有正值(靠调研撑)
    out = build("event")(f, "2026-07-24", 10)
    assert len(out) == 0


def test_event_not_enabled_by_default():
    """新信号入场纪律:默认不进生产 recall_channels(scan_config 未列 = 不启用)。

    Wave4 终审 I-1(已实测):原断言 `cfg.get(...) or []` 在 key **缺失**时被 `or []` 空过——
    而"删掉 recall_channels 整行"正是 `scan_config.jsonc` 注释里白纸黑字写着的推荐回滚动作,
    删掉后 `event` 会经 `registered_channels()` 直接上生产主漏斗,此断言却仍然绿。加
    `assert enabled`(照抄姊妹守卫 `test_disabled_channel_buckets_have_zero_floor`)堵死这条路。
    """
    import json
    import re
    from pathlib import Path
    raw = Path(".claude/skills/scan-market/scan_config.jsonc").read_text(encoding="utf-8")
    cfg = json.loads(re.sub(r"//.*", "", raw))
    enabled = cfg.get("funnel", {}).get("recall_channels") or []
    assert enabled, "recall_channels 读不到(= 用全部注册通道,含 event)= 本测试失去意义"
    assert "event" not in enabled, \
        "event 路须累计 ≥10 日 unique_excess_t2 为正、经人批才可启用"
