"""价格断言对账(确定性,零 LLM;spec 2026-07-22 dossier design ⑤-2)。

卡片/情报里的价格类断言(某日 涨X% / 涨停)与 lake OHLCV 对账——pr_20260714_006
(intel 捏造涨停)的机制化根治。精度优先:只认领**句内出现本票名称/代码/本股指代**的断言
(防把"科创50 +10%"记到个股头上);缺 bar 的日期跳过(nodata 不算失败)。advisory 用途,
一切失败路径返回空,绝不抛异常。
"""
from __future__ import annotations

import contextlib
import re

_SENT_SPLIT = re.compile(r"[。;;\n]")
# 日期:2026-07-21 / 07-21 / 7/21 / 7月21日(可带年)
_DATE = re.compile(r"(?:(20\d{2})[-/年])?(\d{1,2})[-/月](\d{1,2})日?")
_PCT = re.compile(r"(?:上涨|大涨|涨|下跌|大跌|跌|涨幅|跌幅)[^%。;;\n]{0,12}?([+-]?\d+(?:\.\d+)?)\s*%"
                  r"|([+-]\d+(?:\.\d+)?)\s*%")
_LIMIT = re.compile(r"涨停|跌停")
_SELF_MARKS = ("本股", "个股", "该股", "本票")


def _own_sentence(sent: str, name: str, code6: str) -> bool:
    if name and name in sent:
        return True
    if code6 and code6 in sent:
        return True
    return any(m in sent for m in _SELF_MARKS)


def _fmt_date(m: re.Match, year_hint: int) -> str:
    y = int(m.group(1) or year_hint)
    return f"{y:04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"


def extract_price_claims(text: str, *, name: str, code6: str, year_hint: int) -> list[dict]:
    out: list[dict] = []
    for sent in _SENT_SPLIT.split(text or ""):
        if not sent.strip() or not _own_sentence(sent, name, code6):
            continue
        dm = _DATE.search(sent)
        if not dm:
            continue
        date = _fmt_date(dm, year_hint)
        pm = _PCT.search(sent)
        if pm:
            val = float(pm.group(1) or pm.group(2))
            out.append({"date": date, "kind": "pct", "value": val, "snippet": sent.strip()[:60]})
            continue
        if _LIMIT.search(sent):
            out.append({"date": date, "kind": "limit", "value": None, "snippet": sent.strip()[:60]})
    return out


def _limit_floor(code6: str) -> float:
    # 创业板 300/301、科创板 688/689 = 20cm;其余按 10cm 主板口径(ST 不细分,advisory 容忍)
    return 19.0 if code6.startswith(("30", "68")) else 9.5


def reconcile_claims(claims: list[dict], bars: dict[str, float], *,
                     code6: str, tol_pp: float = 1.5) -> list[dict]:
    bad: list[dict] = []
    for c in claims:
        actual = bars.get(c["date"])
        if actual is None:                      # nodata:非交易日/湖缺 → 跳过,不算失败
            continue
        if c["kind"] == "pct":
            if abs(abs(float(c["value"])) - abs(float(actual))) > tol_pp:
                bad.append({**c, "claimed": float(c["value"]), "actual": round(float(actual), 2)})
        elif c["kind"] == "limit" and abs(float(actual)) < _limit_floor(code6):
            bad.append({**c, "claimed": None, "actual": round(float(actual), 2)})
    return bad


def bars_for(code6: str, dates: list[str], today: str) -> dict[str, float]:
    """按日整市场 daily(湖命中为主,universe 已预热)→ 过滤本票 pct_chg。失败 → {}。"""
    out: dict[str, float] = {}
    for dd in sorted(set(dates)):
        with contextlib.suppress(Exception):
            from autoresearch.data.cache import get_or_fetch
            df = get_or_fetch("daily", {"trade_date": dd}, today=today)
            if df is None or not len(df) or "ts_code" not in df.columns:
                continue
            hit = df[df["ts_code"].astype(str).str.startswith(code6)]
            if len(hit):
                out[dd] = float(hit.iloc[0]["pct_chg"])
    return out


def audit_card_text(text: str, *, name: str, code6: str, date: str, bars_fn=bars_for) -> dict:
    year_hint = int(str(date)[:4])
    claims = extract_price_claims(text or "", name=name, code6=code6, year_hint=year_hint)
    if not claims:
        return {"n_claims": 0, "mismatches": []}
    bars = bars_fn(code6, [c["date"] for c in claims], date) or {}
    return {"n_claims": len(claims), "mismatches": reconcile_claims(claims, bars, code6=code6)}
