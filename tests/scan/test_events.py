"""全市场事件计数契约(Wave4 Task 2):湖优先、缺数据降级留痕、正催化口径。"""
import pandas as pd

from autoresearch.scan import events


def _frames():
    """三端点的日帧(字段名与真身一致:holdertrade 用 in_de,repurchase 用 proc)。"""
    return {
        "stk_holdertrade": pd.DataFrame(
            [{"ts_code": "300857.SZ", "in_de": "IN"}, {"ts_code": "002371.SZ", "in_de": "DE"}]),
        "repurchase": pd.DataFrame(
            [{"ts_code": "300857.SZ", "proc": "实施"}, {"ts_code": "600000.SH", "proc": "预案"}]),
        "stk_surv": pd.DataFrame(
            [{"ts_code": "300857.SZ"}, {"ts_code": "300857.SZ"}, {"ts_code": "002371.SZ"}]),
    }


def test_market_event_counts_whole_market_no_code_filter(monkeypatch):
    """全市场:不传 want,湖里有谁就算谁(与 L3 阶段只算 L2-200 的口径相反)。"""
    fr = _frames()
    monkeypatch.setattr(events, "_fetch_day",
                        lambda ep, day, date: fr.get(ep, pd.DataFrame()))
    ev = events.market_event_counts("2026-07-24", lookback_days=1)
    assert set(ev["code"]) == {"300857", "002371", "600000"}
    r = ev.set_index("code").loc["300857"]
    assert r["ev_holder_in"] == 1 and r["ev_rep_impl"] == 1 and r["ev_surv_n"] == 2
    assert ev.set_index("code").loc["002371"]["ev_holder_de"] == 1


def test_ev_pos_excludes_reduction(monkeypatch):
    """正催化口径与 catalyst_ledger._POS 对齐:减持不算正。"""
    fr = _frames()
    monkeypatch.setattr(events, "_fetch_day", lambda ep, day, date: fr.get(ep, pd.DataFrame()))
    ev = events.market_event_counts("2026-07-24", lookback_days=1).set_index("code")
    assert ev.loc["002371", "ev_pos"] == 1          # 只有 surv_n=1;holder_de 不计
    assert ev.loc["300857", "ev_pos"] == 4          # in 1 + impl 1 + surv 2


def test_market_event_counts_all_legs_fail_is_loud(monkeypatch, capsys):
    """三腿全失败 → 空帧 + 显式告警(降级留痕),不静默返回空。"""
    def _boom(ep, day, date):
        raise RuntimeError("no permission")
    monkeypatch.setattr(events, "_fetch_day", _boom)
    ev = events.market_event_counts("2026-07-24", lookback_days=1)
    assert list(ev.columns)[0] == "code" and len(ev) == 0
    assert "事件取数" in (capsys.readouterr().err)


def test_attach_event_cols_parity_and_fill():
    scored = pd.DataFrame({"code": ["300857", "999999"], "composite": [50.0, 60.0]})
    ev = pd.DataFrame({"code": ["300857"], "ev_rep_impl": [1.0], "ev_rep_plan": [0.0],
                       "ev_holder_in": [1.0], "ev_holder_de": [0.0], "ev_surv_n": [2.0],
                       "ev_pos": [4.0]})
    out = events.attach_event_cols(scored, ev)
    assert len(out) == 2 and out.set_index("code").loc["999999", "ev_pos"] == 0.0
    assert list(scored.columns) == ["code", "composite"]      # 不就地改入参
    # 空事件帧 → 全 0 列(parity:下游 channel 拿到列但恒 0 = 空帧降级)
    out2 = events.attach_event_cols(scored, pd.DataFrame({"code": []}))
    assert all(c in out2.columns for c in events.EVENT_COLS)
    assert out2["ev_pos"].sum() == 0.0
