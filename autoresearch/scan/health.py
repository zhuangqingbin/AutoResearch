#!/usr/bin/env python3
"""scan-market · 运行健康 + 现场索引(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-observability-design.md §1

一次 scan 的"体检报告 + 导航页":关键产物在位表、因子 NaN 降级、finalist 逐日重叠
(churn,卡片复用的前置读数)、L4 阶段效能(早停/满卡/复用/P4 翻盘)、买单计数。
retro 读 run_health.json 提示"勿把数据病当因子病";index.md 是第二天复盘的入口。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

# 关键产物在位表(缺 = 流程段没跑/失败;market_view/watchlist 是可选段,缺了只提示不报错)
_ARTIFACTS = ["L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv",
              "finalists.csv", "market_view.md", "verify.csv", "watchlist_status.csv",
              "gate_fires.csv", "weights_used.json", "L3_judged_full.csv"]
_CORE = {"L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv", "finalists.csv"}

# NaN 体检的关键因子列(L1_recall 口径;降级 = 该组权限缺/端点挂,IC 读数打折扣)
_FACTOR_COLS = ["composite", "main_net_ratio", "winner_rate", "chip_concentration",
                "hk_ratio", "rsi6", "cmf_20", "pe", "np_yoy", "pct_60d"]

_P4_RE = re.compile(r"进入P4倾向[:：]\s*\**(Buy|Overweight|Hold|Underweight|Sell)", re.IGNORECASE)


def _read(p: Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(p, dtype={"code": str}) if p.exists() else None
    except Exception:  # noqa: BLE001
        return None


def nan_report(scan_dir: Path, thresh: float = 0.30) -> tuple[dict, list[str]]:
    """L1_recall 关键因子 NaN 率 → ({col: rate}, 降级列表)。缺文件 → ({}, [])。"""
    df = _read(Path(scan_dir) / "L1_recall_top1000.csv")
    if df is None or not len(df):
        return {}, []
    rates, degraded = {}, []
    for c in _FACTOR_COLS:
        if c not in df.columns:
            continue
        r = float(pd.to_numeric(df[c], errors="coerce").isna().mean())
        rates[c] = round(r, 3)
        if r > thresh:
            degraded.append(c)
    return rates, degraded


def finalist_churn(scan_dir: Path) -> dict | None:
    """与上一 scan 日 finalists 的重叠(卡片 TTL 复用的前置测量)。无前日 → None。"""
    scan_dir = Path(scan_dir)
    today = _read(scan_dir / "finalists.csv")
    if today is None or "code" not in today.columns:
        return None
    prevs = sorted((p for p in scan_dir.parent.iterdir()
                    if p.is_dir() and p.name[:2] == "20" and p.name < scan_dir.name
                    and (p / "finalists.csv").exists()), reverse=True)
    if not prevs:
        return None
    prev = _read(prevs[0] / "finalists.csv")
    if prev is None or "code" not in prev.columns:
        return None
    t = set(today["code"].astype(str).str.zfill(6))
    y = set(prev["code"].astype(str).str.zfill(6))
    rep = t & y
    return {"prev_date": prevs[0].name, "n_prev": len(y), "n_today": len(t),
            "n_repeat": len(rep), "repeat_rate": round(len(rep) / len(t), 3) if t else 0.0}


def l4_phase_stats(scan_dir: Path) -> dict | None:
    """L4 阶段效能:早停/满卡/复用分布 + P4 翻盘率(卡片契约 `进入P4倾向: <Rating>`)。

    早停率低 = 早停没在省;P4 翻盘率≈0 = 陷阱核可条件化(先测量后动刀)。无卡 → None。
    """
    base = Path(scan_dir) / "details"
    cards = sorted(base.glob("*.md")) if base.is_dir() else []
    if not cards:
        return None
    from autoresearch.agents.utils.rating import parse_rating  # lazy 防环
    n_stop = n_reuse = p4_seen = p4_flips = 0
    for p in cards:
        text = p.read_text(encoding="utf-8")
        if "♻️" in text and "复用" in text:
            n_reuse += 1
            continue
        if "早停因" in text:
            n_stop += 1
        m = _P4_RE.search(text)
        if m:
            p4_seen += 1
            if m.group(1).title() != parse_rating(text):
                p4_flips += 1
    return {"n_cards": len(cards), "n_earlystop": n_stop, "n_reused": n_reuse,
            "n_full": len(cards) - n_stop - n_reuse, "p4_seen": p4_seen, "p4_flips": p4_flips}


def final_ratings(scan_dir: Path) -> dict[str, str]:
    """{code: 最终评级}(finalists → parse_rating(卡)→ verify 降级折回)。与 assemble 同口径。"""
    scan_dir = Path(scan_dir)
    fin = _read(scan_dir / "finalists.csv")
    if fin is None or "code" not in fin.columns:
        return {}
    from autoresearch.agents.utils.rating import parse_rating  # lazy 防环
    from autoresearch.scan.assemble import _apply_verify_downgrade, _load_verify
    vmap = _load_verify(scan_dir)
    out: dict[str, str] = {}
    for code in fin["code"].astype(str).str.zfill(6):
        p = scan_dir / "details" / f"{code}.md"
        if not p.exists():
            continue
        rating = parse_rating(p.read_text(encoding="utf-8"))
        v = vmap.get(code)
        if v and v["verdict"] in ("降级", "否决"):
            rating = _apply_verify_downgrade(rating, v["verdict"])
        out[code] = rating
    return out


def count_buys(scan_dir: Path) -> int:
    """最终买单数(≥OW,verify 折回后)。"""
    return sum(1 for r in final_ratings(scan_dir).values() if r in ("Buy", "Overweight"))


def run_health(scan_dir: Path) -> dict:
    """一次 scan 的体检 dict(artifacts/counts/NaN 降级/churn/L4 阶段/meta 回显)。"""
    scan_dir = Path(scan_dir)
    arts = {a: (scan_dir / a).exists() for a in _ARTIFACTS}
    missing = [a for a, ok in arts.items() if not ok]
    meta = {}
    mp = scan_dir / "meta.json"
    if mp.exists():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}

    def _n(name: str) -> int:
        df = _read(scan_dir / name)
        return len(df) if df is not None else 0

    cards = len(list((scan_dir / "details").glob("*.md"))) if (scan_dir / "details").is_dir() else 0
    rates, degraded = nan_report(scan_dir)
    return {"date": scan_dir.name, "artifacts": arts, "missing": missing,
            "core_missing": sorted(_CORE & set(missing)),
            "counts": {"l1_full": _n("L1_scored_full.csv"), "recall": _n("L1_recall_top1000.csv"),
                       "l2": _n("L2_gbdt_top200.csv"), "finalists": _n("finalists.csv"),
                       "cards": cards, "buys": count_buys(scan_dir)},
            "nan_rates": rates, "degraded_fields": degraded,
            "regime": meta.get("regime"), "l2_engine": meta.get("l2_engine"),
            "weights_source": meta.get("weights_source"),
            "churn": finalist_churn(scan_dir), "l4_phases": l4_phase_stats(scan_dir)}


def write_run_health(scan_dir: Path) -> Path:
    p = Path(scan_dir) / "run_health.json"
    p.write_text(json.dumps(run_health(scan_dir), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def index_md(scan_dir: Path, report_dir: Path) -> str:
    """报告目录导航页:summary/决策卡/trace 链接 + staging 在位 + 上一 run + 健康一行。"""
    scan_dir, report_dir = Path(scan_dir), Path(report_dir)
    h = run_health(scan_dir)
    lines = [f"# 扫描现场索引 — 数据日 {h['date']}(run `{report_dir.name}`)\n",
             "- **读我**:[summary.md](summary.md)(buy-list + 漏斗 + 各阶段概览)"]
    cards = sorted((report_dir / "details").glob("*.md")) if (report_dir / "details").is_dir() else []
    if cards:
        lines.append(f"- **决策卡**({len(cards)} 张):" + "、".join(
            f"[{p.stem}](details/{p.name})" for p in cards[:40]))
    tr = sorted((report_dir / "trace").glob("*")) if (report_dir / "trace").is_dir() else []
    if tr:
        lines.append("- **溯源 trace/**:" + "、".join(f"[{p.name}](trace/{p.name})"
                                                      for p in tr if p.is_file()))
    ok = [a for a, v in h["artifacts"].items() if v]
    lines.append(f"- **staging(中间结果)**:`{scan_dir}/` — 在位:{('、'.join(ok)) or '—'}"
                 + (f";**缺**:{'、'.join(h['missing'])}" if h["missing"] else ""))
    if (scan_dir / "retro").is_dir():
        lines.append(f"- **复盘现场**:`{scan_dir}/retro/`(retro_input.md / attribution.csv)")
    prevs = sorted((p.name for p in report_dir.parent.iterdir()
                    if p.is_dir() and p.name < report_dir.name and (p / "summary.md").exists()),
                   reverse=True)
    if prevs:
        lines.append(f"- **上一 run**:[`{prevs[0]}`](../{prevs[0]}/summary.md)")
    c, ch = h["counts"], h["churn"]
    hl = (f"- **健康一行**:L1 {c['recall']} → L2 {c['l2']} → finalists {c['finalists']} → "
          f"卡 {c['cards']} → 买 {c['buys']}")
    if ch:
        hl += f";finalist 重叠 {ch['n_repeat']}/{ch['n_today']}(vs {ch['prev_date']})"
    if h["degraded_fields"]:
        hl += f";⚠️ 降级字段:{'、'.join(h['degraded_fields'])}"
    lines.append(hl)
    return "\n".join(lines) + "\n"
