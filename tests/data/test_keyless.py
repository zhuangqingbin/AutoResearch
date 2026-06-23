"""免 token 直连源:同花顺一致预期 EPS 解析 + 取数桩 + 降级。NO network(合成 HTML)。"""
from __future__ import annotations

from autoresearch.data.keyless import (
    consensus_eps_block,
    fetch_consensus_eps,
    fwd_eps,
    parse_consensus_eps,
)

# 真实 worth.html 内嵌的 yjycData 形态(SJ=实际 / YC=预测)
_HTML = (
    '<div class="bd"><div id="yjycData" class="none">'
    '[["2024","68.64","862.28","SJ"],["2025","65.66","823.20","SJ"],'
    '["2026","68.82","861.83","YC"],["2027","72.61","909.21","YC"]]'
    "</div><div id='yjycChart'></div></div>"
)


def test_parse_consensus_eps_splits_actual_and_forecast():
    df = parse_consensus_eps(_HTML)
    assert list(df.columns) == ["year", "eps", "np_yi", "kind"]
    assert len(df) == 4
    assert set(df[df["kind"] == "YC"]["year"]) == {"2026", "2027"}     # 预测年
    assert set(df[df["kind"] == "SJ"]["year"]) == {"2024", "2025"}     # 实际年
    assert abs(float(df[df["year"] == "2026"]["eps"].iloc[0]) - 68.82) < 1e-9
    assert abs(float(df[df["year"] == "2027"]["np_yi"].iloc[0]) - 909.21) < 1e-9


def test_parse_consensus_eps_no_blob_degrades_empty():
    df = parse_consensus_eps("<html><body>无预测块</body></html>")
    assert df.empty and list(df.columns) == ["year", "eps", "np_yi", "kind"]


def test_fwd_eps_picks_forecast_year():
    df = parse_consensus_eps(_HTML)
    assert abs(fwd_eps(df, 2026) - 68.82) < 1e-9
    assert abs(fwd_eps(df, "2027") - 72.61) < 1e-9
    assert fwd_eps(df, 2030) is None          # 无该年预测
    assert fwd_eps(df, 2024) is None          # 2024 是实际(SJ)非预测


def test_fetch_consensus_eps_uses_injected_get():
    out = fetch_consensus_eps("600519", get=lambda *a, **k: _HTML)
    assert abs(fwd_eps(out, 2026) - 68.82) < 1e-9


def test_fetch_consensus_eps_degrades_on_error():
    def boom(*a, **k):
        raise RuntimeError("no net")
    out = fetch_consensus_eps("600519", get=boom)
    assert out.empty and list(out.columns) == ["year", "eps", "np_yi", "kind"]


def test_consensus_eps_block_with_price_shows_fwd_pe():
    out = consensus_eps_block("600519.SS", 1222.45, fetch=lambda c: parse_consensus_eps(_HTML))
    assert "fwd-PE" in out and "17.8" in out and "2026" in out      # 1222.45/68.82 ≈ 17.8x


def test_consensus_eps_block_no_price_lists_eps_no_pe():
    out = consensus_eps_block("600519.SS", None, fetch=lambda c: parse_consensus_eps(_HTML))
    assert "68.82" in out and "fwd-PE" not in out                   # 无价 → 列 EPS 不算 PE


def test_consensus_eps_block_degrades_on_empty():
    out = consensus_eps_block("600519.SS", 1222.45, fetch=lambda c: parse_consensus_eps("<html></html>"))
    assert "降级" in out
