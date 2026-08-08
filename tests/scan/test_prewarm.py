"""P1b:夜间预热——结算日解析(19:15 门)+ 步骤编排 + _prewarm.json + env 生命周期。"""
import json
import os
from datetime import datetime

import pytest


def _patch_tradedays(monkeypatch):
    import autoresearch.data.tushare_source as ts
    monkeypatch.setattr(ts, "_pro", lambda: object())
    monkeypatch.setattr(ts, "_trade_days",
                        lambda pro, s, e: [d for d in ("20260709", "20260710") if d <= e.replace("-", "")])


def test_latest_settled_before_1915_falls_back(monkeypatch):
    _patch_tradedays(monkeypatch)
    from autoresearch.scan.prewarm import latest_settled_trade_date
    assert latest_settled_trade_date(datetime(2026, 7, 10, 18, 0)) == "2026-07-09"
    assert latest_settled_trade_date(datetime(2026, 7, 10, 19, 30)) == "2026-07-10"


def test_run_prewarm_writes_manifest_and_env_lifecycle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                      # context/scan/<date> 落 tmp
    _patch_tradedays(monkeypatch)
    import autoresearch.scan.prewarm as pw
    monkeypatch.setattr(pw, "_frame_lake", lambda date: "帧 4000 只已入湖")
    monkeypatch.setattr(pw, "_prewarm_evidence", lambda date: "21 次端点预拉")
    monkeypatch.setattr(pw, "_temperature", lambda date: "1 行")
    monkeypatch.setattr(pw, "_dossier_prefetch", lambda date: "池预取 3/3")
    monkeypatch.setattr(pw, "_hot_rank_snapshot",
                        lambda date: "stock_hot_rank_em✓(100行) · stock_hot_follow_xq✓(5619行)")
    res = pw.run_prewarm(now=datetime(2026, 7, 10, 19, 30))
    assert res["date"] == "2026-07-10" and res["ok"]
    assert os.environ.get("LAKE_ASSUME_SETTLED") is None            # 收尾必清
    j = json.loads((tmp_path / "context/scan/2026-07-10/_prewarm.json").read_text(encoding="utf-8"))
    assert j["ended_at"] >= j["started_at"]
    assert [s["step"] for s in j["steps"]] == \
        ["frame_lake", "evidence_lake", "temperature", "dossier_prefetch", "hot_rank_snapshot"]


def test_run_prewarm_past_date_no_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _patch_tradedays(monkeypatch)
    import autoresearch.scan.prewarm as pw
    seen = {}
    monkeypatch.setattr(pw, "_frame_lake",
                        lambda date: seen.setdefault("env", os.environ.get("LAKE_ASSUME_SETTLED")))
    monkeypatch.setattr(pw, "_prewarm_evidence", lambda date: "")
    monkeypatch.setattr(pw, "_temperature", lambda date: "")
    monkeypatch.setattr(pw, "_dossier_prefetch", lambda date: "")
    monkeypatch.setattr(pw, "_hot_rank_snapshot", lambda date: "")
    pw.run_prewarm(date="2026-07-09", now=datetime(2026, 7, 10, 19, 30))
    assert seen["env"] is None                       # 目标日≠今天 → 不设豁免


# ══════════ Wave12 T3:热度快照夜间采集接线(断采可见)══════════
#
# design: docs/research/2026-08-09-hot-rank-probe.md;T2 已注册 stock_hot_rank_em/
# stock_hot_follow_xq 两个 B 级 as_of 端点。本节测「夜间入口」本体:两源各自被调用,
# 单源失败不 raise、不拖累另一源,且必须显式记账(不是 `_prewarm_evidence` 那种
# `except: pass` 静默吞掉的旧模式——B 级"降级不留痕才是真病")。


def test_hot_rank_snapshot_calls_both_sources_and_reports_success(monkeypatch):
    import pandas as pd

    import autoresearch.data.cache as cache_mod
    import autoresearch.scan.prewarm as pw

    calls = []

    def fake_get_or_fetch(ep, params, today=None, fetch=None):
        calls.append(ep)
        return pd.DataFrame({"a": [1, 2, 3]})

    monkeypatch.setattr(cache_mod, "get_or_fetch", fake_get_or_fetch)
    note = pw._hot_rank_snapshot("2026-08-07")
    assert calls == ["stock_hot_rank_em", "stock_hot_follow_xq"]
    assert "✓" in note and "✗" not in note
    assert "3行" in note or "3 行" in note


