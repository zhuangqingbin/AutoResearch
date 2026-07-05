"""OTEL 遥测解析器:多形态容错 + 累计/增量自动判别 + cache 命中率表。合成,无网络。

spec: docs/specs/2026-07-05-scan-metering-calibration-wave-design.md §4.2
metric = claude_code.token.usage,维度 type∈{input,output,cacheRead,cacheCreation} × agent.name。
"""
from __future__ import annotations

import json


def _flat(type_, agent, value):
    return json.dumps({"name": "claude_code.token.usage",
                       "attributes": {"type": type_, "agent.name": agent}, "value": value})


def test_parse_flat_and_skip_garbage():
    from autoresearch.trace.telemetry import parse_lines
    lines = [_flat("input", "l4-card", 100),
             "TUI noise ███ not json",
             json.dumps({"name": "claude_code.other_metric", "value": 1}),
             _flat("output", "l4-card", 50)]
    recs = parse_lines(lines)
    assert len(recs) == 2
    assert recs[0] == {"agent": "l4-card", "type": "input", "value": 100.0}


def test_aggregate_cumulative_vs_delta():
    """同 key 单调不减(≥2 点)= 累计计数器 → 取末值;否则 delta → 求和。"""
    from autoresearch.trace.telemetry import aggregate, parse_lines
    lines = [_flat("input", "l4-card", 100), _flat("input", "l4-card", 250),
             _flat("input", "l4-card", 400),
             _flat("output", "l4-card", 30), _flat("output", "l4-card", 20)]
    df = aggregate(parse_lines(lines)).set_index(["agent", "type"])
    assert df.at[("l4-card", "input"), "tokens"] == 400.0     # 累计 → 末值
    assert df.at[("l4-card", "output"), "tokens"] == 50.0     # 非单调 → 求和


def test_parse_otlp_and_console_exporter_shapes():
    from autoresearch.trace.telemetry import parse_lines
    otlp = json.dumps({"resourceMetrics": [{"scopeMetrics": [{"metrics": [
        {"name": "claude_code.token.usage", "sum": {"dataPoints": [
            {"attributes": [{"key": "type", "value": {"stringValue": "cacheRead"}},
                            {"key": "agent.name", "value": {"stringValue": "l4-card"}}],
             "asInt": "1000"}]}}]}]}]})
    console = json.dumps({"descriptor": {"name": "claude_code.token.usage"},
                          "dataPoints": [{"attributes": {"type": "input",
                                                         "query_source": "subagent"},
                                          "value": 77}]})
    recs = parse_lines([otlp, console])
    assert {"agent": "l4-card", "type": "cacheRead", "value": 1000.0} in recs
    assert {"agent": "subagent", "type": "input", "value": 77.0} in recs  # 无 agent.name → query_source 兜底


def test_render_cache_hit_rate():
    from autoresearch.trace.telemetry import aggregate, parse_lines, render
    lines = [_flat("input", "l4-card", 800), _flat("cacheRead", "l4-card", 200),
             _flat("output", "l4-card", 100), _flat("input", "main", 50)]
    md = "\n".join(render(aggregate(parse_lines(lines))))
    assert "l4-card" in md and "20%" in md                    # cacheRead/(input+cacheRead)
    assert "合计" in md


def test_cli_main(tmp_path):
    from autoresearch.trace.telemetry import main
    raw = tmp_path / "telemetry.jsonl"
    raw.write_text("\n".join([_flat("input", "l4-card", 10), "garbage"]), encoding="utf-8")
    out = tmp_path / "token_telemetry.md"
    assert main([str(raw), "--out", str(out)]) == 0
    assert out.exists() and "l4-card" in out.read_text(encoding="utf-8")
