#!/usr/bin/env python3
"""scan-market v2 · L5 整合阶段 —— 漏斗溯源 + 三段 summary + A_pipeline 发布。

design: docs/specs/2026-06-20-scan-market-v2-design.md(§7 整合)

读 context/scan/<date>/ 的漏斗产物(meta.json 计数 + L1_recall_top1000.csv 召回 +
L2_coarse_keep200.csv 粗排 + finalists.csv 精排[带 thesis/risk/catalyst] + details/<ticker>.md
L4 决策卡),用项目 parse_rating 提五档评级 + 仪表盘,产出三段 summary:
  1. 漏斗数量      —— 选集→召回→粗排→精排→研究 各阶段出量 + 卡点标准
  2. 各阶段卡点 & 股票概览 —— 逐阶段"砍了什么/活下来哪类票/代表股"
  3. 投资建议      —— buy-list(评级/目标/R:R)+ 组合视角 + 诚实局限
发布到 reports/scan/<YYYYMMDD>/<HHMM>_summary.md + <HHMM>_detail/(决策卡 + A_pipeline/ 溯源)。

纯确定性(stdlib + parse_rating),零 LLM。

用法:
  uv run --no-sync python scripts/assemble_scan.py 2026-06-20
  uv run --no-sync python scripts/assemble_scan.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import tempfile
from collections import Counter
from datetime import date, datetime
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


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _strip(s: str | None) -> str:
    return (s or "").replace("**", "").strip()


def _parse_dashboard(text: str) -> dict[str, str]:
    """取决策卡里第一张含『评级』的表(决策仪表盘),按表头→数据配成 dict。"""
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


def _decision_text(scan_dir: Path, ticker: str) -> str | None:
    """定位 finalist 的 lite 决策卡:context/scan/<date>/details/<ticker>.md,按 6 位代码 glob 兜底。"""
    base = scan_dir / "details"
    code = ticker.split(".")[0]
    tries = [base / f"{ticker}.md"]
    if base.is_dir():
        tries += sorted(p for p in base.glob(f"{code}*.md"))
    seen: set[Path] = set()
    for p in tries:
        if p in seen:
            continue
        seen.add(p)
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


def _finalist_row(scan_dir: Path, fr: dict) -> dict:
    ticker = (fr.get("ticker") or fr.get("code") or "").strip()
    text = _decision_text(scan_dir, ticker)
    if text is None:
        return {**fr, "rating": "—", "target": "⚠️卡片缺失", "rr": "—", "proposal": "—", "conf": "—"}
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


# ───────────────────────── 三段 summary ─────────────────────────


def _funnel_rows(meta: dict, n_l2, n_l3, n_cards) -> list[str]:
    return [
        "| 阶段 | 名称 | 出量 | 卡点标准 |", "|---|---|---:|---|",
        f"| L0 | 选集 | {meta.get('universe', '?')} | 全A {meta.get('universe_raw', '?')} → 硬门(剔ST/退/停牌/次新, 市值地板, 含北交所) |",
        f"| L1 | 召回 | {meta.get('recall_n', '?')} | 轻门 + 行业条件化复合分(T+1 IC 校准) top |",
        f"| L2 | 粗排 | {n_l2} | AI 资深投资师 keep/cut(信号共振 / 排陷阱) |",
        f"| L3 | 精排 | {n_l3} | 增量真证据 + 论点/红队(确信度−脆弱度) |",
        f"| L4 | 研究 | {n_cards} 卡 | analyze-ticker-lite 决策卡 |",
    ]


def _stage_overview(label: str, rows: list[dict], reason: str) -> list[str]:
    if not rows:
        return [f"\n**{label}** — _无 staging,跳过_"]
    inds = Counter(r.get("industry", "") for r in rows if r.get("industry"))
    top = "、".join(f"{k}({v})" for k, v in inds.most_common(5)) or "—"
    reps = ", ".join(str(r.get("name", "")) for r in rows[:6])
    return [f"\n**{label}** — {reason}", f"- 行业分布 top5:{top}", f"- 代表股:{reps}"]


def _portfolio_note(rows: list[dict]) -> str:
    secs = Counter((r.get("sector") or r.get("industry") or "?") for r in rows)
    top = "、".join(f"{k}×{v}" for k, v in secs.most_common(5))
    buys = sum(1 for r in rows if r.get("rating") in ("Buy", "Overweight"))
    return (f"买入/超配 **{buys}** 只;板块集中度:{top or '—'}。"
            "注意单板块过度集中的相关性风险;按评级×置信度分配仓位,催化日历做节奏。")


def _knowledge_note(rows: list[dict]) -> str:
    """浮出与 buy-list 标的/行业相关的 active 经验 + 未决反馈(闭环记忆注回报告骨架)。

    store 空 / feedback_store 不可用 → 返回空串(向后兼容,老路径不破)。
    """
    try:
        import feedback_store as fs
    except Exception:  # noqa: BLE001 — 知识库是可选层,缺了不影响出报告
        return ""
    codes = {str(r.get("code")) for r in rows if r.get("code")}
    scopes: list = [("global", "*")]
    for r in rows:
        if r.get("code"):
            scopes.append(("ticker", str(r["code"])))
        ind = r.get("sector") or r.get("industry")
        if ind:
            scopes.append(("industry", ind))
    try:
        lessons = fs.lessons_for(scopes)
        open_fb = [f for f in fs._read_jsonl(fs._FEEDBACK)
                   if f.get("status") == "open"
                   and (f.get("scope", {}).get("kind") == "global"
                        or f.get("scope", {}).get("value") in codes)]
    except Exception:  # noqa: BLE001
        return ""
    if not lessons and not open_fb:
        return ""
    lines = ["## 📌 经验 / 未决反馈(闭环记忆)"]
    if lessons:
        lines.append("**生效经验**(已注入 L2/L3 校准 + 本次研判):")
        for lsn in lessons[:8]:
            sc = lsn.get("scope", {})
            tag = "" if sc.get("kind") == "global" else f"[{sc.get('value')}] "
            lines.append(f"- {tag}{lsn['rule']}  _(conf {lsn.get('confidence', 0):.2f})_")
    if open_fb:
        lines.append("**未决反馈**(待 retro / 后续消化):")
        for f in open_fb[:6]:
            lines.append(f"- ({f.get('verdict')}) {str(f.get('note', ''))[:50]} — `{f.get('id')}`")
    return "\n".join(lines) + "\n"


def _self_review_banner(scan_dir: Path, rows: list[dict], summary_text: str) -> str:
    """发布前机械自检(self_review 硬门)→ 报告顶部 banner。缺依赖/无问题 → 空串(老路不破)。"""
    try:
        import self_review
    except Exception:  # noqa: BLE001
        return ""
    l1 = {}
    if (scan_dir / "L1_scored_full.csv").exists():
        l1 = {str(r.get("code", "")).zfill(6): r for r in _read_csv(scan_dir / "L1_scored_full.csv")}
    finals = []
    for r in rows:
        lf = l1.get(str(r.get("code", "")).zfill(6), {})
        finals.append({"code": str(r.get("code", "")).zfill(6), "rating": r.get("rating"),
                       "sector": r.get("sector") or r.get("industry"),
                       "composite": lf.get("composite"), "winner_rate": lf.get("winner_rate"),
                       "pct_60d": lf.get("pct_60d"), "rsi6": lf.get("rsi6")})
    n_present = sum(1 for r in rows if r.get("target") != "⚠️卡片缺失")
    lessons = []
    try:
        import feedback_store as fs
        lessons = fs.lessons_for([("global", "*")])
    except Exception:  # noqa: BLE001
        pass
    ctx = {"finalists": finals, "n_cards_expected": len(rows), "n_cards_present": n_present,
           "summary_text": summary_text, "lessons": lessons}
    return self_review.render_banner(self_review.review(ctx))


def build_summary(scan_dir: Path, analysis_date: str, hhmm: str, compact: str) -> str:
    meta = _load_json(scan_dir / "meta.json")
    recall = _read_csv(scan_dir / "L1_recall_top1000.csv")
    keep = _read_csv(scan_dir / "L2_coarse_keep200.csv")
    finals = _read_csv(scan_dir / "finalists.csv")
    rows = [_finalist_row(scan_dir, fr) for fr in finals]
    rows.sort(key=_sortkey)

    out = [f"# A股扫描 v2 · Buy-List & 漏斗 — {analysis_date} {hhmm[:2]}:{hhmm[2:]}\n",
           "_六段漏斗:选集→召回→粗排→精排→研究→整合。Claude 为引擎,**仅供研究,非投资建议。**_\n"]

    # ── 1. 漏斗数量 ──
    out += ["## 1. 漏斗(数量)"] + _funnel_rows(meta, len(keep) or "?", len(finals), len(rows)) + [""]

    # ── 2. 各阶段卡点 + 概览 ──
    out += ["## 2. 各阶段卡点 & 股票概览"]
    out += _stage_overview("召回(L1)", recall, "复合分 top;快因子(动量/资金结构/技术)主导排序,慢因子带下游判断。")
    out += _stage_overview("粗排(L2)", keep, "资深投资师粗筛,剔信号矛盾/明显陷阱。")
    out += ["", "**精排(L3)入选(含论点/风险/催化)**:"]
    if finals:
        for fr in finals[:15]:
            out.append(f"- **{fr.get('name', '')}({fr.get('code', '')})** · {fr.get('sector', '')} — "
                       f"多头:{_strip(fr.get('thesis', ''))};风险:{_strip(fr.get('risk', ''))};"
                       f"催化:{_strip(fr.get('catalyst', ''))}")
    else:
        out.append("_无 finalists.csv_")
    out.append("")

    # ── 3. 投资建议 ──
    out += [f"## 3. 投资建议(buy-list, {len(rows)} 只,按 评级 → 确信度 排序)\n",
            "| # | 代码 | 名称 | 板块 | 评级 | 目标(EV) | R:R | 提案 | 置信度 | 论点一句 |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        out.append(
            f"| {i} | {r.get('code', '')} | {r.get('name', '')} | {r.get('sector') or r.get('industry', '')} "
            f"| **{r.get('rating', '—')}** | {r.get('target', '—')} | {r.get('rr', '—')} | {r.get('proposal', '—')} "
            f"| {r.get('conf', '—')} | {_strip(r.get('thesis') or r.get('triage_reason', ''))} |")
    out += ["", "### 组合视角", _portfolio_note(rows), ""]
    kn = _knowledge_note(rows)
    if kn:
        out += [kn]
    out += ["## 诚实局限",
            "- 召回为启发式 + T+1 单 horizon IC 校准,随 regime 漂移;L2/L3 为 Claude 推理产出。",
            "- 业绩/龙虎榜/预告有披露滞后;无权限端点降级标注。",
            "- A股涨跌停/停牌使名义止损未必可执行(见各决策卡执行段)。",
            f"\n_明细 + 漏斗溯源:`reports/scan/{compact}/{hhmm}_detail/`(决策卡 + A_pipeline/)_"]
    body = "\n".join(out)
    banner = _self_review_banner(scan_dir, rows, body)   # UZI self-review 硬门:fail 顶到最前
    return f"{banner}\n{body}" if banner else body


# ───────────────────────── 发布 ─────────────────────────


def _publish_details(scan_dir: Path, detail_out: Path) -> int:
    """把 L4 staging 的 lite 决策卡(context/scan/<date>/details/*.md)复制进带时间戳的 detail/。"""
    src = scan_dir / "details"
    n = 0
    if src.is_dir():
        for p in sorted(src.glob("*.md")):
            shutil.copy2(p, detail_out / p.name)
            n += 1
    return n


def _funnel_md(scan_dir: Path, analysis_date: str) -> str:
    meta = _load_json(scan_dir / "meta.json")
    keep = _read_csv(scan_dir / "L2_coarse_keep200.csv")
    finals = _read_csv(scan_dir / "finalists.csv")
    lines = [f"# 漏斗溯源 — {analysis_date}\n", "六段:选集→召回→粗排→精排→研究→整合。\n"]
    lines += _funnel_rows(meta, len(keep) or "?", len(finals), len(finals))
    lines += ["", f"权重来源:{meta.get('weights_source', '?')};universe 源:{meta.get('source', '?')}。",
              "各阶段明细见同目录 CSV(L1_recall_top1000 / L2_coarse_keep200 / L3_fine_finalists)。"]
    return "\n".join(lines)


def _archive_reasoning(scan_dir: Path, pdir: Path) -> int:
    """把各阶段 LLM 中间推理件(prompt/批表/keep-judged/calib)归档到
    A_pipeline/reasoning/{l2,l3,l4}/,让发布报告自带可追溯的 LLM 输入;缺失静默跳过。"""
    routes = [
        ("l2", lambda n: n.startswith("_l2") or n.startswith("_calib") or n == "L2a_bucketed.csv"),
        ("l3", lambda n: n.startswith("_l3")),
        ("l4", lambda n: n.startswith("_l4")),
    ]
    n = 0
    for stage, match in routes:
        for p in sorted(scan_dir.glob("*")):
            if p.is_file() and match(p.name):
                dst = pdir / "reasoning" / stage
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst / p.name)
                n += 1
    return n


def _publish_pipeline(scan_dir: Path, detail_out: Path, analysis_date: str) -> int:
    """把各阶段 staging 产物发布到 <HHMM>_detail/A_pipeline/(漏斗溯源 + reasoning 推理留痕)。"""
    pdir = detail_out / "A_pipeline"
    pdir.mkdir(parents=True, exist_ok=True)
    mapping = {
        "meta.json": "L0_universe_meta.json",
        "L1_scored_full.csv": "L1_scored_full.csv",        # 全量打分(所有过门股 sorted + recalled 标记)
        "L1_recall_top1000.csv": "L1_recall_top1000.csv",  # 召回工作集(top N)
        "L2_scored_full.csv": "L2_scored_full.csv",        # 粗排全量(若有:1000 只带 keep/cut + l2 分)
        "L2_coarse_keep200.csv": "L2_coarse_keep200.csv",  # 粗排保留
        "L3_judged_full.csv": "L3_judged_full.csv",        # 精排全量判断(所有粗排入选,非仅 finalists)
        "finalists.csv": "L3_fine_finalists.csv",          # 精排最终入选(top N)
    }
    n = 0
    for src, dst in mapping.items():
        p = scan_dir / src
        if p.exists():
            shutil.copy2(p, pdir / dst)
            n += 1
    wp = Path("context/factor_lab/weights.json")
    if wp.exists():
        shutil.copy2(wp, pdir / "L1_weights.json")
        n += 1
    (pdir / "funnel.md").write_text(_funnel_md(scan_dir, analysis_date), encoding="utf-8")
    n += _archive_reasoning(scan_dir, pdir)
    return n + 1


def run(analysis_date: str, scan_dir: Path | None = None, out_root: Path | None = None,
        hhmm: str | None = None) -> Path:
    scan_dir = scan_dir or Path("context/scan") / analysis_date
    out_root = out_root or Path("reports/scan")
    hhmm = hhmm or datetime.now().strftime("%H%M")
    compact = analysis_date.replace("-", "")
    out_base = out_root / compact
    detail_out = out_base / f"{hhmm}_detail"
    detail_out.mkdir(parents=True, exist_ok=True)
    n_cards = _publish_details(scan_dir, detail_out)
    n_pipe = _publish_pipeline(scan_dir, detail_out, analysis_date)
    md = build_summary(scan_dir, analysis_date, hhmm, compact)
    summary_path = out_base / f"{hhmm}_summary.md"
    summary_path.write_text(md, encoding="utf-8")
    print(f"[L5 整合] summary → {summary_path}")
    print(f"[L5 整合] details → {detail_out}  ({n_cards} 张卡 + A_pipeline/ {n_pipe} 件溯源)")
    return summary_path


# ───────────────────────── 离线自测(无网络) ─────────────────────────


def _selftest() -> int:
    fails: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = "2026-06-20"
        scan = root / "context/scan" / d
        (scan / "details").mkdir(parents=True)
        (scan / "meta.json").write_text(json.dumps({
            "universe": 5483, "recall_n": 1000, "source": "tushare",
            "weights_source": "factor_lab.calibrate"}), encoding="utf-8")
        # L1 召回(概览用)
        with (scan / "L1_recall_top1000.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["code", "name", "industry", "composite"])
            w.writeheader()
            for i in range(20):
                w.writerow({"code": f"{300000 + i:06d}", "name": f"光{i}", "industry": "电子",
                            "composite": 90 - i})
        # L2 粗排
        with (scan / "L2_coarse_keep200.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["code", "name", "industry", "l2_score"])
            w.writeheader()
            for i in range(8):
                w.writerow({"code": f"{300000 + i:06d}", "name": f"光{i}", "industry": "电子", "l2_score": 80 - i})
        # L3 精排 finalists(带 thesis/risk/catalyst)
        with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ticker", "code", "name", "sector", "lenses", "conviction",
                                              "triage_lean", "triage_reason", "thesis", "risk", "catalyst"])
            w.writeheader()
            w.writerow({"ticker": "300476", "code": "300476", "name": "甲", "sector": "光模块",
                        "lenses": "动量", "conviction": "203", "triage_lean": "看多", "triage_reason": "加速",
                        "thesis": "AI 光模块需求超预期", "risk": "估值高", "catalyst": "Q2 财报"})
            w.writerow({"ticker": "600519", "code": "600519", "name": "乙", "sector": "白酒",
                        "lenses": "价值", "conviction": "125", "triage_lean": "中性", "triage_reason": "低估",
                        "thesis": "现金牛低估", "risk": "需求弱", "catalyst": "中报"})
            w.writerow({"ticker": "002384", "code": "002384", "name": "丙", "sector": "光模块",
                        "lenses": "动量", "conviction": "118", "triage_lean": "回避", "triage_reason": "过热",
                        "thesis": "x", "risk": "y", "catalyst": "z"})
        # L4 决策卡(002384 故意缺卡 → 测降级)
        for tk, rating, prop in [("300476", "Overweight", "BUY"), ("600519", "Hold", "HOLD")]:
            (scan / "details" / f"{tk}.md").write_text(
                "# 决策卡\n## 决策仪表盘\n| 评级 | 现价 | EV目标 | R:R | 置信度 |\n|---|---|---|---|---|\n"
                f"| **{rating}** | 100元 | 130元(+30%) | 2.1:1 | 中 |\n\n**Rating**: {rating}\n\n"
                f"FINAL TRANSACTION PROPOSAL: **{prop}**\n", encoding="utf-8")
        # 中间推理件(应归档到 A_pipeline/reasoning/{l2,l3,l4})
        for fn in ("_calib.md", "_l2_prompt_trend.md", "_l2_batch_0.md", "_l3_judged_0.csv", "_l4_prompt.md"):
            (scan / fn).write_text("x", encoding="utf-8")
        summary_path = run(d, scan_dir=scan, out_root=root / "reports/scan", hhmm="0930")
        md = summary_path.read_text(encoding="utf-8")
        pdir = root / "reports/scan/20260620/0930_detail/A_pipeline"
        for fn in ("L1_recall_top1000.csv", "L2_coarse_keep200.csv", "L3_fine_finalists.csv", "funnel.md",
                   "L0_universe_meta.json"):
            if not (pdir / fn).exists():
                fails.append(f"A_pipeline 缺 {fn}")
        if not (root / "reports/scan/20260620/0930_detail/300476.md").exists():
            fails.append("决策卡未发布")
        rdir = pdir / "reasoning"
        for stage, fn in [("l2", "_calib.md"), ("l2", "_l2_batch_0.md"),
                          ("l3", "_l3_judged_0.csv"), ("l4", "_l4_prompt.md")]:
            if not (rdir / stage / fn).exists():
                fails.append(f"reasoning 归档缺 {stage}/{fn}")

    for must in ["## 1. 漏斗", "## 2. 各阶段", "## 3. 投资建议", "5483", "1000", "选集", "召回", "粗排",
                 "精排", "Overweight", "+30%", "BUY", "⚠️卡片缺失", "AI 光模块需求超预期", "组合视角"]:
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
    print("SELFTEST ✅  L5 三段(漏斗/各阶段概览/投资建议)+ A_pipeline 发布 + reasoning 留痕 "
          "+ 缺卡降级 + 排序 全过")
    return 0


# ───────────────────────── CLI ─────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="scan-market L5 整合(漏斗 + 三段 summary + A_pipeline)")
    ap.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    ap.add_argument("--selftest", action="store_true", help="离线验证解析/排序逻辑(无网络)")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    run(args.date or date.today().isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
