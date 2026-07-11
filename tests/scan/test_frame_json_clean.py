"""frame --json 纯净 stdout —— market_pack.json 日志污染回归锁。NO network。

背景(2026-07-09 现场中招):workflow 用 `frame <date> --json > market_pack.json` 落盘,
但 main() 里 `[frame]`/`[sentinel·盘前预告]`/`[macro_state]` 三行 print 曾走 stdout,
混进重定向出的 JSON 文件,导致 `json.load` 炸。锁死:--json 时整个 stdout 必须是单份可
解析 JSON,三条信息行改落 stderr(人看终端不受影响)。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.scan import frame

DATE = "2026-07-09"


def _patch_deps(monkeypatch, tmp_path):
    """monkeypatch main() 实际调用的四处外部依赖(源码 :128-162),隔离网络与真实 context/。"""
    df = pd.DataFrame({"code": ["000001"], "close": [10.0]})
    monkeypatch.setattr(frame, "build_market_frame",
                        lambda d, **k: (df, {"universe_raw": 1, "universe": 1, "after_gate_a": 1}))
    monkeypatch.setattr("autoresearch.scan.market.market_pack_from_frame",
                        lambda f: {"date": DATE, "regime": {"label": "risk_off"}})
    monkeypatch.setattr("autoresearch.scan.menu.sentinel_advice_from_frame",
                        lambda f: ("full", "ok"))
    monkeypatch.setattr("autoresearch.macro.state.load_macro_state",
                        lambda today, regime_today=None, path=None:
                        (None, "无 macro_state.json → 只用日频 pack"))
    monkeypatch.chdir(tmp_path)   # 隔离真实 context/scan/<date>(--json 落 user_config_echo.json)
    return df


def test_json_stdout_is_pure_json(monkeypatch, capsys, tmp_path):
    _patch_deps(monkeypatch, tmp_path)
    rc = frame.main([DATE, "--json"])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    json.loads(out)          # 整个 stdout 必须是可解析 JSON(不能有任何 print 行混入)


def test_json_mode_info_lines_go_to_stderr_not_stdout(monkeypatch, capsys, tmp_path):
    """三条信息行仍然打印(人看终端不受影响)——只是通道从 stdout 换到 stderr。"""
    _patch_deps(monkeypatch, tmp_path)
    rc = frame.main([DATE, "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    for marker in ("[frame]", "[sentinel·盘前预告]", "[macro_state]"):
        assert marker not in captured.out, f"{marker} 泄漏进 stdout"
        assert marker in captured.err, f"{marker} 未落 stderr"


def test_no_json_mode_info_lines_still_go_to_stderr(monkeypatch, capsys, tmp_path):
    """无 --json 时人看终端行为照旧可见,只是通道统一为 stderr(不再回归 stdout)。"""
    _patch_deps(monkeypatch, tmp_path)
    rc = frame.main([DATE])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == ""
    for marker in ("[frame]", "[sentinel·盘前预告]", "[macro_state]"):
        assert marker in captured.err
