"""L4 helpers 单测 —— force_full_card(强先验白名单)
+ harvest_slim_batch(P3 ThreadPool 并发)。无网络。

覆盖 spec §Leaf L4:
  - 强先验(高 conviction + 多路/lane 救回)→ 强制满卡(不被表面早停误杀)
  - 弱先验 → 不强制
  - 卡片自评 gate=True 但正文矛盾 → flag(防 gaming);一致/未声明 → 不 flag
  - harvest_slim_batch workers=4 默认并发真跑(lock+peak 计数证非串行)、workers=1
    退化串行时 failures 仍按 tickers 原序(GATE 3 报告顺序不变、.SH 归一漏网判定不变)
"""
from __future__ import annotations

from autoresearch.scan.agents.l4_card import force_full_card


def test_force_full_strong_prior():
    assert force_full_card({"conviction": 80, "n_channels": 5}) is True


def test_force_full_weak_prior():
    assert force_full_card({"conviction": 40, "n_channels": 2}) is False


def test_force_full_lane_reserved_path():
    assert force_full_card({"conviction": 75, "n_channels": 1, "l2_lane_reserved": True}) is True


def test_force_full_high_conv_but_single_channel_no_lane():
    assert force_full_card({"conviction": 90, "n_channels": 1}) is False    # 高 conv 但孤路无 lane → 不强制


def test_pick_opportunity_candidates(tmp_path):
    """0买日机会成本红队名单:Hold 按 L3 conviction 降序取 top-k(spec 2026-07-02 任务E)。"""
    import pandas as pd

    from autoresearch.scan.agents.l4_card import pick_opportunity_candidates
    pd.DataFrame({"code": ["000001", "000002", "000003", "000004"],
                  "conviction": [55, 70, 62, 90]}).to_csv(tmp_path / "finalists.csv", index=False)
    ratings = {"000001": "Hold", "000002": "Hold", "000003": "Hold", "000004": "Underweight"}
    out = pick_opportunity_candidates(ratings, tmp_path, k=2)
    assert out == ["000002", "000003"]        # UW 不入;按 conviction 70>62>55 取 2
    assert pick_opportunity_candidates({}, tmp_path) == []
    assert pick_opportunity_candidates(ratings, tmp_path / "nope") == []   # 缺 finalists 优雅


def test_force_full_card_pinned_always_full():
    """📌 保送持仓票恒强制满卡:你真金白银持有的票,盈利质量/偿付(爆雷)两维不允许标『未核』。

    pinned 票绕过 L3 finalist tier(finalist=false → conviction 常 50–55),按 conv_min=70
    的通用判据必然落进早停 → 2026-07-12 实测 4/4 持仓卡全部早停在 P3、爆雷维未核。
    """
    assert force_full_card({"lane": "pinned", "conviction": 50, "n_channels": 1}) is True
    assert force_full_card({"lane": "pinned"}) is True                     # 连 conviction 都缺也强制
    assert force_full_card({"lane": "value", "conviction": 50, "n_channels": 1}) is False


def _valid_slim(pad: int = 10_000) -> str:
    """GATE3 结构合格的 slim 正文(四道锚 + 真 OHLCV Close)。

    2026-07-14 起 GATE3 判据 = 结构+内容(见 l4_card._slim_defect),纯 "x"*N 填充物不再算合格 ——
    只想撑体积/测并发的用例用这个,别退回填充物。
    """
    return (
        "## Verified market snapshot (source of truth)\n"
        "### Latest verified OHLCV row\n\n| Field | Value |\n|---|---:|\n| Close | 41.00 |\n"
        "## Market context — A股 (主力/技术/筹码/北向 · 复用L1召回)\n"
        "## Fundamentals overview\n"
    ) + "x" * pad


def test_harvest_slim_batch_parallel_workers(tmp_path):
    """P3:workers=4 默认并发——fake_hv 用 lock+peak 计数器证明真并发(串行时 peak 恒为 1)。"""
    import threading

    from autoresearch.scan.agents.l4_card import harvest_slim_batch
    scan_dir = tmp_path / "2026-07-10"
    scan_dir.mkdir(parents=True)
    (scan_dir / "_harvest_list.txt").write_text("AAA BBB CCC DDD", encoding="utf-8")
    lock, state = threading.Lock(), {"now": 0, "peak": 0}

    def fake_hv(t, dt):
        import time
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text(_valid_slim(), encoding="utf-8")   # 结构合格(本测只验并发,不验校验逻辑)
        with lock:
            state["now"] -= 1
        return p

    res = harvest_slim_batch("2026-07-10", root=tmp_path, harvest_fn=fake_hv, workers=4)
    assert res["ok"] and res["n"] == 4 and res["failures"] == []
    assert state["peak"] >= 2                       # 真并发(串行时 peak==1)


def test_harvest_slim_batch_workers1_failures_ordered(tmp_path):
    """P3:workers=1 退化串行——failures 仍严格按 tickers 原序(GATE 3 报告读序不能乱)。"""
    from autoresearch.scan.agents.l4_card import harvest_slim_batch
    scan_dir = tmp_path / "2026-07-10"
    scan_dir.mkdir(parents=True)
    (scan_dir / "_harvest_list.txt").write_text("AAA 600000.SH BBB", encoding="utf-8")

    def tiny(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text("x", encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-10", root=tmp_path, harvest_fn=tiny, workers=1)
    assert not res["ok"]
    assert [f["ticker"] for f in res["failures"]] == ["AAA", "600000.SH", "BBB"]
    assert res["failures"][1]["why"] == ".SH 未归一"
