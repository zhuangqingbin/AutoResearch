import pandas as pd

from autoresearch.scan.market import render_fallback_pulse, render_funnel_readout


def test_fallback_pulse_from_pack():
    pack = {"regime": {"label": "risk_off"},
            "breadth": {"above_ma60": 0.27, "med_pct_60d": -13.0, "falling_knife": 0.42},
            "sectors": {"red": [{"industry": "半导体"}], "black": [{"industry": "软件开发"}]}}
    s = render_fallback_pulse(pack)
    assert "避险" in s and "半导体" in s


def test_fallback_pulse_empty_regime():
    assert render_fallback_pulse({"regime": None}) == ""


def _mk_details(tmp_path, cards, verify_rows=None, finalists=None, l1=None):
    d = tmp_path / "s"
    (d / "details").mkdir(parents=True)
    for code, rating in cards.items():
        (d / "details" / f"{code}.md").write_text(f"## 卡\n**Rating**: {rating}\n", encoding="utf-8")
    if finalists:
        pd.DataFrame(finalists).to_csv(d / "finalists.csv", index=False)
    if verify_rows is not None:
        pd.DataFrame(verify_rows).to_csv(d / "verify.csv", index=False)
    if l1 is not None:
        pd.DataFrame(l1).to_csv(d / "L1_scored_full.csv", index=False)
    return d


def test_funnel_readout_zero_buy(tmp_path):
    d = _mk_details(tmp_path, {"000001": "Hold", "000002": "Underweight"},
                    l1=[{"code": "000001", "above_ma60": 0.0, "pct_60d": -25.0}] * 4)
    s = render_funnel_readout(d)
    assert "0 买" in s and "避险" in s


def test_funnel_readout_with_buys(tmp_path):
    d = _mk_details(tmp_path, {"000001": "Overweight", "000002": "Hold"},
                    finalists=[{"code": "000001", "name": "测试股"}])
    s = render_funnel_readout(d)
    assert "1 买" in s and "测试股" in s


def test_funnel_readout_verify_downgrade(tmp_path):
    d = _mk_details(tmp_path, {"000001": "Overweight"},
                    verify_rows=[{"code": "000001", "verdict": "降级", "bull": "", "bear": "PB高",
                                  "trigger": "中报", "consensus": ""}],
                    finalists=[{"code": "000001", "name": "胜宏"}])
    s = render_funnel_readout(d)
    assert "0 买" in s and "观察单" in s     # OW 被 skeptic 降级 → 剔出买单、进观察单
