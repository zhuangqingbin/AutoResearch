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
    """事件列在但全 0(三腿失败后 attach 填 0)→ 空帧,不得召回一堆零事件票。"""
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
    """新信号入场纪律:默认不进生产 recall_channels(scan_config 未列 = 不启用)。"""
    import json
    import re
    from pathlib import Path
    raw = Path(".claude/skills/scan-market/scan_config.jsonc").read_text(encoding="utf-8")
    cfg = json.loads(re.sub(r"//.*", "", raw))
    assert "event" not in (cfg.get("funnel", {}).get("recall_channels") or []), \
        "event 路须累计 ≥10 日 unique_excess_t2 为正、经人批才可启用"
