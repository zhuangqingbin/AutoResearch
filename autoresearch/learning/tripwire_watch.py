#!/usr/bin/env python3
"""持仓盯梢线日检 —— 两次扫描之间唯一盯着持仓的腿(确定性,零 LLM)。

design: docs/specs/2026-07-28-wave7-unified-roadmap-design.md §6 P2

L4 决策卡的「失效」一直是写给人看的自由文本(实例:「收盘破 303.28 → 隔日清仓」),
信息在,但没有任何机器读得了它 —— 于是卡片写完那一刻起,直到下一次扫描为止,持仓
处于**无人盯梢**状态。Wave7 起卡片额外写一段结构化「盯梢线」,本模块每日对湖复核。

三型(与 `.claude/agents/l4-card.md` 的契约逐字对应):
  - `[价格线] close < 303.28 → 隔日清仓`     —— 对当日收盘
  - `[日期线] 2026-08-25 中报披露 → 验证日`   —— 临近(≤3 日)即预警
  - `[事件旗] 减持|质押|问询 → 触发即重研`     —— 对个股新闻标题

**覆盖率诚实声明**:事件旗只扫 `stock_news_em` 标题(公告面 anns_d 端点已退役,本项目无
权限)。查不到不等于没发生 —— 命中是硬证据,**没命中只是"这条渠道没看见"**,渲染时明写。

  uv run --no-sync python -m autoresearch.learning.tripwire_watch 2026-07-28
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

_PRICE_RE = re.compile(r"\[价格线\]\s*close\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)\s*(?:→\s*(.*))?")
_DATE_RE = re.compile(r"\[日期线\]\s*(\d{4}-\d{2}-\d{2})\s*(.*)")
_EVENT_RE = re.compile(r"\[事件旗\]\s*([^→\n]+?)\s*(?:→\s*(.*))?$")
_DATE_LEAD_DAYS = 3          # 日期线提前几天开始预警(交易日近似 = 日历日)
_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}


def parse_tripwires(card_text: str) -> list[dict]:
    """卡片正文 → 结构化盯梢线列表(认不出的行不进表,留给人眼)。"""
    out: list[dict] = []
    for raw in (card_text or "").splitlines():
        line = raw.strip().lstrip("-").strip()
        m = _PRICE_RE.search(line)
        if m:
            out.append({"kind": "price", "op": m.group(1), "level": float(m.group(2)),
                        "action": (m.group(3) or "").strip(), "raw": line})
            continue
        m = _DATE_RE.search(line)
        if m:
            out.append({"kind": "date", "date": m.group(1),
                        "action": m.group(2).strip(), "raw": line})
            continue
        m = _EVENT_RE.search(line)
        if m:
            kws = [w.strip() for w in m.group(1).split("|") if w.strip()]
            if kws:
                out.append({"kind": "event", "keywords": kws,
                            "action": (m.group(2) or "").strip(), "raw": line})
    return out


def latest_card(code6: str, scan_root: Path | str = "context/scan") -> tuple[str, str] | None:
    """该票**最新一张**卡的 (日期, 正文);无卡 → None。盯梢盯的是最新判断,不是历史。"""
    root = Path(scan_root)
    if not root.is_dir():
        return None
    for d in sorted((p for p in root.iterdir() if p.is_dir() and p.name[:2] == "20"), reverse=True):
        p = d / "details" / f"{code6}.md"
        if p.exists():
            try:
                return d.name, p.read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
    return None


def _close_today(code6: str, scan_dir: Path) -> float | None:
    for fname in ("L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv"):
        p = scan_dir / fname
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, dtype={"code": str}, usecols=["code", "close"])
        except Exception:  # noqa: BLE001
            continue
        sub = df[df["code"].astype(str).str.zfill(6) == code6]
        if len(sub):
            v = pd.to_numeric(sub.iloc[0]["close"], errors="coerce")
            return None if pd.isna(v) else float(v)
    return None


def _news_titles(code6: str, date: str) -> list[str] | None:
    """个股新闻标题;取不到 → None(**不是空列表**:分不清「没新闻」与「没查到」)。"""
    try:
        from autoresearch.data.cache import get_or_fetch
        df = get_or_fetch("stock_news_em", {"symbol": code6, "as_of": date.replace("-", "")},
                          today=date)
        if df is None or not len(df):
            return None
        col = next((c for c in df.columns if "标题" in str(c) or "title" in str(c).lower()), None)
        return None if col is None else [str(x) for x in df[col].tolist()]
    except Exception:  # noqa: BLE001
        return None


def check(date: str, codes: list[str] | None = None,
          scan_root: Path | str = "context/scan") -> list[dict]:
    """对给定持仓码逐条复核盯梢线 → 命中列表(每条含 code/kind/raw/detail)。

    codes 缺省 = `pinned.jsonc` 当日仍生效的保送持仓。任何异常路径都吞掉并继续下一条 ——
    盯梢是 advisory 提醒,不该有能力阻断 prelude。
    """
    root = Path(scan_root)
    scan_dir = root / date
    if codes is None:
        try:
            from autoresearch.scan.user_config import load_pinned
            codes = [str(e.get("code", "")).split(".")[0].zfill(6)
                     for e in (load_pinned(date).get("kept") or []) if e.get("code")]
        except Exception:  # noqa: BLE001
            codes = []
    hits: list[dict] = []
    try:
        as_of = datetime.strptime(date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return hits
    for code in codes:
        got = latest_card(code, root)
        if not got:
            continue
        card_date, text = got
        wires = parse_tripwires(text)
        if not wires:
            continue
        close = _close_today(code, scan_dir)
        titles = None
        if any(w["kind"] == "event" for w in wires):
            titles = _news_titles(code, date)
        for w in wires:
            if w["kind"] == "price" and close is not None:
                if _OPS[w["op"]](close, w["level"]):
                    hits.append({"code": code, "kind": "price", "card_date": card_date,
                                 "raw": w["raw"],
                                 "detail": f"收盘 {close:.2f} {w['op']} {w['level']:.2f}"
                                           + (f" → {w['action']}" if w["action"] else "")})
            elif w["kind"] == "date":
                try:
                    gap = (datetime.strptime(w["date"], "%Y-%m-%d") - as_of).days
                except ValueError:
                    continue
                if 0 <= gap <= _DATE_LEAD_DAYS:
                    hits.append({"code": code, "kind": "date", "card_date": card_date,
                                 "raw": w["raw"],
                                 "detail": f"{w['date']} 还有 {gap} 天"
                                           + (f":{w['action']}" if w["action"] else "")})
            elif w["kind"] == "event" and titles:
                got_kw = [k for k in w["keywords"] if any(k in t for t in titles)]
                if got_kw:
                    hits.append({"code": code, "kind": "event", "card_date": card_date,
                                 "raw": w["raw"],
                                 "detail": f"新闻标题命中 {'/'.join(got_kw)}"
                                           + (f" → {w['action']}" if w["action"] else "")})
    return hits


def render_line(hits: list[dict], n_watched: int) -> str | None:
    """prelude 当日件行(与 📐/🔁/🚪 同款格式);无持仓被盯 → None(presence-gated)。"""
    if not n_watched:
        return None
    if not hits:
        return (f"⚡tripwire:{n_watched} 只持仓盯梢线全部未触发"
                "(事件旗仅 news_em 标题覆盖,未命中≠未发生)")
    parts = [f"{h['code']} {h['detail']}" for h in hits[:4]]
    more = f" 等 {len(hits)} 条" if len(hits) > 4 else ""
    return f"⚡tripwire 触发:{'; '.join(parts)}{more} —— 持仓需人裁"


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import date as _date

    ap = argparse.ArgumentParser(description="持仓盯梢线日检(确定性,零 LLM)")
    ap.add_argument("date", nargs="?", default=_date.today().isoformat())
    a = ap.parse_args(argv)
    try:
        from autoresearch.scan.user_config import load_pinned
        n = len([e for e in (load_pinned(a.date).get("kept") or []) if e.get("code")])
    except Exception:  # noqa: BLE001
        n = 0
    hits = check(a.date)
    line = render_line(hits, n)
    print(line or "[tripwire_watch] 无保送持仓(presence-gated,跳过)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
