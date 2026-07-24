"""dossier 判例聚合(spec ⑦;确定性,零 LLM):t1 快环逐笔 + retro 归因 → per-code 战绩。

pnl 口径与 render_ledger_report 对齐;**方向计数 n_dir 含 sealed 是本档案的有意口径**
(一字板不影响"方向判对没判对",只影响"吃不吃得到")。行业超额优先(`excess_ind` 缺退
`excess`)、sealed(一字板)不计可实现 pnl、Hold(verdict「—」)不算方向票、UW/Sell 顺
方向收益 = 负超额为赢(sign=-1)。保送票本就不进 t1 账本(2026-07-17 用户裁定「保送
不算」),此处天然继承该口径。
"""
from __future__ import annotations

import json
from pathlib import Path

_T1_LEDGER = Path("context/learning/t1_review.jsonl")
_DIR_SIGN = {"Overweight": 1.0, "Buy": 1.0, "Underweight": -1.0, "Sell": -1.0}
_RETRO_WINDOW = 20


def code_track_record(code6: str, *, ledger_path: Path | str | None = None) -> dict:
    """t1 快环按票聚合:方向判定 n/准/不准/中性 + 顺方向超额均值(pp)。缺账本 → 全零。

    `n_dir`(方向判定笔数)含 sealed(一字板)行;`n_pnl`(≤n_dir)是刨去 sealed 后
    可实现的 pnl 样本数 —— 两者不相等是常态,不是 bug。
    """
    p = Path(ledger_path or _T1_LEDGER)
    out = {"n_dir": 0, "right": 0, "wrong": 0, "neutral": 0, "avg_pp": None}
    if not p.exists():
        return out
    code6 = str(code6).zfill(6)
    pnl: list[float] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except Exception:  # noqa: BLE001 — 坏行跳过,余行照聚
            continue
        if str(r.get("code", "")).zfill(6) != code6:
            continue
        v = r.get("verdict")
        if v not in ("准", "不准", "中性"):
            continue
        out["n_dir"] += 1
        out["right"] += v == "准"
        out["wrong"] += v == "不准"
        out["neutral"] += v == "中性"
        sign = _DIR_SIGN.get(r.get("rating"))
        ex = r.get("excess_ind") if r.get("excess_ind") is not None else r.get("excess")
        if sign is not None and ex is not None and not r.get("sealed"):
            pnl.append(sign * float(ex))
    if pnl:
        out["avg_pp"] = sum(pnl) / len(pnl) * 100
    out["n_pnl"] = len(pnl)
    return out


def retro_buckets(code6: str, *, scan_root: str | Path = "context/scan",
                  max_days: int = _RETRO_WINDOW) -> dict[str, int]:
    """retro 归因按票聚合:近 max_days 个有归因的扫描日,该票的桶计数(空桶不计)。"""
    import pandas as pd
    root = Path(scan_root)
    out: dict[str, int] = {}
    if not root.exists():
        return out
    code6 = str(code6).zfill(6)
    days = sorted((p for p in root.iterdir()
                   if p.is_dir() and (p / "retro" / "attribution.csv").exists()),
                  key=lambda p: p.name, reverse=True)[:max_days]
    for d in days:
        try:
            df = pd.read_csv(d / "retro" / "attribution.csv", dtype={"code": str},
                             usecols=["code", "bucket"])
            sub = df[df["code"].astype(str).str.zfill(6) == code6]
            if not len(sub):
                continue
            b = str(sub.iloc[0].get("bucket") or "").strip()
            if b and b.lower() != "nan":
                out[b] = out.get(b, 0) + 1
        except Exception:  # noqa: BLE001 — 单日坏档不挡聚合
            continue
    return out


def render_precedent_value(precedent_n: int, rec: dict) -> str:
    """摘要「判例:」实值:入围计数(builder 现文本,parity)+ t1 战绩尾巴(有才附)。"""
    from autoresearch.dossier import builder
    base = builder.render_summary_calc(None, precedent_n)["判例"]
    if not rec or not rec.get("n_dir"):
        return base
    tail = (f";t1 方向 {rec['n_dir']} 笔 准{rec['right']}/不准{rec['wrong']}"
            f"/中性{rec.get('neutral', 0)}")
    if rec.get("avg_pp") is not None:
        n_pnl = rec.get("n_pnl")
        pnl_tag = f"(pnl n={n_pnl})" if n_pnl is not None else ""
        tail += f",顺方向超额均值 {rec['avg_pp']:+.1f}pp{pnl_tag}"
    return base + tail


def render_track_block(code6: str, *, scan_root: str | Path = "context/scan",
                       ledger_path: Path | str | None = None) -> str:
    """§7 尾部「覆盖战绩」确定性块;无任何读数 → ""(presence-gated)。"""
    rec = code_track_record(code6, ledger_path=ledger_path)
    buckets = retro_buckets(code6, scan_root=scan_root)
    lines: list[str] = []
    if rec.get("n_dir"):
        n_pnl = rec.get("n_pnl")
        pnl_tag = f"(pnl n={n_pnl})" if n_pnl is not None else ""
        avg = (f",顺方向超额均值 {rec['avg_pp']:+.1f}pp{pnl_tag}"
               if rec.get("avg_pp") is not None else "")
        small = " (⚠n<10 只看不裁)" if rec["n_dir"] < 10 else ""
        lines.append(f"- **t1 快环战绩**:方向判定 {rec['n_dir']} 笔,"
                     f"准{rec['right']}/不准{rec['wrong']}/中性{rec['neutral']}{avg}{small}")
    if buckets:
        seg = "、".join(f"{k}×{v}" for k, v in sorted(buckets.items()))
        lines.append(f"- **retro 归因桶(近{_RETRO_WINDOW}日)**:{seg}")
    if not lines:
        return ""
    return "### 📊 覆盖战绩(确定性聚合,δ 自动刷新)\n" + "\n".join(lines)
