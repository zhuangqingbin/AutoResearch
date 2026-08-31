"""T6:日历一次瞬断不许把整晚预热带走。

病灶(2026-08-29 计划 T6):`date = date or latest_settled_trade_date(now)` 原来写在
`_step()` **之外** —— `trade_cal` 一次 DNS 瞬断就让 `run_prewarm` 抛栈退出,一步取数都
没跑、磁盘上一点证据都没有。08-28 20:13 就是这么死的(`NameResolutionError
api.waditu.com` ×3),于是 `lake/daily/20260828` 干脆缺席,而 launchd 那边没有任何重试。

本文件锁四件事:
  ① 日历失败 = 一行 `resolve_date` 失败账 + `ok=False` 返回,**不抛**;
  ② 失败后**不往下跑取数步**(没有日期,后面每一步都会拿 `None` 去取数);
  ③ 失败记录仍要落盘(`_prewarm_failed.json`)—— 「整晚白过」必须留下素材;
  ④ 显式传日期**完全不碰日历**;成功自动选日时账面仍是那五步(`_prewarm.json` 的既有
     读者 —— prelude 热度告警、stage_timing、tests/scan/test_prewarm.py —— 按取数步骤读)。
"""
import json
from datetime import datetime

import pytest

from autoresearch.common import workspace as ws

FETCH_STEPS = ("_frame_lake", "_prewarm_evidence", "_temperature",
               "_dossier_prefetch", "_hot_rank_snapshot")


def _raise_dns(*_a, **_k):
    raise OSError("NameResolutionError: Failed to resolve 'api.waditu.com'")


@pytest.fixture
def pw(tmp_path, monkeypatch):
    """prewarm 模块 + 全部取数步替换成「记一笔调用」的桩(取数不是本文件的被测对象)。"""
    monkeypatch.chdir(tmp_path)                       # context_*/scan/<date> 落 tmp
    import autoresearch.scan.prewarm as module
    calls: list[str] = []
    for name in FETCH_STEPS:
        monkeypatch.setattr(module, name,
                            lambda date, _n=name: calls.append(f"{_n}({date})") or f"{_n} ok")
    monkeypatch.setattr(module, "calls", calls, raising=False)   # 测试自用挂件,退出即撤
    return module


def test_calendar_failure_is_a_recorded_step_not_a_crash(pw, monkeypatch):
    monkeypatch.setattr(pw, "latest_settled_trade_date", _raise_dns)
    res = pw.run_prewarm(None)                        # 不传 date → 走日历解析
    assert res["ok"] is False
    assert res["date"] is None
    bad = [s for s in res["steps"] if s["step"] == "resolve_date"]
    assert bad and bad[0]["ok"] is False
    assert "api.waditu.com" in bad[0]["note"]


def test_calendar_failure_does_not_run_fetch_steps(pw, monkeypatch):
    """没有日期就往下跑 = 五步全拿 `None` 去取数(污染湖 / 刷一屏假失败)。"""
    monkeypatch.setattr(pw, "latest_settled_trade_date", _raise_dns)
    res = pw.run_prewarm(None)
    assert pw.calls == []
    assert [s["step"] for s in res["steps"]] == ["resolve_date"]


def test_calendar_failure_still_persists_a_record(pw, monkeypatch):
    """无日期 → `scan_dir` 无处可建,记录落 `scan_root()/_prewarm_failed.json`。"""
    monkeypatch.setattr(pw, "latest_settled_trade_date", _raise_dns)
    pw.run_prewarm(None)
    p = ws.scan_root() / "_prewarm_failed.json"
    assert p.is_file(), "整晚白过必须留下素材,否则次日只能靠 /tmp 日志考古"
    rec = json.loads(p.read_text(encoding="utf-8"))
    assert rec["date"] is None
    assert rec["ended_at"] >= rec["started_at"]
    assert [s["step"] for s in rec["steps"]] == ["resolve_date"]
    assert rec["steps"][0]["ok"] is False


def test_explicit_date_skips_calendar(pw, monkeypatch):
    """显式日期(补跑/离线)不该碰日历 —— 碰了就会被这个必炸的桩打死。"""
    monkeypatch.setattr(pw, "latest_settled_trade_date", _raise_dns)
    res = pw.run_prewarm("2026-08-26")
    assert res["ok"] is True and res["date"] == "2026-08-26"
    assert all(s["step"] != "resolve_date" for s in res["steps"])
    expected = [f"{n}(2026-08-26)" for n in FETCH_STEPS]
    assert pw.calls == expected


def test_successful_autoresolve_keeps_the_five_step_ledger(pw, monkeypatch):
    """成功解析**不**占账面一行:日期已是记录的 `date` 字段,steps 是取数账。

    (`_prewarm.json` 的既有读者按这五步读;多塞一行 = 悄悄改契约。)
    """
    monkeypatch.setattr(pw, "latest_settled_trade_date", lambda now: "2026-08-26")
    res = pw.run_prewarm(None, now=datetime(2026, 8, 26, 19, 30))
    assert res["ok"] is True and res["date"] == "2026-08-26"
    assert [s["step"] for s in res["steps"]] == \
        ["frame_lake", "evidence_lake", "temperature", "dossier_prefetch", "hot_rank_snapshot"]
    rec = json.loads((ws.scan_root() / "2026-08-26/_prewarm.json").read_text(encoding="utf-8"))
    assert rec["date"] == "2026-08-26"
