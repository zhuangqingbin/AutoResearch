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

  uv run --no-sync python -m autoresearch.scan.tripwire_watch 2026-07-28
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws

_WS_SCAN_ROOT = ws.scan_root()  # B008 修法:默认值须为模块级单例(def 时求值,与旧字面量常量同语义)

_PRICE_RE = re.compile(r"\[价格线\]\s*close\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)\s*(?:→\s*(.*))?")
_DATE_RE = re.compile(r"\[日期线\]\s*(\d{4}-\d{2}-\d{2})\s*(.*)")
_EVENT_RE = re.compile(r"\[事件旗\]\s*([^→\n]+?)\s*(?:→\s*(.*))?$")
# 执行线(2026-08-26 §3 路A · A4)——**入场条件**,与上面三型「持有中的退出条件」不同族:
#   `[执行线] pct_chg <= 3.0 → 当日涨超 3% 放弃本次尾盘入场`
#   `[执行线] pos_in_range < 0.7 → 收在当日区间上 30% 放弃`
# 为什么必须机读:此前卡片的「入场否决」是自由文本,写完那一刻起没有任何机器复核过 ——
# 与「决策卡的失效条件没人读」是同一个病(本模块存在的理由)。而这条尤其要紧:实测
# 卡片写的入场否决方向**反了**(四年全湖 1086 日,收在当日区间上 30% 的票隔夜比全体差
# 0.13~0.27pp,逐年同号),自由文本让这件事整整没被发现过。
_EXEC_RE = re.compile(
    r"\[执行线\]\s*(pct_chg|pos_in_range)\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)\s*(?:→\s*(.*))?")
_DATE_LEAD_DAYS = 3          # 日期线提前几天开始预警(交易日近似 = 日历日)


def date_lead_days() -> int:
    """`scan_config.tripwire.date_lead_days`(缺省 = _DATE_LEAD_DAYS)。"""
    from autoresearch.scan.user_config import knob
    return int(knob("tripwire", "date_lead_days", None, _DATE_LEAD_DAYS))
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
        m = _EXEC_RE.search(line)
        if m:
            out.append({"kind": "exec", "metric": m.group(1), "op": m.group(2),
                        "level": float(m.group(3)),
                        "action": (m.group(4) or "").strip(), "raw": line})
            continue
        m = _EVENT_RE.search(line)
        if m:
            kws = [w.strip() for w in m.group(1).split("|") if w.strip()]
            if kws:
                out.append({"kind": "event", "keywords": kws,
                            "action": (m.group(2) or "").strip(), "raw": line})
    return out


def latest_card(code6: str, scan_root: Path | str = _WS_SCAN_ROOT, *,
                before: str | None = None) -> tuple[str, str] | None:
    """该票**最新一张**卡的 (日期, 正文);无卡 → None。盯梢盯的是最新判断,不是历史。

    `before`(可选,Wave9 final-fix I-2):只考虑**严格早于**这个日期(`YYYY-MM-DD`
    字符串序即日期序)的卡。默认 `None` = 不过滤,维持本函数原语义(日常持仓盯梢
    ——prelude/CLI——今日卡若已存在也纳入候选,因为那正是"目前最新判断")。仅
    `decision_finalize._tripwire_hits`(两尺分歧框专用)会传 `before=analysis_date`:
    该框在 assemble **今日卡已写完之后**才被调用,若不排除今日自己刚写的卡,比对的
    就是"LLM 今天拿着今天收盘写的线"而非"用户昨天在用的那条线",几乎只能测出卡片
    自相矛盾(见 final-review Important-2)。
    """
    root = Path(scan_root)
    if not root.is_dir():
        return None
    for d in sorted((p for p in root.iterdir() if p.is_dir() and p.name[:2] == "20"), reverse=True):
        if before is not None and d.name >= before:
            continue
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
          scan_root: Path | str = _WS_SCAN_ROOT, *,
          card_before: str | None = None) -> list[dict]:
    """对给定持仓码逐条复核盯梢线 → 命中列表(每条含 code/kind/raw/detail)。

    codes 缺省 = `pinned.jsonc` 当日仍生效的保送持仓。任何异常路径都吞掉并继续下一条 ——
    盯梢是 advisory 提醒,不该有能力阻断 prelude。

    `card_before`(Wave9 final-fix I-2,透传给 `latest_card` 同名参数 `before`):日常
    持仓盯梢(prelude/CLI)不传,维持原语义;只有 `decision_finalize._tripwire_hits`
    传自身的 `analysis_date`。
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
        got = latest_card(code, root, before=card_before)
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
                if 0 <= gap <= date_lead_days():
                    hits.append({"code": code, "kind": "date", "card_date": card_date,
                                 "raw": w["raw"],
                                 "detail": f"{w['date']} 还有 {gap} 天"
                                           + (f":{w['action']}" if w["action"] else "")})
            # `kind == "exec"` **故意不在这里判**:执行线是 T+1 尾盘的**入场**条件,
            # 而本函数是「持仓在两次扫描之间的退出盯梢」——两者时点与语义都不同。
            # 它的事后计量在 `scan/outcome.exec_ok`(逐票记「若按此执行会怎样」),
            # 当场执行由人在 T+1 尾盘按卡片那一行做。给它加一条 hit 分支 = 每天对着
            # 已经过去的入场时点报警,是噪音不是信息。
            elif w["kind"] == "event" and titles:
                got_kw = [k for k in w["keywords"] if any(k in t for t in titles)]
                if got_kw:
                    hits.append({"code": code, "kind": "event", "card_date": card_date,
                                 "raw": w["raw"],
                                 "detail": f"新闻标题命中 {'/'.join(got_kw)}"
                                           + (f" → {w['action']}" if w["action"] else "")})
    hits.extend(_overseas_hits(date, codes or [], scan_root))
    return hits


def _overseas_hits(date: str, codes: list[str], scan_root: Path | str) -> list[dict]:
    """D-2:持仓映射名在两窄窗里的已知海外事件(`scan/overseas.py`)。

    **风险可见性,不是触发器**:`kind="overseas"` 只是「这只票的映射对象今晚有事」,
    它不主张因果、不给动作、不改评级 —— 08-26 真跑里 📌 300857(算力链)在 NVDA 盘后
    财报当晚零提示,补的就是这一面。取不到映射 / 无日历 → [](presence-gated)。
    """
    if not codes:
        return []
    try:
        from autoresearch.data.readthrough import load_map
        from autoresearch.scan.overseas import tripwire_rows
    except Exception:  # noqa: BLE001 — 可选层
        return []
    try:
        mapping = (load_map(date) or {}).get("codes") or {}
    except Exception:  # noqa: BLE001
        return []
    scan_dir = Path(scan_root) / date
    out: list[dict] = []
    for code in codes:
        code6 = str(code).zfill(6)
        items = mapping.get(code6) or mapping.get(str(code)) or []
        syms = [it.get("symbol") for it in items if isinstance(it, dict) and it.get("symbol")]
        for line in tripwire_rows(scan_dir, code6, syms):
            out.append({"code": code6, "kind": "overseas", "card_date": None,
                        "raw": "readthrough_map", "detail": line.lstrip("- ")})
    return out


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
