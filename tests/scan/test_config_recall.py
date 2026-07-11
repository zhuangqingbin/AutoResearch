"""Phase 2: ScanConfig 多路召回字段 + CLI 解析。"""
from __future__ import annotations

import json

from autoresearch.scan.cli import _config_from_args, build_parser
from autoresearch.scan.config import ScanConfig


def _args(*extra):
    return build_parser().parse_args(["run", "2026-06-20", *extra])


def _write_cfg(tmp_path, obj):
    d = tmp_path / ".claude" / "skills" / "scan-market"
    d.mkdir(parents=True)
    (d / "scan_config.jsonc").write_text(json.dumps(obj), encoding="utf-8")


def test_config_defaults_multi():
    cfg = ScanConfig()
    assert cfg.recall_mode == "multi"
    assert cfg.recall_channels is None
    assert "recall_mode" in cfg.to_dict()


def test_cli_parses_recall_mode_and_channels():
    args = build_parser().parse_args(["run", "2026-06-20", "--recall-mode", "composite",
                                      "--recall-channels", "composite,momentum,value"])
    assert args.recall_mode == "composite"
    assert args.recall_channels == "composite,momentum,value"


# ── scan_config.json funnel 接进 CLI(_config_from_args 叠加;缺文件=parity)──


def test_config_from_args_no_scan_config_is_parity(monkeypatch, tmp_path):
    """无 scan_config.json → _config_from_args 与波前一致:recall_channels=None(未传 flag)。"""
    monkeypatch.chdir(tmp_path)                       # cwd 无 .claude/skills/scan-market/scan_config.json
    assert _config_from_args(_args()).recall_channels is None


def test_config_from_args_applies_funnel_recall_channels(monkeypatch, tmp_path):
    """scan_config.json funnel.recall_channels(整编:剔 accumulation/northbound)→ 落进 ScanConfig。"""
    _write_cfg(tmp_path, {"funnel": {"recall_channels":
               ["composite", "momentum", "reversal_confirm", "reversal", "value",
                "main_fund", "heat", "growth", "healthy"]}})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",   # 显式指回本测试的真配置(冲销 conftest 隔离)
                        tmp_path / ".claude" / "skills" / "scan-market" / "scan_config.jsonc")
    rc = _config_from_args(_args()).recall_channels
    assert rc is not None and "accumulation" not in rc and "northbound" not in rc
    assert "reversal_confirm" in rc and len(rc) == 9


def test_cli_recall_channels_flag_wins_over_config(monkeypatch, tmp_path):
    """CLI 显式 --recall-channels 优先于 scan_config.json(本次运行显式意图)。"""
    _write_cfg(tmp_path, {"funnel": {"recall_channels": ["composite", "value"]}})
    monkeypatch.chdir(tmp_path)
    rc = _config_from_args(_args("--recall-channels", "momentum,heat")).recall_channels
    assert rc == ["momentum", "heat"]                # flag 赢,不被 config 覆盖


# ── Task 8:channel_quotas/floors 从 scan_config.json 一路透传到 universe.run(cmd_run 接线) ──


def test_cmd_run_wires_channel_quotas_and_floors_from_config(monkeypatch, tmp_path):
    """scan_config.json funnel.channel_quotas/floors → cmd_run 透传给 universe.run(与 recall_select
    的同名 override 形参对齐)。mock universe.run + Pipeline.run,不碰网络/真实漏斗。"""
    _write_cfg(tmp_path, {"funnel": {"channel_quotas": {"heat": 150}, "channel_floors": {"heat": 30}}})
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",   # 显式指回本测试的真配置(冲销 conftest 隔离)
                        tmp_path / ".claude" / "skills" / "scan-market" / "scan_config.jsonc")

    import autoresearch.scan.cli as cli_mod
    from autoresearch.scan import universe as smu

    captured = {}

    def _fake_run(analysis_date, **kwargs):
        captured.update(kwargs)
        return {"universe": 0, "after_gate_a": 0, "recall_n": 0, "l2_n": 0,
                "l2_engine": "x", "sectors": 0, "outdir": str(tmp_path)}

    monkeypatch.setattr(smu, "run", _fake_run, raising=True)
    monkeypatch.setattr(cli_mod.Pipeline, "run", lambda self, ctx: "fake_run_id", raising=True)

    rc = cli_mod.cmd_run(_args())
    assert rc == 0
    assert captured["channel_quotas"] == {"heat": 150}
    assert captured["channel_floors"] == {"heat": 30}


def test_cmd_run_no_config_passes_none_channel_overrides(monkeypatch, tmp_path):
    """无 scan_config.json → cmd_run 传给 universe.run 的 channel_quotas/floors 均 None(parity)。"""
    monkeypatch.chdir(tmp_path)

    import autoresearch.scan.cli as cli_mod
    from autoresearch.scan import universe as smu

    captured = {}

    def _fake_run(analysis_date, **kwargs):
        captured.update(kwargs)
        return {"universe": 0, "after_gate_a": 0, "recall_n": 0, "l2_n": 0,
                "l2_engine": "x", "sectors": 0, "outdir": str(tmp_path)}

    monkeypatch.setattr(smu, "run", _fake_run, raising=True)
    monkeypatch.setattr(cli_mod.Pipeline, "run", lambda self, ctx: "fake_run_id", raising=True)

    rc = cli_mod.cmd_run(_args())
    assert rc == 0
    assert captured["channel_quotas"] is None
    assert captured["channel_floors"] is None
