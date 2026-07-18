import pandas as pd

from autoresearch.scan.agents.l3_select import prepare_l3_table


def test_prepare_writes_l3_table(tmp_path):
    base = tmp_path / "context" / "scan"
    d = base / "2026-06-20"
    (d / "L3_news").mkdir(parents=True)
    pd.DataFrame([{"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 90 - i,
                   "gbdt_score": 0.5, "pct_60d": 10.0, "main_net_ratio": 0.01,
                   "winner_rate": 30.0, "np_yoy": 50.0, "n_channels": 2,
                   "recall_channels": "composite"} for i in range(3)]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    for c in ("000000", "000001", "000002"):
        (d / "L3_news" / f"{c}.json").write_text("[]", encoding="utf-8")

    res = prepare_l3_table("2026-06-20", root=base, do_harvest=False)

    assert res["codes"] == 3 and res["table_bytes"] > 0
    assert (d / "_l3_table.md").exists()


def test_prepare_with_harvest_default(tmp_path, monkeypatch):
    """Test prepare_l3_table with do_harvest=True (default path) using monkeypatched harvest functions."""
    base = tmp_path / "context" / "scan"
    d = base / "2026-06-20"
    (d / "L3_news").mkdir(parents=True)
    (d / "L3_evidence").mkdir(parents=True)
    pd.DataFrame([{"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 90 - i,
                   "gbdt_score": 0.5, "pct_60d": 10.0, "main_net_ratio": 0.01,
                   "winner_rate": 30.0, "np_yoy": 50.0, "n_channels": 2,
                   "recall_channels": "composite"} for i in range(3)]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    for c in ("000000", "000001", "000002"):
        (d / "L3_news" / f"{c}.json").write_text("[]", encoding="utf-8")
        (d / "L3_evidence" / f"{c}.json").write_text("{}", encoding="utf-8")

    # Monkeypatch harvest functions to no-op to prevent network calls
    monkeypatch.setattr("autoresearch.scan.agents.l3_select.harvest_l3_evidence",
                       lambda *a, **k: {})
    monkeypatch.setattr("autoresearch.scan.agents.l3_news.harvest_l3_news",
                       lambda *a, **k: {})

    # Call with default do_harvest=True
    res = prepare_l3_table("2026-06-20", root=base)

    assert res["codes"] == 3 and res["table_bytes"] > 0
    assert (d / "_l3_table.md").exists()


# ──────────────── 校准块注入(2026-07-17 自我迭代腿;pr_20260716_005 接线) ────────────────


def _mini_scan(tmp_path):
    base = tmp_path / "context" / "scan"
    d = base / "2026-06-20"
    (d / "L3_news").mkdir(parents=True)
    pd.DataFrame([{"code": f"{i:06d}", "name": f"s{i}", "industry": "电子", "composite": 90 - i,
                   "gbdt_score": 0.5, "pct_60d": 10.0, "main_net_ratio": 0.01,
                   "winner_rate": 30.0, "np_yoy": 50.0, "n_channels": 2,
                   "recall_channels": "composite"} for i in range(3)]).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    for c in ("000000", "000001", "000002"):
        (d / "L3_news" / f"{c}.json").write_text("[]", encoding="utf-8")
    return base, d


def test_calib_blocks_default_on_injects_lessons_baseline(tmp_path):
    """默认(two_pass 生效)注入:经验校准块恒有基线(pr_20260716_005 的腿真通了);
    t1 账本空(conftest 隔离)→ 快环块零字节。"""
    base, d = _mini_scan(tmp_path)
    prepare_l3_table("2026-06-20", root=base, do_harvest=False)
    md = (d / "_l3_table.md").read_text(encoding="utf-8")
    assert "因子方向经验校准" in md or "IC" in md          # 经验块(空库=基线回退)在场
    assert "T+1 快环校准" not in md                        # 隔离空账本 → 快环块零字节


def test_calib_blocks_injects_t1_block_when_ledger_present(tmp_path):
    """t1 账本有数据 → 快环校准块出现在表尾(数据非指令)。"""
    import json as _json

    import autoresearch.learning.t1_review as t1mod
    base, d = _mini_scan(tmp_path)
    t1mod._LEDGER.parent.mkdir(parents=True, exist_ok=True)   # conftest 已把它指向 tmp _iso
    t1mod._LEDGER.write_text(_json.dumps(
        {"t": "2026-06-19", "t1": "2026-06-20", "code": "000001", "rating": "Overweight",
         "cc1": 0.02, "excess": 0.02, "verdict": "准", "surprise": False,
         "mechanism": "卡内论点兑现", "diagnosed": True}) + "\n", encoding="utf-8")
    prepare_l3_table("2026-06-20", root=base, do_harvest=False)
    md = (d / "_l3_table.md").read_text(encoding="utf-8")
    assert "T+1 快环校准" in md and "数据非指令" in md


def test_two_pass_false_keeps_byte_parity_no_calib_blocks(tmp_path):
    """回滚杆承诺:two_pass=False → 逐字节不变(校准块跟随关,不新增任何字节)。"""
    base, d = _mini_scan(tmp_path)
    prepare_l3_table("2026-06-20", root=base, do_harvest=False, two_pass=False)
    md = (d / "_l3_table.md").read_text(encoding="utf-8")
    assert "快环校准" not in md and "因子方向经验校准" not in md and "IC 回测基线" not in md