def test_hot_rank_snapshot_records_degradation_on_failure_without_raising(monkeypatch):
    import pandas as pd

    import autoresearch.data.cache as cache_mod
    import autoresearch.data.contracts as contracts_mod
    import autoresearch.scan.prewarm as pw

    contracts_mod.clear_degradations()

    def flaky_get_or_fetch(ep, params, today=None, fetch=None):
        if ep == "stock_hot_rank_em":
            raise RuntimeError("network boom")
        return pd.DataFrame({"a": [1]})

    monkeypatch.setattr(cache_mod, "get_or_fetch", flaky_get_or_fetch)
    note = pw._hot_rank_snapshot("2026-08-07")            # 不应抛
    assert "✗" in note and "stock_hot_rank_em" in note
    assert "stock_hot_follow_xq✓" in note                  # 断采只损失当日:另一源仍被尝试

    recs = contracts_mod.degradations()
    assert len(recs) == 1
    assert recs[0]["endpoint"] == "stock_hot_rank_em" and recs[0]["source"] == "direct"
    contracts_mod.clear_degradations()


def test_hot_rank_snapshot_wired_into_run_prewarm_steps(tmp_path, monkeypatch):
    """接线断言:`_hot_rank_snapshot` 进了 `run_prewarm` 的 steps 列表,与既有四步同款
    `_step()` 包装(单步失败记录继续,不阻断整晚预热)。"""
    monkeypatch.chdir(tmp_path)
    _patch_tradedays(monkeypatch)
    import autoresearch.scan.prewarm as pw
    monkeypatch.setattr(pw, "_frame_lake", lambda date: "")
    monkeypatch.setattr(pw, "_prewarm_evidence", lambda date: "")
    monkeypatch.setattr(pw, "_temperature", lambda date: "")
    monkeypatch.setattr(pw, "_dossier_prefetch", lambda date: "")
    monkeypatch.setattr(pw, "_hot_rank_snapshot",
                        lambda date: "stock_hot_rank_em✓(1行) · stock_hot_follow_xq✓(1行)")
    res = pw.run_prewarm(now=datetime(2026, 7, 10, 19, 30))
    assert res["ok"]
    names = [s["step"] for s in res["steps"]]
    assert "hot_rank_snapshot" in names
    hot = next(s for s in res["steps"] if s["step"] == "hot_rank_snapshot")
    assert hot["ok"] and "✓" in hot["note"]


def test_mutation_probe_without_internal_catch_second_source_never_tried(monkeypatch):
    """变异探针(还原"内部无 try/except"的旧行为):若第一个源的异常不被内部捕获而是
    直接冒泡,第二个源根本不会被尝试到——反向证明"连续断采仅损失当日"这句话依赖的
    正是 `_hot_rank_snapshot` 内部的逐源 try/except,不是外层 `_step()` 的兜底(外层
    兜底只保证"这一整步"不拖垮其余步骤,救不回"步内第二个源被第一个拖累不被调用"
    这件事)。"""
    import pandas as pd

    import autoresearch.data.cache as cache_mod
    import autoresearch.scan.prewarm as pw

    calls = []

    def flaky(ep, params, today=None, fetch=None):
        calls.append(ep)
        if ep == "stock_hot_rank_em":
            raise RuntimeError("boom")
        return pd.DataFrame({"a": [1]})

    monkeypatch.setattr(cache_mod, "get_or_fetch", flaky)

    # 反向:没有内部 try/except 的朴素写法 —— 第一源一炸,第二源永远不会被调用。
    with pytest.raises(RuntimeError):
        cache_mod.get_or_fetch("stock_hot_rank_em", {}, today="2026-08-07")
        cache_mod.get_or_fetch("stock_hot_follow_xq", {}, today="2026-08-07")
    assert calls == ["stock_hot_rank_em"]

    # 真实实现:两源都被尝试到 —— 证明内部 try/except 确实在起作用。
    calls.clear()
    pw._hot_rank_snapshot("2026-08-07")
    assert calls == ["stock_hot_rank_em", "stock_hot_follow_xq"]
