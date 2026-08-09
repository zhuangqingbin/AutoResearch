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
                        lambda date: "eastmoney_hot_rank✓(100行) · stock_hot_follow_xq✓(5619行)")
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
# design: docs/research/2026-08-09-hot-rank-probe.md;T2 注册的三个分片 =
# eastmoney_hot_rank(自采第一跳,绕开被封的 push2)+ stock_hot_follow_xq × 2 个 symbol
# (最热门=累计关注 / 本周新增=follow7d)。本节测「夜间入口」本体:每源各自被调用,
# 单源失败不 raise、不拖累其余源,且必须显式记账(不是 `_prewarm_evidence` 那种
# `except: pass` 静默吞掉的旧模式——B 级"降级不留痕才是真病")。


def _full_frames(ep, params=None):
    """足量的假帧(过契约行数下限)—— 用它就是"这一晚采齐了"。"""
    import pandas as pd

    from autoresearch.data.contracts import CONTRACTS
    n = CONTRACTS[ep].min_rows
    cols = {c: list(range(n)) for c in sorted(CONTRACTS[ep].required_cols)}
    return pd.DataFrame(cols)


def test_hot_rank_snapshot_calls_all_sources_and_reports_success(monkeypatch):
    import autoresearch.data.cache as cache_mod
    import autoresearch.scan.prewarm as pw

    calls = []

    def fake_get_or_fetch(ep, params, today=None, fetch=None):
        calls.append((ep, dict(params)))
        return _full_frames(ep)

    monkeypatch.setattr(cache_mod, "get_or_fetch", fake_get_or_fetch)
    note = pw._hot_rank_snapshot("2026-08-07")
    assert calls == [("eastmoney_hot_rank", {}),
                     ("stock_hot_follow_xq", {}),
                     ("stock_hot_follow_xq", {"symbol": "本周新增"})]
    assert "✓" in note and "✗" not in note
    assert "本周新增" in note, "M1:7 日新增关注(follow_delta)同样不可回填,必须每晚采"
    assert "100行" in note and "5000行" in note


def test_hot_rank_snapshot_partitions_by_observation_day_not_target_trade_date(monkeypatch):
    """I1:分区键必须是**观测日**。节假日 launchd / 补跑时 `date` 是上一个交易日,而快照
    接口给的永远是"此刻" —— 按 `date` 分区就是把今天的观测写成那天的假历史。"""
    import datetime as _dt

    import autoresearch.data.cache as cache_mod
    import autoresearch.scan.prewarm as pw

    seen = []
    monkeypatch.setattr(cache_mod, "get_or_fetch",
                        lambda ep, params, today=None, fetch=None:
                        seen.append(today) or _full_frames(ep))
    note = pw._hot_rank_snapshot("2026-08-07")            # 目标交易日 = 上周五
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    assert seen == [today, today, today], "传给 lake 的 as-of 必须是墙上时钟今天"
    assert f"观测日 {today.replace('-', '')}" in note, "note 要留下观测日,事后可甄别"


def test_hot_rank_snapshot_flags_half_return_that_did_not_raise(monkeypatch):
    """C2:雪球中途限流会**静默**返回 3000 行(每页解析失败被 akshare 自己 `except TypeError`
    吞掉),取数不抛异常 —— note 必须记 ✗ 而不是 `✓(3000行)`,否则 prelude 永远不告警。"""
    import pandas as pd

    import autoresearch.data.cache as cache_mod
    import autoresearch.scan.prewarm as pw

    def half(ep, params, today=None, fetch=None):
        if ep == "stock_hot_follow_xq" and not params:
            return pd.DataFrame({"股票代码": ["SH600519"] * 3000, "关注": [1.0] * 3000})
        return _full_frames(ep)

    monkeypatch.setattr(cache_mod, "get_or_fetch", half)
    monkeypatch.setattr("autoresearch.data.contracts.CHECK_ROWS", True)
    note = pw._hot_rank_snapshot("2026-08-07")
    assert "stock_hot_follow_xq✗(3000行" in note and "行数腰斩" in note
    assert "eastmoney_hot_rank✓" in note                   # 半截只影响它自己那一片


