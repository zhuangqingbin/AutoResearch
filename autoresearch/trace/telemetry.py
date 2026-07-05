#!/usr/bin/env python3
"""OTEL 遥测解析器 —— token 真计量 + cache 命中审计(确定性,零 LLM)。

design: docs/specs/2026-07-05-scan-metering-calibration-wave-design.md §4

跑扫描的 Claude Code 会话从带遥测 env 的 shell 启动(CLAUDE_CODE_ENABLE_TELEMETRY=1 +
OTEL_METRICS_EXPORTER=console + OTEL_METRIC_EXPORT_INTERVAL=30000 + OTEL_LOG_TOOL_DETAILS=1),
原始导出(console 重定向 / prometheus 轮询抓取)喂本 CLI → 按 `agent.name × type` 聚合
markdown 表 + cache 命中率(= cacheRead/(input+cacheRead))。**生产派发路径零改动,仪器旁路**。

  uv run --no-sync python -m autoresearch.trace.telemetry <raw> [--out reports/scan/<run>/token_telemetry.md]

解析容错:未知行/TUI 噪声跳过不抛;兼容三种形态(平面 {name,attributes,value}、
console exporter {descriptor,dataPoints}、OTLP resourceMetrics 嵌套);累计/增量计数器
按 key 单调性自动判别(单调不减≥2 点 = 累计取末值,否则求和)。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_METRIC = "claude_code.token.usage"
_TYPES = ("input", "output", "cacheRead", "cacheCreation")


def _attrs_dict(attrs) -> dict:
    """attributes 归一:OTLP list[{key,value:{stringValue…}}] 或平面 dict → dict。"""
    if isinstance(attrs, dict):
        return attrs
    out: dict = {}
    if isinstance(attrs, list):
        for a in attrs:
            if isinstance(a, dict) and "key" in a:
                v = a.get("value")
                if isinstance(v, dict):
                    v = next(iter(v.values()), None)
                out[a["key"]] = v
    return out


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _emit(attrs, value, out: list[dict]) -> None:
    a = _attrs_dict(attrs)
    t, v = a.get("type"), _num(value)
    if not t or v is None:
        return
    agent = a.get("agent.name") or a.get("query_source") or "unknown"
    out.append({"agent": str(agent), "type": str(t), "value": v})


def _walk(node, out: list[dict]) -> None:
    """递归扫任意 JSON,遇 `claude_code.token.usage` 记录即抽取(其余子树继续下钻)。"""
    if isinstance(node, list):
        for x in node:
            _walk(x, out)
        return
    if not isinstance(node, dict):
        return
    desc = node.get("descriptor")
    name = node.get("name") or (desc.get("name") if isinstance(desc, dict) else None)
    if name == _METRIC:
        val = node.get("value", node.get("asInt", node.get("asDouble")))
        if val is not None and not isinstance(val, (dict, list)):
            _emit(node.get("attributes"), val, out)
        dps = node.get("dataPoints")
        if dps is None:
            for k in ("sum", "gauge", "histogram"):
                sub = node.get(k)
                if isinstance(sub, dict) and "dataPoints" in sub:
                    dps = sub["dataPoints"]
                    break
        for dp in dps or []:
            if isinstance(dp, dict):
                _emit(dp.get("attributes"), dp.get("value", dp.get("asInt", dp.get("asDouble"))), out)
        return
    for v in node.values():
        _walk(v, out)


def parse_lines(lines) -> list[dict]:
    """原始导出行 → [{agent, type, value}](非 JSON/无关行静默跳过)。"""
    out: list[dict] = []
    for line in lines:
        s = str(line).strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            continue
        _walk(obj, out)
    return out


def aggregate(records: list[dict]) -> pd.DataFrame:
    """按 (agent, type) 聚合 → tokens。同 key 单调不减(≥2 点)= 累计计数器 → 取末值;
    否则视为 delta → 求和(OTEL temporality 未知时的稳健读法)。"""
    cols = ["agent", "type", "tokens", "mode"]
    if not records:
        return pd.DataFrame(columns=cols)
    rows = []
    for (agent, t), g in pd.DataFrame(records).groupby(["agent", "type"]):
        vs = g["value"].tolist()
        cumulative = len(vs) >= 2 and all(b >= a for a, b in zip(vs, vs[1:], strict=False))
        rows.append({"agent": agent, "type": t,
                     "tokens": float(vs[-1] if cumulative else sum(vs)),
                     "mode": "cum" if cumulative else "sum"})
    return pd.DataFrame(rows, columns=cols)


def render(df: pd.DataFrame) -> list[str]:
    out = ["# token 遥测(claude_code.token.usage;cache命中 = cacheRead/(input+cacheRead))", ""]
    if df is None or not len(df):
        return out + ["_无遥测记录(检查 CLAUDE_CODE_ENABLE_TELEMETRY / OTEL_METRICS_EXPORTER 配置,"
                      "或原始导出里 metric 名不符)_"]
    piv = df.pivot_table(index="agent", columns="type", values="tokens", aggfunc="sum")
    for c in _TYPES:
        if c not in piv.columns:
            piv[c] = 0.0
    piv = piv[list(_TYPES)].fillna(0.0)
    piv.loc["合计"] = piv.sum()
    out += ["| agent | input | output | cacheRead | cacheCreation | cache命中 |",
            "|---|---|---|---|---|---|"]
    for agent, r in piv.iterrows():
        denom = r["input"] + r["cacheRead"]
        hit = f"{r['cacheRead'] / denom:.0%}" if denom else "—"
        out.append(f"| {agent} | {r['input']:,.0f} | {r['output']:,.0f} | {r['cacheRead']:,.0f} "
                   f"| {r['cacheCreation']:,.0f} | {hit} |")
    out += ["", "_对账:与当次 run 落稿估算 token 表并读(估算 vs 真实,缺口=过程读写+system);"
            "l4-card 行 cacheRead≈0 → 并发 cache 写入竞态坐实(修法另议,勿预做)。_"]
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="OTEL 遥测解析(token 真计量 + cache 审计,零 LLM)")
    ap.add_argument("raw", help="原始导出文件(console 重定向 / prometheus 抓取)")
    ap.add_argument("--out", default=None, help="markdown 落点(缺省打印 stdout)")
    args = ap.parse_args(argv)
    lines = Path(args.raw).read_text(encoding="utf-8", errors="replace").splitlines()
    md = "\n".join(render(aggregate(parse_lines(lines))))
    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(md + "\n", encoding="utf-8")
        print(f"[telemetry] → {outp}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
