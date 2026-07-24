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
from datetime import datetime
from pathlib import Path

import pandas as pd

# 关键产物在位表(缺 = 流程段没跑/失败;market_view 是可选段,缺了只提示不报错。
# watchlist_status.csv 随观察单日检退役摘除,fb_20260714_002)
_ARTIFACTS = ["L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv",
              "finalists.csv", "market_view.md", "verify.csv",
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


def anns_empty_rate(scan_dir: Path) -> float | None:
    """L3_news 空稿率。=1.0 为 **expected 非告警**(anns_d 已退役 2026-07-18:无接口权限,
    结构化公告标题流由 stock_news_em 头条 + l4-intel 活体盲搜双重覆盖)。无目录 → None。
    键与数值保留供下游消费,expected 语义见 `run_health` 并列布尔 `anns_expected`。
    Wave4 Task1:`index_md` 现会据此(`anns_expected`)渲染一行「公告标题流不可用」——
    此前只在 `run_health.json` 里挂 `anns_expected=True`,报告正文完全无感,断链留痕。"""
    d = Path(scan_dir) / "L3_news"
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        return None
    def _n(p: Path) -> int:
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
            return len(v) if isinstance(v, list) else 0
        except Exception:  # noqa: BLE001 — 坏 JSON 记空
            return 0
    empty = sum(1 for p in files if _n(p) == 0)
    return round(empty / len(files), 3)


def northbound_probe(scan_dir: Path) -> dict | None:
    """northbound 召回通道空转读数:该路召回票数 + 其 hk_ratio NaN 率(=1.0 → quota 白占)。

    只取证不动结构(spec 2026-07-05 wave §顺带修);坐实后另走 proposal 人拍板。
    """
    df = _read(Path(scan_dir) / "L1_recall_top1000.csv")
    if df is None or "recall_channels" not in df.columns:
        return None
    sub = df[df["recall_channels"].astype(str).str.contains("northbound", na=False)]
    if not len(sub):
        return {"n": 0, "hk_nan": None}
    nanr = (round(float(pd.to_numeric(sub["hk_ratio"], errors="coerce").isna().mean()), 3)
            if "hk_ratio" in sub.columns else None)
    return {"n": int(len(sub)), "hk_nan": nanr}


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


# D3 清欠(spec 2026-07-12 P0-1):这四本"存在但不会自己长大"的账本现已纳入 prelude._ledgers()
# 白名单(见 autoresearch/scan/prelude.py),本节验它们是否真的被刷新。
_D3_LEDGERS = ("channel_ledger", "gate_ledger", "zero_buy_ledger", "changelog_ledger")


def ledger_freshness(scan_dir: Path, learning_root: Path | str | None = None) -> dict:
    """账本新鲜度体检(P0-1 裁决法产物):复盘欠账日数 + 四账本 mtime 滞后 + 两本买单计数一致性。

    - `pending_retro_days`/`pending_retro_list`:有 `retro/attribution.csv`(fwd 已实现,
      `attribute()` 才会成功落盘)但无 `retro/done.json` 的天数——纯本地文件判定,**刻意不复用**
      `retro.pending_days()`(它要连 `factor_lab._pro()`/交易日历,实测每次 ~0.5-0.8s;`run_health`
      被 assemble/index_md 等到处调用,接上会把网络依赖/延迟传染到全仓库测试套件)。语义上是它的
      保守子集(只数"已确认欠账"的天,不判"是否够资格复盘"这层日历逻辑,那是 prelude 里
      `retro_pending` 步骤 + `_retro_input_nag` 的管辖)。
    - `ledger_lag_days`:`_D3_LEDGERS` 各自报告 mtime 落后多少个 scan 日(从未生成过 → None)。
    - `buy_count_consistent`/`buy_count_mismatches`:journal vs zero_buy_ledger 重叠日买单计数
      是否一致——D5 口径统一后应恒为 True,留作日后再分叉的回归检测器;无重叠日 → None(非误判)。

    presence-gated、每项各自 try/except:任一子计算失败不拖垮整体,退化为空/None。全函数零网络。
    """
    scan_dir = Path(scan_dir)
    scan_root = scan_dir.parent
    day_dirs = sorted((p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20"),
                      key=lambda p: p.name) if scan_root.exists() else []
    days = [p.name for p in day_dirs]

    pending = sorted(p.name for p in day_dirs
                     if (p / "retro" / "attribution.csv").exists()
                     and not (p / "retro" / "done.json").exists())

    troot = Path(learning_root) if learning_root else Path("reports/learning")
    lag: dict[str, int | None] = {}
    for name in _D3_LEDGERS:
        p = troot / f"{name}.md"
        if not p.exists():
            lag[name] = None
            continue
        try:
            mdate = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
            lag[name] = sum(1 for d in days if d > mdate)
        except Exception:  # noqa: BLE001
            lag[name] = None

    consistent: bool | None = None
    mismatches: list[str] = []
    try:
        from autoresearch.learning import (
            journal as _journal_mod,  # lazy 防环
            zero_buy_ledger as _zbl_mod,
        )
        jr, zr = _journal_mod.roll(scan_root), _zbl_mod.roll(scan_root)
        if len(jr) and len(zr) and "buys" in jr.columns and "n_bought" in zr.columns:
            jm, zm = jr.set_index("date")["buys"], zr.set_index("date")["n_bought"]
            common = jm.index.intersection(zm.index)
            for dte in common:
                jv, zv = jm.loc[dte], zm.loc[dte]
                if pd.notna(jv) and pd.notna(zv) and int(jv) != int(zv):
                    mismatches.append(str(dte))
            if len(common):
                consistent = not mismatches
    except Exception:  # noqa: BLE001
        consistent = None

    return {"pending_retro_days": len(pending), "pending_retro_list": pending,
            "ledger_lag_days": lag, "buy_count_consistent": consistent,
            "buy_count_mismatches": mismatches}


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
    anns_rate = anns_empty_rate(scan_dir)
    return {"date": scan_dir.name, "artifacts": arts, "missing": missing,
            "core_missing": sorted(_CORE & set(missing)),
            "counts": {"l1_full": _n("L1_scored_full.csv"), "recall": _n("L1_recall_top1000.csv"),
                       "l2": _n("L2_gbdt_top200.csv"), "finalists": _n("finalists.csv"),
                       "cards": cards, "buys": count_buys(scan_dir)},
            "nan_rates": rates, "degraded_fields": degraded,
            # anns_d 已退役(2026-07-18):=1.0 是 expected(no-permission·covered by
            # news_em+intel),非告警。键名/数值原样保留(下游兼容),expected 语义走并列布尔。
            "anns_empty_rate": anns_rate,
            "anns_expected": anns_rate is None or anns_rate >= 1.0,
            "northbound": northbound_probe(scan_dir),
            "regime": meta.get("regime"), "l2_engine": meta.get("l2_engine"),
            "weights_source": meta.get("weights_source"),
            "churn": finalist_churn(scan_dir), "l4_phases": l4_phase_stats(scan_dir),
            "ledger_freshness": ledger_freshness(scan_dir)}


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
    if h["anns_expected"]:
        # anns_d 已退役(2026-07-18):此前只在 run_health.json 里挂 anns_expected=True,报告
        # 正文完全无感——这正是 news_n/news_sent/news_head 三个扫描日全为 0 却无人察觉的成因
        # 之一(线 D 退役配套,断链必须留痕)。expected 语义不变,只是从"静默"改为显式一行。
        lines.append("- **公告标题流**:不可用(anns_d 已退役,详见 `run_health.json` "
                     "`anns_empty_rate`)")
    return "\n".join(lines) + "\n"
