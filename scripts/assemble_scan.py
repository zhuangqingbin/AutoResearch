#!/usr/bin/env python3
"""scan-market · L4 — 综合 finalists 全量报告 + L2 板块榜 → 一页 scan_summary.md。

design: docs/specs/2026-06-20-scan-market-design.md

读 context/scan/<date>/ 的 finalists.csv(L3a 分诊出的 ~30,带标签)+ sectors.csv
(L2 板块排名)+ meta.json(漏斗计数);对每只 finalist 找 reports/<date>/<ticker>/
的 PM 决策,用项目自己的 parse_rating 提取五档评级 + 仪表盘(目标/R:R/置信度)+
FINAL TRANSACTION PROPOSAL;按 评级 → 确信度 排成 buy-list,叠加板块结论,落
reports/scan/<date>/scan_summary.md。

纯确定性(stdlib + parse_rating),零 LLM。报告内容本身是 L3b 时 Claude 的推理产出。
仪表盘按"表头→数据"映射解析,容忍两版报告的列序/列名差异;报告缺失则降级标注。

用法:
  uv run --no-sync python scripts/assemble_scan.py 2026-06-20
  uv run --no-sync python scripts/assemble_scan.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import tempfile
from datetime import date
from pathlib import Path

from tradingagents.agents.utils.rating import RATINGS_5_TIER, parse_rating

TIER_RANK = {r: i for i, r in enumerate(RATINGS_5_TIER)}  # Buy=0 … Sell=4

_PROPOSAL_RE = re.compile(r"FINAL TRANSACTION PROPOSAL[:\s*]*\**\s*(BUY|HOLD|SELL)", re.IGNORECASE)
_CONF_RE = re.compile(r"置信度[:：]\s*\**\s*([高中低]+)")


# ───────────────────────── 解析 helpers ─────────────────────────


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _strip(s: str | None) -> str:
    return (s or "").replace("**", "").strip()


def _fmt(v) -> str:
    try:
        return f"{float(v):+.2f}"
    except (TypeError, ValueError):
        return str(v or "—")


def _parse_dashboard(text: str) -> dict[str, str]:
    """取 decision.md 里第一张含『评级』的表(决策仪表盘),按表头→数据配成 dict。

    按表头映射(而非固定列序)→ 容忍两版报告列序/列名的差异。
    """
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("|") and "评级" in s and i + 2 < len(lines):
            header = [c.strip() for c in s.strip("|").split("|")]
            data = [_strip(c) for c in lines[i + 2].strip().strip("|").split("|")]
            if len(data) == len(header):
                return dict(zip(header, data, strict=True))
    return {}


def _get(d: dict[str, str], *needles: str) -> str:
    for k, v in d.items():
        if any(n in k for n in needles):
            return v
    return ""


def _decision_text(reports_root: Path, analysis_date: str, ticker: str) -> str | None:
    """定位 finalist 的 PM 决策文本:先 <ticker>/,再按 6 位代码 glob 兜底。"""
    base = reports_root / analysis_date
    tries = [base / ticker]
    code = ticker.split(".")[0]
    if base.is_dir():
        tries += sorted(d for d in base.glob(f"{code}*") if d.is_dir())
    seen: set[Path] = set()
    for d in tries:
        if d in seen:
            continue
        seen.add(d)
        for rel in ("4_portfolio/decision.md", "complete_report.md"):
            p = d / rel
            if p.exists():
                return p.read_text(encoding="utf-8")
    return None


# ───────────────────────── 组装 ─────────────────────────


def _finalist_row(reports_root: Path, analysis_date: str, fr: dict) -> dict:
    ticker = (fr.get("ticker") or fr.get("code") or "").strip()
    text = _decision_text(reports_root, analysis_date, ticker)
    if text is None:
        return {**fr, "rating": "—", "target": "⚠️报告缺失", "rr": "—", "proposal": "—", "conf": "—"}
    dash = _parse_dashboard(text)
    conf = _get(dash, "置信度")
    if not conf:
        m = _CONF_RE.search(text)
        conf = m.group(1) if m else "—"
    prop = _PROPOSAL_RE.search(text)
    return {
        **fr,
        "rating": parse_rating(text),
        "target": _get(dash, "EV目标", "目标") or "—",
        "rr": _get(dash, "R:R") or "—",
        "proposal": prop.group(1).upper() if prop else "—",
        "conf": conf or "—",
    }


def _sortkey(r: dict):
    tier = TIER_RANK.get(r.get("rating", ""), 99)
    try:
        conv = float(r.get("conviction") or 0)
    except ValueError:
        conv = 0.0
    return (tier, -conv)


def build_summary(scan_dir: Path, reports_root: Path, analysis_date: str) -> str:
    finalists = _read_csv(scan_dir / "finalists.csv")
    sectors = _read_csv(scan_dir / "sectors.csv")
    meta_p = scan_dir / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}

    rows = [_finalist_row(reports_root, analysis_date, fr) for fr in finalists]
    rows.sort(key=_sortkey)

    out: list[str] = [
        f"# A股扫描 · Buy-List & 板块结论 — {analysis_date}\n",
        "_Engine: scan-market — 确定性漏斗(L0–L2)+ Claude 分诊/深挖(L3)+ 综合(L4)。"
        "**仅供研究,非投资建议。**_\n",
    ]

    out += [
        "## 漏斗",
        f"全市场 **{meta.get('universe', '?')}** →(L1 四透镜)**{meta.get('survivors', '?')}** "
        f"→(L2 top{meta.get('top_sectors', '?')}板块)**{meta.get('in_top_sectors', '?')}** "
        f"→(L3a 分诊)**{len(finalists)}** 全量深挖\n",
    ]

    top_secs = [s for s in sectors if str(s.get("is_top")).lower() == "true"] or sectors[:5]
    out += ["## 强势板块(L2)\n",
            "| # | 板块 | 板块分 | survivors | 跨透镜 | 中位主力净流入(亿) | 中位60日% |",
            "|---|---|---:|---:|---:|---:|---:|"]
    for i, s in enumerate(top_secs, 1):
        out.append(f"| {i} | {s.get('industry', '')} | {s.get('sector_score', '')} | "
                   f"{s.get('n_survivors', '')} | {s.get('n_lenses', '')} | "
                   f"{_fmt(s.get('median_inflow_yi'))} | {_fmt(s.get('median_pct_60d'))} |")
    out.append("")

    out += [f"## Buy-List({len(rows)} 只,按 评级 → 确信度 排序)\n",
            "| # | 代码 | 名称 | 板块 | 命中透镜 | 评级 | 目标(EV) | R:R | 提案 | 置信度 | 分诊一句话 |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        out.append(
            f"| {i} | {r.get('code', '')} | {r.get('name', '')} | {r.get('sector') or r.get('industry', '')} "
            f"| {r.get('lenses', '')} | **{r.get('rating', '—')}** | {r.get('target', '—')} | {r.get('rr', '—')} "
            f"| {r.get('proposal', '—')} | {r.get('conf', '—')} | {_strip(r.get('triage_reason', ''))} |")
    out.append("")

    out += [
        "## 诚实局限",
        "- 筛选(L1/L2)为**启发式粗筛,无回测**;权重为默认起步值。",
        "- 业绩数据有**披露滞后**(用最近可得报告期);动量/资金为分析日实时。",
        "- finalist 深挖与分诊为 **Claude 推理产出**,非自动引擎;每份报告附完整证据链。",
        "- A股**涨跌停/停牌**使名义止损未必可执行(见各报告执行段)。",
        f"\n_明细报告:`reports/{analysis_date}/<代码>/complete_report.md`_",
    ]
    return "\n".join(out)


def run(analysis_date: str, scan_dir: Path | None = None, reports_root: Path | None = None) -> Path:
    scan_dir = scan_dir or Path("context/scan") / analysis_date
    reports_root = reports_root or Path("reports")
    md = build_summary(scan_dir, reports_root, analysis_date)
    out_path = Path("reports/scan") / analysis_date / "scan_summary.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"[L4] scan_summary → {out_path}")
    return out_path


# ───────────────────────── 离线自测(无网络) ─────────────────────────


def _selftest() -> int:
    fails: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = "2026-06-20"
        scan = root / "context/scan" / d
        scan.mkdir(parents=True)
        with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ticker", "code", "name", "sector", "lenses",
                                              "conviction", "triage_lean", "triage_reason"])
            w.writeheader()
            w.writerow({"ticker": "300476", "code": "300476", "name": "甲", "sector": "光模块",
                        "lenses": "动量,成长", "conviction": "203", "triage_lean": "看多",
                        "triage_reason": "加速+资金"})
            w.writerow({"ticker": "600519", "code": "600519", "name": "乙", "sector": "白酒",
                        "lenses": "价值", "conviction": "125", "triage_lean": "中性",
                        "triage_reason": "低估但缺催化"})
            w.writerow({"ticker": "002384", "code": "002384", "name": "丙", "sector": "光模块",
                        "lenses": "动量", "conviction": "118", "triage_lean": "回避",
                        "triage_reason": "过热"})
        with (scan / "sectors.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["industry", "sector_score", "n_survivors", "n_lenses",
                                              "median_inflow_yi", "median_pct_60d", "is_top"])
            w.writeheader()
            w.writerow({"industry": "光模块", "sector_score": "88.0", "n_survivors": "7", "n_lenses": "3",
                        "median_inflow_yi": "1.2", "median_pct_60d": "45.0", "is_top": "True"})
            w.writerow({"industry": "白酒", "sector_score": "55.0", "n_survivors": "3", "n_lenses": "1",
                        "median_inflow_yi": "-0.3", "median_pct_60d": "-5.0", "is_top": "True"})
        (scan / "meta.json").write_text(
            json.dumps({"universe": 5400, "survivors": 150, "in_top_sectors": 100, "top_sectors": 5}),
            encoding="utf-8")
        reports = root / "reports"
        for tk, rating, prop in [("300476", "Overweight", "BUY"), ("600519", "Hold", "HOLD")]:
            dd = reports / d / tk / "4_portfolio"
            dd.mkdir(parents=True)
            (dd / "decision.md").write_text(
                "# PM\n## 决策仪表盘\n| 评级 | 现价 | EV目标 | R:R | 置信度 |\n|---|---|---|---|---|\n"
                f"| **{rating}** | 100元 | 130元(+30%) | 2.1:1 | 中 |\n\n**Rating**: {rating}\n\n"
                f"FINAL TRANSACTION PROPOSAL: **{prop}**\n", encoding="utf-8")
        md = build_summary(scan, reports, d)

    for must in ["Buy-List", "光模块", "300476", "Overweight", "+30%", "2.1:1", "BUY", "⚠️报告缺失", "漏斗", "5400"]:
        if must not in md:
            fails.append(f"summary 缺 '{must}'")
    i_ow, i_hold, i_miss = md.find("300476"), md.find("600519"), md.find("002384")
    if not (i_ow < i_hold < i_miss):
        fails.append(f"buy-list 排序错: ow={i_ow} hold={i_hold} miss={i_miss}")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  L4 解析(parse_rating/仪表盘/提案/置信度)+ 排序 + 缺报告降级 全通过")
    return 0


# ───────────────────────── CLI ─────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="scan-market L4 综合(finalists 报告 + 板块榜 → scan_summary.md)")
    ap.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    ap.add_argument("--selftest", action="store_true", help="离线验证解析/排序逻辑(无网络)")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    run(args.date or date.today().isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