def test_hot_rank_snapshot_records_degradation_on_failure_without_raising(monkeypatch):
    import autoresearch.data.cache as cache_mod
    import autoresearch.data.contracts as contracts_mod
    import autoresearch.scan.prewarm as pw

    contracts_mod.clear_degradations()

    def flaky_get_or_fetch(ep, params, today=None, fetch=None):
        if ep == "eastmoney_hot_rank":
            raise RuntimeError("network boom")
        return _full_frames(ep)

    monkeypatch.setattr(cache_mod, "get_or_fetch", flaky_get_or_fetch)
    note = pw._hot_rank_snapshot("2026-08-07")            # 不应抛
    assert "✗" in note and "eastmoney_hot_rank" in note
    assert "stock_hot_follow_xq✓" in note                  # 断采只损失当日:其余源仍被尝试

    recs = contracts_mod.degradations()
    assert len(recs) == 1
    assert recs[0]["endpoint"] == "eastmoney_hot_rank" and recs[0]["source"] == "direct"
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
                        lambda date: "eastmoney_hot_rank✓(100行) · stock_hot_follow_xq✓(5619行)")
    res = pw.run_prewarm(now=datetime(2026, 7, 10, 19, 30))
    assert res["ok"]
    names = [s["step"] for s in res["steps"]]
    assert "hot_rank_snapshot" in names
    hot = next(s for s in res["steps"] if s["step"] == "hot_rank_snapshot")
    assert hot["ok"] and "✓" in hot["note"]


def test_mutation_probe_without_internal_catch_second_source_never_tried(monkeypatch):
    """变异探针(还原"内部无 try/except"的旧行为):若第一个源的异常不被内部捕获而是
    直接冒泡,后面的源根本不会被尝试到——反向证明"连续断采仅损失当日"这句话依赖的
    正是 `_hot_rank_snapshot` 内部的逐源 try/except,不是外层 `_step()` 的兜底(外层
    兜底只保证"这一整步"不拖垮其余步骤,救不回"步内第二个源被第一个拖累不被调用"
    这件事)。"""
    import autoresearch.data.cache as cache_mod
    import autoresearch.scan.prewarm as pw

    calls = []

    def flaky(ep, params, today=None, fetch=None):
        calls.append(ep)
        if ep == "eastmoney_hot_rank":
            raise RuntimeError("boom")
        return _full_frames(ep)

    monkeypatch.setattr(cache_mod, "get_or_fetch", flaky)

    # 反向:没有内部 try/except 的朴素写法 —— 第一源一炸,第二源永远不会被调用。
    with pytest.raises(RuntimeError):
        cache_mod.get_or_fetch("eastmoney_hot_rank", {}, today="2026-08-07")
        cache_mod.get_or_fetch("stock_hot_follow_xq", {}, today="2026-08-07")
    assert calls == ["eastmoney_hot_rank"]

    # 真实实现:三个分片都被尝试到 —— 证明内部 try/except 确实在起作用。
    calls.clear()
    pw._hot_rank_snapshot("2026-08-07")
    assert calls == ["eastmoney_hot_rank", "stock_hot_follow_xq", "stock_hot_follow_xq"]


def test_mutation_probe_row_count_alone_cannot_tell_half_from_full(monkeypatch):
    """变异探针(还原"note 只写行数、不问契约"的旧实现):同一个 3000 行半截会被写成
    `✓(3000行)` —— 与真·成功在 note 里**长得一模一样**,prelude 的 ✗ 判据于是永远静默。
    反向证明 `_hot_rank_snapshot` 里那次 `violations()` 复核不是装饰。"""
    import pandas as pd

    import autoresearch.data.contracts as contracts_mod

    half = pd.DataFrame({"股票代码": ["SH600519"] * 3000, "关注": [1.0] * 3000})
    monkeypatch.setattr(contracts_mod, "CHECK_ROWS", True)
    naive_note = f"stock_hot_follow_xq✓({len(half)}行)"          # 旧写法
    assert "✗" not in naive_note                                  # ← 病灶:半截伪装成成功
    assert contracts_mod.violations("stock_hot_follow_xq", half), "而契约本来是逮得住的"
