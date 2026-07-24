"""event 召回路契约(Wave4 Task 3):事件门 + 非涨幅排序 + 缺列降级 + 默认不启用。"""
import pandas as pd

from autoresearch.scan.recall import CHANNEL_DEFAULTS, build, registered_channels


def _frame(n=6):
    return pd.DataFrame({
        "code": [f"00000{i}" for i in range(n)],
        "composite": [50.0 + i for i in range(n)],
        "pct_1d": [10.0, 9.5, 0.2, -1.0, 0.5, 0.1],     # 涨幅:不得成为排序依据
        "amount_yi": [5.0] * n,
        "ev_pos": [0.0, 0.0, 5.0, 3.0, 1.0, 0.0],
        "ev_rep_impl": [0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
        "ev_holder_in": [0.0, 0.0, 1.0, 1.0, 0.0, 0.0],
        "ev_surv_n": [0.0, 0.0, 3.0, 2.0, 1.0, 0.0],
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
    assert got[0] == "000002", "按 ev_pos 降序(5 > 3 > 1)"
    assert got[:3] == ["000002", "000003", "000004"]


def test_event_channel_missing_cols_degrades_to_empty():
    """事件列缺失(取数全失败)→ 空帧,与其余 10 路同款降级契约。"""
    f = _frame().drop(columns=["ev_pos"])
    out = build("event")(f, "2026-07-24", 10)
    assert len(out) == 0 and list(out.columns) == ["code", "channel_rank", "channel_score"]


def test_event_channel_all_zero_degrades_to_empty():
    """事件列在但全 0(三腿失败后 attach 填 0)→ 空帧,不得召回一堆零事件票。"""
    f = _frame()
    f["ev_pos"] = 0.0
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
