"""价格断言对账(确定性,零 LLM;spec 2026-07-22 dossier design ⑤-2)。

卡片/情报里的价格类断言(某日 涨X% / 涨停)与 lake OHLCV 对账——pr_20260714_006
(intel 捏造涨停)的机制化根治。精度优先:只认领**句内出现本票名称/代码/本股指代**的断言
(防把"科创50 +10%"记到个股头上);缺 bar 的日期跳过(nodata 不算失败)。advisory 用途,
一切失败路径返回空,绝不抛异常。

**语义边界(2026-07-23 终审 C-1 收紧 + round2 复核 R-1 补,四类真卡假阳):抽取器只认领
「某日已实现的单日股价移动」。**四类被排除(否则对账器会对读者输出「分析师捏造股价」的自信
误指控):
  1. 区间/累计移动(`A日→B日 涨X%` 或「累计涨幅达 X%」):日期与 % 之间出现区间标记
     (→/至/到/从/累计)⇒ 弃(华海 600521:`本股 6/09 14.64→7/16 17.63(+20%)` 是累计
     +20%,非 6/09 单日;R-1:`7-16 累计涨幅达 20%` 同族,不靠箭头,靠「累计」一词)。
  2. 前向情景/目标 %:% 前的局部窗出现情景/目标语境(Bull/Base/Bear/情景/目标/EV/延续至/
     看至/上望/上行/下行/p60/R:R)⇒ 弃该 %(普冉 688766 同句里 `延续至 510(+8.5%)` 弃、
     `7/21 单日已实测 +17.5%` 留——按每个 % 匹配点的局部语境判定,非整句一刀切)。
  3. 基本面/非股价 %:% 前后 8 字内出现基本面名词(营收/收入/净利/利润/毛利/EPS/业绩/订单/
     产能/份额/同比/环比/预告/预增/预盈/中报/年报/季报/归母)⇒ 弃(协创 300857:
     `营收同比上涨12%` 是营收%非股价%;R-1:`中报预告 +66%`/`中报预增 +105%` 是业绩
     预测%非股价%,窗内「中报/预告/预增」任一命中即弃)。
  4. 百分区间 `X%~Y%`(R-1):两个 % 由 `~` 相连 ⇒ 整段判定为预测区间,两端都弃(协创
     `中报预告 +247%~+340%`——右端离基本面名词已超 8 字窗,单靠类 3 抓不住,须整段识别)。

**已知简化(终审遗留#1):每句只取首个可认领断言(under-report,非假阳)——上面四类先滤掉
非价格 %,再取首个存活的 % 当断言;同句更靠后的第二条已实现移动会漏抽。**
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

# ── C-1 收紧(2026-07-23):只认「某日已实现单日股价移动」的三类排除词表 ──
# (a) 区间/累计标记:出现在日期与 % 之间 → 「A日→B日 累计涨X%」非单日(华海 6/09→7/16 +20%);
#     「累计」本身也算(R-1:「7-16 累计涨幅达 20%」不靠箭头,靠这个词)
_RANGE_MARKS = ("→", "至", "到", "从", "累计")
# (b) 情景/目标语境(% 之前的局部窗)→ 前向情景 EV/目标价,非已实现(普冉 Bull 延续至510 +8.5%)
_SCENARIO = ("Bull", "Base", "Bear", "bull", "base", "bear", "情景", "目标", "EV",
             "延续至", "看至", "上望", "上行", "下行", "p60", "R:R", "R：R")
# (c) 基本面/非股价 %(% 前后 8 字内)→ 营收/净利/同比 涨,非股价涨(协创 营收同比 +12%)
#     (毛利率被「毛利」覆盖,不单列;R-1 补业绩预告类:预告/预增/预盈/中报/年报/季报/归母
#     ——「中报预告 +66%」是业绩预测%非股价%,同族)
_FUND = ("营收", "收入", "净利", "利润", "毛利", "EPS", "业绩", "订单",
         "产能", "份额", "同比", "环比",
         "预告", "预增", "预盈", "中报", "年报", "季报", "归母")
_CTX_BACK = 14      # 情景语境向前看的字符窗(延续至/目标/看至 通常紧邻 % 之前)
_FUND_WIN = 8       # 基本面名词判定窗(brief:% 前后 8 字内)
# (d) 百分区间 `X%~Y%`(R-1,协创 `中报预告 +247%~+340%`):两个 % 由 `~` 相连 ⇒ 整段
#     判定为预测区间,两端都弃(右端常离基本面名词超 8 字窗,类 c 单独抓不住)
_PCT_TILDE_RANGE = re.compile(r"[+-]?\d+(?:\.\d+)?%\s*~\s*[+-]?\d+(?:\.\d+)?%")

# ── round3 立项(2026-07-23):指数名黑名单——句级自指(个股/本股等)夹带指数名时,
#    指数自己的涨跌% 不该被记到本票头上(协创 07-21 真卡:句含"个股"但 +10% 是科创50 涨幅)──
_INDEX_NAMES = ("科创50", "沪深300", "上证指数", "上证综指", "深证成指", "深成指",
                "创业板指", "北证50", "中证500", "中证1000", "恒生", "纳指", "标普")


def _near_index_name(sent: str, pct_pos: int, window: int = 12) -> bool:
    """% 候选左邻 window 字内出现指数名 → 该 % 属指数,不认领给本票。"""
    left = sent[max(0, pct_pos - window):pct_pos]
    return any(ix in left for ix in _INDEX_NAMES)


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


def _num_pos(pm: re.Match) -> int:
    """% 匹配里「数字」的起始位——语境检查全锚在这里,不锚在 match.start():verb 支路(如"超跌…
    延续至 510(**+8.5")会从很靠左的动词起匹配,窗口锚 match.start 会漏掉数字前的情景/区间标记。"""
    return pm.start("num") if pm.group("num") is not None else pm.start("num2")


def _range_left(sent: str, dates: list[re.Match], dm: re.Match, npos: int, gap: int = 16) -> int:
    """从最近在前日期 dm 向前吸收「紧邻(≤gap 字)的更早日期」成日期簇(`6/09 14.64→7/16` = 一个
    区间簇),返回簇最左日期的 start——区间标记须从这里查到数字,才能逮到落在两日期之间的 →/至;
    gap 卡死簇宽度:相隔很远的两个同名日期(普冉句里两个 7/21 相隔 ~100 字)不会被误并成区间。"""
    idx = dates.index(dm)
    start = dm.start()
    i = idx - 1
    while i >= 0 and dates[i].end() <= npos and start - dates[i].end() <= gap:
        start = dates[i].start()
        i -= 1
    return start


def _is_realized_price_pct(sent: str, dm: re.Match, pm: re.Match, dates: list[re.Match]) -> bool:
    """pm(% 匹配)配 dm(其最近在前日期)是否为一条「已实现单日股价移动」。六类排除:
      · 区间幻影(如"5-10%"):_DATE 把 "5-10" 当日期、_PCT 把 "-10%" 当字面负号,两匹配抢同段字符;
      · 百分区间 `X%~Y%`(R-1):该数字与另一个 % 由 `~` 相连 → 预测区间,两端都非单日已实现;
      · 区间标记(→/至/到/从/累计)落在日期簇与数字之间 → 累计/区间移动非单日(华海
        6/09→7/16 +20%;R-1:「累计涨幅达 20%」同族,靠词不靠箭头);
      · 数字之前局部窗有情景/目标语境 → 前向情景 EV/目标价(普冉 延续至510 +8.5%);
      · 数字左邻 12 字内有指数名(round3)→ 该 % 属指数非本票,不认领(协创 07-21:句含
        "个股"但 +10% 是科创50 涨幅);
      · 数字前后 8 字内有基本面名词(含 R-1 补的 预告/预增/预盈/中报/年报/季报/归母)→
        营收/净利/业绩预告% 非股价%(协创 营收同比 +12%、中报预告 +66%)。"""
    if _overlaps(dm.span(), pm.span()):
        return False
    if any(_overlaps(pm.span(), rm.span()) for rm in _PCT_TILDE_RANGE.finditer(sent)):
        return False
    npos = _num_pos(pm)
    if any(r in sent[_range_left(sent, dates, dm, npos):npos] for r in _RANGE_MARKS):
        return False
    if any(k in sent[max(0, npos - _CTX_BACK):npos] for k in _SCENARIO):
        return False
    if _near_index_name(sent, npos):
        return False
    return not any(k in sent[max(0, npos - _FUND_WIN):pm.end() + _FUND_WIN] for k in _FUND)


def _first_realized_pct(sent: str, dates: list[re.Match]):
    """句内首个「已实现单日股价移动」%:每个 % 配其最近在前日期(缺则退回首日期),先滤掉区间/
    情景/基本面 %,取首个存活者。返回 (带号数值, 配对日期匹配) 或 None(每句只取首个=已知简化)。"""
    for pm in _PCT.finditer(sent):
        prev = [d for d in dates if d.end() <= pm.start()]
        dm = prev[-1] if prev else dates[0]
        if _is_realized_price_pct(sent, dm, pm, dates):
            return _pct_value(pm), dm
    return None


def extract_price_claims(text: str, *, name: str, code6: str, year_hint: int) -> list[dict]:
    out: list[dict] = []
    for sent in _SENT_SPLIT.split(text or ""):
        if not sent.strip() or not _own_sentence(sent, name, code6):
            continue
        dates = list(_DATE.finditer(sent))
        if not dates:
            continue
        hit = _first_realized_pct(sent, dates)
        if hit is not None:
            val, dm = hit
            out.append({"date": _fmt_date(dm, year_hint), "kind": "pct",
                        "value": val, "snippet": sent.strip()[:60]})
            continue
        lm = _LIMIT.search(sent)
        if lm:
            out.append({"date": _fmt_date(dates[0], year_hint), "kind": "limit", "value": None,
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
