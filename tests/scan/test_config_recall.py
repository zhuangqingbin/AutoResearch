"""Phase 2: ScanConfig 多路召回字段 + CLI 解析。"""
from __future__ import annotations

from autoresearch.scan.cli import build_parser
from autoresearch.scan.config import ScanConfig


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
