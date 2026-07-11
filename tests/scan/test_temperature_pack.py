"""S1 温度计消费端:market_pack `temperature` 块 + 共用 helper `_temperature_block`。

design: docs/specs/2026-07-11-funnel-p0p1-wave-plan.md Task 5。presence-gated:
csv 缺/当日缺行 → 键不出现(parity,与改前逐字一致)。NO network(CSV_PATH 全程 monkeypatch)。
"""
import pandas as pd

from autoresearch.scan import market


def test_pack_has_temperature_when_csv_present(tmp_path, monkeypatch):
    csv = tmp_path / "temperature.csv"
    pd.DataFrame({"date": ["2026-07-08", "2026-07-09"], "score": [28.0, 41.0],
                  "phase": ["修复", "发酵"]}).to_csv(csv, index=False)
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH", csv)
    market.market_pack_from_frame(None)   # 不崩 = 及格线;下面才是真正的断言
    # 以 market_pack_from_frame 实际入参组装方式为准:temperature 块注入两个 pack 函数共用的 helper
    blk = market._temperature_block("2026-07-09")
    assert blk == {"score": 41.0, "phase": "发酵", "trend5": [28.0, 41.0]}


def test_pack_parity_without_csv(monkeypatch, tmp_path):
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH", tmp_path / "none.csv")
    assert market._temperature_block("2026-07-09") is None


def test_block_none_when_date_missing_from_csv(tmp_path, monkeypatch):
    csv = tmp_path / "temperature.csv"
    pd.DataFrame({"date": ["2026-07-08"], "score": [28.0], "phase": ["修复"]}).to_csv(csv, index=False)
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH", csv)
    assert market._temperature_block("2026-07-09") is None    # 当日缺行 → None(非抛错)


def test_block_trend5_caps_at_five_and_ignores_future_rows(tmp_path, monkeypatch):
    csv = tmp_path / "temperature.csv"
    pd.DataFrame({"date": [f"2026-07-{d:02d}" for d in range(1, 9)],
                  "score": [float(d) for d in range(1, 9)],
                  "phase": ["修复"] * 8}).to_csv(csv, index=False)
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH", csv)
    blk = market._temperature_block("2026-07-06")
    assert blk["score"] == 6.0
    assert blk["trend5"] == [2.0, 3.0, 4.0, 5.0, 6.0]          # 尾 5 行,含未来行(07/07-08)被排除


# ───────────────────────── 两个 pack 组装处的接线 ─────────────────────────


def test_market_pack_injects_temperature(tmp_path, monkeypatch):
    csv = tmp_path / "temperature.csv"
    pd.DataFrame({"date": ["2026-07-09"], "score": [41.0], "phase": ["发酵"]}).to_csv(csv, index=False)
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH", csv)
    scan_dir = tmp_path / "2026-07-09"                          # 目录名 = 日期(与真实 scan 现场同构)
    scan_dir.mkdir()
    pd.DataFrame([{"code": "000001", "pct_60d": 3.0}]).to_csv(scan_dir / "L1_scored_full.csv", index=False)
    pack = market.market_pack(scan_dir)
    assert pack["temperature"] == {"score": 41.0, "phase": "发酵", "trend5": [41.0]}


def test_market_pack_parity_no_temperature_key(tmp_path, monkeypatch):
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH", tmp_path / "none.csv")
    scan_dir = tmp_path / "2026-07-09"
    scan_dir.mkdir()
    pd.DataFrame([{"code": "000001", "pct_60d": 3.0}]).to_csv(scan_dir / "L1_scored_full.csv", index=False)
    pack = market.market_pack(scan_dir)
    assert "temperature" not in pack                            # csv 缺 → 键不出现(parity)


def test_market_pack_from_frame_injects_temperature_with_explicit_date(tmp_path, monkeypatch):
    csv = tmp_path / "temperature.csv"
    pd.DataFrame({"date": ["2026-07-09"], "score": [41.0], "phase": ["发酵"]}).to_csv(csv, index=False)
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH", csv)
    frame = pd.DataFrame([{"code": "000001", "pct_60d": 3.0, "industry": "半导体"}])
    pack = market.market_pack_from_frame(frame, date="2026-07-09")
    assert pack["temperature"] == {"score": 41.0, "phase": "发酵", "trend5": [41.0]}


def test_market_pack_from_frame_no_date_skips_temperature(tmp_path, monkeypatch):
    """库函数不碰 wall-clock:不传 date → 不注入 temperature(即便 csv 有当日行)——
    调用方(如 frame.py CLI)须显式传 date,与老调用点(未传 date)保持向后兼容(parity)。"""
    csv = tmp_path / "temperature.csv"
    pd.DataFrame({"date": ["2026-07-09"], "score": [41.0], "phase": ["发酵"]}).to_csv(csv, index=False)
    monkeypatch.setattr("autoresearch.scan.temperature.CSV_PATH", csv)
    frame = pd.DataFrame([{"code": "000001", "pct_60d": 3.0, "industry": "半导体"}])
    pack = market.market_pack_from_frame(frame)
    assert "temperature" not in pack
