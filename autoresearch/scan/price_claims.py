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
_PCT = re.compile(r"(?P<verb>上涨|大涨|涨|下跌|大跌|跌|涨幅|跌幅)[^%。;;\n]{0,12}?(?P<num>[+-]?\d+(?:\.\d+)?)\s*%"
                  r"|(?P<num2>[+-]\d+(?:\.\d+)?)\s*%")
_LIMIT = re.compile(r"涨停|跌停")
_SELF_MARKS = ("本股", "个股", "该股", "本票")
_UP_VERBS = ("上涨", "大涨", "涨", "涨幅")
_DOWN_VERBS = ("下跌", "大跌", "跌", "跌幅")


def _own_sentence(sent: str, name: str, code6: str) -> bool:
    if name and name in sent:
        return True
    if code6 and code6 in sent:
        return True
    return any(m in sent for m in _SELF_MARKS)


def _fmt_date(m: re.Match, year_hint: int) -> str:
    y = int(m.group(1) or year_hint)
    return f"{y:04d}{int(m.group(2)):02d}{int(m.group(3)):02d}"


def _pct_value(pm: re.Match) -> float:
    """verb 缺席(纯 +/- 字面量分支)→ 保留字面正负;verb 在场且数字无字面符号 → 按动词方向定号
    (下跌/大跌/跌/跌幅 → 负,上涨/大涨/涨/涨幅 → 正);字面 +/- 优先于动词方向。"""
    verb = pm.group("verb")
    if not verb:
        return float(pm.group("num2"))
    raw = pm.group("num")
    val = float(raw)
    if raw[0] in "+-":
        return val
    return -val if verb in _DOWN_VERBS else val


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


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
            if _overlaps(dm.span(), pm.span()):
                # 区间幻影(如"5-10%"):_DATE 把 "5-10" 当日期、_PCT 把 "-10%" 当字面负号,
                # 两个匹配抢同一段字符 → 不是真实的"某日涨跌X%"断言,整条跳过
                continue
            val = _pct_value(pm)
            out.append({"date": date, "kind": "pct", "value": val, "snippet": sent.strip()[:60]})
            continue
        lm = _LIMIT.search(sent)
        if lm:
            out.append({"date": date, "kind": "limit", "value": None,
                        "dir": 1 if lm.group() == "涨停" else -1,
                        "snippet": sent.strip()[:60]})
    return out


def _limit_floor(code6: str) -> float:
    # 创业板 300/301、科创板 688/689 = 20cm;其余按 10cm 主板口径(ST 不细分,advisory 容忍)
    # 已知简化:北交所(43/83/87/92 开头,30% 板)未细分,按 9.5 处理(advisory 容忍,非结算口径)
    return 19.0 if code6.startswith(("30", "68")) else 9.5


def reconcile_claims(claims: list[dict], bars: dict[str, float], *,
                     code6: str, tol_pp: float = 1.5) -> list[dict]:
    bad: list[dict] = []
    for c in claims:
        actual = bars.get(c["date"])
        if actual is None:                      # nodata:非交易日/湖缺 → 跳过,不算失败
            continue
        actual = float(actual)
        if c["kind"] == "pct":
            if abs(float(c["value"]) - actual) > tol_pp:          # 有号对账,不再抹方向
                bad.append({**c, "claimed": float(c["value"]), "actual": round(actual, 2)})
        elif c["kind"] == "limit":
            d = c.get("dir")
            if d == 1:                                            # 涨停:实涨须 >= floor
                mismatch = actual < _limit_floor(code6)
            elif d == -1:                                         # 跌停:实跌须 <= -floor
                mismatch = actual > -_limit_floor(code6)
            else:                                                 # 无 dir(手搭 dict,旧契约)→ 幅度口径不辨涨跌停
                mismatch = abs(actual) < _limit_floor(code6)
            if mismatch:
                bad.append({**c, "claimed": None, "actual": round(actual, 2)})
    return bad


def bars_for(code6: str, dates: list[str], today: str) -> dict[str, float]:
    """按日整市场 daily(湖命中为主,universe 已预热)→ 过滤本票 pct_chg。失败 → {}。"""
    dates = dates or []
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
    try:
        year_hint = int(str(date)[:4])
    except (ValueError, TypeError):    # date 空/非数字(advisory 入口,禁止抛异常上溯)
        return {"n_claims": 0, "mismatches": []}
    claims = extract_price_claims(text or "", name=name, code6=code6, year_hint=year_hint)
    if not claims:
        return {"n_claims": 0, "mismatches": []}
    bars = bars_fn(code6, [c["date"] for c in claims], date) or {}
    return {"n_claims": len(claims), "mismatches": reconcile_claims(claims, bars, code6=code6)}
