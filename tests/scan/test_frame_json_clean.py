"""frame --json 纯净 stdout —— market_pack.json 日志污染回归锁。NO network。

背景(2026-07-09 现场中招):workflow 用 `frame <date> --json > market_pack.json` 落盘,
但 main() 里 `[frame]`/`[sentinel·盘前预告]`/`[macro_state]` 三行 print 曾走 stdout,
混进重定向出的 JSON 文件,导致 `json.load` 炸。锁死:--json 时整个 stdout 必须是单份可
解析 JSON,三条信息行改落 stderr(人看终端不受影响)。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.scan import frame, run_contract

DATE = "2026-07-09"


def _patch_deps(monkeypatch, tmp_path):
    """monkeypatch main() 实际调用的四处外部依赖(源码 :128-162),隔离网络与真实 context/。

    build_market_frame 桩内故意 print 一行到 stdout——模拟湖冷时取数层([L0·tushare] 等)的进度行
    (2026-07-09 现场污染的完整根因),锁死 --json 的 redirect_stdout 必须把库层 print 也圈走。"""
    df = pd.DataFrame({"code": ["000001"], "close": [10.0]})

    def _stub_build(d, **k):
        print("[L0·tushare] as-of 交易日=模拟取数进度行(库层 stdout)")
        return df, {"universe_raw": 1, "universe": 1, "after_gate_a": 1}

    monkeypatch.setattr(frame, "build_market_frame", _stub_build)
    monkeypatch.setattr("autoresearch.scan.market.market_pack_from_frame",
                        lambda f, **k: {"date": DATE, "regime": {"label": "risk_off"}})
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
    """无 --json 时人看终端行为照旧可见:info 行在 stderr,库层 print 仍走 stdout(不重定向)。"""
    _patch_deps(monkeypatch, tmp_path)
    rc = frame.main([DATE])
    captured = capsys.readouterr()
    assert rc == 0
    assert "[L0·tushare]" in captured.out      # 无 --json:库层进度行保持原通道(parity)
    for marker in ("[frame]", "[sentinel·盘前预告]", "[macro_state]"):
        assert marker in captured.err


def test_json_mode_contains_library_stdout(monkeypatch, capsys, tmp_path):
    """--json 时库层(取数)print 一并被圈进 stderr——stdout 只剩 JSON(完整根因回归锁)。"""
    _patch_deps(monkeypatch, tmp_path)
    rc = frame.main([DATE, "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "[L0·tushare]" not in captured.out and "[L0·tushare]" in captured.err
    json.loads(captured.out.strip())


def test_json_mode_writes_contract_and_short_ref(monkeypatch, capsys, tmp_path):
    """frame 是运行身份最早边界：完整契约落盘，pack 只携带定长引用。"""
    _patch_deps(monkeypatch, tmp_path)
    # RunContract 真身 2026-08-31(D6.2)搬到 `common/run_identity.py`;
    # 桩必须打在定义模块上,`scan.run_contract` 只是 re-export 的名字。
    monkeypatch.setattr(
        "autoresearch.common.run_identity.resolve_git_sha", lambda root=".": "deadbeef"
    )
    rc = frame.main([DATE, "--json"])
    payload = json.loads(capsys.readouterr().out)
    contract_path = tmp_path / ws.scan_root() / DATE / "run_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    assert rc == 0
    # schema_version 取常量而非字面量:锁的是「short_ref 恰好这四个键、值与落盘契约一致」,
    # 不是「版本号永远是 1」——版本本来就会随字段增补前进(v2 = 2026-08-26 现场留存波
    # 加 git_dirty/dirty_paths/prompt_hashes)。写死字面量会让每次加字段都误报一条红。
    assert payload["run_contract"] == {
        "schema_version": run_contract.RUN_CONTRACT_SCHEMA_VERSION,
        "run_id": contract["run_id"],
        "contract_hash": contract["contract_hash"],
        "config_hash": contract["config_hash"],
    }
    assert contract["analysis_date"] == DATE
    assert contract["git_sha"] == "deadbeef"
    assert contract["data_policy"] == {
        "source": "tushare",
        "cap_floor_yi": 30.0,
        "include_bj": True,
    }
    assert contract["stage_budgets"] == {
        "baseline_run": "20260727_2140",
        "cache_hit_min": 0.85,
        "concurrency": {          # 注册表缺省(contracts/scan_config.DEFAULT_CONCURRENCY)
            "l4_stock": 64,
            "tushare": 4,
        },
        # 2026-09-26:l3.finalist_max 退役 → 卡数只由 l4.max_cards 决定(scan/l4/card_count)。
        "l4_max_cards": 13,
        "maturity": {"phase1": {"cost_reduction": 0.15, "p50": 75, "p90": 100},   # budgets.maturity 缺省
                     "phase2": {"cost_reduction": 0.25, "p50": 65, "p90": 90}},
        "min_real_scans": 10,
        "pinned_cap": 5,
        "pinned_ttl_days": 10,
        # 2026-10-03 A5:相对预算带(budgets.relative 缺省)随 normalize_budgets 进 run contract。
        "relative": {"window": 10, "min_runs": 3, "warn_ratio": 1.5, "alarm_ratio": 2.0},
        # 2026-10-10 token 防膨胀:每场预算线与场后信封(budgets.declared / envelope 缺省)。
        "declared": {"claude_run_usd": 41.0, "codex_run_window_points": 92.0},
        "envelope": {"host_weighted_max": 100_000.0, "prefix_drift_warn": 1.2, "prefix_drift_fail": 2.0,
                     "output_drift_warn": 2.0, "ratchet_margin": 1.15, "idle_runs": 5},
        # 2026-09-04 S5:两个 weighted 预算键随 normalize_budgets 进 run contract。
        # 值是 float 不是 jsonc 里的 int —— normalize_budgets 统一过 _finite_number,
        # 所以这里的字面量与 scan_config.jsonc 的写法不同是对的,不要"修正"成 int。
        "run_weighted_warn": 7_000_000.0,
        "run_weighted_target": 5_000_000.0,
        "stage_cost_usd": {},
        "stage_wall_seconds": {},
    }
    from autoresearch.scan.stage_result import load_stage_result
    stage = load_stage_result(
        tmp_path / ws.scan_root() / DATE / "stage_results" / "frame.json"
    )
    assert stage.status == "SUCCEEDED"
    assert stage.artifacts == ["run_contract"]
    assert stage.contract_hash == contract["contract_hash"]
    assert stage.metrics["frame_rows"] == 1
    assert stage.metrics["sentinel_level"] == "full"
