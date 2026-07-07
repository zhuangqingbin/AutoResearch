import pandas as pd

from autoresearch.data import tushare_source
from autoresearch.scan.agents.l4_card import _seat_mark, fetch_seats


def _bulk_stub(dates):
    # 两天龙虎榜:600000 机构专用净买 +500万/-200万;300001 游资营业部 +80万
    def frame(rows):
        return pd.DataFrame(rows)

    return {
        dates[0]: frame([{"ts_code": "600000.SH", "exalter": "机构专用", "net_buy": 5_000_000},
                         {"ts_code": "300001.SZ", "exalter": "某某营业部", "net_buy": 800_000}]),
        dates[-1]: frame([{"ts_code": "600000.SH", "exalter": "机构专用", "net_buy": -2_000_000}]),
    }


def test_fetch_seats_aggregates_inst_vs_retail(tmp_path, monkeypatch):
    # 缺 code 分支在 bulk_fn 之前先探 resolve_momentum_dates/_trade_days/_pro(真 tushare);
    # 全部 stub 掉 → 离线 hermetic,bulk_fn 才会真正被调用。
    monkeypatch.setattr(tushare_source, "resolve_momentum_dates",
                        lambda *a, **k: ("20260707", "20260707"), raising=True)
    monkeypatch.setattr(tushare_source, "_trade_days",
                        lambda *a, **k: ["20260701", "20260707"], raising=True)
    monkeypatch.setattr(tushare_source, "_pro", lambda *a, **k: object(), raising=True)
    d = tmp_path / "2026-07-07"
    d.mkdir()
    pd.DataFrame({"code": ["600000", "300001"]}).to_csv(d / "finalists.csv", index=False)
    out = fetch_seats(d, bulk_fn=lambda dates: _bulk_stub(["20260701", "20260707"]))
    row = out.set_index("code").loc["600000"]
    assert row["inst_net_wan"] == 300      # (5_000_000 - 2_000_000)/1e4
    assert row["n_appear"] == 2
    assert (d / "seats.csv").exists()
    r2 = out.set_index("code").loc["300001"]
    assert r2["retail_net_wan"] == 80 and r2["inst_net_wan"] == 0


def test_seat_mark_flags_inst_contra_indicator(tmp_path):
    pd.DataFrame({"code": ["600000"], "inst_net_wan": [300.0],
                  "retail_net_wan": [80.0], "n_appear": [2]}).to_csv(tmp_path / "seats.csv", index=False)
    s = _seat_mark(tmp_path, "600000")
    assert "机构" in s and "反指" in s and "游资" in s
    assert _seat_mark(tmp_path, "000999") == ""      # 未上榜 → 空
    assert _seat_mark(tmp_path / "nope", "600000") == ""  # 无 seats.csv → 空
